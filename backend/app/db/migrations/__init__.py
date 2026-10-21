"""Explicit, transactional SQLite version-foundation migration.

No database is opened at import. Callers must name an authorized workspace root.
Default is dry-run; credential-bearing databases require a separately recorded
approval/recovery decision. This module never copies a business database.
"""
from __future__ import annotations
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

VERSION = '0001_document_versions'
SQL_FILE = Path(__file__).with_name(VERSION + '.sql')
TABLES = frozenset({'schema_migrations', 'document_series', 'document_versions', 'parse_revisions', 'parse_revision_chunks', 'audit_events'})
PROTECTED = ('knowledge_bases', 'documents', 'chunks', 'parse_tasks', 'sessions', 'messages', 'citations', 'qa_feedbacks', 'user_kb_access', 'stat_events', 'industry_templates')
REQUIRED = {'knowledge_bases', 'documents', 'chunks', 'parse_tasks', 'sessions', 'messages', 'citations', 'users'}
EXPECTED_COLUMNS = {
 'schema_migrations': 'version checksum applied_at baseline_json',
 'document_series': 'id kb_id title business_key owner_id state revision created_at updated_at',
 'document_versions': 'version_id series_id kb_id version_label sha256 publication_state validity effective_from effective_to applicability_json supersedes_id metadata_revision is_legacy coexistence_note created_at updated_at',
 'parse_revisions': 'id version_id task_id parser_version file_sha256 content_hash source_integrity state is_current chunk_count error_message created_at finished_at',
 'parse_revision_chunks': 'chunk_id parse_revision_id content_sha256 locator_json',
 'audit_events': 'id actor_id kb_id object_type object_id action reason before_json after_json created_at',
}
MAX_AUTO_HASH_BYTES = 512 * 1024 * 1024

class MigrationError(RuntimeError):
    pass

class SensitiveDatabaseError(MigrationError):
    pass

def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()

def _approved_path(path: Path | str, allowed_root: Path | str, *, exists: bool = True) -> Path:
    root = Path(allowed_root).resolve(strict=True)
    if not root.is_dir() or root.parent == root:
        raise MigrationError('An explicit non-drive workspace/target directory is required')
    target = Path(path).resolve(strict=exists)
    if target == root or not target.is_relative_to(root):
        raise MigrationError('Target is outside the explicitly authorized directory')
    return target

def _connect(database: Path, *, readonly: bool):
    connection = sqlite3.connect(database.as_uri() + ('?mode=ro' if readonly else '?mode=rw'), uri=True, timeout=5, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    if readonly:
        connection.execute('PRAGMA query_only=ON')
    return connection

def _table_names(connection) -> set[str]:
    return {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

def credential_presence(connection) -> dict[str, bool]:
    """Only booleans are returned. Secret values are never selected or logged."""
    tables = _table_names(connection)
    result = {}
    for table, candidates in [('llm_channels', ('api_key', 'api_key_encrypted', 'access_token', 'secret_key')), ('users', ('password_digest', 'password_hash', 'password_salt'))]:
        if table not in tables:
            continue
        columns = {r[1] for r in connection.execute('PRAGMA table_info("' + table + '")')}
        for column in candidates:
            if column in columns:
                sql = 'SELECT EXISTS(SELECT 1 FROM "' + table + '" WHERE "' + column + '" IS NOT NULL AND length(trim("' + column + '"))>0)'
                result[table + '.' + column] = bool(connection.execute(sql).fetchone()[0])
    return result

def protected_snapshot(connection, *, tables=PROTECTED) -> dict:
    """Fingerprint domain rows, excluding all auth/provider/settings tables."""
    result = {}
    existing = _table_names(connection)
    for table in tables:
        if table not in existing:
            continue
        digest, count = hashlib.sha256(), 0
        cursor = connection.execute('SELECT * FROM "' + table + '" ORDER BY rowid')
        columns = [item[0] for item in cursor.description]
        digest.update(_json(columns).encode('utf-8'))
        for row in cursor:
            digest.update(b'\n')
            digest.update(_json(list(row)).encode('utf-8'))
            count += 1
        result[table] = {'count': count, 'sha256': digest.hexdigest()}
    return result

def _hash_file(path: Path) -> tuple[str, str]:
    sha256, sha1 = hashlib.sha256(), hashlib.sha1()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            sha256.update(block); sha1.update(block)
    return sha256.hexdigest(), sha1.hexdigest()

def _source_manifest(connection, *, file_base, upload_root, allowed_root, max_total_bytes) -> tuple[list[dict], list[dict]]:
    upload = _approved_path(upload_root, allowed_root)
    if not upload.is_dir():
        raise MigrationError('Upload root must be an existing directory')
    base = Path(file_base).resolve(strict=True)
    rows = connection.execute('SELECT id, storage_path, fingerprint FROM documents ORDER BY id').fetchall()
    resolved, unique_files, issues = [], {}, []
    for row in rows:
        if not row['storage_path']:
            issues.append({'version_id': row['id'], 'reason': 'missing_storage_path'}); continue
        stored = Path(row['storage_path'])
        target = (stored if stored.is_absolute() else base / stored).resolve()
        if not target.is_relative_to(upload) or not target.is_relative_to(Path(allowed_root).resolve()):
            issues.append({'version_id': row['id'], 'reason': 'file_outside_authorized_upload_root'}); continue
        if not target.is_file():
            issues.append({'version_id': row['id'], 'reason': 'source_file_missing'}); continue
        unique_files[target] = target.stat().st_size
        resolved.append((row, target))
    if issues:
        raise MigrationError('Source-file preflight failed: ' + _json(issues))
    total = sum(unique_files.values())
    if max_total_bytes is not None and total > max_total_bytes:
        raise MigrationError(f'Hashing {total} bytes exceeds the pre-reviewed {max_total_bytes}-byte limit; review scale before raising it')
    hashes = {file: _hash_file(file) for file in unique_files}
    manifest, warnings = [], []
    for row, target in resolved:
        sha256, sha1 = hashes[target]
        manifest.append({'version_id': row['id'], 'path': str(target), 'relative_upload_path': target.relative_to(upload).as_posix(), 'bytes': unique_files[target], 'sha256': sha256})
        if row['fingerprint'] and row['fingerprint'] != sha1:
            warnings.append({'version_id': row['id'], 'reason': 'legacy_upload_fingerprint_differs_current_file; old rows retained, source mapping not asserted'})
    return manifest, warnings

def _statements(sql: str):
    buffer = ''
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ''
    if any(line.strip() and not line.lstrip().startswith('--') for line in buffer.splitlines()):
        raise MigrationError('Incomplete migration SQL statement')

def migration_checksum() -> str:
    return hashlib.sha256(SQL_FILE.read_bytes() + b'\x00legacy-backfill-v1').hexdigest()

def _normalized_sql(sql: str) -> str:
    sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
    return re.sub(r'[\s";]', '', sql).casefold().replace('ifnotexists', '')

def _verify_definitions(connection, existing: set[str], *, require_all: bool) -> None:
    for statement in _statements(SQL_FILE.read_text(encoding='utf-8')):
        match = re.search(r'CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX|TRIGGER)\s+IF\s+NOT\s+EXISTS\s+(\w+)', statement, re.I)
        if not match:
            continue
        kind, name = match[1].lower(), match[2]
        actual = connection.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind, name)).fetchone()
        if actual is None:
            if require_all:
                raise MigrationError('Applied migration is missing ' + kind + ' ' + name)
            continue
        if _normalized_sql(actual[0]) != _normalized_sql(statement):
            raise MigrationError('Existing ' + kind + ' definition differs: ' + name)

def _schema_state(connection) -> str:
    existing = _table_names(connection)
    missing = REQUIRED - existing
    if missing:
        raise MigrationError('Required legacy tables missing: ' + ', '.join(sorted(missing)))
    _verify_definitions(connection, existing, require_all=False)
    for table in TABLES & existing:
        actual = {r[1] for r in connection.execute('PRAGMA table_info("' + table + '")')}
        if actual != set(EXPECTED_COLUMNS[table].split()):
            raise MigrationError('Unexpected existing schema for ' + table + '; refusing a guessed repair')
    if 'schema_migrations' in existing:
        applied = connection.execute('SELECT version, checksum FROM schema_migrations ORDER BY version').fetchall()
        if any(row['version'] != VERSION for row in applied):
            raise MigrationError('Database has an unrecognized/newer migration; use its matching migrator')
        if applied:
            if applied[0]['checksum'] != migration_checksum():
                raise MigrationError('Applied migration checksum differs from the immutable migration file')
            if not TABLES.issubset(existing):
                raise MigrationError('Applied migration is missing required tables')
            _verify_definitions(connection, existing, require_all=True)
            return 'already_applied'
    for table in TABLES & existing:
        if connection.execute('SELECT EXISTS(SELECT 1 FROM "' + table + '")').fetchone()[0]:
            raise MigrationError('Untracked version data exists in ' + table + '; refusing to merge it')
    return 'pending'

def _preflight(connection, *, database, file_base, upload_root, allowed_root, max_total_bytes) -> dict:
    state = _schema_state(connection)
    if connection.execute('PRAGMA foreign_key_check').fetchall():
        raise MigrationError('Foreign-key violations must be resolved before migration; no replacement history is fabricated')
    if state == 'already_applied':
        return {'status': state, 'version': VERSION, 'database': str(database), 'checksum': migration_checksum()}
    if connection.execute("SELECT COUNT(*) FROM parse_tasks WHERE phase NOT IN ('success','failed')").fetchone()[0]:
        raise MigrationError('Active/unknown parse tasks exist; establish quiescence before migration')
    if connection.execute('SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.kb_id!=d.kb_id').fetchone()[0]:
        raise MigrationError('Cross-library chunk ownership mismatch')
    files, warnings = _source_manifest(connection, file_base=file_base, upload_root=upload_root, allowed_root=allowed_root, max_total_bytes=max_total_bytes)
    baseline = protected_snapshot(connection)
    credentials = credential_presence(connection)
    plan = {'status': 'planned', 'version': VERSION, 'database': str(database), 'checksum': migration_checksum(), 'baseline': baseline, 'files': files, 'warnings': warnings, 'credential_material_present': credentials, 'creates_tables': sorted(TABLES), 'base_rows_rewritten': False, 'legacy_date_policy': 'unknown_no_inferred_effective_dates', 'legacy_citations_without_chunk': connection.execute('SELECT COUNT(*) FROM citations WHERE chunk_id IS NULL').fetchone()[0]}
    plan['plan_digest'] = _digest(plan)
    return plan

def plan_migration(database, *, file_base, upload_root, allowed_root, max_total_bytes=MAX_AUTO_HASH_BYTES) -> dict:
    database = _approved_path(database, allowed_root)
    connection = _connect(database, readonly=True)
    try:
        connection.execute('BEGIN')
        return _preflight(connection, database=database, file_base=file_base, upload_root=upload_root, allowed_root=allowed_root, max_total_bytes=max_total_bytes)
    finally:
        connection.close()

def _backfill(connection, plan: dict) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='microseconds')
    files = {file['version_id']: file for file in plan['files']}
    for doc in connection.execute('SELECT * FROM documents ORDER BY id').fetchall():
        series = connection.execute('INSERT INTO document_series (kb_id,title,business_key,owner_id,state,revision,created_at,updated_at) VALUES (?,?,NULL,?,\'active\',1,?,?)', (doc['kb_id'], doc['filename'], doc['uploaded_by'], now, now)).lastrowid
        chunks = connection.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY seq_no,id', (doc['id'],)).fetchall()
        was_queryable = doc['status'] == 'success' and bool(chunks)
        connection.execute('INSERT INTO document_versions (version_id,series_id,kb_id,version_label,sha256,publication_state,validity,effective_from,effective_to,applicability_json,supersedes_id,metadata_revision,is_legacy,coexistence_note,created_at,updated_at) VALUES (?,?,?,?,?,?,\'unknown\',NULL,NULL,\'{}\',NULL,1,1,\'\',?,?)', (doc['id'], series, doc['kb_id'], '旧资料初始版', files[doc['id']]['sha256'], 'published' if was_queryable else 'draft', now, now))
        state = 'success' if chunks else 'failed' if doc['status'] == 'failed' else 'legacy_empty'
        content_hash = _digest([dict(row) for row in chunks])
        revision = connection.execute('INSERT INTO parse_revisions (version_id,task_id,parser_version,file_sha256,content_hash,source_integrity,state,is_current,chunk_count,error_message,created_at,finished_at) VALUES (?,NULL,\'legacy-snapshot-unknown-parser\',NULL,?,\'legacy_unverified\',?,?,?,?,?,?)', (doc['id'], content_hash, 'building', 0, len(chunks), doc['error_message'] or '', now, None)).lastrowid
        for chunk in chunks:
            try:
                original_meta = json.loads(chunk['meta_json'] or '{}')
            except (ValueError, TypeError):
                original_meta = {'unparsed_meta_sha256': hashlib.sha256((chunk['meta_json'] or '').encode('utf-8')).hexdigest()}
            locator = {'kind': 'legacy_excerpt', 'reliability': 'legacy_unverified', 'seq_no': chunk['seq_no'], 'page_no': chunk['page_no'], 'parser_meta': original_meta}
            connection.execute('INSERT INTO parse_revision_chunks (chunk_id,parse_revision_id,content_sha256,locator_json) VALUES (?,?,?,?)', (chunk['id'], revision, hashlib.sha256(chunk['content'].encode('utf-8')).hexdigest(), _json(locator)))
        connection.execute('UPDATE parse_revisions SET state=?,is_current=?,finished_at=? WHERE id=?', (state, int(was_queryable), now, revision))
        connection.execute('INSERT INTO audit_events (actor_id,kb_id,object_type,object_id,action,reason,before_json,after_json,created_at) VALUES (NULL,?,\'document_version\',?,\'legacy_backfill\',\'无日期/身份/原定位推测的旧数据回填\',\'{}\',?,?)', (doc['kb_id'], doc['id'], _json({'series_id': series, 'parse_revision_id': revision, 'validity': 'unknown', 'source_integrity': 'legacy_unverified'}), now))
    connection.execute('INSERT INTO schema_migrations (version,checksum,applied_at,baseline_json) VALUES (?,?,?,?)', (VERSION, migration_checksum(), now, _json(plan['baseline'])))

def migrate_database(database, *, file_base, upload_root, allowed_root, expected_plan_digest: str | None = None, execute: bool = False, sensitive_approval_reference: str | None = None, max_total_bytes=MAX_AUTO_HASH_BYTES, _failure_hook: Callable[[str], None] | None = None) -> dict:
    """Execute only after an exact dry-run plan; no backups or filesystem cleanup.

    sensitive_approval_reference is a caller-supplied record of a real, separate
    authorization/recovery decision, never a generated substitute for approval.
    The coding agent must not supply it for the real business DB without consent.
    """
    if not isinstance(execute, bool):
        raise MigrationError('execute must be an explicit boolean')
    if not execute:
        return plan_migration(database, file_base=file_base, upload_root=upload_root, allowed_root=allowed_root, max_total_bytes=max_total_bytes)
    database = _approved_path(database, allowed_root)
    connection = _connect(database, readonly=False)
    try:
        connection.execute('BEGIN IMMEDIATE')
        plan = _preflight(connection, database=database, file_base=file_base, upload_root=upload_root, allowed_root=allowed_root, max_total_bytes=max_total_bytes)
        if plan['status'] == 'already_applied':
            connection.rollback()
            return plan
        if any(plan['credential_material_present'].values()) and not (isinstance(sensitive_approval_reference, str) and sensitive_approval_reference.strip()):
            raise SensitiveDatabaseError('Credential/auth material present; a real approved no-secret-copy recovery plan is required. Nothing migrated or backed up.')
        if not expected_plan_digest or expected_plan_digest != plan['plan_digest']:
            raise MigrationError('Dry-run plan is absent or stale; inspect a fresh plan before writing')
        for statement in _statements(SQL_FILE.read_text(encoding='utf-8')):
            connection.execute(statement)
        if _failure_hook:
            _failure_hook('after_schema')
        _backfill(connection, plan)
        if _failure_hook:
            _failure_hook('after_backfill')
        after = protected_snapshot(connection)
        if after != plan['baseline']:
            raise MigrationError('Migration changed protected legacy rows; transaction rolled back')
        for file in plan['files']:
            if _hash_file(Path(file['path']))[0] != file['sha256']:
                raise MigrationError('A source file changed during migration; transaction rolled back')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise MigrationError('New foreign-key violation; transaction rolled back')
        connection.commit()
        return {'status': 'applied', 'version': VERSION, 'database': str(database), 'checksum': migration_checksum(), 'plan_digest': plan['plan_digest'], 'legacy_rows_unchanged': True, 'versions_created': len(plan['files']), 'legacy_rows': plan['baseline'], 'warnings': plan['warnings'], 'product_acceptance': 'NOT_PROVEN_BY_MIGRATION'}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
