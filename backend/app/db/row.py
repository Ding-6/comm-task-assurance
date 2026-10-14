"""轻量 row 容器：dataclass（MappedDict）。

不依赖 ORM，纯标准库 dataclass + Dict[str, Any] 互转。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional


def _coerce(v: Any) -> Any:
    """把 PyMySQL 出来的 datetime/Decimal 归一化。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return v
    if isinstance(v, Decimal):
        return float(v)
    return v


@dataclass
class MappedRow:
    """所有表 row 的基类：dict ↔ dataclass 互转。"""

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]):
        if d is None:
            return None
        init_args = {}
        for f in fields(cls):
            v = d.get(f.name)
            init_args[f.name] = _coerce(v)
        return cls(**init_args)

    def to_dict(self) -> Dict[str, Any]:
        return {k: _coerce(v) for k, v in asdict(self).items()}


# ============================================================
# 9 张表的 dataclass row
# ============================================================

@dataclass
class CommTaskRow(MappedRow):
    task_code: str
    task_name: str
    duration_min: int
    initial_demand_gbps: float
    critical_min: Optional[int] = None
    owner_role: Optional[str] = None
    current_link_code: Optional[str] = None
    lookahead_min: Optional[int] = None


@dataclass
class CommLinkRow(MappedRow):
    link_code: str
    link_name: str
    capacity_gbps: float
    warning_gbps: float
    oper_state: str
    avg_repair_min: Optional[int] = None
    lookahead_min: Optional[int] = None
    backup_link_code: Optional[str] = None
    current_load_gbps: Optional[float] = None
    future_load_gbps: Optional[float] = None
    warn_status: Optional[str] = None


@dataclass
class TrafficDemandRow(MappedRow):
    demand_code: str
    task_code: str
    valid_from_min: int
    demand_gbps: float
    valid_to_min: Optional[int] = None


@dataclass
class ResourceAlarmRow(MappedRow):
    alarm_code: str
    link_code: str
    alarm_type: str
    native_severity: str
    event_min: int
    predicted_min: Optional[int] = None


@dataclass
class ApprovalRecordRow(MappedRow):
    approval_code: str
    plan_code: Optional[str] = None
    decision: Optional[str] = None
    reason: Optional[str] = None
    approver: Optional[str] = None
    decision_source: Optional[str] = None
    decided_min: Optional[int] = None


@dataclass
class CommPlanRow(MappedRow):
    plan_code: str
    alarm_code: str
    plan_type: str
    link_code: str
    target_link_code: Optional[str] = None
    est_repair_min: Optional[int] = None
    predicted_load_after_gbps: Optional[float] = None
    can_meet_critical: Optional[str] = None
    status: Optional[str] = None
    is_default: Optional[str] = None
    recommendation: Optional[str] = None
    generated_min: Optional[int] = None


@dataclass
class RepairOrderRow(MappedRow):
    order_code: str
    link_code: str
    request_min: int
    alarm_code: Optional[str] = None
    est_finish_min: Optional[int] = None
    actual_finish_min: Optional[int] = None
    status: Optional[str] = None


@dataclass
class SceneEventRow(MappedRow):
    event_code: str
    event_type: str
    occur_min: int
    ref_type: Optional[str] = None
    ref_code: Optional[str] = None
    summary: Optional[str] = None


@dataclass
class TaskLinkBindingRow(MappedRow):
    id: Optional[int] = None
    task_code: str = ""
    link_code: str = ""
    assigned_min: int = 0
    note: Optional[str] = None
