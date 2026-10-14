"""添加任务容量预检（PyMySQL 原生版，不落库）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.scene_clock import get_clock
from app.db.repo import DemandRepo, LinkRepo, TaskLinkRepo, TaskRepo
from app.db.row import CommLinkRow, TrafficDemandRow


def _latest_tasks_on_link(link_code: str) -> List[Tuple[str, List[TrafficDemandRow]]]:
    bindings = TaskLinkRepo.list_by_link(link_code)
    latest: Dict[str, Any] = {}
    for b in bindings:
        latest.setdefault(b.task_code, b)
    if not latest:
        return []
    all_latest = TaskLinkRepo.latest_per_task_global()
    actual_codes = [tc for tc in latest if all_latest.get(tc) and all_latest[tc].link_code == link_code]
    return [(tc, DemandRepo.list_by_task(tc)) for tc in actual_codes]


def precheck_task(link_code: str, demand_gbps: float, as_of_min: Optional[int] = None) -> Dict[str, Any]:
    clock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()
    link = LinkRepo.get(link_code)
    if link is None:
        raise ValueError(f"链路不存在: {link_code}")
    if link.oper_state != "up":
        raise ValueError(f"链路非 up 状态: {link_code} state={link.oper_state}")
    if demand_gbps < 0:
        raise ValueError("demand_gbps 必须 >= 0")

    capacity = link.capacity_gbps or 0.0
    warning = link.warning_gbps or 0.0
    task_with_demands = _latest_tasks_on_link(link_code)

    link_la = link.lookahead_min if link.lookahead_min else 30
    tasks_la = [t.lookahead_min if t.lookahead_min else 30 for t in TaskRepo.list_all()]
    lookahead = max([link_la] + tasks_la + [30])

    def _active(demands: List[TrafficDemandRow], at_min: int) -> float:
        return sum(d.demand_gbps or 0.0 for d in demands
                   if d.valid_from_min <= at_min and (d.valid_to_min is None or at_min < d.valid_to_min))

    current_before = sum(_active(d, now) for _, d in task_with_demands)
    current_after = current_before + demand_gbps

    checkpoints: Set[int] = {now}
    for _, demands in task_with_demands:
        for d in demands:
            if now < d.valid_from_min <= now + lookahead:
                checkpoints.add(d.valid_from_min)

    predicted_max = 0.0
    for m in sorted(checkpoints):
        total = sum(_active(d, m) for _, d in task_with_demands)
        if m >= now:
            total += demand_gbps
        if total > predicted_max:
            predicted_max = total

    will_exceed_capacity = predicted_max > capacity
    will_exceed_warning = current_after > warning

    if will_exceed_capacity:
        message = (
            f"{link_code} 当前 {current_before:.2f}G + {demand_gbps:.2f}G = "
            f"{current_after:.2f}G > {capacity:.2f}G。可以登记，"
            f"登记后会进入方案审批流程。"
        )
    elif will_exceed_warning:
        message = (
            f"{link_code} 当前 {current_before:.2f}G + {demand_gbps:.2f}G = "
            f"{current_after:.2f}G > {warning:.2f}G 预警线。"
            f"建议加容量备用链路或下调需求。"
        )
    else:
        message = "容量充足，可直接登记。"

    return {
        "target_link_code": link_code,
        "current_after": round(current_after, 2),
        "current_before": round(current_before, 2),
        "predicted_max_after": round(predicted_max, 2),
        "capacity_gbps": capacity,
        "warning_gbps": warning,
        "lookahead": lookahead,
        "will_exceed_warning": will_exceed_warning,
        "will_exceed_capacity": will_exceed_capacity,
        "message": message,
    }
