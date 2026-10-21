"""集中入口：所有 ORM 模型与常量。各模块统一 ``from app.db.models import X``。"""
from .constants import (  # noqa: F401
    ChannelType,
    DocStatus,
    FeedbackStatus,
    FeedbackType,
    GenMode,
    KbStatus,
    RoleCode,
    TaskPhase,
)
from .entity import (  # noqa: F401
    Chunk,
    Citation,
    ChatSession,
    Document,
    IndustryTemplate,
    KnowledgeBase,
    LlmChannel,
    Message,
    ParseTask,
    QaFeedback,
    Role,
    StatEvent,
    SysParam,
    User,
    UserKbAccess,
)

from .versioning import (  # noqa: F401
    AuditEvent, DocumentSeries, DocumentVersion, ParseRevision, RevisionChunk, SchemaMigration, VERSIONING_TABLE_NAMES,
)
