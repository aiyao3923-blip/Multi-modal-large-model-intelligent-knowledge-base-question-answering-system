"""认证相关接口：登录/刷新/登出/当前用户。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import CurrentUser, get_current_user
from ..core.security import create_token, verify_password
from ..db.models import User
from ..db.session import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role_code: str
    email: str

    model_config = {"from_attributes": True}


class LoginOut(BaseModel):
    token: str
    token_type: str = "bearer"
    user: UserOut


@router.post("/login", response_model=LoginOut)
def login(req: LoginReq, db: Session = Depends(get_db)) -> LoginOut:
    user = db.scalar(select(User).where(User.username == req.username))
    if not user or not verify_password(req.password, user.password_salt, user.password_digest):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    token = create_token({"uid": user.id, "role": user.role_code, "username": user.username})
    return LoginOut(token=token, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> UserOut:
    user = db.get(User, current.id)
    return UserOut.model_validate(user)
