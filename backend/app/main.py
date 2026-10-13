"""FastAPI 入口。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import comm as comm_api
from app.api import scene as scene_api
from app.common.exceptions import register_exception_handlers

app = FastAPI(title="通信任务保障后端", version="0.1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局异常处理
register_exception_handlers(app)

# 路由
app.include_router(comm_api.router)
app.include_router(scene_api.router)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    """健康检查。"""
    return {"status": "ok"}
