"""SK-01~SK-10 业务路由 + 查询接口（PyMySQL 原生版本，无 ORM）。

路径严格对齐《本体规格说明书 V4.1》§7.1 / §7.2。
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.common.exceptions import BizException
from app.common.ws import get_hub
from app.core.scene_clock import get_clock
from app.db.repo import (
    AlarmRepo, ApprovalRepo, EventRepo, LinkRepo, PlanRepo, RepairRepo, TaskRepo,
)
from app.db.row import (
    ResourceAlarmRow, SceneEventRow, ApprovalRecordRow, CommPlanRow,
    CommLinkRow, CommTaskRow, RepairOrderRow,
)
from app.schemas.comm import (
    ApiResponse, DemandIn, ForecastQuery, LinkAddIn, LinkOut, PlanGenerateIn,
    PlanOut, PrecheckIn, RepairOrderOut, RepairReceiveIn, RestoreIn, SwitchIn,
    TaskAddIn, TaskOut,
)
from app.services.approval import decide_approval
from app.services.engine import (
    add_link, add_task, get_state, reset_default_scene, start_countdown,
)
from app.services.evidence import get_evidence_report
from app.services.occupancy import calc_occupancy, recalc_all, update_link_loads
from app.services.plan import generate_plans, plan_to_dict
from app.services.precheck import precheck_task
from app.services.repair import handle_repair_update
from app.services.restore import handle_link_restored
from app.services.switch import handle_link_down

router = APIRouter(prefix="/comm", tags=["comm"])


# ============================================================
# 查询 / 列表
# ============================================================

@router.get("/links", response_model=ApiResponse)
def list_links() -> ApiResponse:
    update_link_loads()
    rows = LinkRepo.list_all()
    return ApiResponse(data=[r.to_dict() for r in rows])


@router.get("/tasks", response_model=ApiResponse)
def list_tasks() -> ApiResponse:
    rows = TaskRepo.list_all()
    return ApiResponse(data=[r.to_dict() for r in rows])


@router.get("/alarms", response_model=ApiResponse)
def list_alarms() -> ApiResponse:
    rows = AlarmRepo.list_all()
    return ApiResponse(data=[r.to_dict() for r in rows])


@router.get("/plans", response_model=ApiResponse)
def list_plans() -> ApiResponse:
    rows = PlanRepo.list_all()
    return ApiResponse(data=[r.to_dict() for r in rows])


@router.get("/approvals", response_model=ApiResponse)
def list_approvals() -> ApiResponse:
    rows = ApprovalRepo.list_all()
    return ApiResponse(data=[r.to_dict() for r in rows])


@router.get("/repairs", response_model=ApiResponse)
def list_repairs() -> ApiResponse:
    rows = RepairRepo.list_all()
    return ApiResponse(data=[r.to_dict() for r in rows])


@router.get("/events", response_model=ApiResponse)
def list_events() -> ApiResponse:
    rows = EventRepo.list_all()
    return ApiResponse(data=[r.to_dict() for r in rows])


@router.get("/snapshot", response_model=ApiResponse)
def get_snapshot() -> ApiResponse:
    """场景快照：链路 + 任务 + 待审批方案 + 最近事件 + 预订事件 + 当前分钟。"""
    update_link_loads()
    clock = get_clock()
    return ApiResponse(data=get_state(clock.now_min()))


@router.post("/reset", response_model=ApiResponse)
def reset_scene() -> ApiResponse:
    """业务层复位（不动时钟与后台循环）。"""
    reset_default_scene()
    return ApiResponse(message="ok")


# ============================================================
# 扩展能力（说明书未列但保留）
# ============================================================

@router.get("/forecast", response_model=ApiResponse)
def get_link_forecast(
    q: ForecastQuery = Depends(),
) -> ApiResponse:
    from app.services.forecast import get_forecast

    try:
        data = get_forecast(q.link_code, as_of_min=q.as_of_min)
        return ApiResponse(data=data)
    except ValueError as exc:
        return ApiResponse(code=400, message=str(exc))


@router.post("/precheck/task", response_model=ApiResponse)
def precheck_add_task(payload: PrecheckIn) -> ApiResponse:
    try:
        data = precheck_task(
            payload.link_code,
            payload.demand_gbps,
            as_of_min=payload.as_of_min,
        )
        return ApiResponse(data=data)
    except ValueError as exc:
        return ApiResponse(code=400, message=str(exc))


@router.get("/evidence/report", response_model=ApiResponse)
def get_evidence() -> ApiResponse:
    return ApiResponse(data=get_evidence_report())


# ============================================================
# SK-01 注入告警 → POST /api/comm/alarm
# ============================================================

class AlarmInjectIn(BaseModel):
    """SK-01 注入告警入参（仅本路由使用，重复定义避免引入 models.*）。"""
    link_code: str
    alarm_type: str
    native_severity: str
    predicted_min: Optional[int] = None


@router.post("/alarm", response_model=ApiResponse)
def inject_alarm(payload: AlarmInjectIn) -> ApiResponse:
    now = get_clock().now_min()
    seq = AlarmRepo.count() + 1
    code = f"AL-{seq:02d}"
    AlarmRepo.insert(ResourceAlarmRow(
        alarm_code=code, link_code=payload.link_code,
        alarm_type=payload.alarm_type, native_severity=payload.native_severity,
        event_min=now, predicted_min=payload.predicted_min,
    ))
    EventRepo.insert(SceneEventRow(
        event_code=f"EVT-AI-{datetime.now().strftime('%H%M%S%f')}",
        event_type="alarm_injected", ref_type="alarm", ref_code=code,
        occur_min=now, summary=f"{code} {payload.alarm_type} @ {payload.link_code}",
    ))
    return ApiResponse(data=ResourceAlarmRow(
        alarm_code=code, link_code=payload.link_code,
        alarm_type=payload.alarm_type, native_severity=payload.native_severity,
        event_min=now, predicted_min=payload.predicted_min,
    ).to_dict())


# ============================================================
# SK-02 故障切换 → POST /api/comm/switch
# ============================================================

@router.post("/switch", response_model=ApiResponse)
def switch_link(payload: SwitchIn) -> ApiResponse:
    result = handle_link_down(
        alarm_link_code=payload.alarm_link_code,
        as_of_min=payload.as_of_min,
    )
    return ApiResponse(data=result)


# ============================================================
# SK-02 占用计算 → POST /api/comm/occupancy
# ============================================================

class OccupancyIn(BaseModel):
    link_code: str
    as_of_min: Optional[int] = None


@router.post("/occupancy", response_model=ApiResponse)
def calc_occupancy_api(payload: OccupancyIn) -> ApiResponse:
    if not payload.link_code:
        return ApiResponse(code=400, message="link_code 必填")
    try:
        data = calc_occupancy(link_code=payload.link_code, as_of_min=payload.as_of_min)
        return ApiResponse(data=data)
    except ValueError as exc:
        return ApiResponse(code=400, message=str(exc))


# ============================================================
# SK-03 方案生成 → POST /api/comm/plan
# ============================================================

@router.post("/plan", response_model=ApiResponse)
def gen_plans(payload: PlanGenerateIn) -> ApiResponse:
    try:
        result = generate_plans(
            alarm_link_code=payload.alarm_link_code,
            as_of_min=payload.as_of_min,
        )
        return ApiResponse(data=result)
    except ValueError as exc:
        return ApiResponse(code=400, message=str(exc))


# ============================================================
# SK-04 人工审批 → POST /api/comm/approve
# ============================================================

class _ApproveIn(BaseModel):
    plan_code: str
    decision: str
    reason: Optional[str] = None
    approver: str = "ROLE-APPROVER"
    decision_source: str = "人工"


@router.post("/approve", response_model=ApiResponse)
def decide(payload: _ApproveIn) -> ApiResponse:
    try:
        ap = decide_approval(
            plan_code=payload.plan_code,
            decision=payload.decision,
            approver=payload.approver,
            decision_source=payload.decision_source,
            reason=payload.reason,
        )
    except ValueError as e:
        # ValueError → BizException 走统一错误响应（code=400, HTTP 200）
        from app.common.exceptions import BizException
        raise BizException(message=str(e) or e.__class__.__name__, code=400)
    return ApiResponse(data=ap.to_dict() if hasattr(ap, "to_dict") else ap)


# ============================================================
# SK-04b 批量拒绝告警下所有待审批方案 → POST /api/comm/plans/reject-all
# 修复（round2 minor E）：原 onRejectAll 循环 /approve，
#   第 2 次起被后端 _do_decide 标 "已作废" 而 400，前端 toast 噪音。
#   改成一次接口调用：先按 alarm_code 找 pending plans，逐个拒绝并结算。
# ============================================================

class _RejectAllIn(BaseModel):
    alarm_code: str
    reason: Optional[str] = "批量拒绝全部方案"
    approver: str = "ROLE-APPROVER"
    decision_source: str = "人工"


@router.post("/plans/reject-all", response_model=ApiResponse)
def reject_all_plans(payload: _RejectAllIn) -> ApiResponse:
    pendings = PlanRepo.list_pending_by_alarm(payload.alarm_code)
    if not pendings:
        return ApiResponse(data={"rejected": 0, "approvals": []})
    approvals: List[dict] = []
    rejected = 0
    # 逐个拒绝；第一个成功后会把它同 alarm 下的兄弟置"已作废"，
    # 我们用 _do_decide 自身行为来保持一致性。
    for p in pendings:
        if p.status != "待审批":
            continue
        try:
            ap = decide_approval(
                plan_code=p.plan_code,
                decision="拒绝",
                reason=payload.reason,
                approver=payload.approver,
                decision_source=payload.decision_source,
            )
            approvals.append(ap.to_dict() if hasattr(ap, "to_dict") else ap)
            rejected += 1
        except ValueError:
            # 已被并发处理过（已作废 / 状态非待审批）—— 静默跳过
            continue
        except BizException:
            raise
    return ApiResponse(data={"rejected": rejected, "approvals": approvals})


# ============================================================
# SK-06 修理回执 / 完成 → POST /api/comm/repair
# ============================================================

@router.post("/repair", response_model=ApiResponse)
def recv_repair(payload: RepairReceiveIn) -> ApiResponse:
    if payload.est_finish_min is None and payload.actual_finish_min is None:
        return ApiResponse(code=400, message="est_finish_min / actual_finish_min 至少传一个")
    # 修复（major 5）：est/actual_finish_min 已为绝对分钟（与自然语言分支一致），
    # 不再加 now；之前加 now 会让 T+20 时回单 45 变成 65 > 50 → 错判超期 → REPAIR 作废
    result = handle_repair_update(
        order_code=payload.order_code,
        est_finish_min=payload.est_finish_min,
        actual_finish_min=payload.actual_finish_min,
        as_of_min=payload.as_of_min,
    )
    return ApiResponse(data=result)


# ============================================================
# SK-07 链路恢复回迁 → POST /api/comm/restore
# ============================================================

@router.post("/restore", response_model=ApiResponse)
def restore(payload: RestoreIn) -> ApiResponse:
    result = handle_link_restored(link_code=payload.link_code, as_of_min=payload.as_of_min)
    return ApiResponse(data=result)


# ============================================================
# SK-08 添加任务 → POST /api/comm/task
# ============================================================

@router.post("/task", response_model=ApiResponse)
def add_task_route(payload: TaskAddIn) -> ApiResponse:
    result = add_task(
        task_code=payload.task_code,
        task_name=payload.task_name,
        duration_min=payload.duration_min,
        critical_min=payload.critical_min,
        link_code=payload.link_code,
        demand_gbps=payload.demand_gbps,
        owner_role=payload.owner_role,
        lookahead_min=payload.lookahead_min,
    )
    return ApiResponse(data=result)


# ============================================================
# SK-09 添加链路 → POST /api/comm/link
# ============================================================

@router.post("/link", response_model=ApiResponse)
def add_link_route(payload: LinkAddIn) -> ApiResponse:
    result = add_link(
        link_code=payload.link_code,
        link_name=payload.link_name,
        capacity_gbps=payload.capacity_gbps,
        warning_gbps=payload.warning_gbps,
        oper_state=payload.oper_state,
        avg_repair_min=payload.avg_repair_min,
        lookahead_min=payload.lookahead_min,
        backup_link_code=payload.backup_link_code,
    )
    return ApiResponse(data=result)


# ============================================================
# SK-10 需求变更 → POST /api/comm/demand
# ============================================================

@router.post("/demand", response_model=ApiResponse)
def add_demand_route(payload: DemandIn) -> ApiResponse:
    from app.db.repo import DemandRepo
    from app.db.row import TrafficDemandRow

    seq = DemandRepo.count() + 1
    demand_code = f"D-{payload.task_code[2:]}-{seq:02d}"
    valid_from = payload.valid_from_min if payload.valid_from_min is not None else get_clock().now_min()
    DemandRepo.insert(TrafficDemandRow(
        demand_code=demand_code, task_code=payload.task_code,
        valid_from_min=valid_from, demand_gbps=payload.demand_gbps,
        valid_to_min=None,
    ))
    recalc_all(as_of_min=get_clock().now_min())
    return ApiResponse(data={
        "demand_code": demand_code,
        "task_code": payload.task_code,
        "valid_from_min": valid_from,
        "demand_gbps": payload.demand_gbps,
        "triggered_action": "ACT-02",
    })


# ============================================================
# WS
# ============================================================

@router.websocket("/ws")
async def comm_ws(ws: WebSocket) -> None:
    hub = get_hub()
    await hub.connect(ws)
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_text(f"ack: {msg}")
    except WebSocketDisconnect:
        hub.disconnect(ws)
