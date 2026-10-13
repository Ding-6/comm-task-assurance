"""SK-01~SK-10 业务路由 + 查询接口。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.exceptions import BizException
from app.common.ws import get_hub
from app.core.database import get_db
from app.core.scene_clock import get_clock
from app.models.alarm import ResourceAlarm
from app.models.approval import ApprovalRecord
from app.models.event import SceneEvent
from app.models.link import CommLink
from app.models.plan import CommPlan
from app.models.repair_order import RepairOrder
from app.models.task import CommTask
from app.schemas.comm import (
    AlarmInjectIn,
    AlarmOut,
    ApiResponse,
    ApprovalIn,
    ApprovalOut,
    InputIn,
    LinkOut,
    PlanOut,
    RepairOrderOut,
    RepairReceiveIn,
    RepairRequestIn,
    TaskOut,
)
from app.services.approval import decide_approval
from app.services.intent import parse_input, recognize_intent
from app.services.occupancy import update_link_loads
from app.services.plan import generate_plans_for_alarm
from app.services.repair import evaluate_repair_receipt, handle_repair_update, request_repair
from app.services.restore import handle_link_restored, restore_link
from app.services.switch import handle_link_down, switch_due_to_link_down

router = APIRouter(prefix="/comm", tags=["comm"])


# ---------- 查询 ----------
@router.get("/links", response_model=ApiResponse)
def list_links(db: Session = Depends(get_db)) -> ApiResponse:
    update_link_loads(db)
    db.commit()
    rows: List[CommLink] = list(db.scalars(select(CommLink)))
    return ApiResponse(data=[LinkOut.model_validate(r, from_attributes=True) for r in rows])


@router.get("/tasks", response_model=ApiResponse)
def list_tasks(db: Session = Depends(get_db)) -> ApiResponse:
    rows: List[CommTask] = list(db.scalars(select(CommTask)))
    return ApiResponse(data=[TaskOut.model_validate(r, from_attributes=True) for r in rows])


@router.get("/alarms", response_model=ApiResponse)
def list_alarms(db: Session = Depends(get_db)) -> ApiResponse:
    rows: List[ResourceAlarm] = list(db.scalars(select(ResourceAlarm)))
    return ApiResponse(data=[AlarmOut.model_validate(r, from_attributes=True) for r in rows])


@router.get("/plans", response_model=ApiResponse)
def list_plans(db: Session = Depends(get_db)) -> ApiResponse:
    rows: List[CommPlan] = list(db.scalars(select(CommPlan)))
    return ApiResponse(data=[PlanOut.model_validate(r, from_attributes=True) for r in rows])


@router.get("/approvals", response_model=ApiResponse)
def list_approvals(db: Session = Depends(get_db)) -> ApiResponse:
    rows: List[ApprovalRecord] = list(db.scalars(select(ApprovalRecord)))
    return ApiResponse(data=[ApprovalOut.model_validate(r, from_attributes=True) for r in rows])


@router.get("/repairs", response_model=ApiResponse)
def list_repairs(db: Session = Depends(get_db)) -> ApiResponse:
    rows: List[RepairOrder] = list(db.scalars(select(RepairOrder)))
    return ApiResponse(data=[RepairOrderOut.model_validate(r, from_attributes=True) for r in rows])


@router.get("/events", response_model=ApiResponse)
def list_events(db: Session = Depends(get_db)) -> ApiResponse:
    rows: List[SceneEvent] = list(db.scalars(select(SceneEvent).order_by(SceneEvent.occur_min)))
    return ApiResponse(data=[{"event_code": r.event_code, "event_type": r.event_type,
                              "ref_type": r.ref_type, "ref_code": r.ref_code,
                              "occur_min": r.occur_min, "summary": r.summary} for r in rows])


# ---------- SK-01 注入告警 ----------
@router.post("/alarms/inject", response_model=ApiResponse)
def inject_alarm(payload: AlarmInjectIn, db: Session = Depends(get_db)) -> ApiResponse:
    seq = len(list(db.scalars(select(ResourceAlarm)))) + 1
    code = f"AL-{seq:02d}"
    alarm = ResourceAlarm(
        alarm_code=code,
        link_code=payload.link_code,
        alarm_type=payload.alarm_type,
        native_severity=payload.native_severity,
        event_min=get_clock().now_min(),
        predicted_min=payload.predicted_min,
    )
    db.add(alarm)
    db.add(
        SceneEvent(
            event_code=f"EVT-AI-{datetime.now().strftime('%H%M%S%f')}",
            event_type="alarm_injected",
            ref_type="alarm",
            ref_code=code,
            occur_min=get_clock().now_min(),
            summary=f"注入告警 {code} -> {payload.link_code}",
        )
    )
    db.commit()
    return ApiResponse(data=AlarmOut.model_validate(alarm, from_attributes=True))


# ---------- SK-02 故障切换 ----------
@router.post("/switch/{link_code}", response_model=ApiResponse)
def switch_link(link_code: str, db: Session = Depends(get_db)) -> ApiResponse:
    n = switch_due_to_link_down(db, link_code)
    db.commit()
    return ApiResponse(data={"switched_tasks": n})


# ---------- SK-03 方案生成 ----------
@router.post("/plans/generate/{alarm_code}", response_model=ApiResponse)
def gen_plans(alarm_code: str, db: Session = Depends(get_db)) -> ApiResponse:
    plans = generate_plans_for_alarm(db, alarm_code)
    db.commit()
    return ApiResponse(data=[PlanOut.model_validate(p, from_attributes=True) for p in plans])


# ---------- SK-04 人工决策（同步：写入；异步：60s 超时） ----------
@router.post("/approvals/decide", response_model=ApiResponse)
async def decide(payload: ApprovalIn, db: Session = Depends(get_db)) -> ApiResponse:
    ap = decide_approval(
        db,
        approval_code=create_or_get_approval(db, payload.plan_code),
        decision=payload.decision,
        approver=payload.approver,
        decision_source="人工",
        reason=payload.reason,
    )
    db.commit()
    return ApiResponse(data=ApprovalOut.model_validate(ap, from_attributes=True))


def create_or_get_approval(db: Session, plan_code: str) -> str:
    """仅返回审批单编号；倒计时在 plan.generate_plans 中启动。"""
    return f"APV-{plan_code}"


# ---------- SK-05 修理申请 ----------
@router.post("/repairs/request", response_model=ApiResponse)
def req_repair(payload: RepairRequestIn, db: Session = Depends(get_db)) -> ApiResponse:
    order = request_repair(db, alarm_code=payload.alarm_code, plan_code=payload.plan_code)
    db.commit()
    return ApiResponse(data=RepairOrderOut.model_validate(order, from_attributes=True))


# ---------- SK-06 修理回执评估 ----------
@router.post("/repairs/receive", response_model=ApiResponse)
def recv_repair(payload: RepairReceiveIn, db: Session = Depends(get_db)) -> ApiResponse:
    # 入参 est/actual_finish_min 是"相对时长"；转换为绝对场景分钟
    from app.core.scene_clock import get_clock as _gc

    now = payload.as_of_min if payload.as_of_min is not None else _gc().now_min()
    est_abs = now + payload.est_finish_min
    actual_abs = (
        now + payload.actual_finish_min if payload.actual_finish_min is not None else None
    )
    result = handle_repair_update(
        db,
        order_code=payload.order_code,
        est_finish_min=est_abs,
        actual_finish_min=actual_abs,
        as_of_min=payload.as_of_min,
    )
    db.commit()
    return ApiResponse(
        data=RepairOrderOut.model_validate(result["order"], from_attributes=True)
    )


# ---------- SK-07 链路恢复回迁 ----------
@router.post("/links/{link_code}/restore", response_model=ApiResponse)
def restore(link_code: str, db: Session = Depends(get_db)) -> ApiResponse:
    n = restore_link(db, link_code)
    db.commit()
    return ApiResponse(data={"restored_tasks": n})


# ---------- SK-10 自然语言输入 ----------
@router.post("/input", response_model=ApiResponse)
def handle_input(payload: InputIn, db: Session = Depends(get_db)) -> ApiResponse:
    parsed = parse_input(payload.text)
    data: Any = {"parsed": parsed, "raw": parsed.get("raw")}

    if parsed.get("category") is None:
        # 兜底：再走老意图识别（兼容旧行为）
        legacy = recognize_intent(payload.text)
        if legacy.get("intent") == "unknown" or not legacy.get("intent"):
            raise BizException(message=parsed.get("message", "未识别指令"), code=400)
        data["legacy"] = legacy
        return _dispatch_legacy(db, data, legacy)

    sub: str = parsed.get("sub_type")
    params: dict = parsed.get("params", {})
    minute_offset = parsed.get("minute_offset")

    # 预订：写 comm_event，到点由 engine.tick 兑现
    if minute_offset is not None and sub in ("link_down", "performance_degrade", "demand_change"):
        now = get_clock().now_min()
        occur = now + int(minute_offset)
        ts_suffix: str = datetime.now().strftime("%H%M%S%f")
        if sub in ("link_down", "performance_degrade"):
            ref_type = "alarm"
            ref_code = params.get("link_code")
            summary = f"{minute_offset} 分钟后 {sub} -> {params.get('link_code')}"
        else:
            ref_type = "demand"
            # 需求预订用唯一 ref_code（带时间戳后缀），避免同 task 多次预订被 ref_code 唯一约束挡掉
            ref_code = f"D-{params['task_code'][2:]}-BK{ts_suffix}"
            # 编码 task= / gbps= 供 _dispatch_booking 解析
            summary = (
                f"task={params['task_code']};gbps={params['demand_gbps']};"
                f"{minute_offset} 分钟后需求变更"
            )
        ev = SceneEvent(
            event_code=f"EVT-BK-{ts_suffix}",
            event_type="task_booked",
            ref_type=ref_type,
            ref_code=ref_code,
            occur_min=occur,
            summary=summary,
        )
        db.add(ev)
        db.commit()
        data["booking"] = {"event_code": ev.event_code, "occur_min": occur}
        return ApiResponse(data=data)

    # 即时：分派到对应 action
    if sub in ("link_down", "performance_degrade"):
        ap = inject_alarm(
            AlarmInjectIn(
                link_code=params["link_code"],
                alarm_type=params.get("alarm_type", "link_down"),
                native_severity=params.get("native_severity", "critical"),
            ),
            db,
        )
        data["result"] = ap.data
    elif sub == "demand_change":
        from app.models.demand import TrafficDemand
        from sqlalchemy.exc import IntegrityError
        from app.models.task import CommTask
        # 计算 demand_code
        seq = len(list(db.scalars(select(TrafficDemand)))) + 1
        demand_code = f"D-{params['task_code'][2:]}-{seq:02d}"
        valid_from = params.get("valid_from_min") or get_clock().now_min()
        new_d = TrafficDemand(
            demand_code=demand_code,
            task_code=params["task_code"],
            valid_from_min=valid_from,
            demand_gbps=params["demand_gbps"],
            valid_to_min=None,
        )
        db.add(new_d)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        data["result"] = {"demand_code": demand_code, "triggered_action": "ACT-02"}
    elif sub == "link_restored":
        data["result"] = handle_link_restored(db, params["link_code"])
    elif sub == "repair_receipt":
        if not params.get("order_code"):
            raise BizException(message="缺少 order_code（请用 '回执 RP-XXX 预计 N 分'）", code=400)
        result = handle_repair_update(
            db,
            order_code=params["order_code"],
            est_finish_min=params.get("est_finish_min"),
        )
        data["result"] = RepairOrderOut.model_validate(result["order"], from_attributes=True).model_dump()
    else:
        raise BizException(message=parsed.get("message", "未识别指令"), code=400)

    db.commit()
    return ApiResponse(data=data)


def _dispatch_legacy(db: Session, data: Any, intent: dict) -> ApiResponse:
    """老 intent 派发（兼容旧 SK-10 调用）。"""
    name: str = intent.get("intent", "unknown")
    if name == "alarm_inject":
        ap = inject_alarm(
            AlarmInjectIn(link_code=intent["link_code"], alarm_type="link_down", native_severity="critical"),
            db,
        )
        data["result"] = ap.data
    elif name == "switch":
        data["result"] = handle_link_down(db, intent["link_code"])
    elif name == "approve" and intent.get("approval_code"):
        ap = decide_approval(db, intent["approval_code"], "批准", "ROLE-APPROVER", "人工")
        data["result"] = ApprovalOut.model_validate(ap, from_attributes=True).model_dump()
    elif name == "reject" and intent.get("approval_code"):
        ap = decide_approval(db, intent["approval_code"], "拒绝", "ROLE-APPROVER", "人工")
        data["result"] = ApprovalOut.model_validate(ap, from_attributes=True).model_dump()
    elif name == "repair_receipt" and intent.get("order_code"):
        # SK-10 输入框隐式约定 est_finish_min=30 相对时长
        from app.core.scene_clock import get_clock as _gc

        now = _gc().now_min()
        result = handle_repair_update(
            db,
            order_code=intent["order_code"],
            est_finish_min=now + 30,
        )
        data["result"] = RepairOrderOut.model_validate(result["order"], from_attributes=True).model_dump()
    elif name == "restore":
        data["result"] = handle_link_restored(db, intent["link_code"])
    else:
        raise BizException(message="未识别指令", code=400)
    db.commit()
    return ApiResponse(data=data)


# ---------- SK-WS 业务事件流（可与 engine 共用 hub） ----------
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
