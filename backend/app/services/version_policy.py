"""纯日期与分类适用规则（W2.0），不访问配置、数据库或模型。

``eligible`` 仅表示发布状态、业务日期和显式业务条件允许成为候选；
它不表示已获访问权限、解析成功、来源可信、事实正确或证据支持充分。
业务区间采用 [start, end)，分类重叠也不等于内容存在语义冲突。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "VersionAssessment",
    "normalize_applicability",
    "assess_version",
    "intervals_overlap",
    "applicability_overlap",
    "same_applicability",
]

_DateInput = date | str | None
_ApplicabilityInput = Mapping[str, str | list[str]] | None
_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_PUBLICATION_STATES = ("draft", "published", "withdrawn", "archived")
_VALIDITIES = ("unknown", "known")


@dataclass(frozen=True)
class VersionAssessment:
    """适用性结果，不携带或隐含权限、来源/事实可信度结论。

    ``reasons`` 是稳定的机器可读代码，依次为发布、日期、缺项、不匹配：
    ``not_published:<state>``、``date_future``、``date_expired``、
    ``date_unknown``、``missing_condition:<key>``、
    ``mismatched_condition:<key>``。各类条件原因按规范化后的键排序。
    ``include_unknown=True`` 不会删除 ``date_unknown`` 原因。
    """

    eligible: bool
    date_status: Literal["current", "future", "expired", "unknown"]
    applicability_status: Literal["match", "missing", "mismatch"]
    missing_conditions: tuple[str, ...]
    reasons: tuple[str, ...]


def _parse_date(value: _DateInput, *, field: str) -> date | None:
    """严格解析业务日期；datetime 虽是 date 子类，也不能作为元数据。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError(f"{field} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field} must be a date or a strict YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid calendar date: {value!r}") from exc


def _validate_interval(
    start: _DateInput,
    end: _DateInput,
    validity: str,
    *,
    field: str,
) -> tuple[date | None, date | None]:
    if not isinstance(validity, str) or validity not in _VALIDITIES:
        raise ValueError(f"{field}.validity must be 'unknown' or 'known'")
    start_date = _parse_date(start, field=f"{field}.effective_from")
    end_date = _parse_date(end, field=f"{field}.effective_to")
    if validity == "unknown" and (start_date is not None or end_date is not None):
        raise ValueError(f"{field}: validity='unknown' requires both dates to be None")
    if start_date is not None and end_date is not None and start_date >= end_date:
        raise ValueError(f"{field}: effective_from must be earlier than effective_to")
    return start_date, end_date


def _business_date(as_of: date | datetime | str | None, timezone_name: str) -> date:
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError("timezone_name must be a non-empty IANA timezone name")
    try:
        zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Invalid timezone_name: {timezone_name!r}") from exc

    if as_of is None:
        return datetime.now(zone).date()
    if isinstance(as_of, datetime):
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of datetime must be timezone-aware; naive datetimes are rejected")
        try:
            return as_of.astimezone(zone).date()
        except (OverflowError, ValueError) as exc:
            raise ValueError("as_of cannot be represented in the requested timezone") from exc
    business_day = _parse_date(as_of, field="as_of")
    # None was handled above; no host-local date or guessed timezone is used.
    assert business_day is not None
    return business_day


def normalize_applicability(value: _ApplicabilityInput) -> dict[str, list[str]]:
    """规范化受限的扁平分类映射；None/{} 表示不限条件。

    键和值先 strip，区分大小写且不做同义词或身份推断；长度按 strip 后
    的 Unicode 字符计。最多 20 个原始键，每键最多 20 个原始候选；键长
    <=64，候选长 <=120。仅接受字符串或非空字符串列表作为映射值。
    trim 后重名键拒绝，防止静默覆盖/扩大范围。候选去重后与键均稳定
    排序，返回全新字典/列表，不修改调用方数据。
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("applicability must be a flat mapping or None")
    if len(value) > 20:
        raise ValueError("applicability allows at most 20 keys")

    normalized: dict[str, list[str]] = {}
    for raw_key, raw_values in value.items():
        if not isinstance(raw_key, str):
            raise ValueError("applicability keys must be strings")
        key = raw_key.strip()
        if not key or len(key) > 64:
            raise ValueError("applicability keys must contain 1 to 64 characters after trimming")
        if key in normalized:
            raise ValueError(f"Duplicate applicability key after trimming: {key!r}")

        if isinstance(raw_values, str):
            values = [raw_values]
        elif isinstance(raw_values, list):
            values = raw_values
        else:
            raise ValueError(f"applicability[{key!r}] must be a string or a non-empty string list")
        if not 1 <= len(values) <= 20:
            raise ValueError(f"applicability[{key!r}] must have 1 to 20 candidate values")

        candidates: set[str] = set()
        for raw_candidate in values:
            if not isinstance(raw_candidate, str):
                raise ValueError(f"applicability[{key!r}] candidates must be strings")
            candidate = raw_candidate.strip()
            if not candidate or len(candidate) > 120:
                raise ValueError(
                    f"applicability[{key!r}] candidates must contain 1 to 120 characters after trimming"
                )
            candidates.add(candidate)
        normalized[key] = sorted(candidates)
    return {key: normalized[key] for key in sorted(normalized)}


def assess_version(
    *,
    publication_state: str,
    validity: str = "unknown",
    effective_from: _DateInput = None,
    effective_to: _DateInput = None,
    applicability: _ApplicabilityInput = None,
    as_of: date | datetime | str | None = None,
    conditions: _ApplicabilityInput = None,
    timezone_name: str = "Asia/Shanghai",
    include_unknown: bool = False,
) -> VersionAssessment:
    """按发布状态、半开业务日期区间和显式分类条件评估候选资格。

    known 且两端 None 表示明确确认不限期；unknown 必须两端 None，默认
    不进入候选。include_unknown 仅放宽未知日期，不放宽其它限制。
    同一分类内候选取 OR，分类间取 AND；额外条件不影响未限定的分类。
    条件采用与 applicability 相同的映射格式（值可为字符串），不会从
    技术 role、权限或其他用户属性补全业务身份。若既缺项又有不匹配，
    mismatch 优先，但 missing_conditions 和两类原因都完整保留。
    所有输入均先验证，即使另一个限制已经阻止候选也不掩盖非法数据。
    """
    if not isinstance(publication_state, str) or publication_state not in _PUBLICATION_STATES:
        raise ValueError("publication_state must be draft, published, withdrawn or archived")
    if not isinstance(include_unknown, bool):
        raise ValueError("include_unknown must be a bool")
    start, end = _validate_interval(effective_from, effective_to, validity, field="version")
    business_day = _business_date(as_of, timezone_name)
    required = normalize_applicability(applicability)
    supplied = normalize_applicability(conditions)

    date_status: Literal["current", "future", "expired", "unknown"]
    if validity == "unknown":
        date_status = "unknown"
    elif start is not None and business_day < start:
        date_status = "future"
    elif end is not None and business_day >= end:
        date_status = "expired"
    else:
        date_status = "current"

    missing = tuple(key for key in required if key not in supplied)
    mismatched = tuple(
        key for key in required
        if key in supplied and set(required[key]).isdisjoint(supplied[key])
    )
    applicability_status: Literal["match", "missing", "mismatch"]
    if mismatched:
        applicability_status = "mismatch"
    elif missing:
        applicability_status = "missing"
    else:
        applicability_status = "match"

    reasons: list[str] = []
    if publication_state != "published":
        reasons.append(f"not_published:{publication_state}")
    if date_status != "current":
        reasons.append(f"date_{date_status}")
    reasons.extend(f"missing_condition:{key}" for key in missing)
    reasons.extend(f"mismatched_condition:{key}" for key in mismatched)

    date_allowed = date_status == "current" or (date_status == "unknown" and include_unknown)
    return VersionAssessment(
        eligible=publication_state == "published" and date_allowed and applicability_status == "match",
        date_status=date_status,
        applicability_status=applicability_status,
        missing_conditions=missing,
        reasons=tuple(reasons),
    )


def intervals_overlap(
    a_from: _DateInput,
    a_to: _DateInput,
    b_from: _DateInput,
    b_to: _DateInput,
    *,
    a_validity: str = "known",
    b_validity: str = "known",
) -> bool | None:
    """判断两个 [from, to) 区间重叠；None 端点开放，未知区间返回 None。

    两个区间都会验证，未知状态不掩盖另一侧非法数据。只判断业务日期
    重叠，不表示版本可发布/可替代，也不是语义冲突结论。
    """
    a_start, a_end = _validate_interval(a_from, a_to, a_validity, field="a")
    b_start, b_end = _validate_interval(b_from, b_to, b_validity, field="b")
    if a_validity == "unknown" or b_validity == "unknown":
        return None
    if a_end is not None and b_start is not None and a_end <= b_start:
        return False
    if b_end is not None and a_start is not None and b_end <= a_start:
        return False
    return True


def applicability_overlap(a: _ApplicabilityInput, b: _ApplicabilityInput) -> bool:
    """类别上是否可能重叠：任一共同键候选不相交就 False，否则 True。

    空映射广泛适用，互不共享分类也可能重叠；不作分类间关联推理，
    更不判断两个版本正文是否存在矛盾或来源/事实是否可信。
    """
    left = normalize_applicability(a)
    right = normalize_applicability(b)
    return all(not set(left[key]).isdisjoint(right[key]) for key in left.keys() & right.keys())


def same_applicability(a: _ApplicabilityInput, b: _ApplicabilityInput) -> bool:
    """去空白、去重和排序后严格相等；仅有交集或包含关系不算相同。"""
    return normalize_applicability(a) == normalize_applicability(b)
