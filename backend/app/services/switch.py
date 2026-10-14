"""ACT-01 链路故障识别与自动切换（PyMySQL 原生版，无 ORM）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db.repo import (
    EventRepo, LinkRepo, RepairRepo, TaskLinkRepo, TaskRepo,
)
from app.db.row import RepairOrderRow, SceneEventRow, TaskLinkBindingRow


def _fault_tasks(link_code: str) -> List[TaskLinkBindingRow]:
    """该链路当前承载的任务（折叠到 task_code 最新一行）。"""
    rows = TaskLinkRepo.list_by_link(link_code)
    latest: Dict[str, TaskLinkBindingRow] = {}
    for b in rows:
        latest.setdefault(b.task_code, b)
    return list(latest.values())


def _pick_target(fault_link_code: str) -> Optional[str]:
    """切换目标选择（说明书 §4.01 步骤 ④ + §1.6 默认剧本）。

    优先级：
    1. 共享链路：oper_state='up' 且 current_load_gbps > 0 的其他链路（取占用最低）
       ——这是 V4.1 说明书 §1.6 "L-1H 故障后 T-A 切到 L-2H" 的行为，
       让 L-2H 推到 9G 进而触发 §1.6 "T+20 容量超限"。
    2. 备用链路：fault.backup_link_code（如果存在且 up）
    3. 任何 up 链路中占用最低者
    """
    candidates = [l for l in LinkRepo.list_all() if l.link_code != fault_link_code and l.oper_state == "up"]
    # 1. 共享链路优先
    shared = [l for l in candidates if l.current_load_gbps and l.current_load_gbps > 0]
    if shared:
        return sorted(shared, key=lambda l: l.current_load_gbps or 0)[0].link_code
    # 2. 备用链路
    fault = LinkRepo.get(fault_link_code)
    if fault and fault.backup_link_code:
        bk = LinkRepo.get(fault.backup_link_code)
        if bk and bk.oper_state == "up":
            return bk.link_code
    # 3. 兜底：占用最低
    if candidates:
        return sorted(candidates, key=lambda l: l.current_load_gbps or 0)[0].link_code
    return None

def handle_link_down(alarm_link_code: str, as_of_min: Optional[int] = None) -> Dict[str, Any]:
    from app.core.scene_clock import get_clock
    clock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()
    fault = LinkRepo.get(alarm_link_code)
    if fault is None:
        raise ValueError(f"链路不存在: {alarm_link_code}")

    # 幂等：已 down 且无承载任务 → 直接返回
    if fault.oper_state == "down" and not _fault_tasks(alarm_link_code):
        return {"switched_tasks": [], "repair_order_code": "", "from_link": alarm_link_code, "to_link": None}

    LinkRepo.update_oper_state(alarm_link_code, "down")

    bindings = _fault_tasks(alarm_link_code)
    target_code = _pick_target(alarm_link_code) if bindings else None

    for b in bindings:
        if target_code:
            TaskLinkRepo.insert(TaskLinkBindingRow(
                task_code=b.task_code, link_code=target_code,
                assigned_min=now, note=f"由 {alarm_link_code} 切换而来",
            ))
            TaskRepo.update_current_link(b.task_code, target_code)

    seq = RepairRepo.next_seq(alarm_link_code)
    order_code = f"RP-{alarm_link_code}-{seq:03d}"
    RepairRepo.insert(RepairOrderRow(
        order_code=order_code, link_code=alarm_link_code, alarm_code=None,
        request_min=now, est_finish_min=fault.avg_repair_min, status="已申请",
    ))

    EventRepo.insert(SceneEventRow(
        event_code=f"EVT-SW-{datetime.now().strftime('%H%M%S%f')}",
        event_type="switch_done", ref_type="link", ref_code=alarm_link_code,
        occur_min=now,
        summary=f"{alarm_link_code} 中断，{len(bindings)} 个任务切换至 {target_code or '无'}",
    ))

    if target_code:
        from app.services.occupancy import calc_occupancy, recalc_all
        calc_occupancy(target_code, as_of_min=now)
    calc_occupancy(alarm_link_code, as_of_min=now)
    recalc_all(as_of_min=now)

    from app.services.engine import safe_push
    title = "链路中断"
    if target_code and bindings:
        message = f"{alarm_link_code} 链路中断，{len(bindings)} 个任务已自动切换至 {target_code} 链路"
    elif target_code:
        message = f"{alarm_link_code} 链路中断（无承载任务）"
    else:
        message = f"{alarm_link_code} 链路中断，无可用目标链路"
    safe_push("modal_notice", {"title": title, "message": message, "level": "info"})

    return {
        "switched_tasks": [b.task_code for b in bindings],
        "repair_order_code": order_code,
        "from_link": alarm_link_code,
        "to_link": target_code,
    }
