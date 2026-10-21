"""Migration foundation tests use schema-only, credential-free fixtures.

All writes/deletes stay in this test's verified temporary workspace. Nothing
imports application settings or opens the real business database.
"""
from __future__ import annotations
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.db.migrations import (MigrationError, SensitiveDatabaseError, TABLES, VERSION, credential_presence, migrate_database, plan_migration, protected_snapshot)

RUNS = (ROOT / 'storage' / 'redesign-validation' / 'migration-tests').resolve()
SCHEMA = Path(__file__).with_name('fixtures') / 'legacy_schema_v1.sql'
NOW = '2026-09-12T00:00:00.000000'

class MigrationFoundationTests(unittest.TestCase):
    def setUp(self):
        RUNS.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='case-', dir=RUNS)).resolve()
        assert self.root.is_relative_to(RUNS) and self.root != RUNS
        self.addCleanup(self.cleanup_owned_fixture)
        self.uploads = self.root / 'uploads'
        self.uploads.mkdir()
        self.db_path = self.root / 'app.db'
        self.db = sqlite3.connect(self.db_path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA.read_text(encoding='utf-8'))
        self.row('roles', code='admin', name='fixture admin')
        self.user = self.row('users', username='fixture', role_code='admin', password_salt='', password_digest='')
        self.kb = self.row('knowledge_bases', name='fixture library', owner_id=self.user, template_id=None, status='enabled')
        self.first = self.document('same-name.md', '材料甲：保留原问题与来源。', 'one.md')
        self.failed = self.document('same-name.md', '材料乙：不同内容，不应按同名合并。', 'two.md', status='failed')
        self.duplicate = self.document('same-name.md', '材料甲：保留原问题与来源。', 'three.md')
        self.chunk = self.row('chunks', document_id=self.first, kb_id=self.kb, seq_no=1, content='材料甲：保留原问题与来源。', page_no=0, meta_json='{"kind":"text"}', chunk_hash='legacy-hash', vector_json='[1,0]')
        self.row('chunks', document_id=self.duplicate, kb_id=self.kb, seq_no=1, content='材料甲：保留原问题与来源。', page_no=0, meta_json='{}', vector_json=None)
        self.row('parse_tasks', document_id=self.first, operator_id=self.user, phase='success', progress=100, chunk_count=1)
        self.row('parse_tasks', document_id=self.failed, operator_id=self.user, phase='failed', progress=0, error_message='fixture failure')
        self.session = self.row('sessions', user_id=self.user, kb_id=self.kb, title='旧问题', status='active')
        self.message = self.row('messages', session_id=self.session, role='assistant', content='旧回答不能改', question='旧问题', kb_id=self.kb, mode='degraded', status='success')
        self.citation = self.row('citations', message_id=self.message, chunk_id=self.chunk, document_id=self.first, kb_id=self.kb, doc_name='same-name.md', content_excerpt='旧引用摘录不能改', rank=1)
        self.before = protected_snapshot(self.db)

    def cleanup_owned_fixture(self):
        if hasattr(self, 'db'):
            self.db.close()
        target = self.root.resolve()
        assert target.is_relative_to(RUNS) and target != RUNS and target == self.root
        shutil.rmtree(target)

    def row(self, table, **values):
        # Table names are fixed by tests; every required old-schema field is populated.
        payload = {}
        for info in self.db.execute('PRAGMA table_info("' + table + '")'):
            if info['pk'] or not info['notnull'] or info['dflt_value'] is not None:
                continue
            kind = info['type'].upper()
            payload[info['name']] = NOW if 'DATE' in kind else 0.0 if 'FLOAT' in kind else 0 if 'INT' in kind or 'BOOL' in kind else ''
        payload.update(values)
        names = list(payload)
        return self.db.execute('INSERT INTO "' + table + '" (' + ','.join('"' + n + '"' for n in names) + ') VALUES (' + ','.join('?' for _ in names) + ')', [payload[n] for n in names]).lastrowid

    def document(self, name, content, stored_name, status='success'):
        file = self.uploads / stored_name
        data = content.encode('utf-8'); file.write_bytes(data)
        return self.row('documents', kb_id=self.kb, filename=name, file_type='md', file_size=len(data), storage_path=str(file), fingerprint=hashlib.sha1(data).hexdigest(), status=status, uploaded_by=self.user, error_message='fixture failure' if status == 'failed' else '')

    def options(self):
        return dict(file_base=self.root, upload_root=self.uploads, allowed_root=self.root)

    def plan(self):
        return plan_migration(self.db_path, **self.options())

    def apply(self, **changes):
        plan = self.plan()
        options = dict(expected_plan_digest=plan.get('plan_digest'), execute=True)
        options.update(changes)
        return migrate_database(self.db_path, **self.options(), **options)

    def has_version_tables(self):
        return bool({r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")} & TABLES)

    def test_dry_run_is_readonly_and_binds_exact_plan(self):
        plan = self.plan()
        self.assertEqual(plan['status'], 'planned')
        self.assertEqual(len(plan['files']), 3)
        self.assertEqual(len(plan['plan_digest']), 64)
        self.assertFalse(any(plan['credential_material_present'].values()))
        self.assertFalse(self.has_version_tables())
        self.assertEqual(protected_snapshot(self.db), self.before)

    def test_default_migrate_is_only_a_plan(self):
        result = migrate_database(self.db_path, **self.options())
        self.assertEqual(result['status'], 'planned'); self.assertFalse(self.has_version_tables())

    def test_success_preserves_all_original_ids_rows_and_unknown_dates(self):
        result = self.apply()
        self.assertEqual(result['status'], 'applied'); self.assertTrue(result['legacy_rows_unchanged'])
        self.assertEqual(protected_snapshot(self.db), self.before)
        rows = self.db.execute('SELECT * FROM document_versions ORDER BY version_id').fetchall()
        self.assertEqual([r['version_id'] for r in rows], [self.first, self.failed, self.duplicate])
        self.assertEqual(len({r['series_id'] for r in rows}), 3)
        self.assertTrue(all(r['version_label'] == '旧资料初始版' and r['validity'] == 'unknown' and r['effective_from'] is None and r['effective_to'] is None and r['is_legacy'] == 1 for r in rows))
        self.assertEqual(rows[0]['sha256'], rows[2]['sha256'])
        self.assertEqual(rows[0]['publication_state'], 'published'); self.assertEqual(rows[1]['publication_state'], 'draft')
        self.assertEqual(self.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_rerun_is_idempotent_not_another_backfill(self):
        self.apply(); before = self.db.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0]
        result = self.apply()
        self.assertEqual(result['status'], 'already_applied')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM document_series').fetchone()[0], 3)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM audit_events').fetchone()[0], before)

    def test_legacy_parse_revisions_preserve_chunk_ids_and_admit_no_guessed_source(self):
        self.apply()
        linked = self.db.execute('SELECT * FROM parse_revision_chunks WHERE chunk_id=?', (self.chunk,)).fetchone()
        revision = self.db.execute('SELECT * FROM parse_revisions WHERE id=?', (linked['parse_revision_id'],)).fetchone()
        self.assertEqual(revision['version_id'], self.first); self.assertEqual(revision['source_integrity'], 'legacy_unverified')
        self.assertIsNone(revision['file_sha256']); self.assertIsNone(revision['task_id']); self.assertEqual(revision['is_current'], 1)
        self.assertEqual(json.loads(linked['locator_json'])['reliability'], 'legacy_unverified')
        self.assertEqual(self.db.execute('SELECT chunk_id,content_excerpt FROM citations WHERE id=?', (self.citation,)).fetchone()['chunk_id'], self.chunk)

    def test_unlinked_legacy_citation_stays_unlinked(self):
        self.db.execute('UPDATE citations SET chunk_id=NULL WHERE id=?', (self.citation,))
        before = protected_snapshot(self.db); self.apply()
        self.assertEqual(protected_snapshot(self.db), before)
        self.assertIsNone(self.db.execute('SELECT chunk_id FROM citations WHERE id=?', (self.citation,)).fetchone()[0])

    def test_legacy_fingerprint_disagreement_is_not_silently_rewritten(self):
        self.db.execute('UPDATE documents SET fingerprint=? WHERE id=?', ('older-upload-fingerprint', self.first))
        plan = self.plan(); self.assertEqual(len(plan['warnings']), 1)
        self.apply()
        self.assertEqual(self.db.execute('SELECT fingerprint FROM documents WHERE id=?', (self.first,)).fetchone()[0], 'older-upload-fingerprint')

    def test_stale_plan_refuses_write(self):
        plan = self.plan(); self.db.execute('UPDATE messages SET content=? WHERE id=?', ('changed after planning', self.message))
        with self.assertRaisesRegex(MigrationError, 'stale'):
            migrate_database(self.db_path, **self.options(), execute=True, expected_plan_digest=plan['plan_digest'])
        self.assertFalse(self.has_version_tables())

    def test_execute_requires_a_boolean(self):
        with self.assertRaisesRegex(MigrationError, 'boolean'):
            migrate_database(self.db_path, **self.options(), execute='false')
        self.assertFalse(self.has_version_tables())

    def test_missing_source_refuses_migration_without_deleting_records(self):
        file = (self.uploads / 'one.md').resolve(); assert file.is_relative_to(self.root); file.unlink()
        with self.assertRaisesRegex(MigrationError, 'source_file_missing'): self.plan()
        self.assertEqual(protected_snapshot(self.db), self.before); self.assertFalse(self.has_version_tables())

    def test_source_path_escape_is_rejected(self):
        self.db.execute('UPDATE documents SET storage_path=? WHERE id=?', (str(self.root / 'outside.md'), self.first))
        (self.root / 'outside.md').write_text('outside upload boundary', encoding='utf-8')
        with self.assertRaisesRegex(MigrationError, 'outside_authorized'): self.plan()
        self.assertFalse(self.has_version_tables())

    def test_database_must_be_inside_named_root(self):
        with self.assertRaisesRegex(MigrationError, 'outside'):
            plan_migration(self.db_path, file_base=self.root, upload_root=self.uploads, allowed_root=self.uploads)

    def test_scale_limit_is_explicit_not_partial_hashing(self):
        with self.assertRaisesRegex(MigrationError, 'exceeds'):
            plan_migration(self.db_path, **self.options(), max_total_bytes=1)
        self.assertFalse(self.has_version_tables())

    def test_active_parse_tasks_block_migration(self):
        self.row('parse_tasks', document_id=self.first, operator_id=self.user, phase='extracting')
        with self.assertRaisesRegex(MigrationError, 'Active'): self.plan()
        self.assertFalse(self.has_version_tables())

    def test_foreign_key_orphans_block_migration(self):
        self.db.execute('PRAGMA foreign_keys=OFF')
        self.db.execute('UPDATE citations SET document_id=9999 WHERE id=?', (self.citation,))
        with self.assertRaisesRegex(MigrationError, 'Foreign-key'): self.plan()
        self.assertFalse(self.has_version_tables())

    def test_cross_library_chunk_mismatch_is_not_merged(self):
        other = self.row('knowledge_bases', name='other library', owner_id=self.user, status='enabled')
        self.db.execute('UPDATE chunks SET kb_id=? WHERE id=?', (other, self.chunk))
        with self.assertRaisesRegex(MigrationError, 'Cross-library'): self.plan()
        self.assertFalse(self.has_version_tables())

    def test_provider_secret_presence_blocks_execute_without_returning_value(self):
        secret = 'synthetic-test-key-never-used'
        self.row('llm_channels', name='test only', channel_type='online', api_key=secret)
        plan = self.plan(); self.assertTrue(plan['credential_material_present']['llm_channels.api_key'])
        self.assertNotIn(secret, json.dumps(plan))
        with self.assertRaises(SensitiveDatabaseError) as failure: self.apply()
        self.assertNotIn(secret, str(failure.exception)); self.assertFalse(self.has_version_tables())

    def test_user_auth_material_also_blocks_automatic_execute(self):
        self.db.execute("UPDATE users SET password_digest='synthetic-not-real' WHERE id=?", (self.user,))
        with self.assertRaises(SensitiveDatabaseError): self.apply()
        self.assertFalse(self.has_version_tables())

    def test_failure_after_schema_rolls_back_ddl_and_data(self):
        def failure(stage):
            if stage == 'after_schema': raise RuntimeError('injected schema failure')
        with self.assertRaisesRegex(RuntimeError, 'injected'): self.apply(_failure_hook=failure)
        self.assertFalse(self.has_version_tables()); self.assertEqual(protected_snapshot(self.db), self.before)

    def test_failure_after_backfill_rolls_back_every_new_row(self):
        def failure(stage):
            if stage == 'after_backfill': raise RuntimeError('injected backfill failure')
        with self.assertRaisesRegex(RuntimeError, 'injected'): self.apply(_failure_hook=failure)
        self.assertFalse(self.has_version_tables()); self.assertEqual(protected_snapshot(self.db), self.before)

    def test_file_changed_during_migration_prevents_commit(self):
        def failure(stage):
            if stage == 'after_backfill': (self.uploads / 'one.md').write_text('changed outside DB transaction', encoding='utf-8')
        with self.assertRaisesRegex(MigrationError, 'source file changed'): self.apply(_failure_hook=failure)
        self.assertFalse(self.has_version_tables()); self.assertEqual(protected_snapshot(self.db), self.before)

    def test_finalized_chunks_versions_and_audit_are_immutable(self):
        self.apply()
        for statement, values in [
            ('UPDATE chunks SET content=? WHERE id=?', ('rewrite', self.chunk)),
            ('DELETE FROM chunks WHERE id=?', (self.chunk,)),
            ('UPDATE documents SET filename=? WHERE id=?', ('renamed.md', self.first)),
            ('DELETE FROM documents WHERE id=?', (self.first,)),
            ('UPDATE document_versions SET sha256=? WHERE version_id=?', ('a'*64, self.first)),
            ('DELETE FROM document_versions WHERE version_id=?', (self.first,)),
            ('UPDATE audit_events SET reason=?', ('rewrite history',)),
        ]:
            with self.subTest(sql=statement), self.assertRaises(sqlite3.IntegrityError): self.db.execute(statement, values)
        self.assertEqual(protected_snapshot(self.db), self.before)

    def test_applied_missing_guard_is_detected(self):
        self.apply(); self.db.execute('DROP TRIGGER versioned_chunk_no_update')
        with self.assertRaisesRegex(MigrationError, 'missing'): self.plan()

    def test_applied_modified_guard_is_detected(self):
        self.apply(); self.db.execute('DROP TRIGGER versioned_chunk_no_update')
        self.db.execute('CREATE TRIGGER versioned_chunk_no_update BEFORE UPDATE ON chunks BEGIN SELECT 1; END')
        with self.assertRaisesRegex(MigrationError, 'definition differs'): self.plan()

    def test_applied_missing_unique_index_is_detected(self):
        self.apply(); self.db.execute('DROP INDEX uq_current_parse_revision')
        with self.assertRaisesRegex(MigrationError, 'missing'): self.plan()

    def test_applied_checksum_tampering_is_detected(self):
        self.apply(); self.db.execute('UPDATE schema_migrations SET checksum=? WHERE version=?', ('0'*64, VERSION))
        with self.assertRaisesRegex(MigrationError, 'checksum'): self.plan()

    def test_current_successful_revision_is_unique(self):
        self.apply()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'UNIQUE'):
            self.row('parse_revisions', version_id=self.first, parser_version='fixture-parser', file_sha256='a'*64, content_hash='b'*64, source_integrity='verified', state='success', is_current=1, chunk_count=1)

    def test_successful_revision_requires_evidence_and_source_state(self):
        self.apply()
        with self.assertRaises(sqlite3.IntegrityError):
            self.row('parse_revisions', version_id=self.first, parser_version='fixture-parser', content_hash='', source_integrity='unverified', state='success', is_current=0, chunk_count=0)

    def test_empty_precreated_schema_can_be_backfilled_once(self):
        from app.db.migrations import SQL_FILE, _statements
        for statement in _statements(SQL_FILE.read_text(encoding='utf-8')):
            self.db.execute(statement)
        result = self.apply()
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(protected_snapshot(self.db), self.before)
        self.assertEqual(self.apply()['status'], 'already_applied')

    def test_cli_defaults_to_dry_run_on_explicit_paths(self):
        import subprocess
        command = [sys.executable, '-B', '-X', 'utf8', str(ROOT / 'backend' / 'scripts' / 'migrate_versions.py'), '--database', str(self.db_path), '--file-base', str(self.root), '--upload-root', str(self.uploads), '--allowed-root', str(self.root)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'planned')
        self.assertFalse(self.has_version_tables())

    def test_regular_initialization_never_creates_version_tables(self):
        import subprocess
        config = self.root / 'init-test.yaml'
        config.write_text('storage:\n  data_dir: "' + self.root.as_posix() + '"\n  db_name: init-check.db\n  upload_dir: "' + self.uploads.as_posix() + '"\n  vector_dir: "' + (self.root / 'vectors').as_posix() + '"\nsecurity:\n  secret_key: synthetic-init-test-only\n', encoding='utf-8')
        program = 'import os,sys,json,contextlib,io,sqlite3; os.environ["APP_CONFIG"]=sys.argv[1]; from app.db.init_db import init_db; from app.core.config import settings; from app.db.models import VERSIONING_TABLE_NAMES; capture=io.StringIO();\nwith contextlib.redirect_stdout(capture): init_db()\nconnection=sqlite3.connect(settings.db_path); tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type=\\\'table\\\'")}; print(json.dumps({"unexpected":sorted(tables & VERSIONING_TABLE_NAMES),"database":str(settings.db_path)})); connection.close()'
        # Use parameters for the schema query in the child to avoid shell quoting.
        program = program.replace('type=\\\'table\\\'', 'type=?').replace('execute("SELECT name FROM sqlite_master WHERE type=?")', 'execute("SELECT name FROM sqlite_master WHERE type=?",("table",))')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', program, str(config)], cwd=ROOT / 'backend', capture_output=True, text=True, encoding='utf-8', check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout); self.assertEqual(data['unexpected'], [])
        self.assertTrue(Path(data['database']).resolve().is_relative_to(self.root))

if __name__ == '__main__':
    unittest.main(verbosity=2)
