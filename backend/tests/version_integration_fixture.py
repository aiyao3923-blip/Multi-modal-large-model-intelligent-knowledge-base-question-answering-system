"""W2 HTTP scenarios, executed only in a fresh interpreter by the test module.

No application import/configuration happens during unittest discovery. All data,
credentials (synthetic only), uploads and parser caches belong to --case-root.
The scheduler is synchronous, but calls the real ingest_document pipeline.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
SCHEMA = Path(__file__).with_name('fixtures') / 'legacy_schema_v1.sql'
NOW = '2026-09-14T00:00:00.000000'
TEXT = '办理居住证需要本人身份证、居住证明、近期一寸照片。申请材料提交后在五个工作日内办结。'
QUESTION = '办理居住证需要哪些材料？'
PASSWORD = 'OnlySyntheticW2Test2026!'
CASE_ROOT: Path | None = None


class VersionIntegrationScenario(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(CASE_ROOT, 'Run this fixture through its CLI, not discovery')
        self.root = CASE_ROOT.resolve(strict=True)
        self.assertNotEqual(self.root.parent, self.root)
        self.assertEqual(self.root.parent.name, 'w2-version-http-integration')
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.trace = []
        self.worker_results = []
        for name in ('data', 'uploads', 'vectors', 'cache'):
            (self.root / name).mkdir()
        self.uploads = self.root / 'uploads'
        self.db_path = self.root / 'data' / 'app.db'
        config = self.root / 'fixture-config.yaml'
        config.write_text(json.dumps({
            'app': {'name': 'W2 isolated HTTP test', 'version': 'test-only'},
            'storage': {'data_dir': str(self.db_path.parent), 'db_name': 'app.db',
                        'upload_dir': str(self.uploads), 'vector_dir': str(self.root / 'vectors')},
            'security': {'secret_key': 'synthetic-w2-only-not-production', 'pbkdf2_iterations': 1000},
        }), encoding='utf-8')
        os.environ['APP_CONFIG'] = str(config)
        self.assertFalse(any(k.startswith('APP_') and k != 'APP_CONFIG' for k in os.environ))
        sys.path.insert(0, str(BACKEND))
        self.db = sqlite3.connect(self.db_path, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.stack.callback(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA.read_text(encoding='utf-8'))
        for role in ('admin', 'operator', 'user'):
            self.insert('roles', code=role, name=role)
        self.users = {}
        for username, role in (('admin', 'admin'), ('operator', 'operator'),
                               ('reader', 'user'), ('other_reader', 'user')):
            self.users[username] = self.insert('users', username=username,
                display_name=username, role_code=role, is_active=1,
                password_salt='', password_digest='')
        self.kb = self.insert('knowledge_bases', name='W2政务知识库',
                             owner_id=self.users['admin'], status='enabled')
        self.private_kb = self.insert('knowledge_bases', name='W2管理员专用库',
                                     owner_id=self.users['admin'], status='enabled')
        for role in ('operator', 'user'):
            self.insert('user_kb_access', kb_id=self.kb, role_code=role)
        from app.db import migrations
        self.migrations = migrations
        self.migration_options = dict(file_base=self.root, upload_root=self.uploads,
                                      allowed_root=self.root, max_total_bytes=1024 * 1024)
        self.migration_plan = None
        if self._testMethodName != 'test_capabilities_legacy_no_implicit_migration':
            self.migration_plan = migrations.plan_migration(self.db_path, **self.migration_options)
            self.assertEqual(self.migration_plan['status'], 'planned')
            self.assertFalse(any(self.migration_plan['credential_material_present'].values()))
            self.migration_result = migrations.migrate_database(self.db_path,
                **self.migration_options, execute=True,
                expected_plan_digest=self.migration_plan['plan_digest'])
            self.assertEqual(self.migration_result['status'], 'applied')
            self.assertTrue(self.migration_result['legacy_rows_unchanged'])
        # Authentication material is created only AFTER the credential-free migration.
        # No sensitive_approval_reference, app.init_db seeds, or provider rows are used.
        import jieba
        jieba.dt.tmp_dir = str(self.root / 'cache')
        from app.core.config import settings
        self.assertEqual(settings.config_path.resolve(), config.resolve())
        for p in (settings.db_path, settings.upload_dir, settings.vector_dir):
            self.assertTrue(p.resolve().is_relative_to(self.root), str(p))
        from app.core.security import hash_password
        salt, digest = hash_password(PASSWORD)
        self.db.execute('UPDATE users SET password_salt=?, password_digest=?', (salt, digest))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM llm_channels').fetchone()[0], 0)
        from app.db.session import get_engine, get_session_factory
        self.engine = get_engine()
        self.factory = get_session_factory()
        self.assertEqual(Path(self.engine.url.database).resolve(), self.db_path.resolve())
        self.stack.callback(self.engine.dispose)
        from app.main import app
        from app.workers.tasks import run_sync_parse
        from fastapi.testclient import TestClient
        self.app = app
        self.TestClient = TestClient
        self.real_sync_parse = run_sync_parse
        # Fail closed if a model/network path is accidentally enabled. TestClient
        # uses its ASGI transport, not these real network transports.
        self.network_guards = [self.stack.enter_context(patch(target,
            side_effect=AssertionError('External HTTP is forbidden in W2 integration tests')))
            for target in ('httpx.HTTPTransport.handle_request',
                           'httpx.AsyncHTTPTransport.handle_async_request')]
        self.stack.callback(self.assert_no_network)
        self.stack.enter_context(patch('app.api.kb.submit_parse', side_effect=self.sync_parse))
        self.stack.enter_context(patch('app.workers.tasks.submit_parse', side_effect=self.sync_parse))
        self.client = self.stack.enter_context(TestClient(app))
        self.headers = {}
        for username in self.users:
            response = self.client.post('/api/auth/login',
                                       json={'username': username, 'password': PASSWORD})
            self.assertEqual(response.status_code, 200, f'Login for synthetic {username} failed')
            self.headers[username] = {'Authorization': 'Bearer ' + response.json()['token']}

    def assert_no_network(self):
        for guard in self.network_guards:
            guard.assert_not_called()

    def insert(self, table, **values):
        # Only fixed test-owned schema/table names reach this helper.
        payload = {}
        for info in self.db.execute(f'PRAGMA table_info("{table}")'):
            if info['pk'] or not info['notnull'] or info['dflt_value'] is not None:
                continue
            kind = info['type'].upper()
            payload[info['name']] = NOW if 'DATE' in kind else 0.0 if 'FLOAT' in kind else 0 if 'INT' in kind or 'BOOL' in kind else ''
        payload.update(values)
        names = list(payload)
        return self.db.execute(f'INSERT INTO "{table}" (' + ','.join('"' + n + '"' for n in names)
            + ') VALUES (' + ','.join('?' for _ in names) + ')',
            [payload[n] for n in names]).lastrowid

    def sync_parse(self, task_id):
        result = self.real_sync_parse(task_id)
        self.worker_results.append({'task_id': task_id, **result})
        return result

    def http(self, method, url, *, who='admin', expected=200, **kwargs):
        self.trace.append({'method': method, 'url': url, 'actor': who,
                           'json': kwargs.get('json'), 'form': kwargs.get('data')})
        try:
            response = self.client.request(method, url, headers=self.headers[who], **kwargs)
        except Exception as error:
            error.add_note(f'HTTP {method} {url}; synthetic actor={who}')
            raise
        self.trace[-1].update(status=response.status_code, response=response.text[:1800])
        self.assertEqual(response.status_code, expected,
                         f'{method} {url} actor={who}: {response.status_code} {response.text}; '
                         f'worker_results={self.worker_results}')
        return response

    def version(self, doc, who='admin'):
        return self.http('GET', f'/api/documents/{doc}/version', who=who).json()

    def upload(self, text=TEXT, name='办事指南.md', who='admin', kb=None):
        response = self.http('POST', f'/api/kbs/{kb or self.kb}/docs', who=who,
            files={'file': (name, text.encode('utf-8'), 'text/plain')}).json()
        self.assertEqual(response['status'], 'success',
                         f'Actual parsing did not succeed: {response}; workers={self.worker_results}')
        self.assertEqual(self.worker_results[-1]['status'], 'success')
        self.assertGreater(self.worker_results[-1]['chunk_count'], 0)
        return response['id']

    def metadata(self, doc, who='admin', **changes):
        current = self.version(doc)
        return self.http('PATCH', f'/api/documents/{doc}/metadata', who=who,
            json={'expected_revision': current['metadata_revision'],
                  'reason': 'W2测试：核对真实文件元数据', **changes}).json()

    def action(self, doc, action, who='admin', **options):
        current = self.version(doc)
        return self.http('POST', f'/api/documents/{doc}/{action}', who=who,
            json={'expected_revision': current['metadata_revision'],
                  'reason': 'W2测试：' + action, **options}).json()

    def publish(self, doc, who='admin', **metadata):
        self.metadata(doc, who=who, validity='known', **metadata)
        return self.action(doc, 'publish', who=who)

    def ask(self, who='reader', kb=None, question=QUESTION):
        return self.http('POST', '/api/qa/ask', who=who,
                         json={'kb_id': kb or self.kb, 'question': question}).json()

    def snapshot(self, tables):
        return self.migrations.protected_snapshot(self.db, tables=tables)

    def source(self, doc):
        stored = self.db.execute('SELECT storage_path FROM documents WHERE id=?', (doc,)).fetchone()[0]
        source = Path(stored).resolve()
        self.assertTrue(source.is_relative_to(self.uploads.resolve()))
        self.assertNotEqual(source, self.uploads.resolve())
        return source

    def assert_citation_binding(self, answer, doc, revision):
        self.assertEqual(answer['mode'], 'degraded', answer)
        self.assertTrue(answer['citations'], answer)
        for citation in answer['citations']:
            self.assertEqual(citation['doc_id'], doc)
            row = self.db.execute('''SELECT c.id, c.document_id, c.content, c.chunk_hash,
                rc.parse_revision_id, rc.content_sha256, r.version_id, r.state
                FROM chunks c JOIN parse_revision_chunks rc ON rc.chunk_id=c.id
                JOIN parse_revisions r ON r.id=rc.parse_revision_id WHERE c.id=?''',
                (citation['chunk_id'],)).fetchone()
            self.assertIsNotNone(row, citation)
            self.assertEqual((row['document_id'], row['version_id'], row['parse_revision_id']),
                             (doc, doc, revision))
            self.assertEqual(row['state'], 'success')
            self.assertEqual(row['content_sha256'], hashlib.sha256(row['content'].encode()).hexdigest())
            self.assertEqual(citation['content_excerpt'], row['content'][:5000])
            saved = self.db.execute('SELECT chunk_id, document_id, content_excerpt FROM citations '
                'WHERE message_id=? AND rank=?', (answer['message_id'], citation['rank'])).fetchone()
            self.assertEqual(tuple(saved), (citation['chunk_id'], doc, citation['content_excerpt']))

    def test_upload_draft_publish_http_chain(self):
        state = self.http('GET', '/api/versions/capabilities', who='reader').json()
        self.assertEqual(state, {'enabled': True, 'schema_version': self.migrations.VERSION, 'mode': 'versioned'})
        doc = self.upload()
        version = self.version(doc)
        self.assertEqual((version['publication_state'], version['validity']), ('draft', 'unknown'))
        revision = version['current_revision']
        self.assertEqual(revision['state'], 'success')
        self.assertTrue(revision['is_current'])
        self.assertEqual(revision['source_integrity'], 'verified')
        self.assertEqual(revision['file_sha256'], hashlib.sha256(TEXT.encode()).hexdigest())
        self.assertEqual(revision['file_sha256'], version['sha256'])
        self.assertEqual(self.source(doc).read_bytes(), TEXT.encode())
        before = self.ask()
        self.assertEqual((before['mode'], before['citations']), ('none', []), before)
        self.http('POST', f'/api/documents/{doc}/publish', who='admin', expected=422,
            json={'expected_revision': version['metadata_revision'], 'reason': '未知日期不能默认发布'})
        published = self.publish(doc)
        self.assertEqual((published['publication_state'], published['date_status']), ('published', 'current'))
        answer = self.ask()
        self.assertIn('身份证', ''.join(c['content_excerpt'] for c in answer['citations']))
        self.assertIn('未启用生成模型', answer['answer'])
        self.assert_citation_binding(answer, doc, revision['id'])
        events = self.db.execute('SELECT action, reason FROM audit_events WHERE object_id=?', (doc,)).fetchall()
        self.assertTrue({'version_uploaded', 'metadata_updated', 'published'} <= {r['action'] for r in events})
        self.assertTrue(all(r['reason'].strip() for r in events))

    def test_capabilities_reject_unknown_migration(self):
        self.insert('schema_migrations', version='9999_unknown_future', checksum='f' * 64, baseline_json='{}')
        with self.assertRaises(self.migrations.MigrationError):
            self.migrations.plan_migration(self.db_path, **self.migration_options)
        state = self.http('GET', '/api/versions/capabilities', who='reader').json()
        self.assertEqual(state['mode'], 'inconsistent', state)
        self.assertFalse(state['enabled'], state)
        self.http('POST', f'/api/kbs/{self.kb}/docs', who='admin', expected=503,
                  files={'file': ('refuse.md', TEXT.encode(), 'text/plain')})

    def test_ordinary_owner_version_lifecycle_and_shared_roles_read_only(self):
        self.db.execute('UPDATE knowledge_bases SET owner_id=? WHERE id=?', (self.users['reader'], self.kb))
        doc = self.upload(who='reader')
        version = self.version(doc, who='reader')
        self.assertTrue(version['can_manage'])
        self.assertEqual(version['publication_state'], 'draft')
        own_docs = self.http('GET', f'/api/kbs/{self.kb}/docs', who='reader').json()
        self.assertEqual(own_docs['total'], 1)
        self.http('GET', f'/api/documents/{doc}/version', who='admin', expected=403)
        self.http('GET', f'/api/documents/{doc}/version', who='operator', expected=404)
        self.http('PATCH', f'/api/documents/{doc}/metadata', who='operator', expected=403,
            json={'expected_revision':version['metadata_revision'],'reason':'shared users cannot write','validity':'known'})
        updated = self.http('PATCH', f'/api/documents/{doc}/metadata', who='reader',
            json={'expected_revision':version['metadata_revision'],'reason':'owner confirms','validity':'known'}).json()
        published = self.http('POST', f'/api/documents/{doc}/publish', who='reader',
            json={'expected_revision':updated['metadata_revision'],'reason':'owner publishes'}).json()
        self.assertEqual(published['publication_state'], 'published')
        shared = self.version(doc, who='operator')
        self.assertFalse(shared['can_manage']); self.assertFalse(shared['can_publish'])
        self.http('POST', f'/api/documents/{doc}/withdraw', who='operator', expected=403,
            json={'expected_revision':published['metadata_revision'],'reason':'not the owner'})
        self.http('POST', f'/api/kbs/{self.kb}/docs', who='admin', expected=403,
            files={'file':('forbidden.md',TEXT.encode(),'text/plain')})

    def test_model_failover_stops_after_version_withdrawal(self):
        from app.services import qa_service
        doc=self.upload();self.publish(doc)
        for name in ('first','fallback'):
            self.insert('llm_channels',name=name,channel_type='online',base_url='http://127.0.0.1:9/v1',model_name='synthetic',enabled=1,health_status='unknown',timeout_s=5)
        self.db.commit()
        def withdraw(*args,**kwargs):
            with closing(sqlite3.connect(self.db_path)) as concurrent, concurrent:
                concurrent.execute("UPDATE document_versions SET publication_state='withdrawn',metadata_revision=metadata_revision+1 WHERE version_id=?",(doc,))
            return qa_service.GenerationFailure('timeout')
        with patch.object(qa_service,'_call_llm_safe',side_effect=withdraw) as calls:
            self.http('POST','/api/qa/ask',who='reader',expected=409,json={'kb_id':self.kb,'question':QUESTION})
        self.assertEqual(calls.call_count,1,'Withdrawn version must never be sent to the next channel')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)

    def tearDown(self):
        (self.root / 'http-trace.json').write_text(json.dumps({
            'http': self.trace, 'real_worker_results': self.worker_results},
            ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    global CASE_ROOT
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True)
    parser.add_argument('--case-root', required=True, type=Path)
    args = parser.parse_args()
    CASE_ROOT = args.case_root
    if not args.case.startswith('test_') or not hasattr(VersionIntegrationScenario, args.case):
        parser.error('Unknown W2 integration case')
    suite = unittest.TestSuite([VersionIntegrationScenario(args.case)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {'case': args.case, 'tests': result.testsRun,
               'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': len(result.skipped), 'success': result.wasSuccessful()}
    print('W2_RESULT=' + json.dumps(summary), flush=True)
    return 0 if result.wasSuccessful() and result.testsRun == 1 and not result.skipped else 1


if __name__ == '__main__':
    raise SystemExit(main())
