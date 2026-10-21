"""应用配置加载：config.yaml + 环境变量覆盖。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT_DIR = Path(__file__).resolve().parents[2]  # backend/
DEFAULT_CONFIG = str(ROOT_DIR / "config.yaml")
ENV_CONFIG = os.environ.get("APP_CONFIG", DEFAULT_CONFIG)


class Settings:
    """轻量配置封装。演示环境不引入 pydantic-settings 全量依赖，
    字段用 __getattr__ 风格访问，保持配置集中可审计。"""

    def __init__(self, path: str = ENV_CONFIG) -> None:
        self._path = Path(path)
        with open(self._path, "r", encoding="utf-8") as f:
            self._data: dict[str, Any] = yaml.safe_load(f) or {}
        # 允许以 APP_<section>__<key> 形式覆盖（本机演示极少用，保留扩展）
        self.root_dir = ROOT_DIR
        self.config_path = self._path

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self._data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def app_name(self) -> str:
        return self.get("app", "name", default="多模态大模型智能知识库问答系统")

    @property
    def version(self) -> str:
        return self.get("app", "version", default="V1.0")

    @property
    def secret_key(self) -> str:
        return os.environ.get("APP_SECRET_KEY", self.get("security", "secret_key", default="dev-secret"))

    @property
    def token_expire_minutes(self) -> int:
        return int(self.get("security", "token_expire_minutes", default=720))

    @property
    def pbkdf2_iterations(self) -> int:
        return int(self.get("security", "pbkdf2_iterations", default=200_000))

    @property
    def db_path(self) -> Path:
        return self.root_dir / self.get("storage", "data_dir", default="storage/data") / self.get(
            "storage", "db_name", default="app.db"
        )

    @property
    def upload_dir(self) -> Path:
        return self.root_dir / self.get("storage", "upload_dir", default="storage/uploads")

    @property
    def vector_dir(self) -> Path:
        return self.root_dir / self.get("storage", "vector_dir", default="storage/vectors")

    @property
    def seed_admin_username(self) -> str:
        return self.get("seed", "admin_username", default="admin")

    @property
    def seed_admin_password(self) -> str:
        return self.get("seed", "admin_password", default="admin123")

    def ensure_dirs(self) -> None:
        for p in (self.db_path.parent, self.upload_dir, self.vector_dir):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()
