"""Exact legacy business-row retirement; NEVER discovers targets or deletes files.

Plan: --db ABS_DB --output ABS_DIR --kb-ids 1,2 [--template-ids 10,11]
Apply: same arguments plus --apply ABS_PLAN --confirm-exact-retirement
No application/config imports, authentication reads, network or whole-DB backup.
Recovery is deliberately manual: verify the complete expected business afterimage,
schema and unused original IDs, review original user references, then INSERT the
backup rows in restore_order under BEGIN IMMEDIATE. Never REPLACE a live database.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

FORMAT = 'exact-business-retirement/v1'
COLUMNS = {name: fields.split() for name, fields in {
    'industry_templates': 'id code name description category_framework_json prompt_template sample_questions_json enabled',
    'knowledge_bases': 'id name description template_id status owner_id created_at updated_at',
    'documents': 'id kb_id filename file_type file_size storage_path fingerprint status page_count error_message uploaded_by created_at updated_at',
    'sessions': 'id user_id kb_id title status created_at updated_at',
    'user_kb_access': 'id kb_id role_code',
    'chunks': 'id document_id kb_id seq_no content page_no meta_json chunk_hash vector_json created_at',
    'messages': 'id session_id role content question kb_id mode status latency_ms created_at',
    'parse_tasks': 'id document_id phase progress chunk_count error_message operator_id started_at finished_at created_at',
    'stat_events': 'id event_type kb_id user_id session_id mode payload_json created_at',
    'citations': 'id message_id chunk_id document_id kb_id doc_name content_excerpt page_no similarity rank',
    'qa_feedbacks': 'id message_id user_id feedback_type comment status handler_id handled_at handler_note created_at',
}.items()}
IGNORED = {'users', 'roles', 'llm_channels', 'sys_params'}
VERSIONED = {'schema_migrations', 'document_series', 'document_versions', 'parse_revisions', 'parse_revision_chunks', 'audit_events'}
DELETE_ORDER = ('qa_feedbacks', 'citations', 'stat_events', 'parse_tasks', 'chunks', 'messages',
                'sessions', 'user_kb_access', 'documents', 'knowledge_bases', 'industry_templates')
FOREIGN_KEYS = {table: set(spec.split()) for table, spec in {
    'industry_templates': '', 'knowledge_bases': 'owner_id:users:id template_id:industry_templates:id',
    'documents': 'kb_id:knowledge_bases:id uploaded_by:users:id',
    'sessions': 'user_id:users:id kb_id:knowledge_bases:id', 'user_kb_access': 'kb_id:knowledge_bases:id',
    'chunks': 'document_id:documents:id kb_id:knowledge_bases:id',
    'messages': 'session_id:sessions:id kb_id:knowledge_bases:id',
    'parse_tasks': 'document_id:documents:id operator_id:users:id',
    'stat_events': 'kb_id:knowledge_bases:id user_id:users:id session_id:sessions:id',
    'citations': 'message_id:messages:id chunk_id:chunks:id document_id:documents:id kb_id:knowledge_bases:id',
    'qa_feedbacks': 'message_id:messages:id user_id:users:id handler_id:users:id',
}.items()}
MAX_ROWS, MAX_BYTES = 200_000, 128 * 1024 * 1024


class RetirementError(RuntimeError):
    pass


class CommitUncertain(RetirementError):
    pass


def require(condition, message):
    if not condition:
        raise RetirementError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sealed(value):
    return {**value, 'sha256': digest(value)}


def local_path(value, *, exists=False):
    path = Path(value)
    require(path.is_absolute() and not str(path).startswith(('\\\\', '//')), 'Use an absolute local path, not a URI/UNC path')
    if os.name == 'nt':
        import ctypes
        require(ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) in (2, 3, 6), 'Network/unknown drives are refused')
    for component in (path, *path.parents):
        require(not component.is_symlink() and not component.is_junction(), 'Linked database/evidence paths are refused')
    return path.resolve(strict=exists)


def identity(path):
    stat = path.stat()
    return {'path': str(path), 'device': stat.st_dev, 'inode': stat.st_ino}


def explicit_ids(value, *, empty=False):
    if value == '' and empty:
        return []
    parts = value.split(',') if isinstance(value, str) else list(value)
    require(parts and all(str(x).isascii() and str(x).isdigit() and int(x) > 0 for x in parts), 'IDs must be explicit positive integers, comma-separated; no ranges/all')
    values = [int(x) for x in parts]
    require(len(values) == len(set(values)), 'Duplicate IDs are refused')
    return sorted(values)


@contextmanager
def open_db(path, *, writable=False, audit=None):
    conn = sqlite3.connect(path.as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    def authorize(action, table, column, database, source):
        if audit:
            audit(action, table, column, database, source)
        if source or database not in (None, 'main'):
            return sqlite3.SQLITE_DENY
        allowed = action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION)
        if action == sqlite3.SQLITE_READ:
            allowed = table in COLUMNS or table in ('sqlite_master', 'sqlite_schema')
        elif action == sqlite3.SQLITE_DELETE:
            allowed = writable and table in COLUMNS
        elif action == sqlite3.SQLITE_PRAGMA:
            allowed = (table in ('foreign_keys', 'query_only', 'trusted_schema') or
                       table == 'table_xinfo' and column in COLUMNS or
                       table == 'foreign_key_list' and column in set(COLUMNS) | IGNORED)
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY
    conn.set_authorizer(authorize)
    try:
        conn.execute('PRAGMA trusted_schema=OFF')
        # SQLite FK execution reads users.id even for child DELETE. Disable only
        # on this connection; metadata/ownership/complete postimage guards below
        # replace automatic actions without reading authentication rows.
        conn.execute('PRAGMA foreign_keys=OFF')
        if not writable:
            conn.execute('PRAGMA query_only=ON')
        yield conn
    finally:
        conn.close()


def read_business(conn):
    objects = [dict(row) for row in conn.execute('SELECT name,type,tbl_name FROM sqlite_schema')]
    tables = {row['name'] for row in objects if row['type'] == 'table' and not row['name'].startswith('sqlite_')}
    require(not tables & VERSIONED, 'Version tables present: this legacy-only tool refuses versioned/partial migrations')
    require(set(COLUMNS) <= tables and tables <= set(COLUMNS) | IGNORED, 'Unknown or incomplete table layout; no retirement')
    require(not any(row['type'] in ('trigger', 'view') for row in objects), 'Triggers/views require separate review; no retirement')
    schema, data, total, size = {'objects': objects}, {}, 0, 0
    # Metadata only: PRAGMA emits no SQLITE_READ of authentication rows.
    for table in sorted(set(COLUMNS) | (IGNORED & tables)):
        fks = [list(row) for row in conn.execute(f'PRAGMA foreign_key_list("{table}")')]
        if table in IGNORED:
            require(not any(row[2] in COLUMNS for row in fks), 'Unsupported inbound foreign key from an excluded table')
        else:
            require({f'{r[3]}:{r[2]}:{r[4]}' for r in fks} == FOREIGN_KEYS[table] and len(fks) == len(FOREIGN_KEYS[table]) and all(r[1] == 0 for r in fks), f'Unknown foreign keys in {table}')
        schema['fk:' + table] = fks
    for table, columns in COLUMNS.items():
        info = [list(row) for row in conn.execute(f'PRAGMA table_xinfo("{table}")')]
        require([row[1] for row in info] == columns and all(row[6] == 0 for row in info), f'Unknown columns in {table}')
        require([row[1] for row in info if row[5]] == ['id'], f'Unexpected primary key in {table}')
        sql = [list(row) for row in conn.execute('SELECT type,name,sql FROM sqlite_schema WHERE tbl_name=? ORDER BY type,name', (table,))]
        schema[table] = {'columns': info, 'ddl': sql}
        data[table] = []
        for row in conn.execute('SELECT ' + ','.join('"' + col + '"' for col in columns) + f' FROM "{table}" ORDER BY id'):
            item = dict(row)
            total, size = total + 1, size + len(canonical(item))
            require(total <= MAX_ROWS and size <= MAX_BYTES, 'Business snapshot exceeds bounded memory budget; use a separately reviewed large-data tool')
            data[table].append(item)
    return digest(schema), data


def fingerprints(data):
    return {table: {str(row['id']): digest(row) for row in rows} for table, rows in data.items()}


def scope_state(conn, kb_ids, template_ids):
    schema_hash, data = read_business(conn)
    index = {table: {row['id']: row for row in rows} for table, rows in data.items()}
    targets, templates = set(kb_ids), set(template_ids)
    require(targets and targets < set(index['knowledge_bases']), 'All requested KBs must exist and at least one KB must remain; no whole-library retirement')
    require(templates <= set(index['industry_templates']), 'A requested template does not exist')
    for kb in data['knowledge_bases']:
        require(kb['template_id'] not in templates or kb['id'] in targets, 'A retained library still references a requested template')
    owners = {table: {} for table in COLUMNS}
    for table in ('knowledge_bases', 'documents', 'sessions', 'user_kb_access'):
        owners[table] = {row['id']: row['id'] if table == 'knowledge_bases' else row['kb_id'] for row in data[table]}
    def bind(table, row, candidates, *, broken=False):
        nonnull = {value for value in candidates if value is not None}
        if nonnull & targets:
            require(not broken and len(nonnull) == 1, f'Cross-library or unbound reference in {table} id={row["id"]}')
        owners[table][row['id']] = next(iter(nonnull)) if len(nonnull) == 1 else None
    for row in data['chunks']:
        doc = index['documents'].get(row['document_id'])
        bind('chunks', row, [row['kb_id'], doc['kb_id'] if doc else None], broken=doc is None)
    for row in data['messages']:
        session = index['sessions'].get(row['session_id'])
        bind('messages', row, [row['kb_id'], session['kb_id'] if session else None], broken=session is None or session['kb_id'] is None)
    for row in data['parse_tasks']:
        owner = owners['documents'].get(row['document_id'])
        owners['parse_tasks'][row['id']] = owner
        require(owner not in targets or row['phase'] in ('success', 'failed'), f'Active parsing task id={row["id"]}; stop/wait and re-plan')
    for row in data['citations']:
        message_owner = owners['messages'].get(row['message_id'])
        chunk = index['chunks'].get(row['chunk_id'])
        # Deleted-document snapshots with chunk_id=NULL remain recoverable data.
        broken = message_owner is None or row['chunk_id'] is not None and (chunk is None or chunk['document_id'] != row['document_id'])
        bind('citations', row, [row['kb_id'], message_owner, owners['documents'].get(row['document_id']),
                               owners['chunks'].get(row['chunk_id'])], broken=broken)
    for row in data['qa_feedbacks']:
        owners['qa_feedbacks'][row['id']] = owners['messages'].get(row['message_id'])
    for row in data['stat_events']:
        session_owner = owners['sessions'].get(row['session_id'])
        bind('stat_events', row, [row['kb_id'], session_owner], broken=row['session_id'] is not None and session_owner is None)
    selected, retained = {}, {}
    for table, rows in data.items():
        selected[table] = [row for row in rows if (row['id'] in templates if table == 'industry_templates' else owners[table].get(row['id']) in targets)]
        ids = {row['id'] for row in selected[table]}
        retained[table] = [row for row in rows if row['id'] not in ids]
    summaries = [{'id': kid, 'name': index['knowledge_bases'][kid]['name'], 'owner_id': index['knowledge_bases'][kid]['owner_id'],
                  'row_sha256': digest(index['knowledge_bases'][kid]),
                  'counts': {table: sum(owner == kid for owner in mapping.values()) for table, mapping in owners.items()}}
                 for kid in kb_ids]
    return {'schema_sha256': schema_hash, 'libraries': summaries, 'selected': selected,
            'preimage': fingerprints(data), 'expected_afterimage': fingerprints(retained),
            'retained_kb_ids': sorted(set(index['knowledge_bases']) - targets)}


def write_json(path, value, *, reuse=False):
    path = local_path(path)
    payload = canonical(value) + b'\n'
    if reuse and path.exists():
        require(path.read_bytes() == payload, 'Existing evidence differs; never overwrite it')
        return
    with path.open('xb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def create_plan(db, output, kb_ids, template_ids=(), *, audit=None):
    db, output = local_path(db, exists=True), local_path(output)
    require(db.is_file(), 'Database must be an existing regular file')
    kb_ids, template_ids = explicit_ids(kb_ids), explicit_ids(template_ids, empty=True) if template_ids else []
    database = identity(db)
    with open_db(db, audit=audit) as conn:
        conn.execute('BEGIN')
        state = scope_state(conn, kb_ids, template_ids)
        require(identity(db) == database, 'Database path identity changed during planning')
    plan = sealed({'format': FORMAT, 'database': database, 'output': str(output), 'kb_ids': kb_ids,
                   'template_ids': template_ids, 'issued_at': datetime.now(timezone.utc).isoformat(),
                   'nonce': uuid.uuid4().hex, 'state': state})
    output.mkdir(parents=True, exist_ok=True)
    path = output / ('plan-' + plan['sha256'][:24] + '.json')
    write_json(path, plan)
    return {'status': 'planned', 'plan': str(path), 'sha256': plan['sha256'], 'libraries': state['libraries'],
            'template_ids': template_ids, 'retained_kb_ids': state['retained_kb_ids'], 'files_deleted': 0}


def delete_selected(conn, selected):
    for table in DELETE_ORDER:
        ids = [(row['id'],) for row in selected[table]]
        if ids:
            cursor = conn.executemany(f'DELETE FROM "{table}" WHERE id=?', ids)
            require(cursor.rowcount == len(ids), f'Deletion CAS mismatch in {table}')


def apply_plan(db, output, kb_ids, template_ids, plan_file, *, confirmed=False, audit=None):
    require(confirmed, 'Apply requires --confirm-exact-retirement in addition to --apply')
    db, output = local_path(db, exists=True), local_path(output, exists=True)
    plan_file = local_path(plan_file, exists=True)
    require(plan_file.parent == output, 'Plan must be in the exact evidence directory')
    require(plan_file.stat().st_size <= 256 * 1024 * 1024, 'Plan exceeds bounded evidence size')
    plan = json.loads(plan_file.read_text(encoding='utf-8'))
    seal = plan.pop('sha256', None)
    require(seal == digest(plan) and plan.get('format') == FORMAT, 'Plan integrity/format verification failed')
    require(plan['database'] == identity(db) and plan['output'] == str(output), 'Database identity/evidence directory changed')
    require(plan['kb_ids'] == explicit_ids(kb_ids) and plan['template_ids'] == (explicit_ids(template_ids) if template_ids else []), 'Explicit scope differs from the issued plan')
    suffix = seal[:24] + '.json'
    backup, intent, receipt = (output / (prefix + suffix) for prefix in ('backup-', 'intent-', 'receipt-'))
    require(not receipt.exists(), 'A receipt already exists; this plan cannot be replayed')
    committing = False
    with open_db(db, writable=True, audit=audit) as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            current = scope_state(conn, plan['kb_ids'], plan['template_ids'])
            require(current == plan['state'], 'Full business preimage/CAS changed (target or retained rows); re-plan')
            evidence = sealed({'format': FORMAT + '/backup', 'plan_sha256': seal, 'database': plan['database'],
                'schema_sha256': current['schema_sha256'], 'columns': COLUMNS, 'rows': current['selected'],
                'expected_afterimage': current['expected_afterimage'], 'restore_order': list(reversed(DELETE_ORDER)),
                'recovery': 'Verify complete afterimage, schema, unused original IDs and original user references. Reviewed INSERT only; never REPLACE a live DB. Files were not touched.'})
            write_json(backup, evidence, reuse=True)  # fsync before the first DELETE
            delete_selected(conn, current['selected'])
            schema_hash, remaining = read_business(conn)
            require(schema_hash == current['schema_sha256'] and fingerprints(remaining) == current['expected_afterimage'], 'Postimage mismatch; retained rows must be identical')
            require(identity(db) == plan['database'], 'Database path identity changed during transaction')
            result = {'status': 'prepared_not_commit_proof', 'plan_sha256': seal, 'backup': str(backup),
                      'backup_sha256': evidence['sha256'], 'afterimage_sha256': digest(current['expected_afterimage']),
                      'retained_kb_ids': current['retained_kb_ids'], 'files_deleted': 0}
            write_json(intent, sealed(result), reuse=True)
            committing = True
            conn.execute('COMMIT')
        except BaseException as error:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            elif committing:
                raise CommitUncertain(f'COMMIT outcome uncertain: do not retry/restore blindly. Compare DB to plan preimage and backup expected_afterimage: {backup}') from error
            raise
    result.update(status='applied', committed=True, receipt=str(receipt))
    try:
        write_json(receipt, sealed(result))
    except (OSError, RetirementError):
        result.update(status='applied_receipt_pending', warning='DB committed; durable backup/intent remain. Do not retry. Verify afterimage and repair receipt separately.')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--db', required=True, help='Absolute local SQLite file; no configured/default database')
    parser.add_argument('--output', required=True, help='Absolute local evidence directory; existing files are never overwritten')
    parser.add_argument('--kb-ids', required=True, help='Exact positive IDs, comma-separated; no names/ranges/all')
    parser.add_argument('--template-ids', default='', help='Exact template IDs; omission preserves ALL templates')
    parser.add_argument('--apply', metavar='ABS_PLAN', help='Apply this previously issued content-hashed plan')
    parser.add_argument('--confirm-exact-retirement', action='store_true')
    args = parser.parse_args(argv)
    try:
        require(args.apply or not args.confirm_exact_retirement, 'Confirmation alone does not apply anything')
        if args.apply:
            result = apply_plan(args.db, args.output, args.kb_ids, args.template_ids, args.apply, confirmed=args.confirm_exact_retirement)
        else:
            result = create_plan(args.db, args.output, args.kb_ids, args.template_ids)
        print(json.dumps(result, ensure_ascii=False))
        return 3 if result['status'] == 'applied_receipt_pending' else 0
    except CommitUncertain as error:
        print(json.dumps({'status': 'commit_uncertain', 'error': str(error)}, ensure_ascii=False))
        return 4
    except (RetirementError, sqlite3.Error, OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({'status': 'refused', 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())