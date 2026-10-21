"""W2.0 纯规则 unittest；不加载应用配置，不访问业务数据库或外部能力。"""
from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services import version_policy
from app.services.version_policy import (
    VersionAssessment,
    applicability_overlap,
    assess_version,
    intervals_overlap,
    normalize_applicability,
    same_applicability,
)


def _assess(**overrides) -> VersionAssessment:
    inputs = {
        "publication_state": "published",
        "validity": "known",
        "as_of": "2024-06-15",
    }
    inputs.update(overrides)
    return assess_version(**inputs)


class DateRulesTests(unittest.TestCase):
    def test_before_start_is_future(self):
        result = _assess(effective_from="2024-06-16")
        self.assertEqual(result.date_status, "future")
        self.assertIs(result.eligible, False)
        self.assertEqual(result.reasons, ("date_future",))

    def test_start_day_is_included(self):
        result = _assess(effective_from="2024-06-15", effective_to="2024-06-16")
        self.assertEqual(result.date_status, "current")
        self.assertIs(result.eligible, True)
        self.assertEqual(result.reasons, ())

    def test_interior_day_is_current(self):
        result = _assess(effective_from="2024-06-01", effective_to="2024-07-01")
        self.assertEqual(result.date_status, "current")
        self.assertIs(result.eligible, True)

    def test_end_day_is_excluded(self):
        result = _assess(effective_from="2024-06-01", effective_to="2024-06-15")
        self.assertEqual(result.date_status, "expired")
        self.assertIs(result.eligible, False)
        self.assertEqual(result.reasons, ("date_expired",))

    def test_past_interval_is_expired(self):
        result = _assess(effective_from="2023-06-01", effective_to="2023-07-01")
        self.assertEqual(result.date_status, "expired")
        self.assertIs(result.eligible, False)

    def test_strings_and_date_objects_are_equivalent(self):
        strings = _assess(effective_from="2024-06-01", effective_to="2024-07-01")
        objects = _assess(
            effective_from=date(2024, 6, 1), effective_to=date(2024, 7, 1), as_of=date(2024, 6, 15),
        )
        mixed = _assess(effective_from=date(2024, 6, 1), effective_to="2024-07-01")
        self.assertEqual(strings, objects)
        self.assertEqual(strings, mixed)

    def test_explicit_known_with_no_endpoints_is_unbounded(self):
        for day in (date.min, date(2024, 6, 15), date.max):
            with self.subTest(day=day):
                result = _assess(as_of=day)
                self.assertEqual(result.date_status, "current")
                self.assertIs(result.eligible, True)

    def test_one_open_boundary(self):
        cases = (
            (None, "2024-06-16", "current", True),
            (None, "2024-06-15", "expired", False),
            ("2024-06-15", None, "current", True),
            ("2024-06-16", None, "future", False),
        )
        for start, end, status, eligible in cases:
            with self.subTest(start=start, end=end):
                result = _assess(effective_from=start, effective_to=end)
                self.assertEqual(result.date_status, status)
                self.assertIs(result.eligible, eligible)

    def test_unknown_is_the_default_not_implicit_unbounded(self):
        result = assess_version(publication_state="published", as_of="2024-06-15")
        self.assertEqual(result.date_status, "unknown")
        self.assertIs(result.eligible, False)
        self.assertEqual(result.reasons, ("date_unknown",))

    def test_included_unknown_still_reports_unknown_reason(self):
        result = _assess(validity="unknown", include_unknown=True)
        self.assertIs(result.eligible, True)
        self.assertEqual(result.date_status, "unknown")
        self.assertEqual(result.reasons, ("date_unknown",))

    def test_unknown_cannot_have_either_date(self):
        cases = (("2024-06-01", None), (None, date(2024, 7, 1)), ("2024-06-01", "2024-07-01"))
        for start, end in cases:
            for include in (False, True):
                with self.subTest(start=start, end=end, include=include):
                    with self.assertRaisesRegex(ValueError, "unknown"):
                        _assess(validity="unknown", effective_from=start, effective_to=end, include_unknown=include)

    def test_include_unknown_does_not_admit_future_or_expired(self):
        for end, start, status in ((None, "2024-07-01", "future"), ("2024-06-15", None, "expired")):
            with self.subTest(status=status):
                result = _assess(effective_from=start, effective_to=end, include_unknown=True)
                self.assertEqual(result.date_status, status)
                self.assertIs(result.eligible, False)

    def test_valid_leap_days(self):
        for year in (2000, 2024):
            with self.subTest(year=year):
                result = _assess(
                    effective_from=f"{year}-02-29", effective_to=f"{year}-03-01", as_of=date(year, 2, 29),
                )
                self.assertIs(result.eligible, True)
                self.assertEqual(result.date_status, "current")

    def test_dates_require_strict_ascii_calendar_format(self):
        invalid = (
            "", "2023-02-29", "1900-02-29", "2024-02-30", "2024-13-01", "2024-00-01", "2024-01-00",
            "0000-01-01", "10000-01-01", "2024-1-01", "2024-01-1", "20240615", "2024-W24-6",
            " 2024-06-15", "2024-06-15 ", "2024-06-15\n", "２０２４-０６-１５",
            "2024-06-15T00:00:00", "2024-06-15T00:00:00+08:00",
        )
        for field in ("effective_from", "effective_to", "as_of"):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, field):
                        _assess(**{field: value})

    def test_metadata_rejects_naive_and_aware_datetimes(self):
        for field in ("effective_from", "effective_to"):
            for value in (datetime(2024, 6, 15), datetime(2024, 6, 15, tzinfo=timezone.utc)):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "not a datetime"):
                        _assess(**{field: value})

    def test_dates_reject_other_types(self):
        for field in ("effective_from", "effective_to", "as_of"):
            for value in (0, True, 1.5, b"2024-06-15", [], {}, object()):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        _assess(**{field: value})

    def test_empty_and_reversed_intervals_are_invalid(self):
        for start, end in (("2024-06-15", "2024-06-15"), (date(2024, 6, 16), date(2024, 6, 15))):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, "earlier than"):
                    _assess(effective_from=start, effective_to=end)

    def test_invalid_validity_is_not_defaulted(self):
        for value in (None, "", "Known", " known", "current", 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "validity"):
                    _assess(validity=value)


class BusinessTimezoneTests(unittest.TestCase):
    def test_utc_to_shanghai_start_boundary(self):
        for instant, status in (
            (datetime(2024, 6, 14, 15, 59, 59, 999999, tzinfo=timezone.utc), "future"),
            (datetime(2024, 6, 14, 16, 0, tzinfo=timezone.utc), "current"),
        ):
            with self.subTest(instant=instant):
                result = _assess(effective_from="2024-06-15", as_of=instant)
                self.assertEqual(result.date_status, status)
                self.assertIs(result.eligible, status == "current")

    def test_utc_to_shanghai_end_boundary(self):
        for instant, status in (
            (datetime(2024, 6, 14, 15, 59, 59, 999999, tzinfo=timezone.utc), "current"),
            (datetime(2024, 6, 14, 16, 0, tzinfo=timezone.utc), "expired"),
        ):
            with self.subTest(instant=instant):
                result = _assess(effective_to="2024-06-15", as_of=instant)
                self.assertEqual(result.date_status, status)
                self.assertIs(result.eligible, status == "current")

    def test_different_aware_offsets_describe_same_instant(self):
        instant = datetime(2024, 6, 14, 16, tzinfo=timezone.utc)
        expected = _assess(effective_from="2024-06-15", as_of=instant)
        for hours in (-5, 8, 10):
            with self.subTest(hours=hours):
                other = instant.astimezone(timezone(timedelta(hours=hours)))
                self.assertEqual(_assess(effective_from="2024-06-15", as_of=other), expected)

    def test_explicit_timezone_controls_business_day(self):
        instant = datetime(2024, 6, 14, 16, tzinfo=timezone.utc)
        shanghai = _assess(effective_from="2024-06-15", as_of=instant)
        utc = _assess(effective_from="2024-06-15", as_of=instant, timezone_name="UTC")
        self.assertEqual(shanghai.date_status, "current")
        self.assertEqual(utc.date_status, "future")

    def test_date_inputs_are_not_shifted_by_timezone(self):
        for day in ("2024-06-15", date(2024, 6, 15)):
            with self.subTest(day=day):
                self.assertEqual(
                    _assess(as_of=day, effective_from="2024-06-15"),
                    _assess(as_of=day, effective_from="2024-06-15", timezone_name="UTC"),
                )

    def test_none_and_omitted_as_of_use_today_in_requested_zone(self):
        seen_zones = []
        instant = datetime(2024, 6, 14, 16, tzinfo=timezone.utc)

        class FrozenClock(datetime):
            @classmethod
            def now(cls, tz=None):
                seen_zones.append(tz.key)
                return instant.astimezone(tz)

        with patch.object(version_policy, "datetime", FrozenClock):
            default = assess_version(
                publication_state="published", validity="known", effective_from="2024-06-15",
            )
            explicit_none = _assess(as_of=None, effective_from="2024-06-15")
            utc = _assess(as_of=None, effective_from="2024-06-15", timezone_name="UTC")
        self.assertEqual(default, explicit_none)
        self.assertEqual(default.date_status, "current")
        self.assertEqual(utc.date_status, "future")
        self.assertEqual(seen_zones, ["Asia/Shanghai", "Asia/Shanghai", "UTC"])

    def test_naive_datetime_and_none_utc_offset_are_rejected(self):
        class NoOffset(tzinfo):
            def utcoffset(self, dt):
                return None

        for instant in (datetime(2024, 6, 15), datetime(2024, 6, 15, tzinfo=NoOffset())):
            with self.subTest(instant=instant):
                with self.assertRaisesRegex(ValueError, "timezone-aware"):
                    _assess(as_of=instant)

    def test_invalid_timezone_is_clear_even_for_date_input(self):
        for name in ("NoSuch/Zone", "", None, 8, [], "../UTC", "/UTC", "Asia//Shanghai", " UTC "):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "timezone_name"):
                    _assess(timezone_name=name)

    def test_unrepresentable_timezone_conversion_raises_value_error(self):
        with self.assertRaisesRegex(ValueError, "cannot be represented"):
            _assess(as_of=datetime.max.replace(tzinfo=timezone.utc))


class PublicationRulesTests(unittest.TestCase):
    def test_only_published_is_eligible(self):
        for state in ("draft", "published", "withdrawn", "archived"):
            with self.subTest(state=state):
                result = _assess(publication_state=state)
                self.assertIs(result.eligible, state == "published")
                self.assertEqual(result.date_status, "current")
                self.assertEqual(result.applicability_status, "match")
                expected = () if state == "published" else (f"not_published:{state}",)
                self.assertEqual(result.reasons, expected)

    def test_invalid_publication_states_are_rejected(self):
        for state in (None, "", "success", "pending", "Published", "published ", True, [], {}):
            with self.subTest(state=state):
                with self.assertRaisesRegex(ValueError, "publication_state"):
                    _assess(publication_state=state)

    def test_include_unknown_never_bypasses_publication(self):
        for state in ("draft", "withdrawn", "archived"):
            with self.subTest(state=state):
                result = _assess(publication_state=state, validity="unknown", include_unknown=True)
                self.assertIs(result.eligible, False)
                self.assertEqual(result.date_status, "unknown")
                self.assertEqual(result.reasons, (f"not_published:{state}", "date_unknown"))

    def test_include_unknown_requires_bool(self):
        for value in (None, 0, 1, "false", "true", [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "include_unknown"):
                    _assess(include_unknown=value)

    def test_ineligibility_does_not_hide_invalid_inputs(self):
        cases = (
            {"publication_state": "draft", "effective_from": "bad"},
            {"publication_state": "archived", "applicability": {"对象": []}},
            {"validity": "unknown", "conditions": {"对象": None}},
            {"effective_from": "2025-01-01", "conditions": "学生"},
            {"publication_state": "withdrawn", "as_of": datetime(2024, 6, 15)},
            {"validity": "unknown", "timezone_name": "NoSuch/Zone"},
        )
        for inputs in cases:
            with self.subTest(inputs=inputs):
                with self.assertRaises(ValueError):
                    _assess(**inputs)


class ApplicabilityNormalizationTests(unittest.TestCase):
    def test_none_and_empty_mapping_are_unrestricted(self):
        for value in (None, {}, MappingProxyType({})):
            with self.subTest(value=value):
                self.assertEqual(normalize_applicability(value), {})

    def test_trim_deduplicate_and_sort_keys_and_values(self):
        value = {" z ": [" b ", "a", "b", " a"], " a ": " x "}
        normalized = normalize_applicability(value)
        self.assertEqual(normalized, {"a": ["x"], "z": ["a", "b"]})
        self.assertEqual(list(normalized), ["a", "z"])
        self.assertEqual(normalize_applicability(normalized), normalized)
        self.assertEqual(normalize_applicability(MappingProxyType(value)), normalized)

    def test_normalization_is_pure_and_returns_detached_containers(self):
        source = {" audience ": ["staff", "student", "staff"]}
        original = deepcopy(source)
        normalized = normalize_applicability(source)
        self.assertEqual(source, original)
        normalized["audience"].append("guest")
        normalized["other"] = ["extra"]
        self.assertEqual(source, original)

    def test_maximum_twenty_keys(self):
        self.assertEqual(len(normalize_applicability({f"k{i}": "v" for i in range(20)})), 20)
        with self.assertRaisesRegex(ValueError, "20 keys"):
            normalize_applicability({f"k{i}": "v" for i in range(21)})

    def test_key_length_limit_counts_trimmed_characters(self):
        key = "类" * 64
        self.assertEqual(normalize_applicability({f" {key} ": "值"}), {key: ["值"]})
        with self.assertRaisesRegex(ValueError, "64 characters"):
            normalize_applicability({"类" * 65: "值"})

    def test_candidate_length_limit_counts_trimmed_characters(self):
        value = "值" * 120
        self.assertEqual(normalize_applicability({"分类": f" {value} "}), {"分类": [value]})
        for values in ("值" * 121, ["值" * 121]):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "120 characters"):
                    normalize_applicability({"分类": values})

    def test_candidate_count_limit_applies_before_deduplication(self):
        values = [f"v{i:02}" for i in range(20)]
        self.assertEqual(normalize_applicability({"k": values}), {"k": values})
        self.assertEqual(normalize_applicability({"k": ["v"] * 20}), {"k": ["v"]})
        for values in ([f"v{i}" for i in range(21)], ["v"] * 21):
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "1 to 20"):
                    normalize_applicability({"k": values})

    def test_top_level_requires_mapping_not_falsey_or_string_values(self):
        for value in ("", "学生", [], (), set(), 0, False, 1.0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "flat mapping"):
                    normalize_applicability(value)

    def test_invalid_keys_are_rejected(self):
        for key in ("", " \t\n", 1, None, False, ("k",)):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    normalize_applicability({key: "v"})

    def test_invalid_empty_nested_or_nonstring_values_are_rejected(self):
        invalid = (
            "", " \t\n", [], None, 1, False, {}, {"nested": "x"}, ("v",), {"v"}, b"v",
            ["v", 1], ["v", None], ["v", " "], ["v", []], ["v", {"nested": "x"}], [False],
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_applicability({"k": value})

    def test_trimmed_key_collisions_are_not_silently_merged(self):
        for other in ("student", "staff"):
            with self.subTest(other=other):
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    normalize_applicability({" audience ": "student", "audience": other})

    def test_no_case_or_semantic_alias_inference(self):
        self.assertEqual(normalize_applicability({"Role": ["Student", "student"]}),
                         {"Role": ["Student", "student"]})
        self.assertIs(same_applicability({"身份": "学生"}, {"身份": "在校生"}), False)


class ApplicabilityAssessmentTests(unittest.TestCase):
    def test_unrestricted_accepts_no_or_extra_conditions(self):
        for requirements in (None, {}):
            for supplied in (None, {}, {"role": "admin"}):
                with self.subTest(requirements=requirements, supplied=supplied):
                    result = _assess(applicability=requirements, conditions=supplied)
                    self.assertIs(result.eligible, True)
                    self.assertEqual(result.applicability_status, "match")
                    self.assertEqual(result.missing_conditions, ())

    def test_explicit_scalar_condition_matches(self):
        result = _assess(applicability={"业务身份": ["教师", "学生"]}, conditions={"业务身份": " 学生 "})
        self.assertIs(result.eligible, True)
        self.assertEqual(result.applicability_status, "match")
        self.assertEqual(result.reasons, ())

    def test_or_within_category_and_and_between_categories(self):
        required = {"身份": ["教师", "学生"], "地区": ["上海", "杭州"]}
        for area, expected in ((["杭州", "苏州"], "match"), (["北京", "苏州"], "mismatch")):
            with self.subTest(area=area):
                result = _assess(applicability=required, conditions={"身份": ["学生", "访客"], "地区": area})
                self.assertEqual(result.applicability_status, expected)
                self.assertIs(result.eligible, expected == "match")

    def test_different_population_is_mismatch_not_missing(self):
        result = _assess(applicability={"身份": "学生"}, conditions={"身份": "教师"})
        self.assertIs(result.eligible, False)
        self.assertEqual(result.applicability_status, "mismatch")
        self.assertEqual(result.missing_conditions, ())
        self.assertEqual(result.reasons, ("mismatched_condition:身份",))

    def test_missing_categories_are_sorted_not_guessed(self):
        result = _assess(applicability={"z": "v", " a ": "v", "middle": "v"}, conditions={"middle": "v"})
        self.assertIs(result.eligible, False)
        self.assertEqual(result.applicability_status, "missing")
        self.assertEqual(result.missing_conditions, ("a", "z"))
        self.assertEqual(result.reasons, ("missing_condition:a", "missing_condition:z"))

    def test_missing_and_mismatch_both_survive_independent_rejections(self):
        result = _assess(
            publication_state="draft", effective_from="2024-07-01",
            applicability={"z": "yes", "a": "yes", "b": "yes", "y": "yes"},
            conditions={"y": "no", "b": "no"},
        )
        self.assertIs(result.eligible, False)
        self.assertEqual(result.date_status, "future")
        self.assertEqual(result.applicability_status, "mismatch")
        self.assertEqual(result.missing_conditions, ("a", "z"))
        self.assertEqual(result.reasons, (
            "not_published:draft", "date_future", "missing_condition:a", "missing_condition:z",
            "mismatched_condition:b", "mismatched_condition:y",
        ))

    def test_opposite_explicit_condition_is_not_match(self):
        result = _assess(applicability={"是否在职": "是"}, conditions={"是否在职": "否"})
        self.assertEqual(result.applicability_status, "mismatch")
        self.assertIs(result.eligible, False)

    def test_technical_role_has_no_implicit_business_identity(self):
        for role in ("admin", "operator", "user", "student", "学生"):
            with self.subTest(role=role):
                result = _assess(applicability={"业务身份": "学生"}, conditions={"role": role})
                self.assertIs(result.eligible, False)
                self.assertEqual(result.applicability_status, "missing")
                self.assertEqual(result.missing_conditions, ("业务身份",))

    def test_extra_technical_role_does_not_override_explicit_identity(self):
        for role in ("admin", "user", "教师"):
            with self.subTest(role=role):
                result = _assess(applicability={"业务身份": "学生"}, conditions={"业务身份": "学生", "role": role})
                self.assertIs(result.eligible, True)
                self.assertEqual(result.applicability_status, "match")

    def test_invalid_conditions_are_errors_even_when_unrestricted(self):
        for conditions in ("学生", {"身份": []}, {"身份": None}, {"身份": ["学生", " "]}):
            with self.subTest(conditions=conditions):
                with self.assertRaises(ValueError):
                    _assess(conditions=conditions)

    def test_unknown_inclusion_never_bypasses_conditions(self):
        cases = ((None, "missing"), ({"身份": "教师"}, "mismatch"), ({"身份": "学生"}, "match"))
        for conditions, status in cases:
            for include in (False, True):
                with self.subTest(conditions=conditions, include=include):
                    result = _assess(
                        validity="unknown", applicability={"身份": "学生"},
                        conditions=conditions, include_unknown=include,
                    )
                    self.assertEqual(result.applicability_status, status)
                    self.assertEqual(result.date_status, "unknown")
                    self.assertIn("date_unknown", result.reasons)
                    self.assertIs(result.eligible, include and status == "match")

    def test_assessment_does_not_mutate_business_conditions(self):
        required = {"身份": [" 学生 ", "教师", "学生"]}
        supplied = {" 身份 ": [" 学生 "]}
        before = deepcopy((required, supplied))
        _assess(applicability=required, conditions=supplied)
        self.assertEqual((required, supplied), before)


class IntervalOverlapTests(unittest.TestCase):
    def test_touching_half_open_intervals_do_not_overlap(self):
        a, b = ("2024-06-01", "2024-06-15"), ("2024-06-15", "2024-07-01")
        self.assertIs(intervals_overlap(*a, *b), False)
        self.assertIs(intervals_overlap(*b, *a), False)

    def test_crossing_contained_and_identical_intervals_overlap(self):
        a = ("2024-06-01", "2024-07-01")
        for b in (("2024-06-15", "2024-08-01"), (date(2024, 6, 15), date(2024, 6, 16)), a):
            with self.subTest(b=b):
                self.assertIs(intervals_overlap(*a, *b), True)
                self.assertIs(intervals_overlap(*b, *a), True)

    def test_separated_intervals_do_not_overlap(self):
        a, b = ("2024-06-01", "2024-06-15"), ("2024-07-01", "2024-08-01")
        self.assertIs(intervals_overlap(*a, *b), False)
        self.assertIs(intervals_overlap(*b, *a), False)

    def test_open_boundaries(self):
        cases = (
            ((None, None), (None, None), True),
            ((None, None), ("2024-06-01", "2024-07-01"), True),
            ((None, "2024-06-15"), ("2024-06-15", None), False),
            ((None, "2024-06-16"), ("2024-06-15", None), True),
            ((None, "2024-06-15"), (None, "2024-07-01"), True),
            (("2024-06-15", None), ("2024-07-01", None), True),
        )
        for a, b, expected in cases:
            with self.subTest(a=a, b=b):
                self.assertIs(intervals_overlap(*a, *b), expected)
                self.assertIs(intervals_overlap(*b, *a), expected)

    def test_unknown_result_is_none_on_either_side(self):
        for a_validity, b_validity in (("unknown", "known"), ("known", "unknown"), ("unknown", "unknown")):
            with self.subTest(a_validity=a_validity, b_validity=b_validity):
                self.assertIsNone(intervals_overlap(
                    None, None, None, None, a_validity=a_validity, b_validity=b_validity,
                ))
        self.assertIsNone(intervals_overlap(None, None, "2024-06-01", "2024-07-01", a_validity="unknown"))

    def test_unknown_does_not_hide_invalid_either_interval(self):
        cases = (
            (("2024-06-01", None, None, None), {"a_validity": "unknown"}),
            ((None, None, None, "2024-07-01"), {"b_validity": "unknown"}),
            ((None, None, "bad", None), {"a_validity": "unknown"}),
            ((None, None, "2024-07-01", "2024-06-01"), {"a_validity": "unknown"}),
            (("bad", None, None, None), {"b_validity": "unknown"}),
            ((None, None, None, None), {"a_validity": "unknown", "b_validity": "bad"}),
        )
        for dates, options in cases:
            with self.subTest(dates=dates, options=options):
                with self.assertRaises(ValueError):
                    intervals_overlap(*dates, **options)

    def test_both_sides_validate_empty_and_reversed_ranges(self):
        for pair in (("2024-06-15", "2024-06-15"), ("2024-06-16", "2024-06-15")):
            for args in ((*pair, None, None), (None, None, *pair)):
                with self.subTest(args=args):
                    with self.assertRaisesRegex(ValueError, "earlier than"):
                        intervals_overlap(*args)

    def test_all_overlap_date_arguments_use_metadata_rules(self):
        for position in range(4):
            for invalid in ("2024-6-15", "2023-02-29", datetime(2024, 6, 15, tzinfo=timezone.utc), 0, ""):
                arguments = [None] * 4
                arguments[position] = invalid
                with self.subTest(position=position, invalid=invalid):
                    with self.assertRaises(ValueError):
                        intervals_overlap(*arguments)
        for key in ("a_validity", "b_validity"):
            for invalid in (None, "", "current", [], True):
                with self.subTest(key=key, invalid=invalid):
                    with self.assertRaises(ValueError):
                        intervals_overlap(None, None, None, None, **{key: invalid})

    def test_small_grid_matches_independent_daily_membership_oracle(self):
        origin = date(2024, 6, 1)
        boundaries = [None] + [origin + timedelta(days=i) for i in range(5)]
        intervals = [(a, b) for a in boundaries for b in boundaries if a is None or b is None or a < b]
        days = [origin + timedelta(days=i) for i in range(-1, 6)]
        for a_from, a_to in intervals:
            for b_from, b_to in intervals:
                expected = any(
                    (a_from is None or a_from <= day) and (a_to is None or day < a_to)
                    and (b_from is None or b_from <= day) and (b_to is None or day < b_to)
                    for day in days
                )
                with self.subTest(a=(a_from, a_to), b=(b_from, b_to)):
                    self.assertIs(intervals_overlap(a_from, a_to, b_from, b_to), expected)


class ApplicabilityOverlapTests(unittest.TestCase):
    def test_empty_mapping_has_broad_overlap(self):
        for empty in (None, {}):
            for other in (None, {}, {"身份": "学生"}):
                with self.subTest(empty=empty, other=other):
                    self.assertIs(applicability_overlap(empty, other), True)
                    self.assertIs(applicability_overlap(other, empty), True)

    def test_disjoint_population_or_any_common_category_prevents_overlap(self):
        cases = (
            ({"身份": "学生"}, {"身份": "教师"}),
            ({"身份": ["学生", "教师"], "地区": "上海"}, {"身份": "学生", "地区": "北京"}),
        )
        for a, b in cases:
            with self.subTest(a=a, b=b):
                self.assertIs(applicability_overlap(a, b), False)
                self.assertIs(applicability_overlap(b, a), False)

    def test_shared_candidate_allows_overlap(self):
        a, b = {" 身份 ": ["学生", "教师"]}, {"身份": [" 学生 ", "访客"]}
        self.assertIs(applicability_overlap(a, b), True)
        self.assertIs(applicability_overlap(b, a), True)
        self.assertIs(same_applicability(a, b), False)

    def test_no_common_key_means_possible_overlap_not_semantic_conflict(self):
        self.assertIs(applicability_overlap({"身份": "学生"}, {"地区": "上海"}), True)
        self.assertIs(same_applicability({"身份": "学生"}, {"地区": "上海"}), False)

    def test_same_applicability_uses_full_normalized_equality(self):
        a = {" z ": ["b", "a", "b"], "a": " x "}
        b = {"a": ["x"], "z": ["a", "b"]}
        self.assertIs(same_applicability(a, b), True)
        self.assertIs(same_applicability(None, {}), True)
        self.assertIs(same_applicability({"身份": ["学生", "教师"]}, {"身份": "学生"}), False)
        self.assertIs(same_applicability({}, {"身份": "学生"}), False)
        self.assertIs(same_applicability({"a": "x"}, {"a": "x", "b": "y"}), False)

    def test_overlap_helpers_validate_both_inputs(self):
        for function in (applicability_overlap, same_applicability):
            for invalid in ([], {"身份": []}, {"身份": {"nested": "学生"}}):
                for a, b in (({}, invalid), (invalid, {})):
                    with self.subTest(function=function.__name__, a=a, b=b):
                        with self.assertRaises(ValueError):
                            function(a, b)

    def test_overlap_helpers_do_not_mutate_inputs(self):
        a, b = {" 身份 ": ["学生", "教师", "学生"]}, {"身份": ["学生"]}
        original = deepcopy((a, b))
        applicability_overlap(a, b)
        same_applicability(a, b)
        self.assertEqual((a, b), original)


class AssessmentContractTests(unittest.TestCase):
    def test_result_is_frozen_dataclass_with_immutable_collections(self):
        result = _assess(applicability={"身份": "学生"})
        self.assertIsInstance(result, VersionAssessment)
        self.assertTrue(is_dataclass(result))
        self.assertIsInstance(result.missing_conditions, tuple)
        self.assertIsInstance(result.reasons, tuple)
        with self.assertRaises(FrozenInstanceError):
            result.eligible = True
        with self.assertRaises(FrozenInstanceError):
            result.reasons += ("pretend_verified",)
        self.assertEqual(hash(result), hash(_assess(applicability={"身份": "学生"})))

    def test_result_exposes_applicability_only_not_source_or_fact_trust(self):
        result = _assess()
        self.assertIs(result.eligible, True)
        self.assertEqual(tuple(field.name for field in fields(result)), (
            "eligible", "date_status", "applicability_status", "missing_conditions", "reasons",
        ))
        for unsupported in ("trusted", "source_verified", "fact_verified", "authorized", "confidence"):
            with self.subTest(unsupported=unsupported):
                self.assertFalse(hasattr(result, unsupported))

    def test_current_status_is_recomputed_not_permanent(self):
        early = _assess(effective_from="2024-06-15", effective_to="2024-06-16", as_of="2024-06-14")
        current = _assess(effective_from="2024-06-15", effective_to="2024-06-16", as_of="2024-06-15")
        late = _assess(effective_from="2024-06-15", effective_to="2024-06-16", as_of="2024-06-16")
        self.assertEqual((early.date_status, current.date_status, late.date_status), ("future", "current", "expired"))
        self.assertEqual((early.eligible, current.eligible, late.eligible), (False, True, False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
