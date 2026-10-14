"""FastAPI 入口。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import comm as comm_api
from app.api import scene as scene_api
from app.common.exceptions import register_exception_handlers
from app.services.engine import set_main_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期：注册主 event loop。"""
    loop = asyncio.get_running_loop()
    set_main_loop(loop)
    try:
        from app.services.approval import _set_main_loop as _asm
        _asm(loop)
    except Exception:
        pass
    yield


app = FastAPI(title="通信任务保障后端（PyMySQL 版）", version="4.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.include_router(comm_api.router, prefix="/api")
app.include_router(scene_api.router, prefix="/api")


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}
