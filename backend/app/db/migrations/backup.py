"""Consistent backup/verification for an explicitly owned isolated fixture.

Uses SQLite's backup API (not a file copy of a live WAL database), copies only
referenced upload files, never overwrites a destination, and rejects detected
provider/auth material. This is not a production credential-separation tool.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Callable
from . import (MAX_AUTO_HASH_BYTES, MigrationError, SensitiveDatabaseError, PROTECTED, TABLES, _approved_path, _connect, _hash_file, _json, _source_manifest, _table_names, credential_presence, protected_snapshot)

SNAPSHOT_TABLES = tuple(PROTECTED) + tuple(sorted(TABLES))

def _snapshot(connection):
    # The migration snapshot helper defaults to old domain tables; backups also
    # preserve the new version/locator/audit rows when those tables are present.
    from . import protected_snapshot
    return protected_snapshot(connection, tables=SNAPSHOT_TABLES)

def create_consistent_backup(database, *, destination, file_base, upload_root, allowed_root, max_total_bytes=MAX_AUTO_HASH_BYTES, _after_snapshot: Callable[[], None] | None = None) -> dict:
    database = _approved_path(database, allowed_root)
    destination = _approved_path(destination, allowed_root, exists=False)
    if destination.exists():
        raise MigrationError('Backup destination already exists; refusing to overwrite it')
    source = _connect(database, readonly=True)
    target = None
    try:
        source.execute('BEGIN')
        flags = credential_presence(source)
        if any(flags.values()):
            raise SensitiveDatabaseError('Detected credential/auth material; do not copy this database. An approved separation/recovery plan is required.')
        if source.execute('PRAGMA foreign_key_check').fetchall():
            raise MigrationError('Source has foreign-key violations; no verified backup can be declared')
        files, warnings = _source_manifest(source, file_base=file_base, upload_root=upload_root, allowed_root=allowed_root, max_total_bytes=max_total_bytes)
        baseline = _snapshot(source)
        table_names = sorted(_table_names(source))
        journal = source.execute('PRAGMA journal_mode').fetchone()[0]
        if _after_snapshot:
            _after_snapshot()
        # Reserve a new directory atomically. Failed attempts remain clearly
        # incomplete; no cleanup code risks deleting someone else's destination.
        destination.mkdir(exist_ok=False)
        database_copy = destination / 'app.db'
        with database_copy.open('xb'):
            pass
        target = sqlite3.connect(database_copy, isolation_level=None)
        target.row_factory = sqlite3.Row
        source.backup(target, pages=128, sleep=.01)
        target.execute('PRAGMA journal_mode=DELETE')
        target.execute('PRAGMA foreign_keys=ON')
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise MigrationError('SQLite backup integrity check failed')
        if target.execute('PRAGMA foreign_key_check').fetchall() or _snapshot(target) != baseline or sorted(_table_names(target)) != table_names:
            raise MigrationError('Backup rows/schema do not match the source read snapshot')
        target.close(); target = None
        copied = {}
        artifacts = []
        for item in files:
            relative = 'uploads/' + item['relative_upload_path']
            output = destination / relative
            if relative not in copied:
                output.parent.mkdir(parents=True, exist_ok=True)
                _approved_path(output, destination, exists=False)
                with Path(item['path']).open('rb') as incoming, output.open('xb') as outgoing:
                    for block in iter(lambda: incoming.read(1024 * 1024), b''):
                        outgoing.write(block)
                copied[relative] = _hash_file(output)[0]
            if copied[relative] != item['sha256'] or _hash_file(Path(item['path']))[0] != item['sha256']:
                raise MigrationError('An associated file changed during backup; no complete manifest was written')
            artifacts.append({'version_id': item['version_id'], 'path': relative, 'sha256': item['sha256'], 'bytes': item['bytes']})
        manifest = {
            'format': 'kb-isolated-sqlite-backup-v1', 'complete': True,
            'method': 'sqlite3.Connection.backup under a read transaction',
            'source_database': str(database), 'source_journal_mode': journal,
            'database': 'app.db', 'database_sha256': _hash_file(database_copy)[0],
            'domain_snapshot': baseline, 'tables': table_names, 'files': artifacts,
            'checked_sensitive_fields': flags, 'warnings': warnings,
            'vectors': 'included in chunks.vector_json; no unobserved external index files claimed',
            'restore_boundary': 'Verified DB and associated bytes only. Activation/path rebinding or overwriting a live target requires a separately verified procedure.',
        }
        with (destination / 'manifest.json').open('x', encoding='utf-8') as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        return manifest
    finally:
        if target is not None:
            target.close()
        source.close()

def verify_backup(backup_directory, *, allowed_root) -> dict:
    directory = _approved_path(backup_directory, allowed_root)
    manifest_path = _approved_path(directory / 'manifest.json', directory)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('format') != 'kb-isolated-sqlite-backup-v1' or manifest.get('complete') is not True:
        raise MigrationError('Backup manifest is missing, incomplete or unsupported')
    database = _approved_path(directory / manifest['database'], directory)
    if _hash_file(database)[0] != manifest['database_sha256']:
        raise MigrationError('Backup database hash mismatch')
    connection = _connect(database, readonly=True)
    try:
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise MigrationError('Backup SQLite integrity failed')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise MigrationError('Backup has foreign-key violations')
        if any(credential_presence(connection).values()):
            raise SensitiveDatabaseError('Backup unexpectedly contains credential/auth material')
        if _snapshot(connection) != manifest['domain_snapshot'] or sorted(_table_names(connection)) != manifest['tables']:
            raise MigrationError('Backup logical contents differ from manifest')
        doc_ids = {r[0] for r in connection.execute('SELECT id FROM documents')}
        if {item['version_id'] for item in manifest['files']} != doc_ids or len(manifest['files']) != len(doc_ids):
            raise MigrationError('Associated file coverage is incomplete or duplicated')
    finally:
        connection.close()
    for item in manifest['files']:
        file = _approved_path(directory / item['path'], directory)
        if file.stat().st_size != item['bytes'] or _hash_file(file)[0] != item['sha256']:
            raise MigrationError('Associated upload file hash mismatch')
    return {'status': 'verified', 'database_integrity': 'ok', 'files': len(manifest['files']), 'source_journal_mode': manifest['source_journal_mode'], 'domain_snapshot': manifest['domain_snapshot'], 'scope': 'isolated DB/file consistency, not live deployment or signed authenticity'}
