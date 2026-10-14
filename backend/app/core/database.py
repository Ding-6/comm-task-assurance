"""数据库层（PyMySQL 版本，无 ORM）。

V4.1 重构：移除 SQLAlchemy，service / api 直接通过 app.db 访问。
原 app.core.database 的 SessionLocal / get_db / Base 已废弃——保留本文件仅为
兼容部分老 import 路径（指向 app.db）。
"""
from __future__ import annotations

# 兼容旧代码：from app.core.database import SessionLocal / get_db
from app.db import get_conn, query, query_one, query_scalar, execute, to_dict  # noqa: F401


def get_db():
    """占位：旧 api/scene.py 等还在依赖此函数；改为无操作 generator。"""
    yield None
