"""W2 版本 HTTP 契约：严格请求与显式响应，不导入配置/数据库。

PATCH 只校验实际提供的字段。日期区间、unknown 与端点关系必须在服务
合并旧值后校验，不能在这里把局部补丁当完整版本。适用性不是事实可信度。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictBool, StringConstraints, field_validator

from .common import ORMModel
from ..services.version_policy import normalize_applicability

PositiveInteger = Annotated[int, Field(strict=True, gt=0)]
VersionLabel = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=80)]
ChangeReason = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)]
SeriesTitle = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255)]
BusinessKey = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=160)]
CoexistenceNote = Annotated[str, StringConstraints(strict=True, max_length=1000)]
PublicationState = Literal["draft", "published", "withdrawn", "archived"]
Validity = Literal["known", "unknown"]
DateStatus = Literal["current", "future", "expired", "unknown"]

_DATE_FORMAT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FORM_INTEGER = re.compile(r"[0-9]+")


def _strict_business_date(value: object) -> date:
    if isinstance(value, datetime):
        raise ValueError("business dates must not be datetime values")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or _DATE_FORMAT.fullmatch(value) is None:
        raise ValueError("business dates must be date objects or strict YYYY-MM-DD strings")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("business date is not a valid calendar date") from exc


def _form_integer(value: object) -> object:
    """multipart 整数来自文本：仅十进制数字，不接受浮点、符号或空白。"""
    if isinstance(value, str):
        if _FORM_INTEGER.fullmatch(value) is None:
            raise ValueError("form IDs and revisions must contain decimal digits only")
        return int(value)
    # Strict positive integer annotations reject bool, float and other types.
    return value


BusinessDate = Annotated[date, BeforeValidator(_strict_business_date)]
Applicability = Annotated[
    dict[str, list[str]],
    BeforeValidator(
        normalize_applicability,
        json_schema_input_type=dict[str, str | list[str]] | None,
    ),
]


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeriesCreate(_RequestModel):
    title: SeriesTitle
    business_key: BusinessKey | None = None

    @field_validator("business_key", mode="before")
    @classmethod
    def empty_business_key_is_unspecified(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        return value


class VersionAction(_RequestModel):
    expected_revision: PositiveInteger
    reason: ChangeReason


class VersionPublish(VersionAction):
    acknowledge_unknown: StrictBool = False


class MetadataPatch(_RequestModel):
    """可变字段白名单；请始终以 model_dump(exclude_unset=True) 交给服务。

    version_label/validity 的默认 None 仅作内部未提供占位，不校验默认值；
    显式传 null 仍由非空类型拒绝。其余 nullable 字段保留显式清空；
    applicability 的显式 null 按纯规则规范化成 {}，不是忽略本次更新。
    """

    version_label: VersionLabel = Field(default=None, validate_default=False)
    validity: Validity = Field(default=None, validate_default=False)
    effective_from: BusinessDate | None = None
    effective_to: BusinessDate | None = None
    applicability: Applicability = Field(default_factory=dict)
    supersedes_id: PositiveInteger | None = None
    coexistence_note: CoexistenceNote | None = None


class MetadataUpdate(VersionAction, MetadataPatch):
    """扁平 PATCH：并发/审计字段与可变元数据同级，不接受 changes 封装。"""


class SeriesVersionUpload(_RequestModel):
    """单文件 multipart 的元数据；文件本体由 API 的 UploadFile 接收。

    JSON 修改请求的 expected_revision 始终严格整数；只有此 form 模型
    显式解析十进制文本。空 supersedes_id 等价于 multipart 未指定值。
    """

    version_label: VersionLabel
    expected_series_revision: PositiveInteger
    supersedes_id: PositiveInteger | None = None

    @field_validator("expected_series_revision", mode="before")
    @classmethod
    def parse_series_revision(cls, value):
        return _form_integer(value)

    @field_validator("supersedes_id", mode="before")
    @classmethod
    def parse_optional_predecessor(cls, value):
        if value is None or value == "":
            return None
        return _form_integer(value)


class RevisionOut(ORMModel):
    id: int
    version_id: int
    task_id: int | None
    parser_version: str
    file_sha256: str | None
    content_hash: str
    source_integrity: Literal["verified", "legacy_unverified", "unverified"]
    state: Literal["building", "success", "failed", "legacy_empty"]
    is_current: bool
    chunk_count: int
    error_message: str
    created_at: datetime
    finished_at: datetime | None


class VersionOut(ORMModel):
    """date_status/eligibility_reasons 仅为适用判断，不代表来源/事实已核验。"""

    version_id: int
    kb_id: int
    series_id: int
    series_title: str
    series_revision: int
    filename: str
    file_type: str
    file_size: int
    version_label: str
    publication_state: PublicationState
    validity: Validity
    effective_from: BusinessDate | None
    effective_to: BusinessDate | None
    applicability: dict[str, list[str]]
    metadata_revision: int
    supersedes_id: int | None
    coexistence_note: str
    sha256: str
    is_legacy: bool
    date_status: DateStatus
    eligibility_reasons: list[str]
    can_manage: bool
    can_publish: bool
    current_revision: RevisionOut | None
    latest_revision: RevisionOut | None
    created_at: datetime
    updated_at: datetime


class SeriesOut(ORMModel):
    id: int
    kb_id: int
    title: str
    business_key: str | None
    owner_id: int
    state: Literal["active", "archived"]
    revision: int
    created_at: datetime
    updated_at: datetime
