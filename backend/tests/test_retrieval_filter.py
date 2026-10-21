"""Explicit metadata exclusion only; synthetic config and in-memory SQLite.
Run: python -B backend/tests/test_retrieval_filter.py -v
"""
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from sqlalchemy import event, select

class RetrievalFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_vector_cache import VectorCacheTests
        VectorCacheTests.setUpClass()  # Reuse its isolated config, never real settings.
        from app.engines import keyword_index
        cls.fixture_type, cls.keyword = VectorCacheTests, keyword_index

    def setUp(self):
        self.f = self.fixture_type('test_rank_threshold_top_k_and_public_helpers')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        for name, options in (('get_session_factory', {'return_value': self.f.factory}),
                              ('_segment', {'side_effect': str.split})):
            mock = patch.object(self.keyword, name, **options)
            mock.start()
            self.addCleanup(mock.stop)
        self.keyword.KeywordIndex.invalidate()
        self.addCleanup(self.keyword.KeywordIndex.invalidate)
        self.kw = self.keyword.KeywordIndex()

    def seed(self, metadata, kb=1):
        doc, ids = self.f.add([[1, 0]] * len(metadata), kb=kb)
        with self.f.factory() as db:
            for cid, raw in zip(ids, metadata):
                chunk = db.get(self.f.m.Chunk, cid)
                chunk.content, chunk.meta_json = 'topic', raw
            db.commit()
        return doc, ids

    def mark(self, doc, changes, *, touch=True, invalidate=False):
        with self.f.factory() as db:
            for cid, raw in changes.items():
                db.get(self.f.m.Chunk, cid).meta_json = raw
            document = db.get(self.f.m.Document, doc)
            if touch:
                document.updated_at += timedelta(seconds=1)
            kb = document.kb_id
            db.commit()
        if invalidate:
            self.f.Store.invalidate(kb)
            self.keyword.KeywordIndex.invalidate(kb)

    def assert_both(self, expected, kb=1, **options):
        for results in (self.kw.search('topic', kb, top_k=100, **options),
                        self.f.store.search([1, 0], kb, top_k=100, **options)):
            self.assertEqual({row['chunk_id'] for row in results}, set(expected))
            self.assertTrue(all({'chunk_id', 'doc_id', 'doc_name', 'page_no', 'content', 'score'} <= row.keys() for row in results))

    def test_only_json_boolean_true_excludes_and_rows_are_retained(self):
        metadata = ['{}', '{"retrieval_excluded":false}', '{"retrieval_excluded":null}',
            '{"retrieval_excluded":1}', '{"retrieval_excluded":0}', '{"retrieval_excluded":"true"}',
            '{"retrieval_excluded":[]}', '{"retrieval_excluded":{}}', '{"nested":{"retrieval_excluded":true}}',
            '', '{broken', 'null', '[]', 'true', '"true"', '{"retrieval_excluded":true}']
        _, ids = self.seed(metadata)
        self.assert_both(ids[:-1])
        self.assertEqual(self.kw.search('topic', 1, top_k=1)[0]['chunk_id'], ids[0])
        self.assertEqual(self.f.store.search([1, 0], 1, top_k=1, threshold=1)[0]['chunk_id'], ids[0])
        with self.f.factory() as db:
            rows = db.scalars(select(self.f.m.Chunk)).all()
            self.assertEqual(len(rows), len(metadata))
            self.assertTrue(all(row.content == 'topic' and row.vector_json for row in rows))

    def test_timestamp_swap_invalidation_and_document_state(self):
        doc, ids = self.seed(['{}', '{"retrieval_excluded":true}', '{}'])
        self.assert_both([ids[0], ids[2]])
        # Count/max ID/creation time stay constant; the document stamp must refresh both caches.
        self.mark(doc, {ids[0]: '{"retrieval_excluded":true}', ids[1]: '{}'})
        self.assert_both(ids[1:])
        self.mark(doc, {ids[0]: '{}', ids[1]: '{"retrieval_excluded":true}'}, touch=False, invalidate=True)
        self.assert_both([ids[0], ids[2]])
        for status in ('parsing', 'failed', 'success'):
            with self.f.factory() as db:
                db.get(self.f.m.Document, doc).status = status
                db.commit()
            self.assert_both([ids[0], ids[2]] if status == 'success' else [])

    def test_kb_isolation_and_targeted_invalidation(self):
        _, first = self.seed(['{}', '{"retrieval_excluded":true}'])
        doc, second = self.seed(['{}'], kb=2)
        self.assert_both(first[:1])
        self.assert_both(second, kb=2)
        self.mark(doc, {second[0]: '{"retrieval_excluded":true}'}, invalidate=True)
        with patch.object(self.f.vs, 'decode_vector_json', wraps=self.f.vs.decode_vector_json) as decode:
            self.assert_both(first[:1])
            self.assertEqual(decode.call_count, 0)
        self.assert_both([], kb=2)

    def test_warm_signature_and_candidates_share_filter_without_redecoding(self):
        _, ids = self.seed(['{}', '{"retrieval_excluded":true}'])
        self.assert_both(ids[:1])
        self.assertEqual(sum(len(s.meta) for s in self.kw._cache.values()), 1)
        self.assertEqual(sum(s.matrix.shape[0] for s in self.f.Store._cache.values()), 1)
        statements = []
        event.listen(self.f.engine, 'before_cursor_execute', lambda conn, cur, stmt, params, ctx, many: statements.append(stmt))
        with patch.object(self.f.vs, 'decode_vector_json', wraps=self.f.vs.decode_vector_json) as decode:
            self.assert_both(ids[:1])
            self.assertEqual(decode.call_count, 0)
        # Metadata in WHERE is necessary; returning its JSON or body on a warm hit is not.
        projections = [stmt.partition('FROM')[0] for stmt in statements if stmt.lstrip().startswith('SELECT')]
        self.assertFalse(any(column in projection for projection in projections
            for column in ('chunks.content', 'chunks.meta_json', 'chunks.vector_json')))

    def test_oversized_uncached_paths_still_apply_filter(self):
        _, ids = self.seed(['{"retrieval_excluded":true}', '{}'])
        with patch.object(self.f.Store, 'max_cache_bytes', 0), patch.object(self.kw, 'max_cached_chunks', 0):
            self.assert_both(ids[1:])
            self.assertFalse(self.f.Store._cache)
            self.assertFalse(self.kw._cache)

    def test_filter_composes_with_real_version_scope(self):
        # Existing synthetic scenario leaves exactly one published/current revision.
        self.f.test_version_scope_pinning_reparse_and_withdrawal()
        current = self.f.store.search([1, 0], 1)[0]
        with self.f.factory() as db:
            scope = self.f.versions.retrieval_scope(db, [1])
            db.get(self.f.m.Chunk, current['chunk_id']).content = 'topic'
            db.commit()
        self.assert_both([current['chunk_id']], scope=scope)
        self.mark(current['doc_id'], {current['chunk_id']: '{"retrieval_excluded":true}'}, invalidate=True)
        self.assert_both([], scope=scope)
        self.mark(current['doc_id'], {current['chunk_id']: '{"retrieval_excluded":false}'}, invalidate=True)
        self.assert_both([current['chunk_id']], scope=scope)

class IsolatedRetrievalFilterTests(unittest.TestCase):
    def test_isolated_suite(self):
        result = subprocess.run([sys.executable, '-I', '-B', __file__, '-v'], capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stderr.strip())

def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(IsolatedRetrievalFilterTests)

if __name__ == '__main__':
    unittest.main(defaultTest='RetrievalFilterTests')
