"""原生 PyMySQL 连接池 + SQL helper（无 ORM）。

说明：
- 智元本体智能体平台通过 .owl + MySQL zb_db2 直连本体层；
- 后端只用 PyMySQL 原生参数化 SQL 跑业务逻辑；
- 本模块提供：连接池、query（SELECT 返回 dict 列表）、
  query_one、execute（INSERT/UPDATE/DELETE）、transaction 上下文。

事务语义（修复 major 6/7）：
- `_TX_CONN` ContextVar 把连接绑定到当前调用链；事务内所有 helper 复用该连接；
- 事务内 execute/query 不再 commit，由 transaction() 统一 commit/rollback；
- 只读 helper 在 finally 显式 rollback 释放 REPEATABLE READ 快照，
  避免池化连接把陈旧数据带到下一次借用。
"""
from __future__ import annotations

import contextvars
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

# 事务内的连接：绑定到当前调用上下文，事务中所有 SQL helper 自动复用
_TX_CONN: "contextvars.ContextVar[Optional[pymysql.connections.Connection]]" = (
    contextvars.ContextVar("_TX_CONN", default=None)
)
# 当前是否处于事务中（避免事务内被自动 commit）
_IN_TX: "contextvars.ContextVar[bool]" = contextvars.ContextVar("_IN_TX", default=False)


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
    """获取一条连接。
    - 处于事务中时：复用 _TX_CONN 绑定的连接（不入池）
    - 否则：从池里取一条，用完归还
    """
    tx = _TX_CONN.get()
    if tx is not None:
        # 事务内：不取池里的连接，调用方负责 commit/rollback
        yield tx
        return

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

def _is_in_tx() -> bool:
    return _IN_TX.get()


def query(sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """执行 SELECT，返回字典列表。只读事务内自动 rollback 释放快照。"""
    in_tx = _is_in_tx()
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                return list(cur.fetchall())
        finally:
            if not in_tx:
                # 结束只读事务，释放 REPEATABLE READ 快照，避免池化连接带回陈旧数据
                try:
                    conn.rollback()
                except Exception:
                    pass


def query_one(sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """执行 SELECT，返回单行字典；无数据时返回 None。"""
    in_tx = _is_in_tx()
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                row = cur.fetchone()
                return row
        finally:
            if not in_tx:
                try:
                    conn.rollback()
                except Exception:
                    pass


def query_scalar(sql: str, params: Optional[Sequence[Any]] = None) -> Any:
    """执行 SELECT，返回第一行第一列。无数据返回 None。"""
    in_tx = _is_in_tx()
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                row = cur.fetchone()
                if row is None:
                    return None
                return next(iter(row.values()))
        finally:
            if not in_tx:
                try:
                    conn.rollback()
                except Exception:
                    pass


def execute(sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """执行 INSERT/UPDATE/DELETE，返回受影响行数。
    事务内：不自动 commit（由 transaction() 统一 commit/rollback）。
    """
    in_tx = _is_in_tx()
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                affected = cur.execute(sql, params or ())
            if not in_tx:
                conn.commit()
            return affected
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


def execute_returning_id(sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """执行 INSERT，返回 lastrowid。事务内不 commit。"""
    in_tx = _is_in_tx()
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params or ())
                last_id = cur.lastrowid
            if not in_tx:
                conn.commit()
            return last_id
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


@contextmanager
def transaction() -> Iterator[pymysql.connections.Connection]:
    """事务上下文：进入时拿连接 + 绑定 ContextVar，
    内部 SQL helper 自动复用该连接；退出时统一 commit / rollback。
    """
    in_tx_token = _IN_TX.set(True)
    conn_token = _TX_CONN.set(None)
    conn: Optional[pymysql.connections.Connection] = None
    try:
        # 直接从池里取一条（不走 get_conn()，因为 get_conn 会反过来读 _TX_CONN）
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
        conn_token = _TX_CONN.set(conn)
        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        _IN_TX.reset(in_tx_token)
        _TX_CONN.reset(conn_token)
        if conn is not None:
            with _POOL_LOCK:
                if len(_POOL) < _POOL_MAX:
                    _POOL.append(conn)
                else:
                    try:
                        conn.close()
                    except Exception:
                        pass


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
