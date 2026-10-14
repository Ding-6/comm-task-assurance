"""T+120 全窗口证据核对报告（PyMySQL 原生版）。"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from app.db.repo import (
    AlarmRepo, ApprovalRepo, EventRepo, PlanRepo, RepairRepo, TaskLinkRepo,
)


_MANUAL_EVENT_TYPES = {"plan_generated", "approval_decided"}
_GAP_THRESHOLD_MIN = 5


def get_evidence_report() -> Dict[str, Any]:
    events = EventRepo.list_all()
    sorted_events = sorted(events, key=lambda e: (e.occur_min, e.event_code))
    gap_at = [int(prev.occur_min) for prev, curr in zip(sorted_events, sorted_events[1:])
              if int(curr.occur_min) - int(prev.occur_min) > _GAP_THRESHOLD_MIN]
    event_continuity = {
        "total": len(sorted_events),
        "has_gap": bool(gap_at),
        "gap_at_min": gap_at,
        "result": "有断点" if gap_at else "连续",
    }

    # 任务归属历史
    bindings = TaskLinkRepo.list_all()
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    task_order: List[str] = []
    for b in sorted(bindings, key=lambda x: (x.task_code, x.assigned_min, x.id or 0)):
        if b.task_code not in grouped:
            task_order.append(b.task_code)
        grouped[b.task_code].append({
            "assigned_min": b.assigned_min,
            "link_code": b.link_code,
            "note": b.note or "",
        })
    task_history = [{"task_code": tc, "history": grouped[tc]} for tc in task_order]

    # 审批
    approvals = ApprovalRepo.list_all()
    approval_records = [{
        "approval_code": a.approval_code, "plan_code": a.plan_code,
        "decision": a.decision or "", "approver": a.approver or "",
        "decision_source": a.decision_source or "",
        "decided_min": a.decided_min, "reason": a.reason or "",
    } for a in approvals]

    plans = PlanRepo.list_all()
    repair_orders = RepairRepo.list_all()
    alarms = AlarmRepo.list_all()
    final_states = {
        "plans": [{
            "plan_code": p.plan_code, "alarm_code": p.alarm_code,
            "plan_type": p.plan_type, "link_code": p.link_code,
            "target_link_code": p.target_link_code, "status": p.status or "",
            "is_default": p.is_default or "", "can_meet_critical": p.can_meet_critical or "",
            "generated_min": p.generated_min,
        } for p in plans],
        "repair_orders": [{
            "order_code": o.order_code, "link_code": o.link_code,
            "alarm_code": o.alarm_code, "request_min": o.request_min,
            "est_finish_min": o.est_finish_min, "actual_finish_min": o.actual_finish_min,
            "status": o.status or "",
        } for o in repair_orders],
        "alarms": [{
            "alarm_code": a.alarm_code, "link_code": a.link_code,
            "alarm_type": a.alarm_type or "", "native_severity": a.native_severity or "",
            "event_min": a.event_min, "predicted_min": a.predicted_min,
        } for a in alarms],
    }

    timeline = [{
        "occur_min": e.occur_min, "event_type": e.event_type or "",
        "summary": e.summary or "", "is_manual_node": (e.event_type or "") in _MANUAL_EVENT_TYPES,
    } for e in sorted_events]

    return {
        "event_continuity": event_continuity,
        "task_history": task_history,
        "approval_records": approval_records,
        "final_states": final_states,
        "timeline": timeline,
    }
