"""Synthetic SQLite only: no application/configuration/production database imports."""
from __future__ import annotations

from contextlib import closing, contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/retire_demo_libraries.py'
SCHEMA = Path(__file__).with_name('fixtures') / 'legacy_schema_v1.sql'
spec = importlib.util.spec_from_file_location('exact_retirement_test_subject', SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class DemoRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='demo-retirement-synthetic-')
        self.root = Path(self.temp.name).resolve(strict=True)
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.db, self.output = self.root / 'synthetic.db', self.root / 'evidence'
        self.read_tables = set()
        with closing(sqlite3.connect(self.db)) as db, db:
            db.executescript(SCHEMA.read_text(encoding='utf-8'))
            db.execute('PRAGMA foreign_keys=ON')
            def insert(table, **given):
                record = {}
                for _, name, kind, required, _, pk in db.execute(f'PRAGMA table_info("{table}")'):
                    if name in given:
                        record[name] = given[name]
                    elif pk:
                        continue
                    elif not required:
                        record[name] = None
                    elif kind.upper().startswith(('INT', 'BOOL', 'FLOAT')):
                        record[name] = 0
                    else:
                        record[name] = '2026-01-01T00:00:00' if name.endswith('_at') else ''
                columns = ','.join('"' + col + '"' for col in record)
                db.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in record)})', tuple(record.values()))
            insert('roles', id=1, code='user', name='Synthetic role')
            insert('llm_channels', id=1, name='DO-NOT-READ-PROVIDER', api_key='SYNTHETIC-AUTH-SECRET', base_url='https://invalid.invalid')
            insert('sys_params', id=1, key='DO-NOT-READ-CONFIG', value='SYNTHETIC-AUTH-SECRET')
            self.files = {}
            for kid, tid in [(1, 10), (2, 20), (8, 80)]:
                uid, doc, chunk, session, message = kid + 10, kid + 100, kid + 200, kid + 400, kid + 500
                marker = f'SELECTED-BUSINESS-{kid}' if kid != 8 else 'RETAINED-PRIVATE-CONTENT'
                insert('users', id=uid, username=f'synthetic-{kid}', role_code='user', password_digest='SYNTHETIC-AUTH-SECRET', password_salt='synthetic-only')
                insert('industry_templates', id=tid, code=f'template-{kid}', name=f'Explicit template {tid}', prompt_template=marker)
                insert('knowledge_bases', id=kid, name=f'Explicitly selected research {kid}' if kid != 8 else 'Demo named but MUST REMAIN', owner_id=uid, template_id=tid, status='enabled')
                path = self.root / 'uploads' / str(kid) / 'original.txt'
                path.parent.mkdir(parents=True); path.write_text(f'UNTOUCHED-FILE-{kid}', encoding='utf-8')
                self.files[path] = path.read_bytes()
                insert('documents', id=doc, kb_id=kid, filename=f'paper-{kid}.txt', file_type='txt', storage_path=str(path), uploaded_by=uid, status='success')
                insert('chunks', id=chunk, document_id=doc, kb_id=kid, content=marker)
                insert('parse_tasks', id=kid + 300, document_id=doc, phase='success', operator_id=uid)
                insert('sessions', id=session, user_id=uid, kb_id=kid, title=marker)
                insert('messages', id=message, session_id=session, role='assistant', kb_id=kid, content=marker)
                insert('citations', id=kid + 600, message_id=message, chunk_id=chunk, document_id=doc, kb_id=kid, doc_name=f'paper-{kid}', content_excerpt=marker)
                insert('qa_feedbacks', id=kid + 700, message_id=message, user_id=uid, feedback_type='like', comment=marker)
                insert('stat_events', id=kid + 900, event_type='qa', kb_id=kid, user_id=uid, session_id=session, payload_json=marker)
                insert('user_kb_access', id=kid + 800, kb_id=kid, role_code='user')
        self.original = self.rows()
        self.auth_original = self.rows(tool.IGNORED)

    def rows(self, tables=None):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.row_factory = sqlite3.Row
            return {table: [dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY id')]
                    for table in (tool.COLUMNS if tables is None else tables)}

    def execute(self, sql, params=()):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute(sql, params)

    def audit(self, action, table, column, database, source):
        if action == sqlite3.SQLITE_READ:
            self.read_tables.add(table)

    def plan(self, *, templates='10,20'):
        result = tool.create_plan(self.db, self.output, '1,2', templates, audit=self.audit)
        self.assertEqual(result['status'], 'planned')
        return result

    def apply(self, plan, *, templates='10,20', confirmed=True):
        return tool.apply_plan(self.db, self.output, '1,2', templates, plan['plan'], confirmed=confirmed, audit=self.audit)

    def assert_files_untouched(self):
        for path, content in self.files.items():
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes(), content)

    def test_plan_readonly_exact_snapshot_and_no_authentication_reads(self):
        plan = self.plan()
        self.assertEqual(self.rows(), self.original)
        self.assertEqual(self.rows(tool.IGNORED), self.auth_original)
        document = json.loads(Path(plan['plan']).read_text(encoding='utf-8'))
        self.assertEqual(plan['retained_kb_ids'], [8])
        self.assertEqual([r['id'] for r in document['state']['selected']['knowledge_bases']], [1, 2])
        self.assertEqual([r['owner_id'] for r in document['state']['selected']['knowledge_bases']], [11, 12])
        self.assertEqual(document['state']['preimage'], tool.fingerprints(self.original))
        raw = Path(plan['plan']).read_text(encoding='utf-8')
        for forbidden in ('SYNTHETIC-AUTH-SECRET', 'DO-NOT-READ-PROVIDER', 'RETAINED-PRIVATE-CONTENT', 'Demo named but MUST REMAIN'):
            self.assertNotIn(forbidden, raw)
        self.assertFalse(self.read_tables & tool.IGNORED, self.read_tables)
        self.assert_files_untouched()

    def test_apply_two_targets_preserves_all_other_rows_files_and_no_network(self):
        with patch.object(socket, 'create_connection', side_effect=AssertionError('No network')), patch.object(socket.socket, 'connect', side_effect=AssertionError('No network')):
            plan = self.plan()
            result = self.apply(plan)
        self.assertEqual(result['status'], 'applied')
        self.assertTrue(result['committed'])
        self.assertEqual(result['retained_kb_ids'], [8])
        issued = json.loads(Path(plan['plan']).read_text(encoding='utf-8'))
        self.assertEqual(tool.fingerprints(self.rows()), issued['state']['expected_afterimage'])
        self.assertEqual(self.rows(tool.IGNORED), self.auth_original)
        self.assertFalse(self.read_tables & tool.IGNORED, self.read_tables)
        backup = json.loads(Path(result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(backup['rows'], issued['state']['selected'])
        self.assertEqual(set(backup['rows']), set(tool.COLUMNS))
        self.assertEqual(json.loads(Path(result['receipt']).read_text(encoding='utf-8'))['status'], 'applied')
        self.assert_files_untouched()

    def test_templates_are_preserved_unless_ids_are_explicit(self):
        result = self.apply(self.plan(templates=''), templates='')
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(self.rows()['industry_templates'], self.original['industry_templates'])

    def test_retained_template_reference_refuses_before_evidence_or_deletion(self):
        self.execute('UPDATE knowledge_bases SET template_id=10 WHERE id=8')
        before = self.rows()
        with self.assertRaisesRegex(tool.RetirementError, 'retained library'):
            self.plan()
        self.assertEqual(self.rows(), before)
        self.assertFalse(self.output.exists())

    def test_active_parse_task_refused_at_plan_and_apply(self):
        plan = self.plan()
        self.execute("UPDATE parse_tasks SET phase='vectorizing' WHERE id=301")
        before = self.rows()
        for action in (self.plan, lambda: self.apply(plan)):
            with self.assertRaisesRegex(tool.RetirementError, 'Active parsing'):
                action()
        self.assertEqual(self.rows(), before)

    def test_target_name_owner_and_row_changes_invalidate_cas(self):
        plan = self.plan()
        self.execute("UPDATE knowledge_bases SET name='Changed approval target',owner_id=18 WHERE id=1")
        before = self.rows()
        with self.assertRaisesRegex(tool.RetirementError, 'preimage/CAS'):
            self.apply(plan)
        self.assertEqual(self.rows(), before)
        self.assertFalse(list(self.output.glob('backup-*')))

    def test_nonmaximum_target_row_change_and_count_change_invalidate_cas(self):
        plan = self.plan()
        self.execute("UPDATE chunks SET content='Changed exact target bytes' WHERE id=201")
        with self.assertRaisesRegex(tool.RetirementError, 'preimage/CAS'):
            self.apply(plan)
        revised = self.plan()
        self.execute('DELETE FROM qa_feedbacks WHERE id=701')
        before = self.rows()
        with self.assertRaisesRegex(tool.RetirementError, 'preimage/CAS'):
            self.apply(revised)
        self.assertEqual(self.rows(), before)

    def test_retained_row_drift_also_invalidates_full_business_cas(self):
        plan = self.plan()
        self.execute("UPDATE chunks SET content='Changed retained content' WHERE id=208")
        before = self.rows()
        with self.assertRaisesRegex(tool.RetirementError, 'preimage/CAS'):
            self.apply(plan)
        self.assertEqual(self.rows(), before)

    def test_outside_citation_into_target_document_is_refused(self):
        self.execute('UPDATE citations SET document_id=101,chunk_id=201 WHERE id=608')
        before = self.rows()
        with self.assertRaisesRegex(tool.RetirementError, 'Cross-library'):
            self.plan()
        self.assertEqual(self.rows(), before)

    def test_cross_reference_between_two_targets_is_also_refused(self):
        self.execute('UPDATE citations SET document_id=102,chunk_id=202 WHERE id=601')
        with self.assertRaisesRegex(tool.RetirementError, 'Cross-library'):
            self.plan()

    def test_deleted_document_citation_snapshot_is_backed_up_without_file_access(self):
        self.execute('UPDATE citations SET document_id=999999,chunk_id=NULL WHERE id=601')
        result = self.apply(self.plan())
        backup = json.loads(Path(result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(backup['rows']['citations'][0]['document_id'], 999999)
        self.assertIsNone(backup['rows']['citations'][0]['chunk_id'])
        self.assert_files_untouched()

    def test_confirmation_scope_and_plan_integrity_are_required(self):
        plan = self.plan()
        with self.assertRaisesRegex(tool.RetirementError, 'confirm-exact-retirement'):
            self.apply(plan, confirmed=False)
        with self.assertRaisesRegex(tool.RetirementError, 'scope differs'):
            tool.apply_plan(self.db, self.output, '1', '10,20', plan['plan'], confirmed=True)
        document = json.loads(Path(plan['plan']).read_text(encoding='utf-8'))
        document['state']['libraries'][0]['owner_id'] = 12345
        Path(plan['plan']).write_text(json.dumps(document), encoding='utf-8')
        with self.assertRaisesRegex(tool.RetirementError, 'integrity'):
            self.apply(plan)
        self.assertEqual(self.rows(), self.original)

    def test_no_whole_library_selection_missing_ids_or_implicit_defaults(self):
        for ids in ('', 'all', '1-2', '1,1', '1,2,8', '1,999', '-1'):
            with self.subTest(ids=ids), self.assertRaises(tool.RetirementError):
                tool.create_plan(self.db, self.output, ids)
        with self.assertRaisesRegex(tool.RetirementError, 'absolute local'):
            tool.create_plan(Path('relative.db'), self.output, '1,2')
        self.assertEqual(self.rows(), self.original)

    def test_unknown_table_column_or_version_layout_fails_closed(self):
        for sql, cleanup in [('CREATE TABLE document_versions (id INTEGER PRIMARY KEY)', 'DROP TABLE document_versions'),
                             ('CREATE TABLE unknown_domain (id INTEGER PRIMARY KEY)', 'DROP TABLE unknown_domain')]:
            self.execute(sql)
            with self.assertRaises(tool.RetirementError):
                self.plan()
            self.execute(cleanup)
        self.execute('ALTER TABLE chunks ADD COLUMN unexpected_secret TEXT')
        with self.assertRaisesRegex(tool.RetirementError, 'Unknown columns'):
            self.plan()

    def test_inbound_foreign_key_from_excluded_table_is_metadata_only_refusal(self):
        self.execute('ALTER TABLE llm_channels ADD COLUMN forbidden_business_ref INTEGER REFERENCES knowledge_bases(id)')
        with self.assertRaisesRegex(tool.RetirementError, 'inbound foreign key'):
            self.plan()
        self.assertFalse(self.read_tables & tool.IGNORED)
        self.assertEqual(self.rows(), self.original)

    def test_trigger_added_after_plan_is_refused_without_auth_reads(self):
        plan = self.plan()
        self.execute("CREATE TRIGGER hidden_side_effect AFTER DELETE ON documents BEGIN DELETE FROM users WHERE id=11; END")
        with self.assertRaisesRegex(tool.RetirementError, 'Triggers/views'):
            self.apply(plan)
        self.assertEqual(self.rows(), self.original)
        self.assertFalse(self.read_tables & tool.IGNORED)

    def test_mid_transaction_failure_rolls_back_all_deletions(self):
        plan = self.plan()
        def failure(conn, selected):
            self.assertTrue(conn.in_transaction)
            conn.execute('DELETE FROM qa_feedbacks WHERE id=?', (701,))
            raise tool.RetirementError('Synthetic transactional failure')
        with patch.object(tool, 'delete_selected', side_effect=failure):
            with self.assertRaisesRegex(tool.RetirementError, 'Synthetic transactional failure'):
                self.apply(plan)
        self.assertEqual(self.rows(), self.original)
        self.assertTrue(list(self.output.glob('backup-*')))
        self.assertFalse(list(self.output.glob('receipt-*')))
        self.assert_files_untouched()
        # Exact preimage permits a deliberate retry; the durable backup is reused only byte-for-byte.
        self.assertEqual(self.apply(plan)['status'], 'applied')

    def test_postimage_guard_rolls_back_an_accidental_outside_deletion(self):
        plan = self.plan()
        original = tool.delete_selected
        def wrong_scope(conn, selected):
            original(conn, selected)
            conn.execute('DELETE FROM chunks WHERE id=208')
        with patch.object(tool, 'delete_selected', side_effect=wrong_scope):
            with self.assertRaisesRegex(tool.RetirementError, 'Postimage mismatch'):
                self.apply(plan)
        self.assertEqual(self.rows(), self.original)
        self.assertEqual(self.rows(tool.IGNORED), self.auth_original)

    def test_commit_failure_rolls_back_and_intent_does_not_claim_success(self):
        plan = self.plan()
        original = tool.open_db
        class FailCommit:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def execute(self, sql, *args):
                if sql == 'COMMIT':
                    raise sqlite3.OperationalError('Synthetic COMMIT failure')
                return self.connection.execute(sql, *args)
        @contextmanager
        def failing_connection(*args, **kwargs):
            with original(*args, **kwargs) as connection:
                yield FailCommit(connection)
        with patch.object(tool, 'open_db', side_effect=failing_connection):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'COMMIT failure'):
                self.apply(plan)
        self.assertEqual(self.rows(), self.original)
        intent = next(self.output.glob('intent-*'))
        self.assertEqual(json.loads(intent.read_text(encoding='utf-8'))['status'], 'prepared_not_commit_proof')
        self.assertFalse(list(self.output.glob('receipt-*')))

    def test_lost_commit_acknowledgement_is_explicitly_uncertain_not_refused(self):
        plan = self.plan()
        original = tool.open_db
        class LostAcknowledgement:
            def __init__(self, connection):
                self.connection = connection
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def execute(self, sql, *args):
                result = self.connection.execute(sql, *args)
                if sql == 'COMMIT':
                    raise OSError('Synthetic lost COMMIT acknowledgement')
                return result
        @contextmanager
        def connection_with_lost_ack(*args, **kwargs):
            with original(*args, **kwargs) as connection:
                yield LostAcknowledgement(connection)
        with patch.object(tool, 'open_db', side_effect=connection_with_lost_ack):
            with self.assertRaisesRegex(tool.CommitUncertain, 'COMMIT outcome uncertain'):
                self.apply(plan)
        self.assertEqual([r['id'] for r in self.rows()['knowledge_bases']], [8])
        self.assertTrue(list(self.output.glob('backup-*')))
        self.assertTrue(list(self.output.glob('intent-*')))
        self.assertFalse(list(self.output.glob('receipt-*')))
        self.assert_files_untouched()

    def test_authorizer_denies_forbidden_reads_and_nonbusiness_writes(self):
        with tool.open_db(self.db, writable=True) as connection:
            for sql in ('SELECT password_digest FROM users', 'SELECT api_key FROM llm_channels',
                        'SELECT value FROM sys_params', 'DELETE FROM users WHERE id=11',
                        "ATTACH DATABASE ':memory:' AS other"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                    connection.execute(sql)
        self.assertEqual(self.rows(tool.IGNORED), self.auth_original)

    def test_template_reference_added_after_plan_is_not_retired(self):
        plan = self.plan()
        self.execute('UPDATE knowledge_bases SET template_id=10 WHERE id=8')
        before = self.rows()
        with self.assertRaisesRegex(tool.RetirementError, 'retained library'):
            self.apply(plan)
        self.assertEqual(self.rows(), before)
        self.assertFalse(list(self.output.glob('backup-*')))

    def test_evidence_failure_before_commit_rolls_back(self):
        plan = self.plan()
        original = tool.write_json
        def fail_intent(path, value, **options):
            if path.name.startswith('intent-'):
                raise OSError('Synthetic full evidence disk')
            return original(path, value, **options)
        with patch.object(tool, 'write_json', side_effect=fail_intent), self.assertRaises(OSError):
            self.apply(plan)
        self.assertEqual(self.rows(), self.original)

    def test_postcommit_receipt_failure_is_not_misreported_as_rollback(self):
        plan = self.plan()
        original = tool.write_json
        def fail_receipt(path, value, **options):
            if path.name.startswith('receipt-'):
                raise OSError('Synthetic receipt failure after COMMIT')
            return original(path, value, **options)
        with patch.object(tool, 'write_json', side_effect=fail_receipt):
            result = self.apply(plan)
        self.assertEqual(result['status'], 'applied_receipt_pending')
        self.assertTrue(result['committed'])
        self.assertTrue(Path(result['backup']).is_file())
        self.assertEqual([r['id'] for r in self.rows()['knowledge_bases']], [8])

    def test_backup_is_lossless_for_reviewed_insert_restore_with_full_afterimage_guard(self):
        result = self.apply(self.plan())
        backup = json.loads(Path(result['backup']).read_text(encoding='utf-8'))
        self.assertEqual(tool.fingerprints(self.rows()), backup['expected_afterimage'])
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('PRAGMA foreign_keys=ON'); db.execute('BEGIN IMMEDIATE')
            for table in backup['restore_order']:
                columns = tool.COLUMNS[table]
                for row in backup['rows'][table]:
                    self.assertIsNone(db.execute(f'SELECT id FROM "{table}" WHERE id=?', (row['id'],)).fetchone())
                    db.execute(f'INSERT INTO "{table}" (' + ','.join('"' + c + '"' for c in columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', [row[c] for c in columns])
        self.assertEqual(self.rows(), self.original)
        self.assertEqual(self.rows(tool.IGNORED), self.auth_original)
        self.assert_files_untouched()

    def test_cli_defaults_to_plan_then_requires_exact_confirmation(self):
        env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP') if key in os.environ}
        command = [sys.executable, '-I', '-X', 'utf8', '-B', str(SCRIPT), '--db', str(self.db), '--output', str(self.output), '--kb-ids', '1,2', '--template-ids', '10,20']
        planned = subprocess.run(command, env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
        plan = json.loads(planned.stdout)
        self.assertEqual(self.rows(), self.original)
        refused = subprocess.run(command + ['--apply', plan['plan']], env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertEqual(self.rows(), self.original)
        applied = subprocess.run(command + ['--apply', plan['plan'], '--confirm-exact-retirement'], env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(json.loads(applied.stdout)['retained_kb_ids'], [8])
        replay = subprocess.run(command + ['--apply', plan['plan'], '--confirm-exact-retirement'], env=env, capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(replay.returncode, 2, replay.stdout + replay.stderr)
        self.assert_files_untouched()


if __name__ == '__main__':
    unittest.main()