"""Synthetic config and in-memory SQLite only; discovery runs a fresh child.
Run: python -B backend/tests/test_vector_cache.py -v
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import patch

import numpy as np
from sqlalchemy import create_engine, delete, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

class VectorCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        with patch.dict(os.environ, {'APP_CONFIG': 'synthetic-vector-cache.yaml'}), patch('builtins.open', return_value=io.StringIO('{}')):
            from app.core import config  # No real config file is opened.
        from app.engines import vector_store
        from app.db import models
        from app.db.session import Base
        from app.services import version_service
        cls.vs, cls.m, cls.Base, cls.versions = vector_store, models, Base, version_service
        cls.Store = vector_store.VectorStore

    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        self.addCleanup(self.engine.dispose)
        self.Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        self.Store.invalidate()
        self.addCleanup(self.Store.invalidate)
        for name, value in (('get_session_factory', self.factory), ('_embedding_identity', None)):
            mock = patch.object(self.vs, name, return_value=value)
            started = mock.start()
            self.addCleanup(mock.stop)
            if name == '_embedding_identity':
                self.identity = started
        self.store = self.Store()

    def add(self, vectors, kb=1, identity=None):
        with self.factory() as db:
            doc = self.m.Document(kb_id=kb, filename=f'kb-{kb}.txt', file_type='txt', uploaded_by=1, status='success')
            db.add(doc); db.flush()
            chunks = [self.m.Chunk(kb_id=kb, document_id=doc.id, content=f'kb-{kb}:{i}', page_no=i,
                vector_json=v if isinstance(v, str) or v is None else self.vs.encode_vector_json(v),
                meta_json=json.dumps({'embedding_model_id': identity})) for i, v in enumerate(vectors)]
            db.add_all(chunks); db.commit()
            return doc.id, [c.id for c in chunks]

    def ids(self, **kwargs):
        return [r['chunk_id'] for r in self.store.search([1, 0], 1, **kwargs)]

    def test_rank_threshold_top_k_and_public_helpers(self):
        _, ids = self.add([[1, 0], [0, 1], [1, 0], [-1, 0], [2, 0]])
        self.assertEqual(self.ids(top_k=3), [ids[4], ids[0], ids[2]])
        self.assertEqual(self.ids(threshold=1), [ids[4], ids[0], ids[2]])
        self.assertEqual(self.ids(threshold=3), [])
        self.assertEqual(self.ids(top_k=0), [])
        result = self.store.search([2, 0], 1, top_k=1)[0]
        self.assertEqual(result, dict(chunk_id=ids[4], doc_id=1, doc_name='kb-1.txt', page_no=4, content='kb-1:4', score=2.0))
        self.assertEqual(self.vs.cosine([1, 0], [1]), 0)
        self.assertEqual(self.vs.decode_vector_json(self.vs.encode_vector_json([.12345678])), [.123457])
        result['content'] = 'caller mutation'
        self.assertEqual(self.store.search([1, 0], 1, top_k=1)[0]['content'], 'kb-1:4')

    def test_matrix_matches_old_cosine(self):
        rng = np.random.default_rng(29)
        vectors = [self.vs.l2_normalize(v.tolist()) for v in rng.normal(size=(257, 17))]
        _, ids = self.add(vectors)
        query = rng.normal(size=17).tolist()
        expected = sorted(zip(ids, [self.vs.cosine(self.vs.l2_normalize(query), self.vs.decode_vector_json(self.vs.encode_vector_json(v))) for v in vectors]), key=lambda row: -row[1])
        actual = self.store.search(query, 1, top_k=300)
        self.assertEqual([r['chunk_id'] for r in actual], [r[0] for r in expected])
        np.testing.assert_allclose([r['score'] for r in actual], [r[1] for r in expected], rtol=0, atol=1e-12)
        self.assertEqual([r['chunk_id'] for r in self.store.search(query, 1, top_k=7, threshold=.1)], [r[0] for r in expected if r[1] >= .1][:7])

    def test_10000_rows_warm_query_does_not_decode_or_read_content(self):
        _, ids = self.add(['[0,1]'] * 9999 + ['[1,0]'])
        statements = []
        with patch.object(self.vs, 'decode_vector_json', wraps=self.vs.decode_vector_json) as decode:
            self.assertEqual(self.ids(top_k=1), [ids[-1]])
            self.assertEqual(decode.call_count, 10000)
            event.listen(self.engine, 'before_cursor_execute', lambda conn, cur, stmt, params, ctx, many: statements.append(stmt))
            self.assertEqual(self.ids(top_k=1), [ids[-1]])
            self.assertEqual(decode.call_count, 10000)
        # SQL may inspect small metadata for eligibility in WHERE; warm queries
        # must not project content, vector JSON or metadata back into Python.
        projections = [stmt.upper().split('FROM', 1)[0].lower() for stmt in statements]
        self.assertFalse(any(column in projection for projection in projections
                             for column in ('chunks.content', 'chunks.vector_json', 'chunks.meta_json')))

    def test_finite_dimension_and_corrupt_vectors(self):
        _, ids = self.add(['[NaN,0]', '[Infinity,0]', '[1]', 'null', '{}', '"text"', '["1",0]', 'bad', '[' + '9' * 400 + ',0]', None, '[1,0]', '[0,0]'])
        self.assertEqual(self.ids(), ids[-2:])
        self.assertEqual([r['chunk_id'] for r in self.store.search([1], 1)], [ids[2]])
        for query in (None, [], [float('nan'), 0], [float('inf'), 0], [[1, 0]]):
            self.assertEqual(self.store.search(query, 1), [])

    def test_kb_model_identity_and_engine_isolation(self):
        _, old = self.add([[1, 0]])
        _, a = self.add([[1, 0]], identity='model-A')
        _, b = self.add([[1, 0]], identity='model-B')
        _, private = self.add([[1, 0]], kb=2, identity='model-A')
        self.assertEqual(self.ids(), old + a + b)
        self.identity.return_value = 'model-A'
        self.assertEqual(self.ids(), a)
        self.assertEqual([r['chunk_id'] for r in self.store.search([1, 0], 2)], private)
        self.identity.return_value = 'model-B'
        self.assertEqual(self.ids(), b)
        self.identity.return_value = None
        other = create_engine('sqlite://')
        self.addCleanup(other.dispose)
        self.Base.metadata.create_all(other)
        with patch.object(self.vs, 'get_session_factory', return_value=sessionmaker(other)):
            self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(), old + a + b)

    def test_reparse_delete_status_and_nonmaximum_document_timestamp(self):
        doc, ids = self.add([[1, 0]])
        newer, other = self.add([[0, 1]])
        with self.factory() as db:
            db.get(self.m.Document, doc).updated_at = datetime(2020, 1, 1)
            db.get(self.m.Document, newer).updated_at = datetime(2099, 1, 1)
            db.commit()
        self.ids()
        with self.factory() as db:
            db.execute(update(self.m.Chunk).where(self.m.Chunk.id == ids[0]).values(content='reparsed', vector_json='[0,1]'))
            d = db.get(self.m.Document, doc)
            d.filename, d.updated_at = 'new.txt', datetime(2020, 1, 2)
            db.commit()
        result = self.store.search([0, 1], 1)[0]
        self.assertEqual((result['content'], result['doc_name'], result['score']), ('reparsed', 'new.txt', 1))
        for state in ('parsing', 'failed', 'success'):
            with self.factory() as db:
                db.get(self.m.Document, doc).status = state
                db.commit()
            self.assertEqual(set(self.ids()), set(other + ids if state == 'success' else other))
        with self.factory() as db:
            db.execute(delete(self.m.Document).where(self.m.Document.id == doc)); db.commit()
        self.assertEqual(self.ids(), other)
        with self.factory() as db:
            db.execute(delete(self.m.Chunk)); db.commit()
        self.assertEqual(self.ids(), [])

    def test_explicit_invalidation_and_manual_edit_boundary(self):
        _, ids = self.add([[1, 0]])
        self.add([[1, 0]], kb=2)
        self.ids(); self.store.search([1, 0], 2)
        with self.factory() as db:
            db.execute(update(self.m.Chunk).where(self.m.Chunk.id == ids[0]).values(vector_json='[0,1]')); db.commit()
        self.assertEqual(self.ids(threshold=.9), ids)  # Deliberately unchanged metadata is outside the automatic contract.
        self.store.invalidate(1)
        with patch.object(self.vs, 'decode_vector_json', wraps=self.vs.decode_vector_json) as decode:
            self.store.search([1, 0], 2)
            self.assertEqual(decode.call_count, 0)
            self.assertEqual(self.ids(threshold=.9), [])
            self.assertEqual(decode.call_count, 1)
        self.Store.invalidate()
        self.assertFalse(self.Store._cache)

    def test_lru_byte_bound_and_oversized_complete_fallback(self):
        for kb in range(1, 4):
            self.add([[1, 0]], kb=kb)
        with patch.object(self.Store, 'max_cached_kbs', 2), patch.object(self.Store, 'max_cache_bytes', 32):
            for kb in (1, 2, 1, 3):
                self.store.search([1, 0], kb)
            self.assertEqual([key[1] for key in self.Store._cache], [1, 3])
            self.assertEqual(sum(s.matrix.nbytes for s in self.Store._cache.values()), 32)
            _, ids = self.add(['[0,1]'] * 2001 + ['[1,0]'], kb=4)
            self.assertEqual(self.store.search([1, 0], 4, top_k=1, threshold=.9)[0]['chunk_id'], ids[-1])
            self.assertNotIn(4, [key[1] for key in self.Store._cache])
            with patch.object(self.Store, 'max_cache_bytes', 16):
                self.store.search([1, 0], 2)
                self.assertEqual(sum(s.matrix.nbytes for s in self.Store._cache.values()), 16)

    def test_version_scope_pinning_reparse_and_withdrawal(self):
        from app.db.migrations import VERSION, migration_checksum
        records = [self.add([[1, 0]]) for _ in range(2)]
        with self.factory() as db:
            db.add(self.m.SchemaMigration(version=VERSION, checksum=migration_checksum()))
            db.add(self.m.DocumentSeries(id=1, kb_id=1, title='synthetic', owner_id=1))
            for n, (did, ids) in enumerate(records, 1):
                db.add(self.m.DocumentVersion(version_id=did, series_id=1, kb_id=1, version_label=str(n), sha256=f'{n:064x}', publication_state='published'))
                db.add(self.m.ParseRevision(id=n, version_id=did, parser_version='test', state='success', is_current=True, chunk_count=1, content_hash='a'*64, source_integrity='legacy_unverified'))
                db.add(self.m.RevisionChunk(chunk_id=ids[0], parse_revision_id=n, content_sha256='a'*64))
            db.commit()
            scope = self.versions.retrieval_scope(db, [1])
        scopes = [dict(scope, revision_ids=[n], keys=[scope['keys'][n-1]], version_ids=[records[n-1][0]]) for n in (1, 2)]
        # Same supplied signature, different actual revision/key sets must not share results.
        for n in (0, 1, 0):
            self.assertEqual(self.ids(scope=scopes[n]), records[n][1])
        self.assertEqual(self.ids(), [row[1][0] for row in records])
        with self.factory() as db:
            db.get(self.m.ParseRevision, 1).is_current = False
            db.get(self.m.DocumentVersion, records[1][0]).publication_state = 'withdrawn'
            db.flush()
            chunk = self.m.Chunk(kb_id=1, document_id=records[0][0], content='new revision', vector_json='[1,0]')
            db.add(chunk); db.flush()
            db.add(self.m.ParseRevision(id=3, version_id=records[0][0], parser_version='test', state='success', is_current=True, chunk_count=1, content_hash='b'*64, source_integrity='legacy_unverified'))
            db.add(self.m.RevisionChunk(chunk_id=chunk.id, parse_revision_id=3, content_sha256='b'*64))
            db.commit(); current_id = chunk.id
        for pinned in scopes:
            self.assertEqual(self.ids(scope=pinned), [])
        self.assertEqual(self.ids(), [current_id])

    def test_concurrent_cold_queries_use_one_real_read_snapshot(self):
        self.add(['[1,0]'] * 100)
        transactions = []
        def observe(conn, cursor, statement, parameters, context, many):
            if 'chunks.vector_json' in statement:
                transactions.append(conn.connection.driver_connection.in_transaction)
        event.listen(self.engine, 'before_cursor_execute', observe)
        with patch.object(self.vs, 'decode_vector_json', wraps=self.vs.decode_vector_json) as decode, ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.ids(), range(8)))
            self.assertEqual(decode.call_count, 100)
        self.assertTrue(transactions and all(transactions))
        self.assertTrue(all(result == results[0] for result in results))

    def test_real_errors_are_not_hidden_by_cache_or_bad_row_handling(self):
        self.add([[1, 0]])
        with patch.object(self.vs, 'decode_vector_json', side_effect=RuntimeError('unexpected')):
            with self.assertRaisesRegex(RuntimeError, 'unexpected'):
                self.ids()
        self.ids()
        with self.factory() as db:
            db.add(self.m.SchemaMigration(version='unknown-schema', checksum='bad')); db.commit()
        with self.assertRaises(self.versions.VersionError):
            self.ids()

class IsolatedVectorCacheTests(unittest.TestCase):
    def test_isolated_suite(self):
        result = subprocess.run([sys.executable, '-I', '-B', __file__, '-v'], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stderr.strip())

def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(IsolatedVectorCacheTests)

if __name__ == '__main__':
    unittest.main(defaultTest='VectorCacheTests')
