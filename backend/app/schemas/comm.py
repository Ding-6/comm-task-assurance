"""业务接口 schema：SK-01~SK-10 + 查询。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------- 通用响应 ----------
class ApiResponse(BaseModel):
    """统一响应外壳。"""

    code: int = 0
    message: str = "ok"
    data: Optional[Any] = None


# ---------- 链路 ----------
class LinkOut(BaseModel):
    """链路视图。"""

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


# ---------- 任务 ----------
class TaskOut(BaseModel):
    """任务视图。"""

    task_code: str
    task_name: str
    duration_min: int
    critical_min: Optional[int] = None
    owner_role: Optional[str] = None
    current_link_code: Optional[str] = None
    initial_demand_gbps: float
    lookahead_min: Optional[int] = None


# ---------- 告警 ----------
class AlarmInjectIn(BaseModel):
    """SK-01 注入告警入参。"""

    link_code: str
    alarm_type: str = Field(description="link_down / 性能劣化 / 容量超限")
    native_severity: str = Field(description="critical / major / minor / warning")
    predicted_min: Optional[int] = None


class AlarmOut(BaseModel):
    """告警视图。"""

    alarm_code: str
    link_code: str
    alarm_type: str
    native_severity: str
    event_min: int
    predicted_min: Optional[int] = None


# ---------- 方案 ----------
class PlanOut(BaseModel):
    """方案视图。"""

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


# ---------- 审批 ----------
class ApprovalIn(BaseModel):
    """SK-04 人工决策入参。"""

    plan_code: str
    decision: str = Field(description="批准 / 拒绝")
    reason: Optional[str] = None
    approver: str = "ROLE-APPROVER"


class ApprovalOut(BaseModel):
    """审批视图。"""

    approval_code: str
    plan_code: Optional[str] = None
    decision: Optional[str] = None
    reason: Optional[str] = None
    approver: Optional[str] = None
    decision_source: Optional[str] = None
    decided_min: Optional[int] = None


# ---------- 修理 ----------
class RepairRequestIn(BaseModel):
    """SK-05 修理申请入参。"""

    alarm_code: str
    plan_code: Optional[str] = None


class RepairReceiveIn(BaseModel):
    """SK-06 修理回执入参。

    est_finish_min / actual_finish_min：相对当前场景分钟数的"预计/实际时长"（分钟）。
    接口层会转换为绝对场景分钟再写入 comm_repair_order。
    """

    order_code: str
    est_finish_min: int
    actual_finish_min: Optional[int] = None
    as_of_min: Optional[int] = None


class RepairOrderOut(BaseModel):
    """修理单视图。"""

    order_code: str
    link_code: str
    alarm_code: Optional[str] = None
    request_min: int
    est_finish_min: Optional[int] = None
    actual_finish_min: Optional[int] = None
    status: Optional[str] = None


# ---------- 输入 ----------
class InputIn(BaseModel):
    """SK-10 自然语言输入框入参。"""

    text: str
