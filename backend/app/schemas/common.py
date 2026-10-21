"""通用与业务 Schema（Pydantic v2）。从 FastAPI 与内部引擎共用。"""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageReq(BaseModel):
    page: int = 1
    size: int = 20


class PageOut(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int


class MsgOut(BaseModel):
    message: str = "ok"


# 状态枚举透传（避免前端硬编码字符串，字段集中定义在 db.models.constants）
class EnumOut(BaseModel):
    key: str
    label: str


class EnumGroupOut(BaseModel):
    group: str
    items: list[EnumOut]
