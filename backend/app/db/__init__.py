"""原生 PyMySQL 数据访问层。

无 ORM，service / api 直接调用本包的 query / execute / transaction。
"""
from app.db.raw import (
    execute,
    execute_returning_id,
    get_conn,
    query,
    query_one,
    query_scalar,
    to_dict,
    transaction,
)

__all__ = [
    "execute",
    "execute_returning_id",
    "get_conn",
    "query",
    "query_one",
    "query_scalar",
    "to_dict",
    "transaction",
]
