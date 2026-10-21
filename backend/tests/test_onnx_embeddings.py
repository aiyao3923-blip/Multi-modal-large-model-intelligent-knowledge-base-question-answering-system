"""Synthetic-only tests: no real app/config import, model files, network or torch.

Run: python -B -m unittest discover -s backend/tests -p test_onnx_embeddings.py -v
"""
from __future__ import annotations
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, MagicMock, patch

import numpy as np

APP = Path(__file__).resolve().parents[1] / 'app'
PACKAGE = '_isolated_e5_test'


class FakeTokenizer:
    def __init__(self):
        self.calls, self.expand = [], False

    def no_truncation(self): pass
    def no_padding(self): pass
    def token_to_id(self, token): return 0

    def encode(self, text, add_special_tokens=True):
        self.calls.append((text, add_special_tokens))
        ids, offsets, start = [], [], 0
        for prefix, token in (('passage: ', 2), ('query: ', 3)):
            if add_special_tokens and text.startswith(prefix):
                ids.append(token); offsets.append((0, len(prefix))); start = len(prefix)
                break
        for index in range(start, len(text)):
            if not text[index].isspace():
                copies = 2 if self.expand and add_special_tokens else 1
                ids.extend([ord(text[index]) + 20] * copies)
                offsets.extend([(index, index + 1)] * copies)
        if add_special_tokens:
            ids, offsets = [1, *ids, 4], [(0, 0), *offsets, (0, 0)]
        return types.SimpleNamespace(ids=ids, offsets=offsets, attention_mask=[1] * len(ids),
                                     type_ids=[0] * len(ids), source=text)


class FakeSession:
    def __init__(self, case): self.case, self.feeds = case, []
    def get_inputs(self): return [types.SimpleNamespace(name=name) for name in self.case.inputs]
    def get_outputs(self): return [types.SimpleNamespace(name='last_hidden_state')]

    @staticmethod
    def hidden(feed):
        ids = feed['input_ids']
        hidden = np.zeros((*ids.shape, 384), dtype=np.float32)
        hidden[..., 0], hidden[..., 1] = 1, ids % 23 / 23
        hidden[..., 2], hidden[..., 3] = (ids == ord('尾') + 20) * 100, (ids == 3) * 5
        hidden[feed['attention_mask'] == 0] = 10000  # Must never enter pooled vectors.
        return hidden

    def run(self, outputs, feed):
        assert outputs == ['last_hidden_state']
        self.feeds.append({key: value.copy() for key, value in feed.items()})
        hidden = self.hidden(feed)
        if self.case.bad == 'shape': hidden = hidden[..., :383]
        elif self.case.bad == 'zero': hidden[:] = 0
        elif self.case.bad: hidden[0, 0, 0] = float(self.case.bad)
        return [hidden]


class OnnxEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rag-e5-test-')
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.cleanup_directory)
        self.model = self.root / 'model'
        (self.model / 'onnx').mkdir(parents=True)
        (self.model / 'onnx/model_quantized.onnx').write_bytes(b'synthetic-onnx')
        (self.model / 'tokenizer.json').write_bytes(b'synthetic-tokenizer')
        self.write_manifest()
        self.config = {'backend': 'onnx_e5', 'model_path': 'model', 'threads': 4, 'batch_size': 8}
        self.settings = types.SimpleNamespace(root_dir=self.root, upload_dir=self.root / 'uploads',
            get=lambda *keys, default=None: self.config.get(keys[1], default) if keys[0] == 'embedding' else default)
        package = types.ModuleType(PACKAGE); package.__path__ = [str(APP)]
        self.tokenizer, self.sessions, self.bad = FakeTokenizer(), [], None
        self.inputs = ['attention_mask', 'input_ids', 'token_type_ids']
        modules = {PACKAGE: package, PACKAGE + '.core.config': types.SimpleNamespace(settings=self.settings),
            'tokenizers': types.SimpleNamespace(Tokenizer=types.SimpleNamespace(from_str=lambda _: self.tokenizer)),
            'onnxruntime': types.SimpleNamespace(SessionOptions=types.SimpleNamespace, InferenceSession=self.session)}
        self.stack_patch = patch.dict(sys.modules, modules)
        self.stack_patch.start(); self.addCleanup(self.stack_patch.stop)
        environment = patch.dict(os.environ, {'APP_EMBEDDING_MODEL_DIR': ''})
        environment.start(); self.addCleanup(environment.stop)
        self.embedder = importlib.import_module(PACKAGE + '.engines.embedder')
        self.engine = importlib.import_module(PACKAGE + '.engines.onnx_embedder')
        self.approved_hashes = self.engine.PINNED_SHA256
        self.engine.PINNED_SHA256 = self.fixture_hashes()

    def cleanup_directory(self):
        self.assertEqual(self.root, self.root.resolve())
        self.assertEqual(self.root.parent, Path(tempfile.gettempdir()).resolve())
        self.temp.cleanup()

    def fixture_hashes(self):
        return {name: hashlib.sha256((self.model / name).read_bytes()).hexdigest()
                for name in self.engine.MODEL_FILES}

    def write_manifest(self, **updates):
        manifest = {'dimension': 384, 'sha256': {name: hashlib.sha256((self.model / name).read_bytes()).hexdigest()
            for name in ('onnx/model_quantized.onnx', 'tokenizer.json')}}
        manifest.update(updates)
        (self.model / 'model_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def session(self, path, *, sess_options, providers):
        self.assertEqual(path, (self.model / 'onnx/model_quantized.onnx').read_bytes())
        self.assertEqual(providers, ['CPUExecutionProvider'])
        self.assertEqual((sess_options.intra_op_num_threads, sess_options.inter_op_num_threads), (4, 1))
        session = FakeSession(self); self.sessions.append(session)
        return session

    def test_prefix_padding_batches_empty_and_determinism(self):
        identity = self.embedder.get_embedding_identity()  # No explicit load required.
        self.assertTrue(identity.startswith('onnx_e5:'))
        vectors = np.asarray(self.embedder.embed_texts(['矿', 'English text'] * 5))
        self.assertEqual(vectors.shape, (10, 384))
        self.assertTrue(np.isfinite(vectors).all())
        np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-12)
        self.assertEqual([len(feed['input_ids']) for feed in self.sessions[0].feeds], [8, 2])
        feed = self.sessions[0].feeds[0]
        expected = (FakeSession.hidden(feed) * feed['attention_mask'][..., None]).sum(axis=1)
        expected /= np.linalg.norm(expected, axis=1, keepdims=True)
        np.testing.assert_allclose(vectors[:8], expected)
        self.assertTrue(all(array.dtype == np.int64 for array in feed.values()))
        self.assertFalse(feed['token_type_ids'].any())
        np.testing.assert_array_equal(vectors[0], self.embedder.embed_texts(['矿'])[0])
        self.assertFalse(np.allclose(vectors[0], self.embedder.embed_query('矿')))
        self.assertEqual(self.embedder.embed_texts([]), [])
        self.assertEqual(np.asarray(self.embedder.embed_texts(['', ' \n '])).shape, (2, 384))
        self.inputs = ['input_ids', 'attention_mask']
        self.config['batch_size'] = 1
        self.embedder.embed_query('中文')
        self.assertNotIn('token_type_ids', self.sessions[-1].feeds[-1])

    def test_long_tail_coverage_weighting_and_linear_tokenization(self):
        text = ' 矿区\n' * 450 + '变化尾'
        vector = np.asarray(self.embedder.embed_texts([text])[0])
        calls = list(self.tokenizer.calls)
        pieces = [source[len('passage: '):] for source, special in calls if special and source.startswith('passage: ') and source != 'passage: ']
        self.assertEqual(''.join(pieces), text)
        self.assertEqual(sum(not special for _, special in calls), 1)
        self.assertLess(sum(len(source) for source, _ in calls), len(text) * 3 + 100)
        self.assertGreater(vector[2], 0)
        self.assertEqual(self.embedder.embed_texts([text[:-1]])[0][2], 0)
        windows = np.asarray([self.embedder.embed_texts([piece])[0] for piece in pieces])
        weights = np.asarray([sum(not char.isspace() for char in piece) for piece in pieces])
        expected = (windows * weights[:, None]).sum(axis=0)
        np.testing.assert_allclose(vector, expected / np.linalg.norm(expected))
        self.assertTrue(all(feed['input_ids'].shape[1] <= 512 for feed in self.sessions[0].feeds))

    def test_boundary_retokenization_preserves_unicode_and_tail(self):
        self.tokenizer.expand = True
        model = self.embedder._load_onnx_model()
        text = '  \n矿😀e\u0301\u200b区 ' * 190 + '尾'
        windows = list(model._windows(text, 'query: '))
        self.assertEqual(''.join(item.source[len('query: '):] for item, _ in windows), text)
        self.assertTrue(all(len(item.ids) <= 512 for item, _ in windows))
        self.assertGreater(self.embedder.embed_query(text)[2], 0)

    def test_identity_reuse_drift_and_manifest_failure(self):
        first = self.embedder.get_embedding_identity()
        self.assertEqual(first, self.embedder.get_embedding_identity())
        self.assertEqual(len(self.sessions), 1)
        self.config['model_path'] = str(self.model)
        self.assertEqual(first, self.embedder.get_embedding_identity())
        (self.model / 'tokenizer.json').write_bytes(b'changed synthetic tokenizer')
        with self.assertRaises(ValueError): self.embedder.get_embedding_identity()
        self.write_manifest()
        with self.assertRaises(ValueError): self.embedder.get_embedding_identity()
        self.engine.PINNED_SHA256 = self.fixture_hashes()  # Approve a controlled fake revision only.
        second = self.embedder.get_embedding_identity()
        self.assertNotEqual(first, second)
        (self.model / 'onnx/model_quantized.onnx').write_bytes(b'changed synthetic model')
        self.write_manifest()
        with self.assertRaises(ValueError): self.embedder.get_embedding_identity()
        self.engine.PINNED_SHA256 = self.fixture_hashes()
        self.assertNotEqual(second, self.embedder.get_embedding_identity())

    def test_rejects_manifest_escape_links_and_invalid_options(self):
        for manifest in ({'dimension': 383}, {'sha256': {'../outside': '0' * 64}}, {'sha256': None},
                         {'model_file': '../outside'}, {'tokenizer_file': '../outside'}, {'max_tokens': 1024}):
            with self.subTest(manifest=manifest):
                self.write_manifest(**manifest)
                with self.assertRaises(ValueError): self.embedder.get_embedding_identity()
        self.write_manifest()
        for path in ('../outside', '//server/model', 'absent'):
            self.config['model_path'] = path
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.embedder.get_embedding_identity()
        self.config['model_path'] = 'model'
        original = Path.lstat
        for mode, attributes in ((stat.S_IFLNK, 0), (stat.S_IFREG, stat.FILE_ATTRIBUTE_REPARSE_POINT)):
            def linked(path, *args, **kwargs):
                return types.SimpleNamespace(st_mode=mode, st_file_attributes=attributes) if path == self.model else original(path, *args, **kwargs)
            with patch.object(Path, 'lstat', linked), self.assertRaises(ValueError):
                self.embedder.get_embedding_identity()
        self.config['batch_size'] = 0
        with self.assertRaises(ValueError): self.embedder.get_embedding_identity()
        self.assertEqual(self.sessions, [])

    def test_unapproved_bytes_never_reach_runtime(self):
        self.engine.PINNED_SHA256 = self.approved_hashes
        with self.assertRaisesRegex(ValueError, 'approved'):
            self.embedder.get_embedding_identity()
        self.assertEqual(self.sessions, [])

    def test_rejects_invalid_outputs_and_nonstring_inputs(self):
        for bad in ('nan', 'inf', 'shape', 'zero'):
            self.bad = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError): self.embedder.embed_query('矿')
        self.bad = None
        with self.assertRaises(TypeError): self.embedder.embed_texts([None])

    def test_unconfigured_and_sentence_transformer_compatibility(self):
        self.config.clear()
        self.assertIsNone(self.embedder.get_embedding_identity())
        self.assertIsNone(self.embedder.embed_texts(['x']))
        self.assertIsNone(self.embedder.embed_query('x'))
        self.config['backend'] = 'onnx_e5'
        self.assertIsNone(self.embedder.get_embedding_identity())
        self.assertIsNone(self.embedder.embed_texts([]))
        self.config.update(backend='sentence_transformers', model_path='model')
        model = Mock(); model.encode.return_value = np.asarray([[0.6, 0.8]])
        constructor = Mock(return_value=model)
        with patch.dict(sys.modules, {'sentence_transformers': types.SimpleNamespace(SentenceTransformer=constructor)}):
            self.assertEqual(self.embedder.embed_query('原文'), [0.6, 0.8])
            constructor.assert_called_once_with(str(self.model), local_files_only=True)
            model.encode.assert_called_once_with(['原文'], normalize_embeddings=True, show_progress_bar=False)
            self.assertIsNone(self.embedder.get_embedding_identity())

    def test_both_ingestors_preserve_metadata_mock_vectors_and_invalidate(self):
        versions = types.SimpleNamespace(versioning_enabled=lambda _: False, require_ready=lambda _: None,
            current_revision=lambda *_: None, verify_source_file=lambda *_: self.root / 'uploads/source.txt',
            json_text=lambda value: json.dumps(value, sort_keys=True), VersionError=RuntimeError)
        store, keywords = Mock(), Mock()
        sys.modules[PACKAGE + '.services.version_service'] = versions
        sys.modules[PACKAGE + '.engines.keyword_index'] = types.SimpleNamespace(KeywordIndex=keywords)
        sys.modules[PACKAGE + '.engines.vector_store'] = types.SimpleNamespace(vector_store=store,
            encode_vector_json=json.dumps, l2_normalize=lambda vector: vector)
        for name in ('doc_ingest', 'versioned_ingest'):
            module = importlib.import_module(PACKAGE + '.engines.' + name)
            for vectors, identities in (([[1, 0]], [None, None]), ([[1, 0]], ['model-id'] * 2),
                                        (None, ['model-id']), ([[]], ['model-id']), ([[1, 0]], ['old', 'new'])):
                with self.subTest(module=name, vectors=vectors, identities=identities):
                    db = MagicMock(); db.__enter__.return_value = db
                    task = types.SimpleNamespace(id=1, document_id=1, phase='queued', operator_id=1, chunk_count=0)
                    doc = types.SimpleNamespace(id=1, kb_id=7, storage_path='uploads/source.txt', file_type='txt')
                    revision = types.SimpleNamespace(id=1, state='building')
                    objects = {module.ParseTask: task, module.Document: doc}
                    if name == 'versioned_ingest':
                        objects.update({module.DocumentVersion: types.SimpleNamespace(sha256='synthetic'), module.ParseRevision: revision})
                    db.get.side_effect = lambda cls, _: objects[cls]
                    db.scalar.side_effect = [revision, None, revision]
                    db.execute.return_value.rowcount = 1
                    meta = {'sheet': '原有定位'}
                    changes = {'embed_texts': Mock(return_value=vectors), 'get_embedding_identity': Mock(side_effect=identities),
                        'chunk_parts': Mock(return_value=[{'content': '合成资料', 'meta': meta}])}
                    if name == 'doc_ingest':
                        changes.update(get_session_factory=Mock(return_value=lambda: db), _run_extract=Mock(return_value=[]))
                    else:
                        changes.update(parse_file=Mock(return_value=[]), _parser_build=Mock(return_value=('synthetic', {})))
                    with patch.multiple(module, **changes):
                        result = module.ingest_document(1) if name == 'doc_ingest' else module.ingest_versioned(1, factory=lambda: db)
                    rows = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], module.Chunk)]
                    drift = identities == ['old', 'new']
                    self.assertEqual(result['status'], 'failed' if drift else 'success')
                    self.assertEqual(meta, {'sheet': '原有定位'})
                    if not drift:
                        expected = {**meta, 'embedding_model_id': 'model-id'} if vectors == [[1, 0]] and identities[0] else meta
                        self.assertEqual(json.loads(rows[0].meta_json), expected)
                        self.assertEqual(result['vectorized'], vectors == [[1, 0]])
                    else:
                        self.assertEqual(rows, [])
                    store.invalidate.assert_called_with(7)


if __name__ == '__main__':
    unittest.main(verbosity=2)
