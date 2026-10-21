"""Isolated diagnostics regressions; no application imports or original data access."""
from __future__ import annotations

from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'backend/scripts/local_diagnostics.py'
RUNS = ROOT / 'storage/local-adaptation/20260915/diagnostics'
SCHEMA = Path(__file__).with_name('fixtures') / 'legacy_schema_v1.sql'
VERSION = '0001_document_versions'
SECRET = 'SYNTHETIC-PRIVATE-DO-NOT-PRINT-5197'
spec = importlib.util.spec_from_file_location('local_diagnostics_under_test', SOURCE)
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)


class LocalDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        if RUNS.resolve() != RUNS or not RUNS.is_relative_to(ROOT):
            raise RuntimeError('Unsafe diagnostics fixture parent')
        RUNS.mkdir(parents=True, exist_ok=True)
        self.owned = Path(tempfile.mkdtemp(prefix='case-', dir=RUNS)).resolve()
        self.addCleanup(self.cleanup_owned)
        self.project = self.owned / '迁移 项目 % #'
        self.backend = self.project / 'backend'
        for relative in ('scripts/local_diagnostics.py', 'requirements.txt', 'app/db/models/entity.py',
                         'app/db/migrations/__init__.py', f'app/db/migrations/{VERSION}.sql'):
            target = self.backend / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'backend' / relative, target)
        (self.backend / 'app/__init__.py').write_text('raise AssertionError("app import forbidden")\n', encoding='utf-8')
        self.script = self.backend / 'scripts/local_diagnostics.py'
        self.config = self.backend / 'config.yaml'
        self.db_path = self.backend / 'storage/data/app.db'
        self.uploads = self.backend / 'storage/uploads'
        self.write_config()

    def cleanup_owned(self):
        target = self.owned.resolve()
        if target != self.owned or target.parent != RUNS or not target.name.startswith('case-'):
            raise RuntimeError('Refusing cleanup outside this test-created directory')
        def missing_child_only(_operation, name, error):
            # SQLite/Windows may retire a sidecar after rmtree enumerates it.
            # Ignore only a vanished descendant; permission/root errors still fail.
            child = Path(name).resolve()
            if isinstance(error, FileNotFoundError) and child != target and child.is_relative_to(target):
                return
            raise error
        shutil.rmtree(target, onexc=missing_child_only)

    def write_config(self, storage=None, path=None):
        path = path or self.config
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'storage': storage or {}, 'security': {'secret_key': SECRET},
                                    'seed': {'admin_password': SECRET}, 'provider': {'api_key': SECRET}},
                                   ensure_ascii=False), encoding='utf-8')
        return path

    def make_database(self, documents=(), *, versioned=False, name='app.db'):
        path = self.db_path.with_name(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(SCHEMA.read_text(encoding='utf-8-sig'))
            for document_id, stored in documents:
                connection.execute('INSERT INTO documents '
                                   '(id,kb_id,filename,file_type,file_size,storage_path,fingerprint,status,page_count,'
                                   'error_message,uploaded_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (document_id, 1, 'fixture.txt', 'txt', 0, stored, '', 'pending', 0, '', 1,
                                    '2026-09-15', '2026-09-15'))
            if versioned:
                sql = (self.backend / f'app/db/migrations/{VERSION}.sql').read_bytes()
                connection.executescript(sql.decode('utf-8-sig'))
                checksum = hashlib.sha256(sql + b'\x00legacy-backfill-v1').hexdigest()
                connection.execute('INSERT INTO schema_migrations VALUES (?,?,?,?)',
                                   (VERSION, checksum, '2026-09-15', json.dumps({'secret': SECRET})))
            connection.commit()
        return path

    def diagnose(self):
        return diag.diagnose(str(self.config), backend=self.backend)

    def cli(self, *args):
        cwd = self.owned / 'unrelated cwd'
        cwd.mkdir(exist_ok=True)
        env = dict(os.environ, APP_CONFIG=str(self.owned / 'ignored-environment-config.yaml'))
        return subprocess.run([sys.executable, '-B', str(self.script), *args], cwd=cwd, env=env,
                              capture_output=True, text=True, encoding='utf-8', timeout=30)

    def snapshot(self):
        return {str(path.relative_to(self.owned)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.owned.rglob('*') if path.is_file()}

    def test_legacy_protocol_and_default_config_from_unrelated_cwd(self):
        self.make_database()
        before = self.snapshot()
        process = self.cli('--json')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(set(result), {'ok', 'python', 'requirements', 'config', 'database', 'warnings', 'errors'})
        self.assertIs(result['ok'], True)
        self.assertEqual(set(result['database']), {'path', 'exists', 'state', 'document_count',
                                                 'missing_upload_ids', 'outside_upload_ids'})
        self.assertEqual(result['database']['state'], 'legacy')
        self.assertEqual(result['database']['document_count'], 0)
        self.assertEqual(result['database']['path'], str(self.db_path))
        self.assertEqual(result['python']['executable'], sys.executable)
        self.assertEqual(result['config']['path'], str(self.config))
        self.assertEqual(process.stderr, '')
        self.assertEqual(before, self.snapshot())

    def test_missing_config_does_not_create_config_storage_or_database(self):
        missing = self.project / 'absent folder/config.yaml'
        process = self.cli('--config', 'absent folder/config.yaml', '--json')
        self.assertEqual(process.returncode, 2)
        result = json.loads(process.stdout)
        self.assertFalse(result['ok'])
        self.assertEqual(result['config']['path'], str(missing))
        self.assertEqual(result['database']['state'], 'unknown')
        self.assertFalse(missing.parent.exists())
        self.assertFalse((self.backend / 'storage').exists())

    def test_missing_database_is_not_created(self):
        process = self.cli('--json')
        self.assertEqual(process.returncode, 2)
        result = json.loads(process.stdout)
        self.assertEqual(result['database']['state'], 'missing')
        self.assertFalse(result['database']['exists'])
        self.assertFalse(self.db_path.parent.exists())

    def test_unicode_space_config_relative_to_project_storage_relative_to_backend(self):
        self.make_database()
        custom = self.write_config(path=self.project / '配置 目录/本机 配置.yaml')
        process = self.cli('--config', '配置 目录/本机 配置.yaml', '--json')
        self.assertEqual(process.returncode, 0, process.stdout)
        result = json.loads(process.stdout)
        self.assertEqual(result['config']['path'], str(custom))
        self.assertEqual(result['database']['path'], str(self.db_path))

    def test_legacy_relative_upload_missing_and_outside_are_warning_only(self):
        valid = self.uploads / '1/中文 附件.txt'
        valid.parent.mkdir(parents=True)
        valid.write_text('synthetic upload', encoding='utf-8')
        outside = self.backend / 'storage/uploads-other/outside.txt'
        outside.parent.mkdir()
        outside.write_text(SECRET, encoding='utf-8')
        self.make_database([(1, str(valid.relative_to(self.backend))), (2, 'storage/uploads/missing.txt'),
                            (3, str(outside)), (4, ''), (5, '../outside-project.txt')])
        before = self.snapshot()
        result = self.diagnose()
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['database']['document_count'], 5)
        self.assertEqual(result['database']['missing_upload_ids'], [2, 4])
        self.assertEqual(result['database']['outside_upload_ids'], [3, 5])
        self.assertEqual(len(result['warnings']), 2)
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(before, self.snapshot())

    def test_external_config_and_upload_root_report_only(self):
        external = self.owned / 'explicit external uploads'
        external.mkdir()
        uploaded = external / 'existing.txt'
        uploaded.write_text(SECRET, encoding='utf-8')
        self.make_database([(1, str(uploaded))])
        custom = self.write_config({'upload_dir': str(external)}, self.owned / 'explicit config.yaml')
        before = self.snapshot()
        result = diag.diagnose(str(custom), backend=self.backend)
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['database']['outside_upload_ids'], [])
        self.assertTrue(any('配置文件位于项目根外' in item for item in result['warnings']))
        self.assertTrue(any('上传目录位于 backend 根外' in item for item in result['warnings']))
        self.assertEqual(before, self.snapshot())

    def test_human_output_and_both_formats_hide_sensitive_config(self):
        self.make_database()
        for arguments in ((), ('--json',)):
            with self.subTest(arguments=arguments):
                process = self.cli(*arguments)
                self.assertEqual(process.returncode, 0, process.stdout)
                self.assertNotIn(SECRET, process.stdout + process.stderr)
                if not arguments:
                    self.assertIn('本机后端诊断：可启动', process.stdout)
                    self.assertFalse(process.stdout.lstrip().startswith('{'))

    def test_malformed_yaml_and_storage_values_are_redacted(self):
        for content in ('security:\n  secret_key: "' + SECRET, json.dumps({'storage': {'data_dir': {SECRET: SECRET}}})):
            with self.subTest(content_type=content[:1]):
                self.config.write_text(content, encoding='utf-8')
                process = self.cli('--json')
                self.assertEqual(process.returncode, 2)
                self.assertFalse(json.loads(process.stdout)['ok'])
                self.assertNotIn(SECRET, process.stdout + process.stderr)
                self.assertEqual(process.stderr, '')
                self.assertFalse(self.db_path.parent.exists())

    def test_missing_and_incompatible_requirements_with_metadata_mock(self):
        self.make_database()
        (self.backend / 'requirements.txt').write_text('missing-pkg>=1\nold-pkg>=2\nfine[extra]>=3 # inline\n'
                                                     '# optional-pkg>=99\nskipped>=1; python_version < "1"\n', encoding='utf-8')
        def installed(name):
            if name == 'missing-pkg':
                raise diag.metadata.PackageNotFoundError(name)
            return {'old-pkg': '1.0', 'fine': '3.5'}[name]
        with mock.patch.object(diag.metadata, 'version', side_effect=installed) as lookup:
            result = self.diagnose()
        self.assertFalse(result['ok'])
        self.assertEqual(result['requirements']['missing'], ['missing-pkg'])
        self.assertEqual(result['requirements']['incompatible'], ['old-pkg==1.0 (要求 >=2)'])
        self.assertEqual(result['requirements']['versions'], {'old-pkg': '1.0', 'fine': '3.5'})
        self.assertEqual(lookup.call_count, 3)
        self.assertEqual(result['database']['state'], 'legacy')

    def test_packaging_missing_still_inspects_database(self):
        self.make_database()
        with mock.patch.dict(sys.modules, {'packaging.requirements': None}):
            result = self.diagnose()
        self.assertFalse(result['ok'])
        self.assertIn('packaging', result['requirements']['missing'])
        self.assertEqual(result['database']['state'], 'legacy')

    def test_yaml_missing_gives_useful_protocol_without_creating_storage(self):
        with mock.patch.dict(sys.modules, {'yaml': None}):
            result = self.diagnose()
        self.assertFalse(result['ok'])
        self.assertIn('PyYAML', result['requirements']['missing'])
        self.assertFalse(self.db_path.parent.exists())

    def test_missing_requirements_is_error_not_an_empty_success(self):
        self.make_database()
        (self.backend / 'requirements.txt').unlink()
        result = self.diagnose()
        self.assertFalse(result['ok'])
        self.assertTrue(any('requirements.txt' in message for message in result['errors']))
        self.assertEqual(result['database']['state'], 'legacy')

    def test_invalid_or_direct_url_requirement_is_redacted_and_blocks(self):
        self.make_database()
        for content in ('not a valid requirement ' + SECRET, 'PyYAML @ https://user:' + SECRET + '@example.invalid/a.whl'):
            (self.backend / 'requirements.txt').write_text(content, encoding='utf-8')
            result = self.diagnose()
            self.assertFalse(result['ok'])
            self.assertNotIn(SECRET, json.dumps(result))

    def test_versioned_readonly_uri_query_only_and_column_allowlist(self):
        self.make_database(versioned=True)
        before, statements = self.snapshot(), []
        connect = sqlite3.connect
        def readonly_connection(database_uri, **kwargs):
            self.assertIn('?mode=ro', database_uri)
            self.assertTrue(kwargs['uri'])
            connection = connect(database_uri, **kwargs)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('CREATE TABLE forbidden_write(id INTEGER)')
            def allow(action, table, column, _database, _trigger):
                permitted = {'documents': {'id', 'storage_path'}, 'schema_migrations': {'version', 'checksum'}}
                if action == sqlite3.SQLITE_READ and table not in ('sqlite_master', 'sqlite_schema'):
                    if column not in permitted.get(table, set()):
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            connection.set_authorizer(allow)
            connection.set_trace_callback(statements.append)
            return connection
        with mock.patch.object(diag.sqlite3, 'connect', side_effect=readonly_connection):
            result = self.diagnose()
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(result['database']['state'], 'versioned')
        self.assertIn('PRAGMA query_only=ON', statements)
        self.assertIn('PRAGMA quick_check', statements)
        self.assertFalse(any('baseline_json' in statement for statement in statements))
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(before, self.snapshot())

    def test_inconsistent_schema_and_unknown_migrations_fail_closed(self):
        cases = {
            'missing_table': (False, 'DROP TABLE messages'),
            'missing_base_column': (False, 'ALTER TABLE documents DROP COLUMN storage_path'),
            'partial_empty_version_table': (False, 'CREATE TABLE document_series(id INTEGER)'),
            'missing_record': (True, 'DELETE FROM schema_migrations'),
            'wrong_checksum': (True, "UPDATE schema_migrations SET checksum='bad'"),
            'unknown_record': (True, "UPDATE schema_migrations SET version='9999_unknown'"),
            'known_plus_unknown': (True, "INSERT INTO schema_migrations VALUES ('9999_unknown','bad','2026','{}')"),
            'missing_trigger': (True, 'DROP TRIGGER versioned_document_no_delete'),
            'missing_version_column': (True, 'ALTER TABLE schema_migrations DROP COLUMN baseline_json'),
        }
        for name, (versioned, sql) in cases.items():
            with self.subTest(case=name):
                path = self.make_database(versioned=versioned, name=name + '.db')
                self.write_config({'db_name': path.name})
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute(sql)
                    connection.commit()
                before = self.snapshot()
                result = self.diagnose()
                self.assertFalse(result['ok'])
                self.assertEqual(result['database']['state'], 'inconsistent', result['errors'])
                self.assertTrue(result['errors'])
                self.assertEqual(before, self.snapshot())

    def test_quick_check_failure_is_redacted_and_blocks(self):
        self.make_database()
        connection = mock.Mock()
        connection.execute.return_value.fetchall.return_value = [('corrupt: ' + SECRET,)]
        with mock.patch.object(diag.sqlite3, 'connect', return_value=connection):
            result = self.diagnose()
        self.assertFalse(result['ok'])
        self.assertEqual(result['database']['state'], 'inconsistent')
        self.assertNotIn(SECRET, json.dumps(result))
        connection.close.assert_called_once()

    def test_wal_database_is_not_opened_or_silently_read_without_wal(self):
        self.make_database()
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE synthetic_wal_marker(id INTEGER)')
            connection.commit()
            before = self.snapshot()
            with mock.patch.object(diag.sqlite3, 'connect', side_effect=AssertionError('must not open WAL database')):
                result = self.diagnose()
            self.assertFalse(result['ok'])
            self.assertEqual(result['database']['state'], 'unknown')
            self.assertTrue(any('WAL' in message for message in result['errors']))
            self.assertEqual(before, self.snapshot())

    def test_corrupt_database_does_not_escape_json_protocol(self):
        self.db_path.parent.mkdir(parents=True)
        self.db_path.write_bytes(b'not a database: ' + SECRET.encode())
        before = self.snapshot()
        process = self.cli('--json')
        self.assertEqual(process.returncode, 2)
        self.assertFalse(json.loads(process.stdout)['ok'])
        self.assertNotIn(SECRET, process.stdout + process.stderr)
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    unittest.main(verbosity=2)
