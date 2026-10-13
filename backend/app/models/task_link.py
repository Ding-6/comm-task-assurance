"""任务链路归属历史 ORM 模型（联合主键）。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class TaskLinkBinding(Base):
    """comm_task_link 表。

    联合主键：task_code + assigned_min。
    """

    __tablename__ = "comm_task_link"

    task_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    link_code: Mapped[str] = mapped_column(String(32), nullable=False)
    assigned_min: Mapped[int] = mapped_column(Integer, primary_key=True)
    note: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
