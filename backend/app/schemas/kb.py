"""知识库、文档与解析任务的数据契约。"""
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal
from pydantic import BaseModel, Field, StringConstraints, field_validator
from .common import ORMModel, PageOut
from .versions import VersionOut

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
RoleName = Literal['admin', 'operator', 'user']

class KbCreate(BaseModel):
    name: Name
    description: str = Field(default='', max_length=1000)
    template_id: int | None = Field(default=None, deprecated=True, description='兼容旧客户端；行业模板选项不再生效')
    role_codes: list[RoleName] = Field(default_factory=list)
    import_samples: bool = Field(default=False, deprecated=True, description='兼容旧客户端；不再导入示范资料')

    @field_validator('role_codes')
    @classmethod
    def unique_roles(cls, value):
        return list(dict.fromkeys(value))

class KbUpdate(BaseModel):
    name: Name | None = None
    description: str | None = Field(default=None, max_length=1000)
    template_id: int | None = Field(default=None, deprecated=True, description='兼容旧客户端；行业模板选项不再生效')
    status: Literal['enabled', 'disabled'] | None = None

class KbGrantReq(BaseModel):
    role_codes: list[RoleName]

    @field_validator('role_codes')
    @classmethod
    def unique_roles(cls, value):
        return list(dict.fromkeys(value))

class KbOut(ORMModel):
    id: int
    name: str
    description: str
    template_id: int | None
    template_name: str = ''
    status: str
    owner_id: int
    can_manage: bool = False
    doc_count: int = 0
    chunk_count: int = 0
    granted_roles: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

class KbStatsOut(BaseModel):
    kb_id: int
    doc_count: int
    chunk_count: int
    success_docs: int
    failed_docs: int
    pending_docs: int
    last_upload_at: datetime | None = None

class DocOut(ORMModel):
    id: int
    kb_id: int
    filename: str
    file_type: str
    file_size: int
    status: str
    page_count: int
    error_message: str
    created_at: datetime
    chunk_count: int = 0
    is_new: bool | None = None
    version: VersionOut | None = None

class DocPageOut(PageOut[DocOut]):
    versioning_enabled: bool = False

class TaskOut(ORMModel):
    id: int
    document_id: int
    kb_id: int = 0
    kb_name: str = ''
    filename: str = ''
    phase: str
    progress: int
    chunk_count: int
    error_message: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime

class ChunkOut(ORMModel):
    id: int
    document_id: int
    seq_no: int
    content: str
    page_no: int
    meta_json: str

class PreviewPart(BaseModel):
    kind: str
    text: str
    page_no: int = 0
    meta: dict = Field(default_factory=dict)
