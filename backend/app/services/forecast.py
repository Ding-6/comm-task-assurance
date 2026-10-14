"""前瞻预测逐分钟数据（PyMySQL 原生版）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.core.scene_clock import get_clock
from app.db.repo import DemandRepo, LinkRepo, TaskLinkRepo, TaskRepo


def _latest_tasks_on_link(link_code: str) -> List[tuple]:
    bindings = TaskLinkRepo.list_by_link(link_code)
    latest: Dict[str, Any] = {}
    for b in bindings:
        latest.setdefault(b.task_code, b)
    if not latest:
        return []
    all_latest = TaskLinkRepo.latest_per_task_global()
    actual = [tc for tc in latest if all_latest.get(tc) and all_latest[tc].link_code == link_code]
    return [(tc, DemandRepo.list_by_task(tc)) for tc in actual]


def get_forecast(link_code: str, as_of_min: Optional[int] = None) -> Dict[str, Any]:
    clock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()
    link = LinkRepo.get(link_code)
    if link is None:
        raise ValueError(f"链路不存在: {link_code}")
    capacity = link.capacity_gbps or 0.0
    warning = link.warning_gbps or 0.0

    if link.oper_state != "up":
        return {
            "link_code": link_code, "now": now, "lookahead": 0,
            "warning_gbps": warning, "capacity_gbps": capacity,
            "points": [], "hit_min": None, "hit_gbps": None, "outside_min": None,
        }

    task_with_demands = _latest_tasks_on_link(link_code)
    link_la = link.lookahead_min if link.lookahead_min else 30
    tasks_la = [t.lookahead_min if t.lookahead_min else 30 for t in TaskRepo.list_all()]
    lookahead = max([link_la] + tasks_la + [30])

    checkpoints: Set[int] = {now}
    for _, demands in task_with_demands:
        for d in demands:
            if now < d.valid_from_min <= now + lookahead:
                checkpoints.add(d.valid_from_min)

    points: List[Dict[str, Any]] = []
    hit_min = None
    hit_gbps = None
    last_value = None
    for m in sorted(checkpoints):
        total = 0.0
        for _, demands in task_with_demands:
            for d in demands:
                if d.valid_from_min <= m and (d.valid_to_min is None or m < d.valid_to_min):
                    total += d.demand_gbps or 0.0
        total = round(total, 2)
        if last_value is None or total != last_value:
            points.append({"min": m, "gbps": total})
            last_value = total
        if hit_min is None and total > capacity:
            hit_min = m
            hit_gbps = total

    return {
        "link_code": link_code,
        "now": now,
        "lookahead": lookahead,
        "warning_gbps": warning,
        "capacity_gbps": capacity,
        "points": points,
        "hit_min": hit_min,
        "hit_gbps": hit_gbps,
        "outside_min": None,
    }
