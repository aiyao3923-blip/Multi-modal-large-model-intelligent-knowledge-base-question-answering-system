"""状态与角色常量：与前端/接口同文，集中定义，禁止散落字符串。"""


class DocStatus:
    PENDING = "pending"      # 待解析
    PARSING = "parsing"      # 解析中
    SUCCESS = "success"      # 已入库
    FAILED = "failed"        # 解析失败


class TaskPhase:
    QUEUED = "queued"
    EXTRACTING = "extracting"   # 内容抽取
    CHUNKING = "chunking"       # 切片
    VECTORIZING = "vectorizing" # 向量化
    SUCCESS = "success"
    FAILED = "failed"


class KbStatus:
    CREATED = "created"
    ENABLED = "enabled"
    DISABLED = "disabled"


class FeedbackStatus:
    PENDING = "pending"
    HANDLED = "handled"
    CLOSED = "closed"


class FeedbackType:
    LIKE = "like"
    DISLIKE = "dislike"
    NO_RELEVANT = "no_relevant"
    LACK_SOURCE = "lack_source"


class ChannelType:
    LOCAL = "local"
    ONLINE = "online"
    DEGRADED = "degraded"


class GenMode:
    LOCAL = "local"
    ONLINE = "online"
    DEGRADED = "degraded"
    NONE = "none"


class RoleCode:
    ADMIN = "admin"
    OPERATOR = "operator"
    USER = "user"
