"""问答契约：支持持久化引用映射和反馈状态回显。"""
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal
from pydantic import AliasChoices, BaseModel, Field, StringConstraints, model_validator
from .common import ORMModel

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

class SessionCreate(BaseModel):
    kb_id: int
    title: Title = '新对话'

class SessionRename(BaseModel):
    title: Title

class SessionOut(ORMModel):
    id: int
    user_id: int
    kb_id: int | None
    title: str
    status: str
    created_at: datetime
    updated_at: datetime

class CitationOut(ORMModel):
    rank: int
    chunk_id: int | None
    doc_id: int = Field(validation_alias=AliasChoices('doc_id', 'document_id'))
    doc_name: str
    page_no: int
    content_excerpt: str
    similarity: float

class RetrievalInfo(BaseModel):
    method: str
    vector_hits: int = 0
    keyword_hits: int = 0
    source_count: int = 0
    embedding_model: str = ''
    vector_status: str = ''
    total_documents: int = 0
    covered_documents: int = 0
    coverage_limited: bool = False

class MessageOut(ORMModel):
    id: int
    session_id: int
    role: str
    content: str
    question: str
    kb_id: int | None
    mode: str
    status: str
    latency_ms: int
    created_at: datetime
    citations: list[CitationOut] = Field(default_factory=list)
    feedback_type: str = ''
    generation_status: str = ''
    notice: str = ''

class QaReq(BaseModel):
    session_id: int | None = None
    kb_id: int
    question: Question
    create_new_session: bool = False

class QaResp(BaseModel):
    message_id: int
    session_id: int
    answer: str
    mode: str
    kb_name: str = ''
    citations: list[CitationOut]
    latency_ms: int
    degraded: bool = False
    notice: str = ''
    generation_status: str = ''
    retrieval: RetrievalInfo | None = None

class FeedbackCreate(BaseModel):
    message_id: int
    feedback_type: Literal['like', 'dislike', 'no_relevant', 'lack_source']
    comment: str = Field(default='', max_length=1000)

class FeedbackOut(ORMModel):
    id: int
    message_id: int
    user_id: int
    feedback_type: str
    comment: str
    status: str
    handler_note: str
    created_at: datetime
    username: str = ''
    question: str = ''
    answer: str = ''
    kb_id: int | None = None


class TranslationReq(BaseModel):
    text: str | None = None
    message_id: int | None = Field(default=None, ge=1)
    target_language: Literal['zh', 'en']

    @model_validator(mode='after')
    def source_is_unambiguous(self):
        if (self.text is None) == (self.message_id is None):
            raise ValueError('请选择粘贴文本或一条已有回答，不可同时提交')
        if self.text is not None and not self.text.strip():
            raise ValueError('请输入待翻译文本')
        return self


class TranslationResp(BaseModel):
    translation: str
    target_language: Literal['zh', 'en']
    source_message_id: int | None = None
    mode: str
    generation_status: Literal['generated'] = 'generated'
