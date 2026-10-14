"""场景引擎（PyMySQL 原生版本，无 ORM）。

包含：
- tick 推进 / 预订事件兑现
- 状态快照
- 复位
- 输入框统一入口
- 内部辅助：add_task / add_link
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.common.ws import get_hub
from app.core.scene_clock import SceneClock, get_clock
from app.db import transaction
from app.db.repo import (
    AlarmRepo, ApprovalRepo, DemandRepo, EventRepo, LinkRepo, PlanRepo,
    RepairRepo, TaskLinkRepo, TaskRepo,
)
from app.db.row import (
    CommLinkRow, CommTaskRow, ResourceAlarmRow, SceneEventRow,
    TaskLinkBindingRow, TrafficDemandRow,
)


# ============================================================
# 全局状态
# ============================================================

_TICK_SECONDS: float = 60.0  # V4.1 固定 1 倍速
_LOOP_TASK: Optional[asyncio.Future[None]] = None
_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None
_CONSUMED_EVENTS: set[str] = set()


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = loop


def safe_push(message_type: str, payload: Any = None) -> None:
    """跨线程安全地把 ws 消息投递到主 event loop。"""
    if _MAIN_LOOP is None or _MAIN_LOOP.is_closed():
        return
    try:
        coro = get_hub().broadcast(message_type, payload)
        asyncio.run_coroutine_threadsafe(coro, _MAIN_LOOP)
    except Exception:
        pass


# ============================================================
# tick 实现
# ============================================================

async def _tick_once() -> None:
    from app.services.occupancy import recalc_all

    clock: SceneClock = get_clock()
    if not clock.is_running() or clock.is_paused:
        return
    now_min = clock.now_min()

    # 1. 兑现预订事件
    booked = EventRepo.list_booked_pending(now_min)
    for ev in booked:
        if ev.event_code in _CONSUMED_EVENTS:
            continue
        _dispatch_booking(ev)
        _CONSUMED_EVENTS.add(ev.event_code)
        # 保留原 task_booked 行；只追加 summary + 新增兑现事件（修复项 6）
        EventRepo.update_summary(ev.event_code, f"{ev.summary or ''} | 已兑现@T+{now_min}")
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-RED-{datetime.now().strftime('%H%M%S%f')}",
            event_type="state_changed", ref_type=ev.ref_type, ref_code=ev.ref_code,
            occur_min=now_min, summary=f"预订事件兑现：{ev.summary}",
        ))

    # 2. 全链路前瞻扫描
    recalc_all(as_of_min=now_min)

    # 3. 推 state 给所有 ws 客户端
    snap = get_state(now_min)
    await get_hub().broadcast("state", snap)


def _dispatch_booking(ev: SceneEventRow) -> None:
    """兑现一个预订事件。"""
    from app.services.plan import generate_plans
    from app.services.switch import handle_link_down

    if ev.ref_type == "alarm" and ev.ref_code:
        link = LinkRepo.get(ev.ref_code)
        if link is None:
            return
        seq = AlarmRepo.count() + 1
        alarm = ResourceAlarmRow(
            alarm_code=f"AL-{seq:02d}", link_code=link.link_code,
            alarm_type="link_down", native_severity="critical",
            event_min=ev.occur_min,
        )
        AlarmRepo.insert(alarm)
        # 补 alarm_injected 事件流水
        now_min = get_clock().now_min()
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-AI-{datetime.now().strftime('%H%M%S%f')}",
            event_type="alarm_injected", ref_type="alarm", ref_code=alarm.alarm_code,
            occur_min=now_min, summary=f"{alarm.alarm_code} link_down @ {link.link_code}",
        ))
        handle_link_down(alarm_link_code=link.link_code, as_of_min=ev.occur_min)
        # 修复（major 1）：link_down 仅发告警 notice，不再立刻 generate_plans。
        # §R1 明确：link_down 只告警；ACT-03（生成方案）必须等 T+20 容量告警。
        # 旧逻辑在 T+15 立刻生成 REROUTE，会在容量告警前抢跑、把 T-A 切到 L-3H。
        # handle_link_down 内已触发 recalc_all，容量告警会自然生成方案。

    elif ev.ref_type == "demand" and ev.ref_code:
        code = ev.ref_code
        existing = DemandRepo.get(code)
        if existing is not None:
            return
        # 解析 summary: task=T-X;gbps=Y
        task_code, gbps = "T-B", 6.00
        if ev.summary:
            for part in ev.summary.split(";"):
                kv = part.split("=", 1)
                if len(kv) != 2:
                    continue
                k, v = kv[0].strip(), kv[1].strip()
                if k == "task":
                    task_code = v
                elif k == "gbps":
                    try:
                        gbps = float(v)
                    except ValueError:
                        pass
        DemandRepo.insert(TrafficDemandRow(
            demand_code=code, task_code=task_code,
            valid_from_min=ev.occur_min, demand_gbps=gbps, valid_to_min=None,
        ))


# ============================================================
# 后台主循环
# ============================================================

async def _engine_loop() -> None:
    while True:
        try:
            await _tick_once()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            try:
                await get_hub().broadcast("engine_error", {"error": str(exc)})
            except Exception:
                pass
        await asyncio.sleep(_TICK_SECONDS)


def _ensure_loop() -> None:
    global _LOOP_TASK
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return
    if _MAIN_LOOP is None or _MAIN_LOOP.is_closed():
        return
    _LOOP_TASK = asyncio.run_coroutine_threadsafe(_engine_loop(), _MAIN_LOOP)


def _stop_loop() -> None:
    global _LOOP_TASK
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        _LOOP_TASK.cancel()
    _LOOP_TASK = None


# ============================================================
# 场景控制
# ============================================================

def start_scene() -> Dict[str, Any]:
    clock = get_clock()
    clock.start()
    _CONSUMED_EVENTS.clear()
    _ensure_loop()
    return get_state(clock.now_min())


def pause_scene() -> Dict[str, Any]:
    get_clock().pause()
    return get_state(get_clock().now_min())


def resume_scene() -> Dict[str, Any]:
    get_clock().resume()
    _ensure_loop()
    return get_state(get_clock().now_min())


def reset_scene() -> Dict[str, Any]:
    """完整复位：停循环 + 重置时钟 + 清空业务表 + 重新加载默认数据。"""
    global _LOOP_TASK
    _stop_loop()
    get_clock().reset()
    _CONSUMED_EVENTS.clear()
    reset_default_scene()
    return get_state(0)


def reset_default_scene() -> None:
    """仅复位业务数据（不动时钟与后台循环）。"""
    with transaction():
        ApprovalRepo.delete_all()
        PlanRepo.delete_all()
        RepairRepo.delete_all()
        AlarmRepo.delete_all()
        EventRepo.delete_all()
        TaskLinkRepo.delete_all()
        DemandRepo.delete_all()
        TaskRepo.delete_all()
        LinkRepo.delete_all()

        for l in [
            CommLinkRow(link_code="L-1H", link_name="指挥通信主用电路",
                        capacity_gbps=10.00, warning_gbps=8.00, oper_state="up",
                        avg_repair_min=30, lookahead_min=30, backup_link_code="L-3H",
                        current_load_gbps=0.00, future_load_gbps=0.00, warn_status="正常"),
            CommLinkRow(link_code="L-2H", link_name="直播与既有共享电路",
                        capacity_gbps=10.00, warning_gbps=8.00, oper_state="up",
                        avg_repair_min=30, lookahead_min=30, backup_link_code=None,
                        current_load_gbps=0.00, future_load_gbps=0.00, warn_status="正常"),
            CommLinkRow(link_code="L-3H", link_name="指挥通信备用电路",
                        capacity_gbps=10.00, warning_gbps=8.00, oper_state="up",
                        avg_repair_min=30, lookahead_min=30, backup_link_code=None,
                        current_load_gbps=0.00, future_load_gbps=0.00, warn_status="正常"),
        ]:
            LinkRepo.insert(l)

        for t in [
            CommTaskRow(task_code="T-A", task_name="指挥通信保障", duration_min=120,
                        initial_demand_gbps=3.00, owner_role="ROLE-DUTY",
                        current_link_code="L-1H", lookahead_min=30),
            CommTaskRow(task_code="T-B", task_name="直播传输保障", duration_min=120,
                        critical_min=50, initial_demand_gbps=4.00,
                        owner_role="ROLE-B", current_link_code="L-2H", lookahead_min=30),
            CommTaskRow(task_code="T-C", task_name="既有传输保障", duration_min=120,
                        initial_demand_gbps=2.00, owner_role="ROLE-DUTY",
                        current_link_code="L-2H", lookahead_min=30),
        ]:
            TaskRepo.insert(t)

        for d in [
            TrafficDemandRow(demand_code="D-A", task_code="T-A",
                             valid_from_min=0, demand_gbps=3.00, valid_to_min=None),
            TrafficDemandRow(demand_code="D-B1", task_code="T-B",
                             valid_from_min=0, demand_gbps=4.00, valid_to_min=50),
            TrafficDemandRow(demand_code="D-C", task_code="T-C",
                             valid_from_min=0, demand_gbps=2.00, valid_to_min=None),
        ]:
            DemandRepo.insert(d)

        for b in [
            TaskLinkBindingRow(task_code="T-A", link_code="L-1H", assigned_min=0, note="场景初始归属"),
            TaskLinkBindingRow(task_code="T-B", link_code="L-2H", assigned_min=0, note="场景初始归属"),
            TaskLinkBindingRow(task_code="T-C", link_code="L-2H", assigned_min=0, note="场景初始归属"),
        ]:
            # 修复 bug1：upsert 取代 insert
            TaskLinkRepo.upsert(
                task_code=b.task_code, link_code=b.link_code,
                assigned_min=b.assigned_min, note=b.note,
            )

        for e in [
            SceneEventRow(event_code="EVT-0001", event_type="task_booked",
                          ref_type="alarm", ref_code="L-1H", occur_min=15,
                          summary="15分钟后线路1故障，预订为T+15兑现"),
            SceneEventRow(event_code="EVT-0002", event_type="task_booked",
                          ref_type="demand", ref_code="D-B2", occur_min=50,
                          summary="task=T-B;gbps=6.00;50分钟后直播需求到6.00G"),
        ]:
            EventRepo.insert(e)

    # 复位后立即重算占用，确保 _pick_target / 快照看到正确负载
    from app.services.occupancy import recalc_all
    recalc_all(as_of_min=0)


# ============================================================
# 状态快照
# ============================================================

def get_state(now_min: Optional[int] = None) -> Dict[str, Any]:
    clock = get_clock()
    if now_min is None:
        now_min = clock.now_min()

    wallclock: Optional[str] = None
    if clock.start_wallclock is not None:
        wc = clock.start_wallclock + timedelta(minutes=now_min)
        wallclock = wc.strftime("%H:%M:%S")

    # 链路
    links = LinkRepo.list_all()
    backup_owners = {
        l.link_code for l in links if l.backup_link_code
    }
    active_repairs: Dict[str, RepairOrderRow] = {}
    for o in RepairRepo.list_all():
        if o.status != "已完成" and o.link_code not in active_repairs:
            active_repairs[o.link_code] = o

    link_dicts = []
    for l in links:
        item = l.to_dict()
        item["is_backup"] = l.link_code in backup_owners
        ro = active_repairs.get(l.link_code)
        item["active_repair_order"] = (
            {"order_code": ro.order_code, "status": ro.status or ""} if ro else None
        )
        link_dicts.append(item)

    # 任务
    tasks = TaskRepo.list_all()
    latest_bindings = TaskLinkRepo.latest_per_task_global()
    all_demands = DemandRepo.list_all()
    demands_by_task: Dict[str, List[TrafficDemandRow]] = {}
    for d in all_demands:
        demands_by_task.setdefault(d.task_code, []).append(d)

    def _current_demand_gbps(task_code: str) -> float:
        total = 0.0
        for d in demands_by_task.get(task_code, []):
            if d.valid_from_min <= now_min and (d.valid_to_min is None or now_min < d.valid_to_min):
                total += d.demand_gbps or 0.0
        return round(total, 2)

    task_dicts = []
    for t in tasks:
        lb = latest_bindings.get(t.task_code)
        task_dicts.append({
            "task_code": t.task_code,
            "task_name": t.task_name,
            "duration_min": t.duration_min,
            "critical_min": t.critical_min,
            "owner_role": t.owner_role,
            "current_link_code": t.current_link_code,
            "initial_demand_gbps": t.initial_demand_gbps or 0.0,
            "current_demand_gbps": _current_demand_gbps(t.task_code),
            "lookahead_min": t.lookahead_min,
            "last_note": lb.note if lb else None,
        })

    # 待审批方案
    plan_dicts = [p.to_dict() for p in PlanRepo.list_pending()]

    # 最近 20 条事件
    event_dicts = [e.to_dict() for e in EventRepo.list_recent(20)]

    # 任务 → 链路 映射
    carrier = {tc: b.link_code for tc, b in latest_bindings.items()}

    # 未来预订事件
    booked_dicts = []
    for b in EventRepo.list_booked_future(now_min):
        booked_dicts.append({
            "occur_min": b.occur_min,
            "summary": b.summary or "",
            "remain_min": b.occur_min - now_min,
        })

    return {
        "running": clock.is_running(),
        "is_paused": clock.is_paused,
        "now_min": now_min,
        "wallclock": wallclock,
        "tick_seconds": _TICK_SECONDS,
        "links": link_dicts,
        "tasks": task_dicts,
        "task_carrier": carrier,
        "pending_approvals": plan_dicts,
        "events": event_dicts,
        "booked_events": booked_dicts,
    }


# ============================================================
# 输入分发（自然语言 + 结构化）
# ============================================================

def handle_input(category: str, payload: Any) -> Dict[str, Any]:
    """输入框统一入口：category=task|link|alarm|notice。"""
    if category == "task":
        return add_task(**(payload or {}))
    if category == "link":
        return add_link(**(payload or {}))
    if category in ("alarm", "notice"):
        from app.services.intent import parse_input
        return _dispatch_parsed(parse_input(str(payload)))
    raise ValueError(f"未知 category: {category}")


def _dispatch_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    # 修复（minor 7）：parse_input 未识别时返回 {category: None, suggestions: ...}
    # 不应再 raise ValueError 让前端收 400；按 OpenAPI SceneInputFailData 透传
    if parsed.get("category") is None or parsed.get("sub_type") is None:
        return parsed
    sub = parsed.get("sub_type")
    params = parsed.get("params", {})
    minute_offset = parsed.get("minute_offset")

    if minute_offset is not None and sub in ("link_down", "performance_degrade", "demand_change"):
        now = get_clock().now_min()
        occur = now + int(minute_offset)
        ts_suffix = datetime.now().strftime("%H%M%S%f")
        if sub in ("link_down", "performance_degrade"):
            ref_type = "alarm"
            ref_code = params.get("link_code")
            summary = f"{minute_offset} 分钟后 {sub} -> {params.get('link_code')}"
        else:
            ref_type = "demand"
            ref_code = f"D-{params['task_code'][2:]}-BK{ts_suffix}"
            summary = f"task={params['task_code']};gbps={params['demand_gbps']};{minute_offset} 分钟后需求变更"
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-BK-{ts_suffix}",
            event_type="task_booked", ref_type=ref_type, ref_code=ref_code,
            occur_min=occur, summary=summary,
        ))
        return {"booking": {"event_code": f"EVT-BK-{ts_suffix}", "occur_min": occur}}

    if sub in ("link_down", "performance_degrade"):
        from app.services.plan import generate_plans
        from app.services.switch import handle_link_down
        link_code = params["link_code"]
        link = LinkRepo.get(link_code)
        if link is None:
            raise ValueError(f"链路不存在: {link_code}")
        now = get_clock().now_min()
        seq = AlarmRepo.count() + 1
        alarm = ResourceAlarmRow(
            alarm_code=f"AL-{seq:02d}", link_code=link_code,
            alarm_type=params.get("alarm_type", "link_down"),
            native_severity=params.get("native_severity", "critical"),
            event_min=now,
        )
        AlarmRepo.insert(alarm)
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-AI-{datetime.now().strftime('%H%M%S%f')}",
            event_type="alarm_injected", ref_type="alarm", ref_code=alarm.alarm_code,
            occur_min=now, summary=f"{alarm.alarm_code} {alarm.alarm_type} @ {link_code}",
        ))
        switch_result = handle_link_down(alarm_link_code=link_code)
        # 修复（major 1）：HTTP 入口 link_down 也不立刻 generate_plans。
        # 返回当前已存在的待审批方案（可能为 0），由后续 ACT-03 触发。
        from app.db.repo import PlanRepo as _PR
        return {
            "alarm_code": alarm.alarm_code,
            "switch": switch_result,
            "plans": [p.plan_code for p in _PR.list_pending() if p.alarm_code == alarm.alarm_code],
        }

    if sub == "demand_change":
        valid_from = params.get("valid_from_min") or get_clock().now_min()
        seq = DemandRepo.count() + 1
        demand_code = f"D-{params['task_code'][2:]}-{seq:02d}"
        DemandRepo.insert(TrafficDemandRow(
            demand_code=demand_code, task_code=params["task_code"],
            valid_from_min=valid_from, demand_gbps=params["demand_gbps"],
            valid_to_min=None,
        ))
        from app.services.occupancy import recalc_all
        recalc_all(as_of_min=get_clock().now_min())
        return {"demand_code": demand_code, "triggered_action": "ACT-02"}

    if sub == "link_restored":
        from app.services.restore import handle_link_restored
        return handle_link_restored(link_code=params["link_code"])

    if sub == "repair_receipt":
        from app.services.repair import handle_repair_update
        if not params.get("order_code"):
            raise ValueError("缺少 order_code")
        return handle_repair_update(
            order_code=params["order_code"],
            est_finish_min=params.get("est_finish_min"),
        )

    raise ValueError(f"未实现的 sub_type: {sub}")


# ============================================================
# 内部：add_task / add_link
# ============================================================

def add_task(
    task_code: str, task_name: str, duration_min: int,
    link_code: str, demand_gbps: float,
    critical_min: Optional[int] = None,
    owner_role: Optional[str] = None,
    lookahead_min: Optional[int] = 30,
) -> Dict[str, Any]:
    if not task_code:
        raise ValueError("缺少 task_code")
    if TaskRepo.get(task_code) is not None:
        return {"task_code": task_code, "demand_code": None, "existed": True}

    TaskRepo.insert(CommTaskRow(
        task_code=task_code, task_name=task_name, duration_min=duration_min,
        initial_demand_gbps=demand_gbps, critical_min=critical_min,
        owner_role=owner_role, current_link_code=link_code, lookahead_min=lookahead_min,
    ))
    # 首条 demand
    seq = DemandRepo.count() + 1
    demand_code = f"D-NEW-{seq:03d}"
    DemandRepo.insert(TrafficDemandRow(
        demand_code=demand_code, task_code=task_code,
        valid_from_min=get_clock().now_min(), demand_gbps=demand_gbps,
        valid_to_min=None,
    ))
    if link_code:
        # 修复 bug1：upsert 取代 insert
        TaskLinkRepo.upsert(
            task_code=task_code, link_code=link_code,
            assigned_min=get_clock().now_min(), note="任务登记初始归属",
        )
    from app.services.occupancy import recalc_all
    recalc_all(as_of_min=get_clock().now_min())
    return {"task_code": task_code, "demand_code": demand_code, "triggered_action": "ACT-02"}


def add_link(
    link_code: str, link_name: str, capacity_gbps: float, warning_gbps: float,
    oper_state: str = "up",
    avg_repair_min: Optional[int] = 30, lookahead_min: Optional[int] = 30,
    backup_link_code: Optional[str] = None,
) -> Dict[str, Any]:
    if not link_code:
        raise ValueError("缺少 link_code")
    if LinkRepo.get(link_code) is not None:
        return {"link_code": link_code, "existed": True}
    LinkRepo.insert(CommLinkRow(
        link_code=link_code, link_name=link_name, capacity_gbps=capacity_gbps,
        warning_gbps=warning_gbps, oper_state=oper_state,
        avg_repair_min=avg_repair_min, lookahead_min=lookahead_min,
        backup_link_code=backup_link_code, current_load_gbps=0.00,
        future_load_gbps=0.00, warn_status="正常",
    ))
    from app.services.occupancy import recalc_all
    recalc_all(as_of_min=get_clock().now_min())
    return {"link_code": link_code, "triggered_action": "ACT-02"}


# ============================================================
# 兼容：start_countdown 由 approval 内部使用；占位防 import 错误
# ============================================================

def start_countdown(alarm_code: str, plan_code: str, timeout_sec: int = 60) -> None:
    """占位：实际倒计时由 services.approval 启动；这里兼容旧 API 入口。"""
    from app.services.approval import start_countdown as _sc
    _sc(alarm_code, plan_code, timeout_sec=timeout_sec)
