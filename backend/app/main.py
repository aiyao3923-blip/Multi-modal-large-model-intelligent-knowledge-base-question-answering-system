"""应用主入口：FastAPI 实例、路由注册、CORS、启动初始化。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import auth, kb, qa, stats, sys, versions, generation
from .services.version_service import VersionError
from .core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    # 初始化与数据迁移须显式运行，应用启动不创建示例资料。
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    lifespan=lifespan,
)

# 本机入口；其他部署须按实际受信任前端配置来源。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(kb.router)
app.include_router(qa.router)
app.include_router(generation.router)
app.include_router(stats.router)
app.include_router(sys.router)
app.include_router(versions.router)

@app.exception_handler(VersionError)
async def version_error_response(_request, error: VersionError):
    return JSONResponse(status_code=error.status_code, content={'detail': error.detail})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "version": settings.version}
