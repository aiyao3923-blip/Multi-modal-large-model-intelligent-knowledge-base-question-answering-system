"""API 依赖：认证、当前用户、角色校验、统一异常。"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..db.models import RoleCode, User
from ..core.security import decode_token

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: int
    username: str
    display_name: str
    role_code: str

    @property
    def is_admin(self) -> bool:
        return self.role_code == RoleCode.ADMIN

    @property
    def is_operator_or_admin(self) -> bool:
        return self.role_code in (RoleCode.ADMIN, RoleCode.OPERATOR)


def _unauthorized(detail: str = "未登录或登录已过期") -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if creds is None:
        raise _unauthorized()
    payload = decode_token(creds.credentials)
    if not payload:
        raise _unauthorized()
    user = db.get(User, payload.get("uid"))
    if not user or not user.is_active:
        raise _unauthorized("账号不存在或已停用")
    # SQLAlchemy keeps clean rows weakly: retain the authenticated row for this
    # request so handlers reuse its identity instead of selecting it again.
    # get_db creates a new Session for every request; disable/role changes are
    # still checked from the database on the next request, never globally cached.
    db.info["authenticated_user"] = user
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role_code=user.role_code,
    )


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要系统管理员权限")
    return user


def require_operator(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_operator_or_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要运营员或管理员权限")
    return user
