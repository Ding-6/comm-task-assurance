"""ACT-03 处置方案生成（repair + reroute）。"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.ws import get_hub
from app.core.scene_clock import SceneClock, get_clock
from app.models.alarm import ResourceAlarm
from app.models.demand import TrafficDemand
from app.models.event import SceneEvent
from app.models.link import CommLink
from app.models.plan import CommPlan
from app.models.task import CommTask
from app.models.task_link import TaskLinkBinding


# ---------- 工具方法 ----------

def _next_plan_seq(db: Session, link_code: str) -> int:
    """本链路已存在的方案数 +1。"""
    rows = list(db.scalars(select(CommPlan).where(CommPlan.link_code == link_code)))
    return len(rows) + 1


def _load_fault_tasks(db: Session, link_code: str) -> List[TaskLinkBinding]:
    """取该链路当前承载的任务（最新归属折叠）。"""
    rows: List[TaskLinkBinding] = list(
        db.scalars(
            select(TaskLinkBinding)
            .where(TaskLinkBinding.link_code == link_code)
            .order_by(TaskLinkBinding.assigned_min.desc())
        )
    )
    latest: Dict[str, TaskLinkBinding] = {}
    for r in rows:
        latest.setdefault(r.task_code, r)
    return list(latest.values())


def _critical_deadline(db: Session) -> int:
    """所有任务 critical_min 的最小非空值（缺省 50）。"""
    rows = list(db.scalars(select(CommTask.critical_min).where(CommTask.critical_min.is_not(None))))
    return min(rows) if rows else 50


def _load_migrated_demands(
    db: Session, task_codes: List[str]
) -> Dict[str, List[TrafficDemand]]:
    """取出待迁移任务的全部需求。"""
    if not task_codes:
        return {}
    rows = list(
        db.scalars(
            select(TrafficDemand).where(TrafficDemand.task_code.in_(task_codes))
        )
    )
    by_task: Dict[str, List[TrafficDemand]] = {}
    for d in rows:
        by_task.setdefault(d.task_code, []).append(d)
    return by_task


def _compute_predicted_load_after(
    db: Session,
    target_link_code: str,
    migrated_demands_by_task: Dict[str, List[TrafficDemand]],
    now: int,
    lookahead: int = 30,
) -> float:
    """把迁移任务的需求加到目标链路上，逐检查点求最大预测占用。"""
    # 目标链路当前承载任务的代码
    target_bindings: List[TaskLinkBinding] = list(
        db.scalars(
            select(TaskLinkBinding)
            .where(TaskLinkBinding.link_code == target_link_code)
            .order_by(TaskLinkBinding.assigned_min.desc())
        )
    )
    target_task_codes: List[str] = []
    seen: set[str] = set()
    for b in target_bindings:
        if b.task_code in seen:
            continue
        seen.add(b.task_code)
        target_task_codes.append(b.task_code)

    # 合并：目标原任务 + 待迁任务的需求
    target_demands: List[TrafficDemand] = list(
        db.scalars(
            select(TrafficDemand).where(TrafficDemand.task_code.in_(target_task_codes))
        )
    )
    all_demands: List[TrafficDemand] = list(target_demands) + [
        d
        for ds in migrated_demands_by_task.values()
        for d in ds
    ]

    # 检查点集合：now+lookahead + 所有未来 valid_from_min
    checkpoints: set[int] = {now + lookahead}
    for d in all_demands:
        if now < d.valid_from_min <= now + lookahead:
            checkpoints.add(d.valid_from_min)

    # 逐检查点求和
    peak: float = 0.0
    for m_prime in sorted(checkpoints):
        total: float = 0.0
        for d in all_demands:
            if d.valid_from_min <= m_prime and (
                d.valid_to_min is None or m_prime < d.valid_to_min
            ):
                total += float(d.demand_gbps or 0)
        if total > peak:
            peak = total
    return peak


# ---------- 主流程 ----------

def generate_plans(
    db: Session,
    alarm_link_code: str,
    as_of_min: Optional[int] = None,
    _override_alarm_code: Optional[str] = None,
) -> Dict[str, Any]:
    """识别资源异常后生成处置方案（repair + reroute），并触发审批流程。

    _override_alarm_code：可选，强制使用传入的 alarm_code 而不是从该链路
    最新容量超限告警中查找。用于让"某个具体告警 → 自己的方案"显式关联。

    返回：
        {
            "plans": [CommPlan, ...],
            "deadline_min": int,
            "trigger_alarm_code": str,
        }
    """
    clock: SceneClock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()

    # 1. 查故障链路
    link: Optional[CommLink] = db.get(CommLink, alarm_link_code)
    if link is None:
        raise ValueError(f"链路不存在: {alarm_link_code}")
    avg_repair_min: int = link.avg_repair_min if link.avg_repair_min else 30
    backup_code: Optional[str] = link.backup_link_code

    # 2. 关键期 dead_line
    dead_line: int = _critical_deadline(db)

    # 3. 找到触发告警
    cap_alarm: Optional[ResourceAlarm] = None
    if _override_alarm_code is not None:
        cap_alarm = db.get(ResourceAlarm, _override_alarm_code)
    if cap_alarm is None:
        cap_alarm = db.scalar(
            select(ResourceAlarm)
            .where(
                ResourceAlarm.link_code == alarm_link_code,
                ResourceAlarm.alarm_type == "容量超限",
            )
            .order_by(ResourceAlarm.event_min.desc())
            .limit(1)
        )
    if cap_alarm is None:
        # 兜底：取该链路最新一条任意告警
        cap_alarm = db.scalar(
            select(ResourceAlarm)
            .where(ResourceAlarm.link_code == alarm_link_code)
            .order_by(ResourceAlarm.event_min.desc())
            .limit(1)
        )
    if cap_alarm is None:
        raise ValueError(f"链路 {alarm_link_code} 未找到任何告警，无法生成方案")
    alarm_code: str = cap_alarm.alarm_code

    # 3.5 幂等检查：同告警 + status='待审批' → 直接返回
    existing: List[CommPlan] = list(
        db.scalars(
            select(CommPlan).where(
                CommPlan.alarm_code == alarm_code,
                CommPlan.status == "待审批",
            )
        )
    )
    if existing:
        return {
            "plans": existing,
            "deadline_min": dead_line,
            "trigger_alarm_code": alarm_code,
        }

    # 4. 故障链路上待迁移任务
    bindings = _load_fault_tasks(db, alarm_link_code)
    migrated_demands = _load_migrated_demands(db, [b.task_code for b in bindings])

    plans: List[CommPlan] = []
    repair_seq = _next_plan_seq(db, alarm_link_code)
    reroute_seq = repair_seq + 1

    # 5. 方案一：修复原链路
    repair_code = f"PLAN-{alarm_link_code}-REPAIR-{repair_seq:02d}"
    can_repair: bool = (now + avg_repair_min) <= dead_line  # 等于算赶上
    repair_plan = CommPlan(
        plan_code=repair_code,
        alarm_code=alarm_code,
        plan_type="repair",
        link_code=alarm_link_code,
        target_link_code=None,
        est_repair_min=avg_repair_min,
        predicted_load_after_gbps=0.0,
        can_meet_critical="是" if can_repair else "否",
        status="待审批",
        is_default="否",
        recommendation=(
            f"预计 {avg_repair_min} 分钟修复，"
            f"{'能' if can_repair else '不能'}赶上关键期第 {dead_line} 分钟"
        ),
        generated_min=now,
    )
    db.add(repair_plan)
    plans.append(repair_plan)

    # 6. 方案二：迁入备用链路
    if backup_code:
        reroute_code = f"PLAN-{alarm_link_code}-REROUTE-{reroute_seq:02d}"
        # 真实计算 predicted_load_after_gbps：把迁移任务需求加到 backup
        predicted_after: float = _compute_predicted_load_after(
            db, backup_code, migrated_demands, now
        )
        backup_link: Optional[CommLink] = db.get(CommLink, backup_code)
        can_reroute: bool = (
            backup_link is not None
            and predicted_after < float(backup_link.capacity_gbps or 0)
        )
        reroute_plan = CommPlan(
            plan_code=reroute_code,
            alarm_code=alarm_code,
            plan_type="reroute",
            link_code=alarm_link_code,
            target_link_code=backup_code,
            est_repair_min=avg_repair_min,
            predicted_load_after_gbps=round(predicted_after, 2),
            can_meet_critical="是" if can_reroute else "否",
            status="待审批",
            is_default="是",
            recommendation=(
                f"将故障链路上的指挥任务迁至备用链路 {backup_code}，"
                f"立即解除容量风险。60 秒未决策将自动执行此方案。"
            ),
            generated_min=now,
        )
        db.add(reroute_plan)
        plans.append(reroute_plan)

    # 7. 写事件流水
    db.add(
        SceneEvent(
            event_code=f"EVT-PL-{datetime.now().strftime('%H%M%S%f')}",
            event_type="plan_generated",
            ref_type="plan",
            ref_code=plans[0].plan_code if plans else alarm_code,
            occur_min=now,
            summary=f"已为 {alarm_code} 生成 {len(plans)} 个方案",
        )
    )
    db.flush()

    # 8. ws 推送 modal_approval（含 60s 倒计时；时钟不挂起）
    payload_plans: List[Dict[str, Any]] = [
        {
            "plan_code": p.plan_code,
            "plan_type": p.plan_type,
            "is_default": p.is_default,
            "can_meet_critical": p.can_meet_critical,
            "predicted_load_after_gbps": float(p.predicted_load_after_gbps or 0),
            "status": p.status,
            "recommendation": p.recommendation,
        }
        for p in plans
    ]

    from app.services.engine import safe_push

    # 8. ws 推送 modal_approval
    safe_push(
        "modal_approval",
        {
            "plans": payload_plans,
            "countdown": 60,
            "deadline_min": dead_line,
            "alarm_code": alarm_code,
            "link_code": alarm_link_code,
        },
    )

    # 9. 启动 60 秒倒计时（修复 M9：原 api/comm.create_or_get_approval 启动会被同步路由 cancel，移到这里）
    if plans:
        try:
            from app.services.approval import start_countdown

            start_countdown(alarm_code, plans[0].plan_code, timeout_sec=60)
        except Exception:
            pass

    return {
        "plans": plans,
        "deadline_min": dead_line,
        "trigger_alarm_code": alarm_code,
    }


# ---------- 兼容旧接口 ----------

def generate_plans_for_alarm(db: Session, alarm_code: str) -> List[CommPlan]:
    """兼容旧调用：透传 alarm_code 关联到方案。"""
    alarm: Optional[ResourceAlarm] = db.get(ResourceAlarm, alarm_code)
    if alarm is None:
        return []
    result = generate_plans(db, alarm.link_code, _override_alarm_code=alarm_code)
    return result.get("plans", [])
