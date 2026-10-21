"""版本 HTTP 契约的 schema 单测与 API AST 核验；不导入 API/数据库或服务 stub。

响应样本仅为显式的合成契约夹具，不代表真实服务执行或 HTTP 集成通过。
"""
from __future__ import annotations

import ast
import json
import sys
import unittest
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.schemas.common import PageOut
from app.schemas.versions import (
    MetadataPatch, MetadataUpdate, RevisionOut, SeriesCreate, SeriesOut,
    SeriesVersionUpload, VersionAction, VersionOut, VersionPublish,
)
from app.services.version_policy import normalize_applicability


def _revision_fixture():
    return {
        "id": 71, "version_id": 11, "task_id": None, "parser_version": "schema-test/v1",
        "file_sha256": None, "content_hash": "", "source_integrity": "unverified",
        "state": "building", "is_current": False, "chunk_count": 0, "error_message": "",
        "created_at": "2024-06-15T08:00:00", "finished_at": None,
    }


def _version_fixture():
    return {
        "version_id": 11, "kb_id": 2, "series_id": 3, "series_title": "合成契约样本",
        "series_revision": 1, "filename": "schema-fixture.txt", "file_type": "txt", "file_size": 12,
        "version_label": "v1", "publication_state": "draft", "validity": "unknown",
        "effective_from": None, "effective_to": None, "applicability": {}, "metadata_revision": 1,
        "supersedes_id": None, "coexistence_note": "", "sha256": "a" * 64, "is_legacy": False,
        "date_status": "unknown", "eligibility_reasons": ["not_published:draft", "date_unknown"],
        "can_manage": True, "can_publish": False, "current_revision": None,
        "latest_revision": _revision_fixture(), "created_at": "2024-06-15T08:00:00",
        "updated_at": "2024-06-15T08:00:00",
    }


def _series_fixture():
    return {
        "id": 3, "kb_id": 2, "title": "合成契约样本", "business_key": None, "owner_id": 5,
        "state": "active", "revision": 1, "created_at": "2024-06-15T08:00:00",
        "updated_at": "2024-06-15T08:00:00",
    }


class ActionInputTests(unittest.TestCase):
    def test_expected_revision_is_required_strict_positive_integer(self):
        for model in (VersionAction, VersionPublish):
            with self.subTest(model=model.__name__):
                self.assertEqual(model(expected_revision=1, reason="核对").expected_revision, 1)
                with self.assertRaises(ValidationError):
                    model(reason="核对")
            for value in (0, -1, True, False, 1.0, "1", None, [], {}):
                with self.subTest(model=model.__name__, value=value):
                    with self.assertRaises(ValidationError):
                        model(expected_revision=value, reason="核对")

    def test_json_does_not_coerce_revision_strings_floats_or_booleans(self):
        for value in ("1", 1.0, True, False, None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    VersionAction.model_validate_json(json.dumps({"expected_revision": value, "reason": "核对"}))

    def test_reason_trimming_and_character_boundaries(self):
        self.assertEqual(VersionAction(expected_revision=1, reason=" \n核对\t ").reason, "核对")
        self.assertEqual(len(VersionAction(expected_revision=1, reason=" 理" + "由" * 999 + " ").reason), 1000)
        for value in ("", " \t\n", "由" * 1001, None, 1, True, b"reason"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    VersionAction(expected_revision=1, reason=value)
        with self.assertRaises(ValidationError):
            VersionAction(expected_revision=1)

    def test_publish_unknown_acknowledgement_is_strict_bool_and_opt_in(self):
        default = VersionPublish(expected_revision=1, reason="核对")
        self.assertIs(default.acknowledge_unknown, False)
        self.assertNotIn("acknowledge_unknown", default.model_fields_set)
        for value in (True, False):
            with self.subTest(value=value):
                self.assertIs(VersionPublish(expected_revision=1, reason="核对", acknowledge_unknown=value).acknowledge_unknown, value)
        for value in (0, 1, "true", "false", "yes", None, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    VersionPublish(expected_revision=1, reason="核对", acknowledge_unknown=value)

    def test_all_input_models_forbid_extra_fields(self):
        cases = (
            (SeriesCreate, {"title": "样本"}),
            (VersionAction, {"expected_revision": 1, "reason": "核对"}),
            (VersionPublish, {"expected_revision": 1, "reason": "核对"}),
            (MetadataPatch, {}),
            (MetadataUpdate, {"expected_revision": 1, "reason": "核对"}),
            (SeriesVersionUpload, {"version_label": "v1", "expected_series_revision": "1"}),
        )
        for model, values in cases:
            with self.subTest(model=model.__name__):
                self.assertEqual(model.model_config.get("extra"), "forbid")
                with self.assertRaises(ValidationError) as failure:
                    model.model_validate({**values, "actor": {"id": 999}})
                self.assertEqual(failure.exception.errors()[0]["type"], "extra_forbidden")


class SeriesCreateTests(unittest.TestCase):
    def test_title_trimming_and_255_character_limit(self):
        self.assertEqual(SeriesCreate(title=" 新资料 ").title, "新资料")
        self.assertEqual(len(SeriesCreate(title="题" * 255).title), 255)
        for value in ("", " \t\n", "题" * 256, None, 1, True, b"title"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    SeriesCreate(title=value)

    def test_optional_business_key_is_trimmed_and_empty_is_unspecified(self):
        self.assertIsNone(SeriesCreate(title="样本").business_key)
        for value in (None, "", " \t\n"):
            with self.subTest(value=value):
                self.assertIsNone(SeriesCreate(title="样本", business_key=value).business_key)
        self.assertEqual(SeriesCreate(title="样本", business_key="  ABC  ").business_key, "ABC")
        self.assertEqual(len(SeriesCreate(title="样本", business_key="键" * 160).business_key), 160)
        for value in ("键" * 161, 7, False, b"key"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    SeriesCreate(title="样本", business_key=value)

    def test_identity_and_owner_are_not_http_create_inputs(self):
        for key in ("kb_id", "owner_id", "id", "state", "revision", "series_id"):
            with self.subTest(key=key):
                with self.assertRaises(ValidationError):
                    SeriesCreate.model_validate({"title": "样本", key: 1})


class MetadataPatchTests(unittest.TestCase):
    def test_field_whitelist_is_exact(self):
        self.assertEqual(set(MetadataPatch.model_fields), {
            "version_label", "validity", "effective_from", "effective_to",
            "applicability", "supersedes_id", "coexistence_note",
        })
        for forbidden in ("sha256", "filename", "file_size", "file_type", "storage_path", "series_id",
                          "version_id", "kb_id", "publication_state", "metadata_revision", "actor_id", "owner_id"):
            with self.subTest(forbidden=forbidden):
                with self.assertRaises(ValidationError):
                    MetadataPatch.model_validate({forbidden: "forbidden"})

    def test_empty_patch_does_not_send_defaults(self):
        patch = MetadataPatch()
        self.assertEqual(patch.model_fields_set, set())
        self.assertEqual(patch.model_dump(exclude_unset=True), {})
        self.assertEqual(patch.model_dump_json(exclude_unset=True), "{}")

    def test_explicit_null_clears_are_distinct_from_omitted_fields(self):
        supplied = {"effective_from": None, "effective_to": None, "supersedes_id": None,
                    "coexistence_note": None, "applicability": None}
        patch = MetadataPatch.model_validate(supplied)
        self.assertEqual(patch.model_fields_set, set(supplied))
        self.assertEqual(patch.model_dump(exclude_unset=True), {**supplied, "applicability": {}})
        for key in ("version_label", "validity"):
            self.assertNotIn(key, patch.model_dump(exclude_unset=True))

    def test_label_is_optional_but_explicit_null_and_blank_are_invalid(self):
        self.assertEqual(MetadataPatch(version_label=" v2 ").model_dump(exclude_unset=True), {"version_label": "v2"})
        self.assertEqual(len(MetadataPatch(version_label="版" * 80).version_label), 80)
        for value in (None, "", " \t\n", "版" * 81, 2, False, b"v2"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    MetadataPatch(version_label=value)

    def test_validity_only_accepts_known_or_unknown_when_provided(self):
        for value in ("known", "unknown"):
            with self.subTest(value=value):
                self.assertEqual(MetadataPatch(validity=value).model_dump(exclude_unset=True), {"validity": value})
        for value in (None, "", "current", "KNOWN", " known ", 1, True):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    MetadataPatch(validity=value)

    def test_dates_accept_only_date_or_strict_calendar_date_string(self):
        for field in ("effective_from", "effective_to"):
            for value in (date(2024, 2, 29), "2024-02-29", None):
                with self.subTest(field=field, value=value):
                    patch = MetadataPatch.model_validate({field: value})
                    expected = None if value is None else date(2024, 2, 29)
                    self.assertEqual(patch.model_dump(exclude_unset=True), {field: expected})
            for value in ("2023-02-29", "2024-2-29", "20240229", "２０２４-０２-２９", "2024-02-29 ",
                          "2024-02-29\n", "2024-02-29T00:00:00", "", 0, 1709164800, True, 1.0,
                          datetime(2024, 2, 29), datetime(2024, 2, 29, tzinfo=timezone.utc), b"2024-02-29"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        MetadataPatch.model_validate({field: value})

    def test_single_date_patch_and_service_owned_cross_field_validation(self):
        # These are field-valid patches, NOT claims that a merged version is valid.
        cases = (
            {"effective_from": "2024-06-15"}, {"effective_to": "2024-06-15"},
            {"validity": "known"}, {"validity": "unknown", "effective_from": "2024-06-15"},
            {"effective_from": "2024-07-01", "effective_to": "2024-06-15"},
        )
        for values in cases:
            with self.subTest(values=values):
                patch = MetadataPatch.model_validate(values)
                self.assertEqual(patch.model_fields_set, set(values))

    def test_json_date_serialization_preserves_explicit_clear(self):
        patch = MetadataPatch.model_validate_json('{"effective_from":"2024-02-29","effective_to":null}')
        self.assertEqual(patch.effective_from, date(2024, 2, 29))
        self.assertEqual(json.loads(patch.model_dump_json(exclude_unset=True)), {
            "effective_from": "2024-02-29", "effective_to": None,
        })
        with self.assertRaises(ValidationError):
            MetadataPatch.model_validate_json('{"effective_from":1709164800}')

    def test_applicability_uses_real_policy_normalization(self):
        original = {" z ": ["b", " a ", "b"], " a ": " student "}
        snapshot = deepcopy(original)
        patch = MetadataPatch(applicability=original)
        self.assertEqual(patch.applicability, normalize_applicability(original))
        self.assertEqual(list(patch.applicability), ["a", "z"])
        self.assertEqual(original, snapshot)
        self.assertEqual(MetadataPatch(applicability=None).model_dump(exclude_unset=True), {"applicability": {}})

    def test_policy_errors_are_pydantic_field_errors_not_silent_coercion(self):
        for value in ({"身份": {"nested": "学生"}}, {"身份": []}, {"身份": ["学生", 1]},
                      {"身份": ["学生"] * 21}, {" ": "学生"}, [], "学生", False):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError) as failure:
                    MetadataPatch(applicability=value)
                self.assertEqual(failure.exception.errors()[0]["loc"], ("applicability",))

    def test_supersedes_id_is_nullable_strict_positive_integer(self):
        self.assertEqual(MetadataPatch(supersedes_id=1).supersedes_id, 1)
        self.assertIsNone(MetadataPatch(supersedes_id=None).supersedes_id)
        for value in (0, -1, True, False, 1.0, "1", "", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    MetadataPatch(supersedes_id=value)

    def test_coexistence_note_can_clear_and_keeps_meaningful_whitespace(self):
        for value in (None, "", " line one\nline two ", "注" * 1000):
            with self.subTest(value=value):
                self.assertEqual(MetadataPatch(coexistence_note=value).model_dump(exclude_unset=True), {"coexistence_note": value})
        for value in ("注" * 1001, 7, False, b"note"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    MetadataPatch(coexistence_note=value)

    def test_metadata_update_is_flat_and_requires_revision_and_reason(self):
        payload = {"expected_revision": 2, "reason": " 校正 ", "effective_to": None,
                   "applicability": {" 人群 ": "学生"}}
        request = MetadataUpdate.model_validate(payload)
        self.assertEqual(request.reason, "校正")
        self.assertEqual(request.model_fields_set, set(payload))
        self.assertEqual(request.model_dump(exclude_unset=True, exclude={"expected_revision", "reason"}),
                         {"effective_to": None, "applicability": {"人群": ["学生"]}})
        unchanged = MetadataUpdate(expected_revision=1, reason="核对")
        self.assertEqual(unchanged.model_dump(exclude_unset=True, exclude={"expected_revision", "reason"}), {})
        for field in ("effective_from", "effective_to", "supersedes_id", "coexistence_note", "applicability"):
            with self.subTest(clear=field):
                cleared = MetadataUpdate.model_validate({"expected_revision": 1, "reason": "核对", field: None})
                expected = {} if field == "applicability" else None
                self.assertEqual(cleared.model_dump(exclude_unset=True, exclude={"expected_revision", "reason"}),
                                 {field: expected})
        partial_date = MetadataUpdate(expected_revision=1, reason="核对", effective_from="2024-02-29")
        self.assertEqual(partial_date.model_dump(exclude_unset=True, exclude={"expected_revision", "reason"}),
                         {"effective_from": date(2024, 2, 29)})
        for missing in ("expected_revision", "reason"):
            with self.subTest(missing=missing):
                with self.assertRaises(ValidationError):
                    MetadataUpdate.model_validate({key: value for key, value in payload.items() if key != missing})
        for invalid in ({"filename": "forbidden"}, {"version_label": None}, {"validity": None},
                        {"effective_from": datetime(2024, 2, 29)}, {"applicability": {"人群": {"nested": "学生"}}},
                        {"supersedes_id": 0}, {"coexistence_note": "注" * 1001},
                        {"expected_revision": True}, {"reason": " \t\n"}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    MetadataUpdate.model_validate({**payload, **invalid})
        for non_object in ([], None):
            with self.subTest(non_object=non_object):
                with self.assertRaises(ValidationError):
                    MetadataUpdate.model_validate(non_object)

    def test_metadata_update_rejects_nested_changes_even_with_valid_flat_fields(self):
        for nested in ({}, {"effective_to": None}, {"applicability": {}}, None, []):
            for flat in ({}, {"effective_to": None}):
                payload = {"expected_revision": 1, "reason": "核对", **flat, "changes": nested}
                with self.subTest(nested=nested, flat=flat):
                    with self.assertRaises(ValidationError) as failure:
                        MetadataUpdate.model_validate(payload)
                    self.assertEqual(failure.exception.errors()[0]["loc"], ("changes",))
                    self.assertEqual(failure.exception.errors()[0]["type"], "extra_forbidden")
                    with self.assertRaises(ValidationError) as json_failure:
                        MetadataUpdate.model_validate_json(json.dumps(payload))
                    self.assertEqual(json_failure.exception.errors()[0]["loc"], ("changes",))
                    self.assertEqual(json_failure.exception.errors()[0]["type"], "extra_forbidden")

    def test_json_schema_advertises_nonnullable_label_and_validity(self):
        schema = MetadataPatch.model_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["version_label"]["type"], "string")
        self.assertEqual(schema["properties"]["validity"]["enum"], ["known", "unknown"])
        self.assertNotIn("version_label", schema.get("required", []))
        self.assertNotIn("validity", schema.get("required", []))
        self.assertIn({"type": "null"}, schema["properties"]["applicability"]["anyOf"])
        flat_schema = MetadataUpdate.model_json_schema()
        self.assertEqual(set(flat_schema["properties"]), set(MetadataPatch.model_fields) | {"expected_revision", "reason"})
        self.assertEqual(set(flat_schema["required"]), {"expected_revision", "reason"})
        self.assertFalse(flat_schema["additionalProperties"])
        self.assertNotIn("changes", flat_schema["properties"])


class UploadMetadataTests(unittest.TestCase):
    def test_form_decimal_strings_become_positive_integers(self):
        value = SeriesVersionUpload(version_label=" v2 ", expected_series_revision="12", supersedes_id="03")
        self.assertEqual(value.model_dump(), {"version_label": "v2", "expected_series_revision": 12, "supersedes_id": 3})
        self.assertEqual(SeriesVersionUpload(version_label="v2", expected_series_revision=1).expected_series_revision, 1)

    def test_form_revision_rejects_coercible_but_invalid_inputs(self):
        for value in ("", "0", "-1", "+1", "1.0", "1e0", " 1", "1 ", "１", "true", True, False, 1.0, None):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    SeriesVersionUpload(version_label="v2", expected_series_revision=value)

    def test_optional_form_predecessor_can_be_empty_but_not_fractional(self):
        for value in (None, ""):
            with self.subTest(value=value):
                self.assertIsNone(SeriesVersionUpload(version_label="v2", expected_series_revision="1", supersedes_id=value).supersedes_id)
        for value in ("0", "-1", "1.0", " 1 ", True, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    SeriesVersionUpload(version_label="v2", expected_series_revision="1", supersedes_id=value)

    def test_upload_label_uses_same_nonblank_limit_as_patch(self):
        self.assertEqual(len(SeriesVersionUpload(version_label="版" * 80, expected_series_revision="1").version_label), 80)
        for value in (None, "", " \n", "版" * 81, 1, b"v2"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    SeriesVersionUpload(version_label=value, expected_series_revision="1")


class ResponseContractTests(unittest.TestCase):
    def test_revision_fields_enums_and_nullable_values(self):
        fixture = _revision_fixture()
        self.assertEqual(set(RevisionOut.model_fields), set(fixture))
        result = RevisionOut.model_validate(fixture)
        self.assertIsNone(result.task_id)
        self.assertIsNone(result.file_sha256)
        self.assertIsNone(result.finished_at)
        self.assertIs(result.is_current, False)
        for key, allowed in {
            "state": ("building", "success", "failed", "legacy_empty"),
            "source_integrity": ("verified", "legacy_unverified", "unverified"),
        }.items():
            for value in allowed:
                with self.subTest(key=key, value=value):
                    self.assertEqual(getattr(RevisionOut.model_validate({**fixture, key: value}), key), value)
            with self.assertRaises(ValidationError):
                RevisionOut.model_validate({**fixture, key: "guessed"})

    def test_version_fields_and_nested_revisions(self):
        fixture = _version_fixture()
        self.assertEqual(set(VersionOut.model_fields), set(fixture))
        result = VersionOut.model_validate(fixture)
        self.assertEqual(result.version_id, 11)
        self.assertIsNone(result.current_revision)
        self.assertIsInstance(result.latest_revision, RevisionOut)
        self.assertEqual(result.latest_revision.version_id, result.version_id)
        self.assertIs(result.can_publish, False)
        self.assertNotIn("trusted", result.model_dump())
        self.assertNotIn("fact_verified", result.model_dump())

    def test_version_status_literals_are_closed(self):
        for key, allowed in {
            "publication_state": ("draft", "published", "withdrawn", "archived"),
            "validity": ("known", "unknown"),
            "date_status": ("current", "future", "expired", "unknown"),
        }.items():
            for value in allowed:
                with self.subTest(key=key, value=value):
                    self.assertEqual(getattr(VersionOut.model_validate({**_version_fixture(), key: value}), key), value)
            with self.assertRaises(ValidationError):
                VersionOut.model_validate({**_version_fixture(), key: "guessed"})

    def test_series_contract_supports_orm_attributes_and_fixed_state(self):
        fixture = _series_fixture()
        self.assertEqual(set(SeriesOut.model_fields), set(fixture))
        result = SeriesOut.model_validate(SimpleNamespace(**fixture))
        self.assertIsNone(result.business_key)
        self.assertEqual(result.state, "active")
        self.assertEqual(SeriesOut.model_validate({**fixture, "state": "archived"}).state, "archived")
        with self.assertRaises(ValidationError):
            SeriesOut.model_validate({**fixture, "state": "published"})

    def test_response_fields_are_required_even_when_nullable(self):
        for model, fixture in ((RevisionOut, _revision_fixture()), (VersionOut, _version_fixture()), (SeriesOut, _series_fixture())):
            for missing in fixture:
                with self.subTest(model=model.__name__, missing=missing):
                    with self.assertRaises(ValidationError):
                        model.model_validate({key: value for key, value in fixture.items() if key != missing})

    def test_page_responses_reuse_existing_pageout(self):
        for model, fixture in ((VersionOut, _version_fixture()), (RevisionOut, _revision_fixture())):
            with self.subTest(model=model.__name__):
                page = PageOut[model].model_validate({"items": [fixture], "total": 1, "page": 1, "size": 20})
                self.assertEqual(set(page.model_dump()), {"items", "total", "page", "size"})
                self.assertIsInstance(page.items[0], model)
                serialized = json.loads(page.model_dump_json())
                self.assertEqual(serialized["items"][0]["created_at"], fixture["created_at"])

    def test_response_does_not_expose_internal_extra_fields(self):
        result = VersionOut.model_validate({**_version_fixture(), "storage_path": "synthetic-private-path", "api_key": "synthetic-not-a-key"})
        self.assertNotIn("storage_path", result.model_dump())
        self.assertNotIn("api_key", result.model_dump())


class ApiAstContractTests(unittest.TestCase):
    """只核验真实路由源码结构，不把 AST 证据当 HTTP 端到端执行。"""

    EXPECTED = {
        ("get", "/versions/capabilities"): ("capabilities", "dict"),
        ("post", "/kbs/{kb_id}/document-series"): ("create_series", "SeriesOut"),
        ("get", "/document-series/{series_id}"): ("series_detail", "SeriesOut"),
        ("get", "/document-series/{series_id}/versions"): ("list_series_versions", "PageOut[VersionOut]"),
        ("get", "/documents/{version_id}/version"): ("version_detail", "VersionOut"),
        ("get", "/documents/{version_id}/revisions"): ("list_revisions", "PageOut[RevisionOut]"),
        ("patch", "/documents/{version_id}/metadata"): ("update_metadata", "VersionOut"),
        ("post", "/documents/{version_id}/publish"): ("publish_version", "VersionOut"),
        ("post", "/documents/{version_id}/withdraw"): ("withdraw_version", "VersionOut"),
        ("post", "/documents/{version_id}/archive"): ("archive_version", "VersionOut"),
        ("post", "/document-series/{series_id}/versions"): ("upload_series_version", "VersionOut"),
    }

    @classmethod
    def setUpClass(cls):
        path = BACKEND / "app/api/versions.py"
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
        cls.tree = ast.parse(source)
        cls.functions = {
            node.name: node for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        cls.route_list = []
        for function in cls.functions.values():
            for decorator in function.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and isinstance(decorator.func.value, ast.Name) and decorator.func.value.id == "router"):
                    cls.route_list.append(((decorator.func.attr, ast.literal_eval(decorator.args[0])), (function, decorator)))
        cls.routes = dict(cls.route_list)

    @staticmethod
    def defaults(function):
        return dict(zip((arg.arg for arg in function.args.args[-len(function.args.defaults):]), function.args.defaults))

    @staticmethod
    def service_calls(function):
        return [node for node in ast.walk(function) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "version_service"]

    def test_router_prefix_and_exact_method_path_set(self):
        routers = [node for node in ast.walk(self.tree) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Name) and node.func.id == "APIRouter"]
        self.assertEqual(len(routers), 1)
        options = {item.arg: ast.literal_eval(item.value) for item in routers[0].keywords}
        self.assertEqual(options["prefix"], "/api")
        self.assertEqual(len(self.route_list), 11)
        self.assertEqual(set(self.routes), set(self.EXPECTED))

    def test_all_routes_require_login_with_owner_checks_delegated_to_service(self):
        for (method, path), (function, _) in self.routes.items():
            with self.subTest(method=method, path=path):
                defaults = self.defaults(function)
                expected = "get_current_user"
                self.assertEqual(ast.unparse(defaults["current"]), f"Depends({expected})")
                self.assertEqual(ast.unparse(defaults["db"]), "Depends(get_db)")

    def test_response_models_use_frozen_structures_and_existing_pageout(self):
        for route, (_, expected) in self.EXPECTED.items():
            with self.subTest(route=route):
                decorator = self.routes[route][1]
                options = {item.arg: ast.unparse(item.value) for item in decorator.keywords}
                self.assertEqual(options["response_model"], expected)

    def test_every_route_calls_exact_frozen_service_function(self):
        keywords = {
            "capabilities": {},
            "create_series": {"kb_id": "kb_id", "title": "data.title", "business_key": "data.business_key"},
            "series_detail": {}, "version_detail": {},
            "list_series_versions": {"series_id": "series_id", "page": "page", "size": "size"},
            "list_revisions": {"version_id": "version_id", "page": "page", "size": "size"},
            "update_metadata": {"version_id": "version_id", "expected_revision": "data.expected_revision",
                                "reason": "data.reason", "changes": "data.model_dump(exclude_unset=True, exclude={'expected_revision', 'reason'})"},
            "publish_version": {"version_id": "version_id", "expected_revision": "data.expected_revision",
                                "reason": "data.reason", "acknowledge_unknown": "data.acknowledge_unknown"},
            "withdraw_version": {"version_id": "version_id", "expected_revision": "data.expected_revision", "reason": "data.reason"},
            "archive_version": {"version_id": "version_id", "expected_revision": "data.expected_revision", "reason": "data.reason"},
            "upload_series_version": {"series_id": "series_id", "filename": "file.filename", "content": "content",
                                      "version_label": "data.version_label", "expected_series_revision": "data.expected_series_revision",
                                      "supersedes_id": "data.supersedes_id"},
        }
        for route, (expected, _) in self.EXPECTED.items():
            with self.subTest(route=route):
                calls = self.service_calls(self.routes[route][0])
                self.assertEqual(len(calls), 1)
                call = calls[0]
                self.assertEqual(call.func.attr, expected)
                expected_args = ["db"] if expected == "capabilities" else ["db", "_actor(db, current)"]
                if expected in ("series_detail", "version_detail"):
                    expected_args.append("series_id" if expected == "series_detail" else "version_id")
                self.assertEqual([ast.unparse(arg) for arg in call.args], expected_args)
                self.assertEqual({kw.arg: ast.unparse(kw.value) for kw in call.keywords}, keywords[expected])

    def test_actor_is_loaded_from_database_using_authenticated_id(self):
        function = self.functions["_actor"]
        lookups = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute) and ast.unparse(node.func) == "db.get"]
        self.assertEqual(len(lookups), 1)
        self.assertEqual([ast.unparse(arg) for arg in lookups[0].args], ["User", "current.id"])

    def test_version_error_status_and_structured_detail_are_preserved(self):
        handler_function = self.functions["_service_errors"]
        handlers = [node for node in ast.walk(handler_function) if isinstance(node, ast.ExceptHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(ast.unparse(handlers[0].type), "version_service.VersionError")
        raises = [node for node in ast.walk(handlers[0]) if isinstance(node, ast.Raise)]
        self.assertEqual(len(raises), 1)
        error = raises[0].exc
        self.assertEqual(ast.unparse(error.func), "HTTPException")
        self.assertEqual({kw.arg: ast.unparse(kw.value) for kw in error.keywords},
                         {"status_code": "exc.status_code", "detail": "exc.detail"})
        for route, (function, _) in self.routes.items():
            with self.subTest(route=route):
                guarded = [node for node in ast.walk(function) if isinstance(node, ast.With)
                           and any(ast.unparse(item.context_expr) == "_service_errors()" for item in node.items)]
                self.assertEqual(len(guarded), 1)
                self.assertEqual(len(self.service_calls(guarded[0])), 1)

    def test_pagination_limits_are_declared_on_both_routes(self):
        for route in (("get", "/document-series/{series_id}/versions"), ("get", "/documents/{version_id}/revisions")):
            with self.subTest(route=route):
                defaults = self.defaults(self.routes[route][0])
                for name, default, bounds in (("page", 1, {"ge": 1}), ("size", 20, {"ge": 1, "le": 100})):
                    query = defaults[name]
                    self.assertEqual(ast.unparse(query.func), "Query")
                    self.assertEqual(ast.literal_eval(query.args[0]), default)
                    self.assertEqual({kw.arg: ast.literal_eval(kw.value) for kw in query.keywords}, bounds)

    def test_multipart_is_single_file_with_declared_fields(self):
        function = self.routes[("post", "/document-series/{series_id}/versions")][0]
        defaults = self.defaults(function)
        annotations = {arg.arg: ast.unparse(arg.annotation) for arg in function.args.args}
        self.assertEqual(annotations["file"], "UploadFile")
        self.assertEqual(ast.unparse(defaults["file"].func), "File")
        for key in ("version_label", "expected_series_revision", "supersedes_id"):
            self.assertEqual(ast.unparse(defaults[key].func), "Form")
        model_calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                       and isinstance(node.func, ast.Name) and node.func.id == "SeriesVersionUpload"]
        self.assertEqual(len(model_calls), 1)
        allowed = [node.value for node in function.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "allowed" for target in node.targets)]
        self.assertEqual(ast.literal_eval(allowed[0]), {"file", "version_label", "expected_series_revision", "supersedes_id"})
        for condition in ("unexpected", "repeated"):
            guarded = [node for node in function.body if isinstance(node, ast.If) and ast.unparse(node.test) == condition]
            self.assertEqual(len(guarded), 1)
            self.assertTrue(any(isinstance(node, ast.Raise) for node in ast.walk(guarded[0])))

    def test_upload_read_is_bounded_closed_and_explicitly_rejected_at_413(self):
        constants = [node.value for node in self.tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "MAX_UPLOAD_BYTES" for target in node.targets)]
        self.assertEqual(ast.unparse(constants[0]), "50 * 1024 * 1024")
        function = self.routes[("post", "/document-series/{series_id}/versions")][0]
        reads = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and ast.unparse(node.func) == "file.read"]
        self.assertEqual(len(reads), 1)
        self.assertEqual([ast.unparse(arg) for arg in reads[0].args], ["MAX_UPLOAD_BYTES + 1"])
        finally_calls = [node for block in ast.walk(function) if isinstance(block, ast.Try)
                         for statement in block.finalbody for node in ast.walk(statement)
                         if isinstance(node, ast.Call) and ast.unparse(node.func) == "file.close"]
        self.assertEqual(len(finally_calls), 1)
        overflow = [node for node in ast.walk(function) if isinstance(node, ast.If)
                    and ast.unparse(node.test) == "len(content) > MAX_UPLOAD_BYTES"]
        self.assertEqual(len(overflow), 1)
        error = overflow[0].body[0].exc
        self.assertEqual(ast.unparse(error.func), "HTTPException")
        self.assertEqual(next(ast.literal_eval(kw.value) for kw in error.keywords if kw.arg == "status_code"), 413)
        self.assertLess(reads[0].lineno, overflow[0].lineno)
        self.assertLess(overflow[0].lineno, self.service_calls(function)[0].lineno)


if __name__ == "__main__":
    unittest.main(verbosity=2)
