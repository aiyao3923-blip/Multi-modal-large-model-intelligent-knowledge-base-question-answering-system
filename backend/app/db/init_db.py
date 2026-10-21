"""初始化数据库：建表 + 种子（角色/管理员/系统参数）。可作为 python -m app.db.init_db 执行。"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许以项目根或 backend 为起点执行
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from .models import (  # noqa: E402
    LlmChannel,
    Role,
    RoleCode,
    SysParam,
    User,
    VERSIONING_TABLE_NAMES,
)
from .session import Base, get_engine, get_session_factory  # noqa: E402
from ..core.config import settings  # noqa: E402
from ..core.security import hash_password  # noqa: E402

ROLES = [
    (RoleCode.ADMIN, "系统管理员", "系统配置、用户角色与模型通道；资料仍按知识库所有者及显式授权隔离"),
    (RoleCode.OPERATOR, "知识库运营员", "维护自己的知识库与文档、解析复核、反馈处理与授权范围内统计"),
    (RoleCode.USER, "问答用户", "创建私人知识库、上传与阅读自己的论文、问答会话与反馈"),
]

DEFAULT_PARAMS = [
    ("qa.top_k", "12", "检索召回 top-k"),
    ("qa.relevance_threshold", "0.35", "相关度阈值"),
    ("qa.max_context_chunks", "10", "组装上下文最大切片数"),
    ("qa.max_context_chars", "24000", "模型证据上下文总字符预算"),
    ("qa.max_history_rounds", "4", "携带历史轮数"),
    ("qa.degraded_source_top", "6", "生成未完成时保留的证据片段数"),
    ("sys.show_degraded_notice", "1", "是否显示降级提示"),
]


def init_db() -> None:
    settings.ensure_dirs()
    engine = get_engine()
    # 版本表仅由显式迁移建立；普通启动/旧初始化不能偷偷迁移现有业务库。
    Base.metadata.create_all(bind=engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in VERSIONING_TABLE_NAMES])
    factory = get_session_factory()
    with factory() as db:
        # 角色
        for code, name, desc in ROLES:
            if not db.scalar(select(Role).where(Role.code == code)):
                db.add(Role(code=code, name=name, description=desc))
        db.commit()
        # 管理员
        admin_name = settings.seed_admin_username
        if not db.scalar(select(User).where(User.username == admin_name)):
            salt, digest = hash_password(settings.seed_admin_password)
            db.add(
                User(
                    username=admin_name,
                    password_salt=salt,
                    password_digest=digest,
                    display_name="系统管理员",
                    email="admin@demo.local",
                    is_active=True,
                    role_code=RoleCode.ADMIN,
                )
            )
        # 普通启动不创建演示账号、行业模板或示范知识库；既有数据不删除。
        # 系统参数
        for key, value, desc in DEFAULT_PARAMS:
            if not db.scalar(select(SysParam).where(SysParam.key == key)):
                db.add(SysParam(key=key, value=value, description=desc))
        # 降级通道默认内置
        if not db.scalar(select(LlmChannel).where(LlmChannel.channel_type == "degraded")):
            db.add(
                LlmChannel(
                    channel_type="degraded",
                    name="检索式降级通道（内置）",
                    model_name="degraded",
                    enabled=True,
                    health_status="ok",
                )
            )
        db.commit()
    print("[init_db] 数据库初始化完成。")
    print(f"  数据文件: {settings.db_path}")
    print(f"  管理员账号: {admin_name}（密码来自 seed 配置，不在日志中输出）")


if __name__ == "__main__":
    init_db()
