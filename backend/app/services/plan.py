"""ACT-03 处置方案生成（PyMySQL 原生版，无 ORM）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.scene_clock import get_clock
from app.db.repo import (
    AlarmRepo, DemandRepo, EventRepo, LinkRepo, PlanRepo, TaskLinkRepo, TaskRepo,
)
from app.db.row import CommPlanRow, SceneEventRow


def _critical_deadline() -> int:
    """所有任务 critical_min 的最小非空值；缺省 50。"""
    cs = [t.critical_min for t in TaskRepo.list_all() if t.critical_min is not None]
    return min(cs) if cs else 50


def _alloc_plan_code(link_code: str, plan_type: str, seq: int) -> str:
    """PLAN-<link>-<REPAIR|REROUTE> 首次简格式；重复 +NN 后缀。"""
    base = f"PLAN-{link_code}-{plan_type.upper()}"
    return base if seq <= 1 else f"{base}-{seq:02d}"


def _next_plan_seq(link_code: str) -> int:
    return len(PlanRepo.list_by_link(link_code)) + 1


def _compute_predicted_load_after(target_link: str, migrated_demands_by_task, now: int, lookahead: int = 30) -> float:
    """目标链路未来窗口内的最大预测占用。"""
    target_bindings = TaskLinkRepo.list_by_link(target_link)
    target_codes = []
    seen = set()
    for b in target_bindings:
        if b.task_code in seen:
            continue
        seen.add(b.task_code)
        target_codes.append(b.task_code)
    # 真实承载 = 全局最新归属就是本链路的
    all_latest = TaskLinkRepo.latest_per_task_global()
    actual_codes = [tc for tc in target_codes if all_latest.get(tc) and all_latest[tc].link_code == target_link]
    target_demands = []
    for tc in actual_codes:
        target_demands.extend(DemandRepo.list_by_task(tc))
    migrated = [d for ds in migrated_demands_by_task.values() for d in ds]
    all_demands = target_demands + migrated
    checkpoints = {now + lookahead}
    for d in all_demands:
        if now < d.valid_from_min <= now + lookahead:
            checkpoints.add(d.valid_from_min)
    peak = 0.0
    for m in sorted(checkpoints):
        total = 0.0
        for d in all_demands:
            if d.valid_from_min <= m and (d.valid_to_min is None or m < d.valid_to_min):
                total += d.demand_gbps or 0.0
        if total > peak:
            peak = total
    return peak


def generate_plans(alarm_link_code: str, as_of_min: Optional[int] = None,
                   _override_alarm_code: Optional[str] = None) -> Dict[str, Any]:
    clock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()
    link = LinkRepo.get(alarm_link_code)
    if link is None:
        raise ValueError(f"链路不存在: {alarm_link_code}")
    avg_repair_min = link.avg_repair_min or 30
    backup_code = link.backup_link_code
    dead_line = _critical_deadline()

    cap_alarm = None
    if _override_alarm_code is not None:
        cap_alarm = AlarmRepo.get(_override_alarm_code)
    if cap_alarm is None:
        cap_alarm = AlarmRepo.latest_capacity_alarm(alarm_link_code)
    if cap_alarm is None:
        cap_alarm = AlarmRepo.latest_any(alarm_link_code)
    if cap_alarm is None:
        raise ValueError(f"链路 {alarm_link_code} 未找到任何告警，无法生成方案")
    alarm_code = cap_alarm.alarm_code

    # 幂等：同告警 + 待审批 → 复用
    existing = PlanRepo.list_pending_by_alarm(alarm_code)
    if existing:
        return {
            "plans": [p.to_dict() for p in existing],
            "deadline_min": dead_line,
            "trigger_alarm_code": alarm_code,
        }

    # 故障链路上待迁移任务
    fault_rows = TaskLinkRepo.list_by_link(alarm_link_code)
    latest: Dict[str, Any] = {}
    for b in fault_rows:
        latest.setdefault(b.task_code, b)
    task_codes = [b.task_code for b in latest.values()]
    migrated_demands: Dict[str, list] = {tc: DemandRepo.list_by_task(tc) for tc in task_codes}

    plans: List[CommPlanRow] = []
    seq = _next_plan_seq(alarm_link_code)
    repair_code = _alloc_plan_code(alarm_link_code, "repair", seq)
    can_repair = (now + avg_repair_min) <= dead_line
    repair_plan = CommPlanRow(
        plan_code=repair_code, alarm_code=alarm_code, plan_type="repair",
        link_code=alarm_link_code, target_link_code=None,
        est_repair_min=avg_repair_min, predicted_load_after_gbps=0.0,
        can_meet_critical="是" if can_repair else "否",
        status="待审批", is_default="否",
        recommendation=f"预计 {avg_repair_min} 分钟修复，{'能' if can_repair else '不能'}赶上关键期第 {dead_line} 分钟",
        generated_min=now,
    )
    PlanRepo.insert(repair_plan)
    plans.append(repair_plan)

    # 修复（major 4）：当故障链路自身没有 backup_link_code 时，
    # 用其上"被切过来"的任务原链路（note 含 "由 X 切换而来"）的 backup_link_code 作为目标。
    # §1.6 场景：L-2H 无 backup，但 L-1H 有 backup=L-3H；T-A 由 L-1H 切到 L-2H 触发容量告警，
    # REROUTE 必须能迁回 L-3H。
    if not backup_code:
        derived_targets: List[str] = []
        for b in latest.values():
            note = b.note or ""
            if "由" in note and "切换而来" in note:
                # note 形如 "L-1H 批准转备用，由 20 执行" 或 "L-1H 切换而来"
                import re as _re
                m = _re.search(r"由\s*(\S+?)\s*(?:批准|切换|执行|故障|恢复)", note)
                if not m:
                    # 退而求其次：取 "由 X" 后面第一个非空 token
                    m = _re.search(r"由\s+(\S+)", note)
                if m:
                    orig = m.group(1)
                    if orig and orig not in (None, alarm_link_code):
                        derived_targets.append(orig)
        # 拿原链路的 backup_link_code 作为目标
        derived_backup = None
        for orig in derived_targets:
            orig_link = LinkRepo.get(orig)
            if orig_link and orig_link.backup_link_code:
                derived_backup = orig_link.backup_link_code
                break
        if derived_backup:
            backup_code = derived_backup

    if backup_code:
        reroute_code = _alloc_plan_code(alarm_link_code, "reroute", seq + 1)
        predicted_after = _compute_predicted_load_after(backup_code, migrated_demands, now)
        bk = LinkRepo.get(backup_code)
        can_reroute = bk is not None and predicted_after < (bk.capacity_gbps or 0)
        reroute_plan = CommPlanRow(
            plan_code=reroute_code, alarm_code=alarm_code, plan_type="reroute",
            link_code=alarm_link_code, target_link_code=backup_code,
            est_repair_min=avg_repair_min,
            predicted_load_after_gbps=round(predicted_after, 2),
            can_meet_critical="是" if can_reroute else "否",
            status="待审批", is_default="是",
            recommendation=f"将故障链路上的指挥任务迁至备用链路 {backup_code}，立即解除容量风险。60 秒未决策将自动执行此方案。",
            generated_min=now,
        )
        PlanRepo.insert(reroute_plan)
        plans.append(reroute_plan)

    EventRepo.insert(SceneEventRow(
        event_code=f"EVT-PL-{datetime.now().strftime('%H%M%S%f')}",
        event_type="plan_generated", ref_type="plan",
        ref_code=plans[0].plan_code if plans else alarm_code,
        occur_min=now, summary=f"已为 {alarm_code} 生成 {len(plans)} 个方案",
    ))

    # ws 推送模态框
    from app.services.engine import safe_push
    payload_plans = [p.to_dict() for p in plans]
    predicted_load = round(float(link.future_load_gbps or 0), 2)
    predicted_min = int(cap_alarm.predicted_min) if cap_alarm.predicted_min is not None else None
    capacity_gbps = float(link.capacity_gbps or 0)
    remain_min = (predicted_min - now) if predicted_min is not None else None
    safe_push("modal_approval", {
        "alarm_code": alarm_code,
        "link_code": alarm_link_code,
        "predicted_min": predicted_min,
        "predicted_load": predicted_load,
        "capacity": capacity_gbps,
        "remain_min": remain_min,
        "fresh_message": None,
        "plans": payload_plans,
        "countdown": 60,
        "deadline_min": dead_line,
    })

    # 启动 60 秒倒计时
    if plans:
        try:
            from app.services.approval import start_countdown
            start_countdown(alarm_code, plans[0].plan_code, timeout_sec=60)
        except Exception:
            pass

    return {
        "plans": [p.to_dict() for p in plans],
        "deadline_min": dead_line,
        "trigger_alarm_code": alarm_code,
    }


def plan_to_dict(p) -> Dict[str, Any]:
    """兼容老 API。"""
    return p.to_dict() if hasattr(p, "to_dict") else dict(p)
