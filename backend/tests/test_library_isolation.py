"""HTTP/library boundary regressions in a child process and synthetic temporary DB.

Run with the project interpreter and ``-B -m unittest discover -s backend/tests
-p test_library_isolation.py -v``. No production configuration, storage, login,
network service, model or migration is used. Authentication is dependency-injected;
uploads use the real synchronous local parser.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LibraryIsolationTests(unittest.TestCase):
    def test_isolated_library_contract(self):
        with tempfile.TemporaryDirectory(prefix='paper-library-isolation-') as directory:
            root = Path(directory).resolve(strict=True)
            self.assertEqual(root.parent, Path(tempfile.gettempdir()).resolve())
            env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'COMSPEC') if key in os.environ}
            env.update(TEMP=str(root), TMP=str(root), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
            command = [sys.executable, '-I', '-X', 'utf8', '-B', str(Path(__file__).resolve()), '--fixture', str(root)]
            result = subprocess.run(command, cwd=root, env=env, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            records = [line for line in result.stdout.splitlines() if line.startswith('LIBRARY_RESULT=')]
            self.assertEqual(len(records), 1, result.stdout + result.stderr)
            summary = json.loads(records[0].split('=', 1)[1])
            self.assertEqual(summary['failures'], 0, result.stdout + result.stderr)
            self.assertEqual(summary['errors'], 0, result.stdout + result.stderr)
            self.assertEqual(summary['skipped'], 0, result.stdout + result.stderr)
            self.assertGreaterEqual(summary['tests'], 18)
            print('Library fixture:', json.dumps(summary, ensure_ascii=False))


def run_fixture(root: Path) -> int:
    # Set APP_CONFIG before any application import, including db/session.
    root = root.resolve(strict=True)
    assert Path.cwd().resolve() == root and root.name.startswith('paper-library-isolation-')
    config = root / 'fixture.yaml'
    config.write_text(json.dumps({
        'app': {'name': 'Synthetic library isolation'},
        'storage': {'data_dir': str(root / 'data'), 'upload_dir': str(root / 'uploads'),
                    'vector_dir': str(root / 'vectors')},
        'security': {'secret_key': 'synthetic-test-only', 'pbkdf2_iterations': 1000},
        'seed': {'admin_username': 'fixture_admin', 'admin_password': 'synthetic-test-only'},
    }), encoding='utf-8')
    os.environ['APP_CONFIG'] = str(config)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select
    from unittest.mock import patch
    from app.api import kb as kb_api, sys as sys_api
    from app.api.deps import CurrentUser, get_current_user
    from app.core.config import settings
    from app.db import init_db as bootstrap
    from app.db.models import (Chunk, Document, IndustryTemplate, KnowledgeBase, ParseTask,
                               Role, SysParam, User, UserKbAccess, VERSIONING_TABLE_NAMES)
    from app.db.session import Base, get_engine, get_session_factory
    from app.engines.keyword_index import KeywordIndex
    from app.engines.vector_store import vector_store
    from app.schemas.kb import KbCreate
    from app.services import kb_service, version_service as versions
    from app.workers.tasks import run_sync_parse

    assert settings.config_path.resolve() == config
    assert settings.db_path.resolve().is_relative_to(root)
    assert settings.upload_dir.resolve().is_relative_to(root)
    settings.ensure_dirs()
    engine, factory = get_engine(), get_session_factory()
    app = FastAPI()
    app.include_router(kb_api.router)
    app.include_router(sys_api.router)

    def current_user(x_test_user: str = Header(default='alice')):
        with factory() as db:
            user = db.scalar(select(User).where(User.username == x_test_user))
            if user is None or not user.is_active:
                raise HTTPException(401, 'Synthetic account unavailable')
            return CurrentUser(user.id, user.username, user.display_name, user.role_code)

    app.dependency_overrides[get_current_user] = current_user

    @app.exception_handler(versions.VersionError)
    async def version_error(_request, error):
        return JSONResponse(status_code=error.status_code, content={'detail': error.detail})

    class Contract(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.client = TestClient(app)
            cls.parse_patch = patch.object(kb_api, 'submit_parse', side_effect=run_sync_parse)
            cls.parse_patch.start()

        @classmethod
        def tearDownClass(cls):
            cls.parse_patch.stop()
            cls.client.close()
            engine.dispose()

        def setUp(self):
            self.assertTrue(Path(engine.url.database).resolve().is_relative_to(root))
            Base.metadata.drop_all(engine)
            Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in VERSIONING_TABLE_NAMES])
            KeywordIndex.invalidate()
            vector_store.invalidate()
            with factory() as db:
                db.add_all([Role(code=role, name=role) for role in ('admin', 'operator', 'user')])
                db.flush()
                db.add_all([User(username=name, display_name=name, role_code=role,
                                 password_salt='unused', password_digest='unused', is_active=True)
                            for name, role in [('alice', 'user'), ('bob', 'user'),
                                               ('operator', 'operator'), ('admin', 'admin')]])
                db.commit()
                self.users = {u.username: u.id for u in db.scalars(select(User))}
                a = KnowledgeBase(name='Alice private papers', owner_id=self.users['alice'])
                b = KnowledgeBase(name='Bob private papers', owner_id=self.users['bob'])
                db.add_all([a, b]); db.commit()
                self.a, self.b = a.id, b.id

        def request(self, method, path, who='alice', **kwargs):
            return self.client.request(method, '/api' + path, headers={'X-Test-User': who}, **kwargs)

        def upload(self, kb=None, who='alice', content=b'Alice paper: attention improves retrieval accuracy.', filename='paper.txt'):
            response = self.request('POST', f'/kbs/{kb or self.a}/docs', who,
                                    files={'file': (filename, content, 'application/octet-stream')})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['id']

        def grant(self, roles):
            response = self.request('POST', f'/kbs/{self.a}/grant', json={'role_codes': roles})
            self.assertEqual(response.status_code, 200, response.text)

        def test_default_private_and_empty_roles(self):
            for payload in ({'name': 'Private default'}, {'name': 'Private empty', 'role_codes': []}):
                response = self.request('POST', '/kbs', json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                created = response.json()
                self.assertEqual(created['granted_roles'], [])
                self.assertTrue(created['can_manage'])
                for who in ('bob', 'operator', 'admin'):
                    self.assertEqual(self.request('GET', f"/kbs/{created['id']}", who).status_code, 403)
            for who, expected in [('bob', [self.b]), ('operator', []), ('admin', [])]:
                self.assertEqual([kb['id'] for kb in self.request('GET', '/kbs', who).json()], expected)
            self.assertEqual(KbCreate(name='schema').role_codes, [])

        def test_ordinary_owner_upload_read_and_reparse(self):
            doc_id = self.upload()
            listing = self.request('GET', f'/kbs/{self.a}/docs').json()
            self.assertEqual(listing['total'], 1)
            self.assertEqual(listing['items'][0]['status'], 'success')
            self.assertGreater(listing['items'][0]['chunk_count'], 0)
            response = self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/chunks')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn('Alice paper', response.json()[0]['content'])
            self.assertEqual(self.request('POST', f'/kbs/{self.a}/docs/{doc_id}/reparse').status_code, 200)
            self.assertEqual(self.request('DELETE', f'/kbs/{self.a}/docs/{doc_id}').status_code, 200)

        def test_two_users_library_boundaries(self):
            doc_id = self.upload()
            for method, path, kwargs in [
                ('GET', f'/kbs/{self.a}', {}), ('GET', f'/kbs/{self.a}/docs', {}),
                ('GET', f'/kbs/{self.a}/stats', {}), ('GET', f'/docs/{doc_id}/chunks', {}),
                ('GET', f'/kbs/{self.a}/docs/{doc_id}/file', {}),
                ('GET', f'/kbs/{self.a}/docs/{doc_id}/chunks', {}),
                ('POST', f'/kbs/{self.a}/docs', {'files': {'file': ('wrong.txt', b'not allowed')}}),
                ('POST', f'/kbs/{self.a}/docs/{doc_id}/reparse', {}),
                ('DELETE', f'/kbs/{self.a}/docs/{doc_id}', {}),
                ('POST', f'/kbs/{self.a}/grant', {'json': {'role_codes': ['user']}}),
                ('PATCH', f'/kbs/{self.a}', {'json': {'name': 'Hijacked'}}),
            ]:
                with self.subTest(method=method, path=path):
                    response = self.request(method, path, 'bob', **kwargs)
                    self.assertEqual(response.status_code, 403, response.text)
                    self.assertNotIn('attention improves', response.text)

        def test_wrong_library_doc_id_even_when_both_libraries_visible(self):
            doc_id = self.upload()
            with factory() as db:
                db.get(KnowledgeBase, self.b).owner_id = self.users['alice']; db.commit()
            for method, suffix in [('GET', 'file'), ('GET', 'chunks'), ('POST', 'reparse'), ('DELETE', '')]:
                path = f'/kbs/{self.b}/docs/{doc_id}' + (f'/{suffix}' if suffix else '')
                self.assertEqual(self.request(method, path).status_code, 404, path)
            response = self.request('GET', f'/docs/{doc_id}/chunks', params={'kb_id': self.b})
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(self.request('GET', f'/kbs/{self.a}/docs').json()['total'], 1)

        def test_identical_uploads_never_reuse_another_library_document(self):
            first = self.upload(content=b'Identical synthetic text.')
            second = self.upload(self.b, 'bob', content=b'Identical synthetic text.')
            self.assertNotEqual(first, second)
            for kb, who, doc in [(self.a, 'alice', first), (self.b, 'bob', second)]:
                response = self.request('GET', f'/kbs/{kb}/docs/{doc}/file', who)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.content, b'Identical synthetic text.')

        def test_explicit_role_sharing_is_read_only_including_admin(self):
            doc_id = self.upload()
            self.grant(['user', 'operator', 'admin', 'user'])
            self.assertEqual(self.request('GET', f'/kbs/{self.a}').json()['granted_roles'], ['user', 'operator', 'admin'])
            for who in ('bob', 'operator', 'admin'):
                detail = self.request('GET', f'/kbs/{self.a}', who)
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertFalse(detail.json()['can_manage'])
                self.assertEqual(self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file', who).status_code, 200)
                self.assertEqual(self.request('DELETE', f'/kbs/{self.a}/docs/{doc_id}', who).status_code, 403)
                self.assertEqual(self.request('POST', f'/kbs/{self.a}/grant', who, json={'role_codes': []}).status_code, 403)
                self.assertEqual(self.request('POST', f'/kbs/{self.a}/docs', who,
                                              files={'file': ('reader.txt', b'not writable')}).status_code, 403)

        def test_revocation_applies_to_existing_file_and_chunk_urls(self):
            doc_id = self.upload()
            self.grant(['user'])
            urls = [f'/kbs/{self.a}/docs/{doc_id}/file', f'/kbs/{self.a}/docs/{doc_id}/chunks', f'/docs/{doc_id}/chunks']
            for url in urls:
                self.assertEqual(self.request('GET', url, 'bob').status_code, 200)
            self.grant([])
            for url in urls:
                self.assertEqual(self.request('GET', url, 'bob').status_code, 403)
            self.assertEqual([row['id'] for row in self.request('GET', '/kbs', 'bob').json()], [self.b])

        def test_disabled_library_blocks_content_but_owner_can_reenable(self):
            doc_id = self.upload()
            response = self.request('PATCH', f'/kbs/{self.a}', json={'status': 'disabled'})
            self.assertEqual(response.status_code, 200, response.text)
            for path in [f'/kbs/{self.a}/docs', f'/kbs/{self.a}/stats',
                         f'/kbs/{self.a}/docs/{doc_id}/file', f'/docs/{doc_id}/chunks']:
                self.assertEqual(self.request('GET', path).status_code, 409, path)
            self.assertEqual(self.request('POST', f'/kbs/{self.a}/docs',
                                          files={'file': ('disabled.txt', b'not writable')}).status_code, 409)
            self.assertEqual(self.request('PATCH', f'/kbs/{self.a}', json={'status': 'enabled'}).status_code, 200)
            self.assertEqual(self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file').status_code, 200)

        def test_download_disposition_nosniff_and_no_store(self):
            content = b'<html><script>alert(1)</script><p>Research result</p></html>'
            doc_id = self.upload(content=content, filename='paper.html')
            response = self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, content)
            self.assertTrue(response.headers['content-disposition'].startswith('attachment;'))
            self.assertEqual(response.headers['content-type'], 'application/octet-stream')
            self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
            self.assertIn('no-store', response.headers['cache-control'])

        def test_pdf_is_inline(self):
            from pypdf import PdfWriter
            output = io.BytesIO()
            writer = PdfWriter(); writer.add_blank_page(width=72, height=72); writer.write(output)
            doc_id = self.upload(content=output.getvalue(), filename='paper.pdf')
            response = self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.content, output.getvalue())
            self.assertEqual(response.headers['content-type'], 'application/pdf')
            self.assertTrue(response.headers['content-disposition'].startswith('inline;'))

        def test_binary_and_base64_transfers_keep_identical_bytes_and_permissions(self):
            import base64
            from pypdf import PdfWriter
            output=io.BytesIO(); writer=PdfWriter();writer.add_blank_page(width=72,height=72);writer.write(output)
            doc_id=self.upload(content=output.getvalue(),filename='paper.pdf')
            url=f'/kbs/{self.a}/docs/{doc_id}/file'
            raw=self.request('GET',url+'?download=true')
            self.assertEqual(raw.content,output.getvalue());self.assertEqual(raw.headers['content-type'],'application/octet-stream')
            self.assertTrue(raw.headers['content-disposition'].startswith('attachment;'))
            encoded=self.request('GET',url+'?encoding=base64')
            self.assertEqual(encoded.status_code,200)
            data=encoded.json();self.assertEqual(base64.b64decode(data['content_base64']),output.getvalue())
            self.assertEqual(data['size'],len(output.getvalue()));self.assertEqual(data['mime_type'],'application/pdf')
            self.assertIn('no-store',encoded.headers['cache-control'])
            self.assertEqual(self.request('GET',url+'?encoding=base64','bob').status_code,403)
            self.assertEqual(self.request('GET',f'/kbs/{self.b}/docs/{doc_id}/file?encoding=base64','bob').status_code,404)

        def test_range_requests_still_authorize_each_library_read(self):
            doc_id = self.upload(content=b'Range-supported synthetic paper.')
            path = f'/api/kbs/{self.a}/docs/{doc_id}/file'
            response = self.client.get(path, headers={'X-Test-User': 'alice', 'Range': 'bytes=0-4'})
            self.assertEqual(response.status_code, 206, response.text)
            self.assertEqual(response.content, b'Range')
            self.assertIn('no-store', response.headers['cache-control'])
            denied = self.client.get(path, headers={'X-Test-User': 'bob', 'Range': 'bytes=0-4'})
            self.assertEqual(denied.status_code, 403, denied.text)

        def test_external_and_cross_library_storage_paths_fail_closed(self):
            doc_id = self.upload()
            other_id = self.upload(self.b, 'bob', content=b'Bob synthetic secret.')
            outside = root / 'not-an-upload.txt'; outside.write_text('Outside synthetic secret.', encoding='utf-8')
            with factory() as db:
                other_path = db.get(Document, other_id).storage_path
            for bad_path in [str(outside), other_path, str(settings.upload_dir / str(self.a) / '..' / str(self.b) / 'other.txt')]:
                with factory() as db:
                    db.get(Document, doc_id).storage_path = bad_path; db.commit()
                response = self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file')
                self.assertEqual(response.status_code, 409, response.text)
                self.assertNotIn('synthetic secret', response.text)

        def test_missing_file_is_404(self):
            doc_id = self.upload()
            with factory() as db:
                db.get(Document, doc_id).storage_path = str(settings.upload_dir / str(self.a) / 'missing.txt'); db.commit()
            self.assertEqual(self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file').status_code, 404)

        def test_symlink_or_junction_component_is_rejected(self):
            doc_id = self.upload()
            # Portable deterministic coverage of the link guard; no OS privileges
            # or links into a real directory are required by this regression.
            original = Path.is_symlink
            with patch.object(Path, 'is_symlink', lambda path: path.name == str(self.a) or original(path)):
                response = self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file')
                self.assertEqual(response.status_code, 409, response.text)

        def test_real_link_cannot_escape_upload_root(self):
            doc_id = self.upload()
            outside = root / 'synthetic-link-target'
            outside.mkdir(exist_ok=True)
            secret = outside / 'outside.txt'
            secret.write_text('Synthetic outside-link content.', encoding='utf-8')
            link = settings.upload_dir / str(self.a) / 'linked-directory'
            self.assertTrue(link.absolute().is_relative_to(root))
            self.assertTrue(outside.resolve(strict=True).is_relative_to(root))
            if os.name == 'nt':
                powershell = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
                quote = lambda value: "'" + str(value).replace("'", "''") + "'"
                result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
                    "New-Item -ItemType Junction -Path " + quote(link) + " -Value " + quote(outside) + " | Out-Null"],
                    capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(link.is_junction())
            else:
                link.symlink_to(outside, target_is_directory=True)
            with factory() as db:
                db.get(Document, doc_id).storage_path = str(link / 'outside.txt'); db.commit()
            response = self.request('GET', f'/kbs/{self.a}/docs/{doc_id}/file')
            self.assertEqual(response.status_code, 409, response.text)
            self.assertNotIn('outside-link content', response.text)
            self.assertEqual(secret.read_text(encoding='utf-8'), 'Synthetic outside-link content.')

        def test_task_lists_follow_library_scope_for_ordinary_owners(self):
            self.upload()
            self.upload(self.b, 'bob', content=b'Bob paper: bounded synthetic corpus.')
            for who, library in [('alice', self.a), ('bob', self.b)]:
                page = self.request('GET', '/tasks/page', who)
                self.assertEqual(page.status_code, 200, page.text)
                self.assertEqual(page.json()['total'], 1)
                self.assertEqual({row['kb_id'] for row in page.json()['items']}, {library})
            self.assertEqual(self.request('GET', '/tasks/page', 'admin').json()['total'], 0)
            self.assertEqual(self.request('GET', '/tasks/page', 'bob', params={'kb_id': self.a}).status_code, 403)
            self.assertEqual(self.request('GET', '/tasks', 'bob', params={'kb_id': self.a}).status_code, 403)

        def test_missing_reparse_source_is_conflict_not_server_error(self):
            doc_id = self.upload()
            with factory() as db:
                db.get(Document, doc_id).storage_path = str(settings.upload_dir / str(self.a) / 'missing-again.txt'); db.commit()
            response = self.request('POST', f'/kbs/{self.a}/docs/{doc_id}/reparse')
            self.assertEqual(response.status_code, 409, response.text)

        def test_inactive_account_cannot_use_service_visibility_or_upload(self):
            with factory() as db:
                owner = db.get(User, self.users['alice'])
                owner.is_active = False; db.commit()
                self.assertEqual(kb_service.get_visible_kb_ids(db, owner), [])
                with self.assertRaises(versions.VersionError) as error:
                    kb_service.save_upload(db, db.get(KnowledgeBase, self.a), owner, 'inactive.txt', b'no write')
                self.assertEqual(error.exception.status_code, 401)

        def test_mismatched_chunk_kb_is_not_mixed_into_doc_or_counts(self):
            doc_id = self.upload()
            with factory() as db:
                good_count = db.scalar(select(func.count(Chunk.id)).where(Chunk.document_id == doc_id))
                db.add(Chunk(kb_id=self.b, document_id=doc_id, seq_no=999, content='FOREIGN CORRUPT CONTENT'))
                db.commit()
            for path in [f'/docs/{doc_id}/chunks', f'/kbs/{self.a}/docs/{doc_id}/chunks']:
                response = self.request('GET', path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()), good_count)
                self.assertNotIn('FOREIGN', response.text)
            self.assertEqual(self.request('GET', f'/kbs/{self.a}/stats').json()['chunk_count'], good_count)
            self.assertEqual(self.request('GET', f'/kbs/{self.a}').json()['chunk_count'], good_count)
            self.assertEqual(self.request('GET', f'/kbs/{self.a}/docs').json()['items'][0]['chunk_count'], good_count)

        def test_service_write_guards_are_not_only_router_dependencies(self):
            doc_id = self.upload()
            with factory() as db:
                kb, doc, reader = db.get(KnowledgeBase, self.a), db.get(Document, doc_id), db.get(User, self.users['bob'])
                for action in [lambda: kb_service.save_upload(db, kb, reader, 'wrong.txt', b'wrong'),
                               lambda: kb_service.create_parse_task(db, doc, reader),
                               lambda: kb_service.delete_doc(db, doc, reader)]:
                    with self.assertRaises(versions.VersionError) as error:
                        action()
                    self.assertEqual(error.exception.status_code, 403)
                self.assertEqual(db.scalar(select(func.count(Document.id))), 1)

        def test_admin_system_maintenance_does_not_imply_private_data_access(self):
            self.assertEqual(self.request('GET', '/sys/params', 'admin').status_code, 200)
            self.assertEqual(self.request('GET', '/sys/params', 'alice').status_code, 403)
            self.assertEqual(self.request('GET', f'/kbs/{self.a}', 'admin').status_code, 403)
            self.assertEqual(self.request('POST', f'/kbs/{self.a}/grant', 'admin',
                                          json={'role_codes': ['admin']}).status_code, 403)

        def test_template_fields_retired_without_effect_or_seed_docs(self):
            response = self.request('POST', '/kbs', json={'name': 'No industry options',
                                    'template_id': 999999, 'import_samples': True})
            self.assertEqual(response.status_code, 200, response.text)
            created = response.json()
            self.assertIsNone(created['template_id'])
            self.assertEqual(created['granted_roles'], [])
            self.assertEqual(created['doc_count'], 0)
            self.assertEqual(self.request('GET', '/templates').status_code, 410)
            self.assertEqual(self.request('GET', '/sys/templates', 'admin').status_code, 410)

        def test_empty_bootstrap_has_no_template_library_or_demo_accounts(self):
            Base.metadata.drop_all(engine)
            bootstrap.init_db(); bootstrap.init_db()
            with factory() as db:
                self.assertEqual(db.scalar(select(func.count(IndustryTemplate.id))), 0)
                self.assertEqual(db.scalar(select(func.count(KnowledgeBase.id))), 0)
                self.assertEqual(db.scalar(select(func.count(Document.id))), 0)
                self.assertEqual(list(db.scalars(select(User.username))), ['fixture_admin'])
                self.assertEqual(db.scalar(select(func.count(Role.id))), 3)
                self.assertEqual(db.scalar(select(func.count(SysParam.id))), len(bootstrap.DEFAULT_PARAMS))

        def test_bootstrap_and_legacy_template_update_preserve_existing_rows(self):
            doc_id = self.upload()
            with factory() as db:
                old = IndustryTemplate(code='legacy', name='Existing user-owned template')
                db.add(old); db.flush()
                old_id = old.id
                db.get(KnowledgeBase, self.a).template_id = old_id; db.commit()
            bootstrap.init_db()
            response = self.request('PATCH', f'/kbs/{self.a}', json={'template_id': None})
            self.assertEqual(response.status_code, 200, response.text)
            with factory() as db:
                self.assertIsNotNone(db.get(IndustryTemplate, old_id))
                self.assertEqual(db.get(KnowledgeBase, self.a).template_id, old_id)
                self.assertIsNotNone(db.get(Document, doc_id))
                self.assertEqual(db.scalar(select(func.count(KnowledgeBase.id))), 2)
                self.assertEqual(db.scalar(select(func.count(IndustryTemplate.id))), 1)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Contract)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
               'skipped': len(result.skipped), 'temporary_database': True}
    print('LIBRARY_RESULT=' + json.dumps(summary))
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--fixture':
        raise SystemExit(run_fixture(Path(sys.argv[2])))
    unittest.main()