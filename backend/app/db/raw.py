"""原生 PyMySQL 连接池 + SQL helper（无 ORM）。

说明：
- 智元本体智能体平台通过 .owl + MySQL zb_db2 直连本体层；
- 后端只用 PyMySQL 原生参数化 SQL 跑业务逻辑；
- 本模块提供：连接池、query（SELECT 返回 dict 列表）、
  query_one、execute（INSERT/UPDATE/DELETE）、transaction 上下文。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

import pymysql
from pymysql.cursors import DictCursor

from app.core.config import get_settings


# ============================================================
# 连接池（自实现，最小可用）
# ============================================================

_POOL: List[pymysql.connections.Connection] = []
_POOL_LOCK = threading.Lock()
_POOL_MAX = 5


def _new_connection() -> pymysql.connections.Connection:
    """新建一条 pymysql 连接。"""
    s = get_settings()
    return pymysql.connect(
        host=s.tma_mysql_host,
        port=s.tma_mysql_port,
        user=s.tma_mysql_user,
        password=s.tma_mysql_password,
        database=s.tma_mysql_db,
        charset=s.charset,
        autocommit=False,
        cursorclass=DictCursor,
    )


@contextmanager
def get_conn() -> Iterator[pymysql.connections.Connection]:
    """获取一条连接（上下文管理器）。用完自动归还。"""
    conn: Optional[pymysql.connections.Connection] = None
    with _POOL_LOCK:
        while _POOL:
            conn = _POOL.pop()
            try:
                conn.ping(reconnect=True)
                break
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
    if conn is None:
        conn = _new_connection()
    try:
        yield conn
    finally:
        with _POOL_LOCK:
            if len(_POOL) < _POOL_MAX:
                _POOL.append(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass


# ============================================================
# SQL helper
# ============================================================

def query(sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """执行 SELECT，返回字典列表。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return list(cur.fetchall())


def query_one(sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """执行 SELECT，返回单行字典；无数据时返回 None。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            return row


def query_scalar(sql: str, params: Optional[Sequence[Any]] = None) -> Any:
    """执行 SELECT，返回第一行第一列。无数据返回 None。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            if row is None:
                return None
            # DictCursor 时 fetchone 返回 dict；取其首个 value
            return next(iter(row.values()))


def execute(sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """执行 INSERT/UPDATE/DELETE，返回受影响行数。"""
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                affected = cur.execute(sql, params or ())
            conn.commit()
            return affected
        except Exception:
            conn.rollback()
            raise


def execute_returning_id(sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """执行 INSERT，返回 lastrowid。"""
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                last_id = cur.lastrowid
            conn.commit()
            return last_id
        except Exception:
            conn.rollback()
            raise


@contextmanager
def transaction() -> Iterator[pymysql.connections.Connection]:
    """事务上下文：进入时拿连接，退出时自动 commit / rollback。"""
    with get_conn() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# ============================================================
# 工具
# ============================================================

def to_dict(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把数据库行 dict 转成纯 dict（处理 datetime / Decimal）。"""
    if row is None:
        return {}
    from datetime import date, datetime
    from decimal import Decimal

    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out
