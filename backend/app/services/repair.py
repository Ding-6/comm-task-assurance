"""ACT-05 修理单申请 + 回执评估（PyMySQL 原生版，无 ORM）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.scene_clock import get_clock
from app.db.repo import (
    AlarmRepo, EventRepo, LinkRepo, PlanRepo, RepairRepo, TaskRepo,
)
from app.db.row import RepairOrderRow, SceneEventRow


def _critical_deadline() -> int:
    cs = [t.critical_min for t in TaskRepo.list_all() if t.critical_min is not None]
    return min(cs) if cs else 50


def request_repair(alarm_code: str, plan_code: Optional[str] = None) -> RepairOrderRow:
    """ACT-01 / ACT-04 内部：发起一张新修理单（已申请）。"""
    clock = get_clock()
    now = clock.now_min()
    alarm = AlarmRepo.get(alarm_code)
    if alarm is None:
        raise ValueError(f"告警不存在: {alarm_code}")
    link = LinkRepo.get(alarm.link_code)
    if link is None:
        raise ValueError(f"链路不存在: {alarm.link_code}")
    seq = RepairRepo.next_seq(alarm.link_code)
    order = RepairOrderRow(
        order_code=f"RP-{alarm.link_code}-{seq:03d}",
        link_code=alarm.link_code, alarm_code=alarm_code,
        request_min=now, est_finish_min=link.avg_repair_min,
        status="已申请",
    )
    RepairRepo.insert(order)
    EventRepo.insert(SceneEventRow(
        event_code=f"EVT-RO-{datetime.now().strftime('%H%M%S%f')}",
        event_type="repair_received", ref_type="repair_order", ref_code=order.order_code,
        occur_min=now, summary=f"已申请修理 {order.order_code}",
    ))
    return order


def handle_repair_update(
    order_code: str,
    est_finish_min: Optional[int] = None,
    actual_finish_min: Optional[int] = None,
    as_of_min: Optional[int] = None,
) -> Dict[str, Any]:
    """处理修理回执 / 修理完成。"""
    clock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()
    order = RepairRepo.get(order_code)
    if order is None:
        raise ValueError(f"修理单不存在: {order_code}")
    plans_updated: List[Any] = []
    event_type = "repair_received"

    # ---------- 1. 回执 ----------
    if est_finish_min is not None:
        RepairRepo.update_receipt(order_code, est_finish_min, "已回执")
        pending = PlanRepo.list_pending_by_link(order.link_code)
        dead_line = _critical_deadline()
        for p in pending:
            if p.plan_type != "repair":
                continue
            if est_finish_min <= dead_line:
                PlanRepo.update_can_meet_critical(p.plan_code, "是")
            else:
                PlanRepo.update_can_meet_critical(p.plan_code, "否", "已作废")
            plans_updated.append(p)
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-ROA-{datetime.now().strftime('%H%M%S%f')}",
            event_type="repair_received", ref_type="repair_order", ref_code=order_code,
            occur_min=now,
            summary=f"修理回执 {order_code} est_finish={est_finish_min} now={now} dead_line={dead_line}",
        ))
        # 推 modal_refresh
        if pending:
            from app.services.engine import safe_push
            payload_plans = [p.to_dict() for p in pending]
            fresh = (
                f"修理单预计T+{est_finish_min}完成，晚于关键期T+{dead_line}，"
                f"转备用为唯一可行方案。倒计时不重置。"
                if est_finish_min > dead_line
                else f"修理单预计T+{est_finish_min}完成，关键期T+{dead_line}可赶上，倒计时不重置。"
            )
            cap = PlanRepo.list_pending_by_link(order.link_code)
            ctx_alarm_code = cap[0].alarm_code if cap else None
            safe_push("modal_refresh", {
                "order_code": order_code, "link_code": order.link_code,
                "alarm_code": ctx_alarm_code, "est_finish_min": est_finish_min,
                "deadline_min": dead_line, "plans": payload_plans,
                "fresh_message": fresh, "reset_countdown": False,
            })

    # ---------- 2. 完成 ----------
    if actual_finish_min is not None:
        RepairRepo.mark_completed(order_code, actual_finish_min)
        event_type = "link_restored"
        link = LinkRepo.get(order.link_code)
        if link is not None:
            LinkRepo.update_oper_state(order.link_code, "up")
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-DN-{datetime.now().strftime('%H%M%S%f')}",
            event_type="state_changed", ref_type="link", ref_code=order.link_code,
            occur_min=now, summary=f"{order.link_code} 修复完成，恢复 up",
        ))
        try:
            from app.services.restore import handle_link_restored
            handle_link_restored(order.link_code, as_of_min=as_of_min)
        except Exception:
            pass

    order = RepairRepo.get(order_code)
    return {"order": order, "event_type": event_type, "plans_updated": plans_updated}
