"""安全与密码工具：PBKDF2 哈希、自签 HMAC Token、当前用户提取。

设计说明（D7/安全基线）：
- 密码：hashlib.pbkdf2_hmac('sha256', salt) 随机 16B salt，防彩虹表；
- Token：自实现简洁 JWT-like（HMAC-SHA256）三段式，避免为单一登记演示引入 jose/cryptography 重依赖；
  如需规范 JWT 可平滑替换为 PyJWT，签名算法相同。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from ..core.config import settings


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, settings.pbkdf2_iterations
    )
    return _b64e(salt), _b64e(digest)


def verify_password(password: str, salt_b64: str, digest_b64: str) -> bool:
    salt = _b64d(salt_b64)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, settings.pbkdf2_iterations
    )
    return hmac.compare_digest(_b64e(digest), digest_b64)


def create_token(payload: dict[str, Any], expires_minutes: int | None = None) -> str:
    """HMAC-SHA256 签名 JSON token。payload 不得含密钥/密码。"""
    expires_minutes = expires_minutes or settings.token_expire_minutes
    now = int(time.time())
    body = dict(payload)
    body["iat"] = now
    body["exp"] = now + expires_minutes * 60
    header_b64 = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload_b64 = _b64e(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(settings.secret_key.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64e(sig)}"


def decode_token(token: str) -> dict[str, Any] | None:
    """校验签名与过期。失败返回 None。"""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        expected = hmac.new(
            settings.secret_key.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        payload = json.loads(_b64d(payload_b64).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
