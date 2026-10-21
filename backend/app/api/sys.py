"""系统设置：账号、模型通道及问答参数；资料访问仍由知识库所有权控制。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from urllib.parse import urlsplit
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..api.deps import CurrentUser, get_current_user, require_admin
from ..core.security import hash_password
from ..db.models import LlmChannel, Role, RoleCode, SysParam, User
from ..db.session import get_db
from ..schemas.common import MsgOut

router = APIRouter(prefix="/api/sys", tags=["system"])


# ---- 用户 ----
class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(default="", max_length=64)
    password: str = Field(min_length=8, max_length=64)
    email: str = Field(default="", max_length=128)
    role_code: Literal["admin", "operator", "user"] = RoleCode.USER
    is_active: bool = True


class UserItem(BaseModel):
    id: int
    username: str
    display_name: str
    email: str
    role_code: str
    is_active: bool
    created_at: object


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    role_code: Literal["admin", "operator", "user"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=64)


@router.get("/users", response_model=list[UserItem])
def list_users(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return db.scalars(select(User).order_by(User.id)).all()


@router.post("/users", response_model=UserItem)
def create_user(data: UserCreate, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    if db.scalar(select(User).where(User.username == data.username)):
        raise HTTPException(400, "用户名已存在")
    salt, digest = hash_password(data.password)
    u = User(
        username=data.username, password_salt=salt, password_digest=digest,
        display_name=data.display_name or data.username, email=data.email,
        role_code=data.role_code, is_active=data.is_active,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@router.patch("/users/{user_id}", response_model=UserItem)
def patch_user(
    user_id: int, data: UserPatch,
    db: Session = Depends(get_db), current: CurrentUser = Depends(require_admin),
):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "用户不存在")
    if user_id == current.id and (data.is_active is False or data.role_code not in (None, "admin")):
        raise HTTPException(409, "不能停用或降低当前管理员自身权限")
    if u.role_code == "admin" and (data.is_active is False or data.role_code not in (None, "admin")):
        active_admins = db.scalar(select(func.count(User.id)).where(User.role_code == "admin", User.is_active.is_(True)))
        if active_admins <= 1:
            raise HTTPException(409, "必须保留至少一个可用管理员")
    for field in ("display_name", "email", "role_code", "is_active"):
        v = getattr(data, field)
        if v is not None:
            setattr(u, field, v)
    if data.password:
        salt, digest = hash_password(data.password)
        u.password_salt, u.password_digest = salt, digest
    db.commit()
    db.refresh(u)
    return u


# ---- 角色 ----
class RoleItem(BaseModel):
    code: str
    name: str
    description: str


@router.get("/roles", response_model=list[RoleItem])
def list_roles(db: Session = Depends(get_db), _: CurrentUser = Depends(get_current_user)):
    return db.scalars(select(Role).order_by(Role.id)).all()


# ---- 模型通道 ----
class ChannelCreate(BaseModel):
    channel_type: Literal['local', 'online']
    name: str = Field(min_length=1, max_length=128)
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str = Field(default='', max_length=512)
    model_name: str = Field(min_length=1, max_length=128)
    enabled: bool = False
    temperature: float = Field(default=.3, ge=0, le=1, allow_inf_nan=False)
    top_p: float = Field(default=.9, ge=0, le=1, allow_inf_nan=False)
    timeout_s: int = Field(default=30, ge=5, le=60)

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        return _validated_url(value)

    @field_validator('name', 'model_name')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('名称不能为空')
        return value.strip()


def _validated_url(value):
    target = value.strip().rstrip('/')
    parts = urlsplit(target)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('服务地址必须为不含凭据、查询参数的 HTTP(S) 基础地址')
    return target


class ChannelItem(BaseModel):
    id: int
    channel_type: str
    name: str
    base_url: str
    model_name: str
    enabled: bool
    health_status: str
    temperature: float
    timeout_s: int
    has_key: bool = False
    # 脱敏展示
    api_key_masked: str = ""


@router.get("/channels", response_model=list[ChannelItem])
def list_channels(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    out = []
    for c in db.scalars(select(LlmChannel).order_by(LlmChannel.id)).all():
        item = ChannelItem(
            id=c.id, channel_type=c.channel_type, name=c.name, base_url=c.base_url,
            model_name=c.model_name, enabled=c.enabled, health_status=c.health_status,
            temperature=c.temperature, timeout_s=c.timeout_s, has_key=bool(c.api_key),
            api_key_masked="",
        )
        out.append(item)
    return out


@router.post("/channels", response_model=ChannelItem)
def create_channel(data: ChannelCreate, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    c = LlmChannel(**data.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    return ChannelItem(
        id=c.id, channel_type=c.channel_type, name=c.name, base_url=c.base_url,
        model_name=c.model_name, enabled=c.enabled, health_status=c.health_status,
        temperature=c.temperature, timeout_s=c.timeout_s, has_key=bool(c.api_key),
        api_key_masked="",
    )


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    model_name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    timeout_s: int | None = Field(default=None, ge=5, le=60)

    @field_validator('base_url')
    @classmethod
    def valid_url(cls, value):
        return _validated_url(value) if value is not None else None


@router.patch("/channels/{channel_id}", response_model=ChannelItem)
def patch_channel(
    channel_id: int, data: ChannelPatch,
    db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin),
):
    c = db.get(LlmChannel, channel_id)
    if not c:
        raise HTTPException(404, "通道不存在")
    if c.channel_type == 'degraded':
        raise HTTPException(409, '内置检索式回退通道无需配置或关闭')
    if data.base_url and c.api_key and data.api_key is None:
        old, new = urlsplit(c.base_url), urlsplit(data.base_url)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise HTTPException(409, '目标端点变更时必须重新填写对应密钥，不能把原密钥自动发送到其他服务')
    if data.base_url is not None or data.model_name is not None or data.api_key is not None:
        c.health_status = 'unknown'
    for field in ("name", "base_url", "api_key", "model_name", "enabled", "temperature", "timeout_s"):
        v = getattr(data, field)
        if v is not None:
            setattr(c, field, v)
    db.commit()
    db.refresh(c)
    return ChannelItem(
        id=c.id, channel_type=c.channel_type, name=c.name, base_url=c.base_url,
        model_name=c.model_name, enabled=c.enabled, health_status=c.health_status,
        temperature=c.temperature, timeout_s=c.timeout_s, has_key=bool(c.api_key),
        api_key_masked="",
    )


@router.post("/channels/{channel_id}/health", response_model=MsgOut)
def check_channel_health(
    channel_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin),
):
    c = db.get(LlmChannel, channel_id)
    if not c:
        raise HTTPException(404, "通道不存在")
    if c.channel_type == 'degraded':
        return MsgOut(message='内置检索式回答可用，无需外部连接')
    if not c.base_url:
        raise HTTPException(400, '请先配置服务地址')
    from ..engines.llm_gateway import ChannelUnavailable, channel_chat
    import datetime as _dt
    c.last_checked_at = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
    try:
        channel_chat(
            {
                "channel_type": c.channel_type, "base_url": c.base_url, "api_key": c.api_key,
                "model_name": c.model_name, "temperature": c.temperature, "timeout_s": min(c.timeout_s, 10),
            },
            [{"role": "user", "content": "ping"}],
        )
        c.health_status = "ok"
        db.commit()
        return MsgOut(message="连接正常")
    except ChannelUnavailable as exc:
        c.health_status = "error"
        db.commit()
        raise HTTPException(502, "连接失败，请检查服务地址、模型名、访问权限和网络；未回显敏感响应")
    except Exception as exc:
        c.health_status = "error"
        db.commit()
        raise HTTPException(502, "连接失败，请检查服务地址、模型名、访问权限和网络；未回显敏感响应")


# ---- 已退休的行业模板兼容入口 ----
@router.get("/templates", deprecated=True)
def list_templates_sys(_: CurrentUser = Depends(require_admin)):
    raise HTTPException(410, "行业模板已退休；已有模板记录保留，不再参与新知识库创建")


# ---- 系统参数 ----
class ParamItem(BaseModel):
    key: str
    value: str
    description: str


@router.get("/params", response_model=list[ParamItem])
def list_params(db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    return db.scalars(select(SysParam).order_by(SysParam.id)).all()


@router.patch("/params/{key}", response_model=ParamItem)
def patch_param(key: str, value: str, db: Session = Depends(get_db), _: CurrentUser = Depends(require_admin)):
    p = db.scalar(select(SysParam).where(SysParam.key == key))
    if not p:
        raise HTTPException(404, "参数不存在")
    p.value = value
    db.commit()
    db.refresh(p)
    return p
