"""Actual SQLite WAL backup tests with synthetic credential-free fixtures."""
from __future__ import annotations
import json
import shutil
import sqlite3
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import test_version_migrations as fixtures
from app.db.migrations import MigrationError, SensitiveDatabaseError
from app.db.migrations.backup import create_consistent_backup, verify_backup

class ConsistentBackupTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.MigrationFoundationTests()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.destination = self.f.root / 'backup'

    def backup(self, **extra):
        return create_consistent_backup(self.f.db_path, destination=self.destination, **self.f.options(), **extra)

    def test_backup_contains_readable_db_every_referenced_file_and_vectors(self):
        manifest = self.backup()
        self.assertEqual(manifest['method'], 'sqlite3.Connection.backup under a read transaction')
        result = verify_backup(self.destination, allowed_root=self.f.root)
        self.assertEqual(result['status'], 'verified'); self.assertEqual(result['files'], 3)
        with sqlite3.connect(self.destination / 'app.db') as copy:
            self.assertEqual(copy.execute('SELECT vector_json FROM chunks WHERE id=?', (self.f.chunk,)).fetchone()[0], '[1,0]')
            self.assertEqual(copy.execute('SELECT content FROM messages WHERE id=?', (self.f.message,)).fetchone()[0], '旧回答不能改')

    def test_wal_commits_are_included_but_later_writer_is_outside_read_snapshot(self):
        self.f.db.execute('PRAGMA journal_mode=WAL')
        self.f.db.execute('PRAGMA wal_autocheckpoint=0')
        self.f.row('messages', session_id=self.f.session, role='user', content='committed in WAL', question='', kb_id=self.f.kb, mode='none', status='success')
        def later_writer():
            self.f.row('messages', session_id=self.f.session, role='user', content='committed after snapshot', question='', kb_id=self.f.kb, mode='none', status='success')
        # This deliberately wrong method is a control, inside the same owned fixture.
        raw = self.f.root / 'raw-file-copy.db'
        shutil.copyfile(self.f.db_path, raw)
        with sqlite3.connect(raw) as connection:
            raw_count = connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
        manifest = self.backup(_after_snapshot=later_writer)
        self.assertEqual(manifest['source_journal_mode'], 'wal')
        with sqlite3.connect(self.destination / 'app.db') as copy:
            copied_count = copy.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
        self.assertEqual(raw_count, 1); self.assertEqual(copied_count, 2)
        self.assertEqual(self.f.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 3)
        self.assertEqual(verify_backup(self.destination, allowed_root=self.f.root)['status'], 'verified')

    def test_version_metadata_and_audit_are_also_preserved(self):
        self.f.apply(); self.backup()
        report = verify_backup(self.destination, allowed_root=self.f.root)
        self.assertEqual(report['domain_snapshot']['document_versions']['count'], 3)
        self.assertEqual(report['domain_snapshot']['audit_events']['count'], 3)

    def test_existing_destination_is_never_overwritten(self):
        self.destination.mkdir(); sentinel = self.destination / 'owner.txt'; sentinel.write_text('do not overwrite', encoding='utf-8')
        with self.assertRaisesRegex(MigrationError, 'already exists'): self.backup()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'do not overwrite')
        self.assertFalse((self.destination / 'app.db').exists())

    def test_destination_outside_named_workspace_is_rejected(self):
        with self.assertRaisesRegex(MigrationError, 'outside'):
            create_consistent_backup(self.f.db_path, destination=self.f.root.parent / 'not-owned', **self.f.options())

    def test_provider_secret_is_blocked_before_creating_a_copy(self):
        self.f.row('llm_channels', name='fixture', channel_type='online', api_key='synthetic-key-not-a-real-provider')
        with self.assertRaises(SensitiveDatabaseError): self.backup()
        self.assertFalse(self.destination.exists())

    def test_auth_material_is_blocked_before_creating_a_copy(self):
        self.f.db.execute("UPDATE users SET password_digest='synthetic-auth-material' WHERE id=?", (self.f.user,))
        with self.assertRaises(SensitiveDatabaseError): self.backup()
        self.assertFalse(self.destination.exists())

    def test_tampered_upload_is_detected(self):
        manifest = self.backup(); file = (self.destination / manifest['files'][0]['path']).resolve()
        assert file.is_relative_to(self.f.root)
        file.write_text('corrupted backup file', encoding='utf-8')
        with self.assertRaisesRegex(MigrationError, 'hash mismatch'): verify_backup(self.destination, allowed_root=self.f.root)

    def test_tampered_database_is_detected(self):
        self.backup()
        with sqlite3.connect(self.destination / 'app.db') as copy:
            copy.execute("UPDATE messages SET content='corrupted backup' WHERE id=?", (self.f.message,)); copy.commit()
        with self.assertRaisesRegex(MigrationError, 'hash mismatch'): verify_backup(self.destination, allowed_root=self.f.root)

    def test_manifest_path_escape_is_rejected(self):
        self.backup(); file = self.destination / 'manifest.json'; data = json.loads(file.read_text(encoding='utf-8'))
        data['files'][0]['path'] = '../uploads/one.md'; file.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(MigrationError, 'outside'): verify_backup(self.destination, allowed_root=self.f.root)

    def test_missing_file_coverage_is_not_reported_as_complete(self):
        self.backup(); file = self.destination / 'manifest.json'; data = json.loads(file.read_text(encoding='utf-8')); data['files'].pop()
        file.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(MigrationError, 'coverage'): verify_backup(self.destination, allowed_root=self.f.root)

if __name__ == '__main__':
    unittest.main(verbosity=2)
