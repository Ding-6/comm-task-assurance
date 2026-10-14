"""ACT-06 链路恢复与任务回迁（PyMySQL 原生版，无 ORM）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.core.scene_clock import get_clock
from app.db.repo import (
    DemandRepo, EventRepo, LinkRepo, TaskLinkRepo, TaskRepo,
)
from app.db.row import SceneEventRow, TaskLinkBindingRow


def _active_demand(task_code: str, at_min: int) -> float:
    total = 0.0
    for d in DemandRepo.list_by_task(task_code):
        if d.valid_from_min <= at_min and (d.valid_to_min is None or at_min < d.valid_to_min):
            total += d.demand_gbps or 0.0
    return total


def _restore_candidates(link_code: str) -> List[TaskLinkBindingRow]:
    """回迁候选：曾归属本链路、但全局最新归属不在本链路的任务。"""
    rows = TaskLinkRepo.list_by_link(link_code)
    candidates: List[TaskLinkBindingRow] = []
    seen: Set[str] = set()
    for b in rows:
        if b.task_code in seen:
            continue
        seen.add(b.task_code)
        global_latest = TaskLinkRepo.latest_per_task_global().get(b.task_code)
        if global_latest is not None and global_latest.link_code != link_code:
            candidates.append(global_latest)
    return candidates


def _current_load_on_link(link_code: str, at_min: int) -> float:
    """该链路在 at_min 时刻的实时占用。"""
    task_codes = {b.task_code for b in TaskLinkRepo.list_by_link(link_code)}
    all_latest = TaskLinkRepo.latest_per_task_global()
    actual = [tc for tc in task_codes if all_latest.get(tc) and all_latest[tc].link_code == link_code]
    return sum(_active_demand(tc, at_min) for tc in actual)


def handle_link_restored(link_code: str, as_of_min: Optional[int] = None) -> Dict[str, Any]:
    clock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()
    link = LinkRepo.get(link_code)
    if link is None:
        raise ValueError(f"链路不存在: {link_code}")

    # 软处理：链路 down 时也允许回迁（但先把状态置 up）
    if link.oper_state != "up":
        prev = link.oper_state
        LinkRepo.update_oper_state(link_code, "up")
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-RS-UP-{datetime.now().strftime('%H%M%S%f')}",
            event_type="state_changed", ref_type="link", ref_code=link_code,
            occur_min=now, summary=f"{link_code} {prev} -> up（恢复通知）",
        ))
        from app.services.engine import safe_push
        safe_push("toast", {"level": "info", "message": f"{link_code} {prev} -> up", "link_code": link_code})

    capacity = link.capacity_gbps or 0.0
    cands = _restore_candidates(link_code)
    if not cands:
        _after_restore(link_code, [], [], now)
        return {
            "link_code": link_code, "restored_tasks": [], "skipped_tasks": [],
            "released_links": [], "released_link_code": link_code,
        }

    current_load = _current_load_on_link(link_code, now)
    restored: List[str] = []
    skipped: List[Dict[str, str]] = []
    original_holders: Set[str] = set()

    for cand in cands:
        original_link = cand.link_code
        demand = _active_demand(cand.task_code, now)
        if current_load + demand > capacity:
            skipped.append({"task_code": cand.task_code, "reason": f"超容量({current_load + demand}>{capacity})"})
            continue
        TaskLinkRepo.insert(TaskLinkBindingRow(
            task_code=cand.task_code, link_code=link_code, assigned_min=now,
            note=f"{link_code} 修复完成，由原承载链路回迁",
        ))
        TaskRepo.update_current_link(cand.task_code, link_code)
        restored.append(cand.task_code)
        current_load += demand
        original_holders.add(original_link)

    _after_restore(link_code, restored, skipped, now)

    # 备用释放
    released_links: List[str] = []
    for holder in original_holders:
        if holder == link_code:
            continue
        holder_bindings = TaskLinkRepo.list_by_link(holder)
        seen = set()
        any_task = False
        all_latest = TaskLinkRepo.latest_per_task_global()
        for b in holder_bindings:
            if b.task_code in seen:
                continue
            seen.add(b.task_code)
            gl = all_latest.get(b.task_code)
            if gl is not None and gl.link_code == holder:
                any_task = True
                break
        if not any_task:
            released_links.append(holder)
            LinkRepo.zero_loads(holder)

    return {
        "link_code": link_code, "restored_tasks": restored, "skipped_tasks": skipped,
        "released_links": released_links, "released_link_code": link_code,
    }


def _after_restore(link_code: str, restored: List[str], skipped: List[Dict[str, str]], now: int) -> None:
    EventRepo.insert(SceneEventRow(
        event_code=f"EVT-RS-{datetime.now().strftime('%H%M%S%f')}",
        event_type="link_restored", ref_type="link", ref_code=link_code,
        occur_min=now, summary=f"{link_code} 恢复，回迁 {len(restored)} 个任务，跳过 {len(skipped)} 个",
    ))
    from app.services.occupancy import recalc_all
    recalc_all(as_of_min=now)
    from app.services.engine import safe_push
    msg = f"{link_code} 已恢复" + (f"，任务回迁完成（{len(restored)} 个）" if restored else "（无任务回迁）")
    safe_push("toast", {"level": "info", "message": msg, "link_code": link_code})
