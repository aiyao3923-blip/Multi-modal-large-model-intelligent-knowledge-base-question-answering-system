"""Independent academic relevance contracts; no real config, DB or model calls.

Run from the project root with the approved interpreter:
    python -B -m unittest discover -s backend/tests -p test_academic_relevance.py -v

QA is imported under a private package name with an in-memory config module.
The real helper, fusion, citation builder and offline answer formatter execute;
retrievers and version-scope freshness are mocked, not integration-tested here.
No temporary files, application startup or other test fixtures are needed.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import importlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch, call


APP_DIR = Path(__file__).resolve().parents[1] / "app"
MODEL = "model-A"
QUESTION = "What changed in the mining area?"
_DEFAULT = object()


def _retrieval():
    return {
        "academic_kb_ids": [8, 9, 10],
        "academic_relevance": {"model_identity": MODEL, "min_similarity": 0.85},
    }


def _blocked(*args, **kwargs):
    raise AssertionError("Real configuration, database and model I/O are forbidden")


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "_academic_relevance_contract_helper", APP_DIR / "engines" / "academic_relevance.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _isolated_qa():
    """Keep synthetic settings and ORM state out of the shared app namespace."""
    namespace = "_academic_relevance_contract_app"
    if any(name == namespace or name.startswith(namespace + ".") for name in sys.modules):
        raise RuntimeError("The private test import namespace is already in use")
    package = ModuleType(namespace)
    package.__path__ = [str(APP_DIR)]
    core = ModuleType(namespace + ".core")
    core.__path__ = [str(APP_DIR / "core")]
    config = ModuleType(namespace + ".core.config")
    config.settings = SimpleNamespace(get=_blocked)
    package.core = core
    core.config = config
    sys.modules.update({namespace: package, core.__name__: core, config.__name__: config})
    try:
        session = importlib.import_module(namespace + ".db.session")
        with ExitStack() as guards:
            guards.enter_context(patch.object(session, "get_engine", side_effect=_blocked))
            guards.enter_context(patch.object(session, "get_session_factory", side_effect=_blocked))
            yield importlib.import_module(namespace + ".services.qa_service")
    finally:
        # Only remove our namespace; do not replace a snapshot of all sys.modules.
        for name in tuple(sys.modules):
            if name == namespace or name.startswith(namespace + "."):
                del sys.modules[name]


def _hit(chunk_id=101, score=0.85):
    return {
        "chunk_id": chunk_id,
        "doc_id": 1000 + chunk_id,
        "doc_name": "Synthetic mining paper.pdf",
        "page_no": 3,
        "content": f"Evidence {chunk_id}: observations identify mining-area changes.",
        "score": score,
    }


class AcademicSimilarityFloorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = _load_helper()

    def floor(self, retrieval, kb_id=8, model_identity=MODEL):
        return self.helper.academic_similarity_floor(retrieval, kb_id, model_identity)

    def test_missing_policy_is_noop_before_unused_profile_validation(self):
        cases = [
            {},
            {"academic_kb_ids": [8, 9, 10]},
            {"academic_kb_ids": "unused legacy value"},
            {"academic_kb_ids": [False]},
            {"academic_kb_ids": None, "academic_relevance": None},
        ]
        for retrieval in cases:
            with self.subTest(retrieval=retrieval):
                self.assertIsNone(self.floor(retrieval))

    def test_nonacademic_kb_does_not_validate_or_apply_policy(self):
        for policy in ([], "invalid", {}, {"model_identity": "", "min_similarity": -1}):
            retrieval = _retrieval()
            retrieval["academic_relevance"] = policy
            with self.subTest(policy=policy):
                self.assertIsNone(self.floor(retrieval, kb_id=1))
        self.assertIsNone(self.floor(_retrieval(), kb_id=1))

    def test_missing_or_empty_scope_does_not_enable_a_floor(self):
        retrieval = _retrieval()
        del retrieval["academic_kb_ids"]
        self.assertIsNone(self.floor(retrieval))
        retrieval["academic_kb_ids"] = []
        self.assertIsNone(self.floor(retrieval))

    def test_matching_model_applies_to_each_explicit_academic_kb(self):
        for kb_id in (8, 9, 10):
            with self.subTest(kb_id=kb_id):
                value = self.floor(_retrieval(), kb_id)
                self.assertIs(type(value), float)
                self.assertEqual(value, 0.85)

    def test_other_or_unknown_model_identity_does_not_reuse_the_floor(self):
        for identity in (None, "model-B", "model-a", "model-A ", ""):
            with self.subTest(identity=identity):
                self.assertIsNone(self.floor(_retrieval(), model_identity=identity))

    def test_integer_and_float_endpoints_are_inclusive_and_return_float(self):
        for value in (0, 0.0, -0.0, 1, 1.0):
            with self.subTest(value=value, kind=type(value).__name__):
                retrieval = _retrieval()
                retrieval["academic_relevance"]["min_similarity"] = value
                result = self.floor(retrieval)
                self.assertIs(type(result), float)
                self.assertEqual(result, float(value))

    def test_retrieval_itself_must_be_a_dict(self):
        for retrieval in (None, [], (), "retrieval", 0, False):
            with self.subTest(retrieval=retrieval), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_profile_id_container_must_be_a_list(self):
        for ids in (None, 8, True, "8", (8, 9), {8, 9}, {8: True}):
            retrieval = _retrieval()
            retrieval["academic_kb_ids"] = ids
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_every_profile_id_must_be_a_positive_nonboolean_integer(self):
        for invalid_id in (0, -1, True, False, 9.0, "9", None, [], {}):
            retrieval = _retrieval()
            retrieval["academic_kb_ids"] = [8, invalid_id]
            with self.subTest(invalid_id=invalid_id), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_present_policy_must_be_a_dict_in_the_active_scope(self):
        for policy in ([], (), "policy", 0, 0.85, True, False):
            retrieval = _retrieval()
            retrieval["academic_relevance"] = policy
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_active_policy_requires_both_fields(self):
        for policy in ({}, {"model_identity": MODEL}, {"min_similarity": 0.85}):
            retrieval = _retrieval()
            retrieval["academic_relevance"] = policy
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_bound_model_identity_must_be_a_nonempty_string(self):
        for identity in (None, "", " ", "\t\n", 7, True, [], {}, b"model-A"):
            retrieval = _retrieval()
            retrieval["academic_relevance"]["model_identity"] = identity
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_similarity_rejects_boolean_and_nonnumeric_values(self):
        for value in (None, True, False, "0.85", "", [], {}, complex(0.85, 0)):
            retrieval = _retrieval()
            retrieval["academic_relevance"]["min_similarity"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_similarity_rejects_nonfinite_and_out_of_range_numbers(self):
        cases = {
            "nan": float("nan"),
            "positive_infinity": float("inf"),
            "negative_infinity": -float("inf"),
            "below_zero": -0.001,
            "above_one": 1.001,
            "negative_integer": -1,
            "positive_integer": 2,
            "positive_integer_beyond_float_range": 10**400,
            "negative_integer_beyond_float_range": -(10**400),
        }
        for name, value in cases.items():
            retrieval = _retrieval()
            retrieval["academic_relevance"]["min_similarity"] = value
            with self.subTest(case=name), self.assertRaises(ValueError):
                self.floor(retrieval)

    def test_calls_are_repeatable_and_do_not_mutate_the_input(self):
        retrieval = _retrieval()
        retrieval["academic_kb_ids"] = [10, 8, 9]
        retrieval["unrelated"] = {"nested": ["preserved"]}
        before = deepcopy(retrieval)
        with patch("builtins.open", side_effect=_blocked):
            for identity, expected in ((MODEL, 0.85), ("model-B", None), (MODEL, 0.85)):
                with self.subTest(identity=identity):
                    self.assertEqual(self.floor(retrieval, model_identity=identity), expected)
                    self.assertEqual(retrieval, before)


class AcademicRelevanceQaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stack = ExitStack()
        cls.addClassCleanup(stack.close)
        cls.qa = stack.enter_context(_isolated_qa())

    def _exercise(self, *, retrieval=None, kb_id=8, identity=MODEL,
                  query_vector=_DEFAULT, keyword=None, vector=None,
                  original_threshold=0.35, scope=None):
        qa = self.qa
        retrieval = _retrieval() if retrieval is None else retrieval
        query_vector = [1.0, 0.0] if query_vector is _DEFAULT else query_vector
        keyword = [] if keyword is None else keyword
        vector = [] if vector is None else vector
        kb = SimpleNamespace(id=kb_id, name="Synthetic academic KB", status="enabled", template_id=None)
        db = Mock(spec_set=["get"])
        db.get.return_value = kb
        options = {"top_k": 2, "threshold": original_threshold, "max_context": 6,
                   "max_rounds": 4, "degraded_top": 3}
        calls = SimpleNamespace(db=db, kb=kb, query_vector=query_vector, scope=scope)
        with ExitStack() as stack:
            def replace(owner, name, **kwargs):
                return stack.enter_context(patch.object(owner, name, **kwargs))

            calls.options = replace(qa, "_options", return_value=options)
            calls.scope_lookup = replace(qa.versions, "retrieval_scope", return_value=scope)
            # Search alone is insufficient: the real constructor obtains a DB factory.
            replace(qa.KeywordIndex, "__init__", return_value=None)
            calls.keyword = replace(qa.KeywordIndex, "search", return_value=keyword)
            calls.embed = replace(qa, "embed_query", return_value=query_vector)
            calls.vector = replace(qa.vector_store, "search", return_value=vector)
            calls.identity = replace(qa, "get_embedding_identity", return_value=identity)
            calls.settings = replace(qa.settings, "get", return_value=retrieval)
            calls.candidates = replace(qa, "_candidate_channels", return_value=[])
            calls.select_channel = replace(qa, "_select_channel", side_effect=_blocked)
            calls.llm = replace(qa, "_call_llm_safe", side_effect=_blocked)
            calls.channel_chat = replace(qa, "channel_chat", side_effect=_blocked)
            calls.degraded = replace(qa, "degraded_answer", wraps=qa.degraded_answer)
            # Assert forwarding only; permissions/version freshness belong to their own suites.
            calls.scope_fresh = replace(qa, "_assert_scope_fresh", return_value=None)
            # This suite isolates calibration. Real row/permission rebinding has
            # separate HTTP tests in test_paper_qa.py, not a fabricated Mock DB.
            calls.bound = replace(qa, "_bound_evidence", side_effect=lambda db, kb, hits, scope: hits)
            calls.expanded = replace(qa, "_expand_evidence", side_effect=lambda db, kb, hits, scope, **kwargs: hits)
            calls.result = qa.run_qa(db, 17, 23, kb_id, QUESTION, [])

        db.get.assert_called_once_with(qa.KnowledgeBase, kb_id)
        calls.options.assert_called_once_with(db)
        calls.scope_lookup.assert_called_once_with(db, [kb_id])
        scope_args = {"scope": scope} if scope is not None else {}
        calls.keyword.assert_called_once_with(QUESTION, kb_id, top_k=6, **scope_args)
        calls.embed.assert_called_once_with(QUESTION)
        if query_vector:
            calls.identity.assert_called_once_with()
            calls.settings.assert_called_once_with("retrieval", default={})
        else:
            calls.identity.assert_not_called()
            calls.vector.assert_not_called()
        calls.select_channel.assert_not_called()
        calls.llm.assert_not_called()
        calls.channel_chat.assert_not_called()
        self.assertEqual(set(calls.result), {
            "answer", "mode", "citations", "latency_ms", "kb_name", "_version_scope",
            "generation_status", "notice", "retrieval", "_source_bindings", "_source_locations",
        })
        calls.bound.assert_called_once()
        self.assertEqual(calls.bound.call_args.args[1], kb_id)
        self.assertIs(calls.bound.call_args.args[3], scope)
        self.assertIs(calls.result["_version_scope"], scope)
        self.assertEqual(calls.result["kb_name"], kb.name)
        self.assertIsInstance(calls.result["answer"], str)
        self.assertTrue(calls.result["answer"])
        self.assertIs(type(calls.result["latency_ms"]), int)
        self.assertGreaterEqual(calls.result["latency_ms"], 0)
        return calls

    def _assert_vector_threshold(self, calls, expected_threshold):
        scope_args = {"scope": calls.scope} if calls.scope is not None else {}
        calls.vector.assert_called_once_with(
            calls.query_vector, calls.kb.id, top_k=6, threshold=expected_threshold, **scope_args
        )

    def _assert_no_evidence(self, calls):
        self.assertEqual(calls.result["mode"], "none")
        self.assertEqual(calls.result["citations"], [])
        calls.candidates.assert_not_called()
        calls.degraded.assert_not_called()
        self.assertEqual(calls.scope_fresh.call_args_list, [call(calls.db,17,calls.kb.id,None),call(calls.db,17,calls.kb.id,calls.scope)])

    def _assert_offline_evidence(self, calls):
        self.assertEqual(calls.result["mode"], "degraded")
        self.assertTrue(calls.result["citations"])
        self.assertIn("未启用生成模型", calls.result["answer"])
        self.assertNotIn("Evidence ", calls.result["answer"])
        calls.candidates.assert_called_once_with(calls.db)
        calls.degraded.assert_not_called()
        self.assertEqual(calls.scope_fresh.call_args_list, [call(calls.db,17,calls.kb.id,None),call(calls.db,17,calls.kb.id,calls.scope)])

    def test_calibrated_vector_miss_cannot_fall_back_to_keyword_or_channels(self):
        # The vector store has already rejected hits below the requested floor.
        for keyword in ([], [_hit(score=12.0)]):
            with self.subTest(keyword_hits=len(keyword)):
                calls = self._exercise(keyword=keyword, vector=[])
                self._assert_vector_threshold(calls, 0.85)
                self._assert_no_evidence(calls)

    def test_valid_vectors_keep_citations_fusion_and_the_stricter_threshold(self):
        # Synthetic _options values test max(); they are not production calibration.
        for original, score, expected in ((0.35, 0.85, 0.85), (0.93, 0.94, 0.93)):
            with self.subTest(original_threshold=original):
                first, second = _hit(101, score), _hit(102, 0.99)
                calls = self._exercise(keyword=[_hit(101, 4.0)], vector=[first, second],
                                       original_threshold=original)
                self._assert_vector_threshold(calls, expected)
                self._assert_offline_evidence(calls)
                expected_citations = []
                for rank, (hit, similarity) in enumerate(
                    ((first, round(2 / 61, 4)), (second, round(1 / 62, 4))), start=1
                ):
                    expected_citations.append({
                        "rank": rank, "chunk_id": hit["chunk_id"], "doc_id": hit["doc_id"],
                        "doc_name": hit["doc_name"], "page_no": hit["page_no"],
                        "content_excerpt": hit["content"], "similarity": similarity,
                    })
                self.assertEqual(calls.result["citations"], expected_citations)

    def test_inactive_scope_policy_or_model_keeps_legacy_keyword_behavior(self):
        cases = (
            ("old_kb", 1, _retrieval(), MODEL),
            ("no_policy", 8, {"academic_kb_ids": [8, 9, 10]}, MODEL),
            ("other_model", 8, _retrieval(), "model-B"),
            ("unknown_model", 8, _retrieval(), None),
        )
        for name, kb_id, retrieval, identity in cases:
            with self.subTest(case=name):
                hit = _hit(score=4.0)
                calls = self._exercise(kb_id=kb_id, retrieval=retrieval, identity=identity,
                                       keyword=[hit], vector=[])
                self._assert_vector_threshold(calls, 0.35)
                self._assert_offline_evidence(calls)
                self.assertEqual(calls.result["citations"], [{
                    "rank": 1, "chunk_id": hit["chunk_id"], "doc_id": hit["doc_id"],
                    "doc_name": hit["doc_name"], "page_no": hit["page_no"],
                    "content_excerpt": hit["content"], "similarity": 4.0,
                }])

    def test_unavailable_query_vector_keeps_bm25_without_identity_lookup(self):
        for query_vector in (None, []):
            with self.subTest(query_vector=query_vector):
                hit = _hit(score=4.0)
                calls = self._exercise(query_vector=query_vector, keyword=[hit])
                self._assert_offline_evidence(calls)
                calls.identity.assert_not_called()
                calls.vector.assert_not_called()
                self.assertEqual(len(calls.result["citations"]), 1)
                self.assertEqual(calls.result["citations"][0]["chunk_id"], hit["chunk_id"])
                self.assertEqual(calls.result["citations"][0]["content_excerpt"], hit["content"])

    def test_explicit_query_scope_is_forwarded_unchanged_on_hit_and_rejection(self):
        for vector in ([], [_hit()]):
            with self.subTest(vector_hits=len(vector)):
                scope = {"signature": "synthetic-scope", "unknown_date_version_ids": [],
                         "notice": "synthetic version notice"}
                calls = self._exercise(keyword=[_hit(score=4.0)], vector=vector, scope=scope)
                self._assert_vector_threshold(calls, 0.85)
                self.assertIs(calls.keyword.call_args.kwargs["scope"], scope)
                self.assertIs(calls.vector.call_args.kwargs["scope"], scope)
                self.assertIs(calls.result["_version_scope"], scope)
                if vector:
                    self._assert_offline_evidence(calls)
                else:
                    self._assert_no_evidence(calls)


if __name__ == "__main__":
    unittest.main()
