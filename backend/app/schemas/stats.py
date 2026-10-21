"""运营统计 Schema。"""
from __future__ import annotations

from pydantic import BaseModel


class FeedbackStats(BaseModel):
    like: int = 0
    dislike: int = 0
    no_relevant: int = 0
    lack_source: int = 0
    total: int = 0


class ModeDist(BaseModel):
    local: int = 0
    online: int = 0
    degraded: int = 0
    none: int = 0


class KbDist(BaseModel):
    kb_id: int
    kb_name: str
    qa_count: int


class CitationStats(BaseModel):
    total: int = 0


class OverviewOut(BaseModel):
    qa_total: int
    assistant_total: int
    cited_messages: int
    cite_rate: float
    doc_total: int
    doc_success: int
    doc_success_rate: float
    citation_total: int
    citation_stats: CitationStats
    feedback: FeedbackStats
    mode_dist: ModeDist
    kb_dist: list[KbDist]


class TrendReq(BaseModel):
    kb_id: int | None = None


class TrendPoint(BaseModel):
    date: str
    qa_count: int
    local: int = 0
    online: int = 0
    degraded: int = 0
    cited: int = 0
    none: int = 0
