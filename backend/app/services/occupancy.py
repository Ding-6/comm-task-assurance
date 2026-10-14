"""ACT-02 占用计算 + 前瞻扫描（PyMySQL 原生版，无 ORM）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from app.common.ws import get_hub
from app.core.scene_clock import get_clock
from app.db.repo import (
    AlarmRepo, DemandRepo, EventRepo, LinkRepo, PlanRepo, TaskLinkRepo, TaskRepo,
)
from app.db.row import (
    CommLinkRow, ResourceAlarmRow, SceneEventRow, TrafficDemandRow,
)


def _pending_demand_bookings() -> Dict[str, float]:
    """读取未兑现的 task_booked / demand_change 事件 → {task_code: gbps}。

    用途：在 valid_to 失效点之后，任务的"最新需求"还在 comm_event 里没兑现，
    仍要计入 predicted_load 的兜底，避免"0G 空档"导致 predicted_load 被低估。

    只取每个任务"最晚一条预订"，符合 future 的"折叠值"语义。
    """
    latest_per_task: Dict[str, float] = {}
    for ev in EventRepo.list_all():
        if ev.event_type != "task_booked" or ev.ref_type != "demand":
            continue
        # 解析 summary: task=T-B;gbps=6.00;...
        if not ev.summary:
            continue
        task_code, gbps = None, None
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
        if task_code and gbps is not None:
            # 多个预订时取最晚一条（最晚意味着占用影响最大）
            if task_code not in latest_per_task or gbps > latest_per_task[task_code]:
                latest_per_task[task_code] = gbps
    return latest_per_task


# ============================================================
# 工具
# ============================================================

def _latest_tasks_on_link(link_code: str) -> Dict[str, List[TrafficDemandRow]]:
    """返回 {task_code: [其全部 demand]}：该链路当前承载的任务。"""
    bindings = TaskLinkRepo.list_by_link(link_code)
    latest: Dict[str, Any] = {}
    for b in bindings:
        latest.setdefault(b.task_code, b)
    if not latest:
        return {}
    # 验证「最新归属是否就是本链路」
    task_codes = list(latest.keys())
    all_latest = TaskLinkRepo.latest_per_task_global()
    actual_codes = [tc for tc in task_codes if all_latest.get(tc) and all_latest[tc].link_code == link_code]
    if not actual_codes:
        return {}
    out: Dict[str, List[TrafficDemandRow]] = {}
    for tc in actual_codes:
        out[tc] = DemandRepo.list_by_task(tc)
    return out


# ============================================================
# 核心
# ============================================================

def calc_occupancy(link_code: str, as_of_min: Optional[int] = None) -> Dict[str, Any]:
    clock = get_clock()
    now = as_of_min if as_of_min is not None else clock.now_min()
    link = LinkRepo.get(link_code)
    if link is None:
        raise ValueError(f"链路不存在: {link_code}")

    tasks_with_demands = _latest_tasks_on_link(link_code)
    capacity = link.capacity_gbps or 0.0
    warning = link.warning_gbps or 0.0

    # 当前占用
    current = 0.0
    for tc, demands in tasks_with_demands.items():
        for d in demands:
            if d.valid_from_min <= now and (d.valid_to_min is None or now < d.valid_to_min):
                current += d.demand_gbps or 0.0

    # 前瞻窗口
    link_la = link.lookahead_min if link.lookahead_min else 30
    tasks_la = [t.lookahead_min if t.lookahead_min else 30 for t in TaskRepo.list_all()]
    lookahead = max([link_la] + tasks_la + [30])

    # 检查点：窗口终点 + 所有未来 valid_from_min + 所有未来 valid_to_min
    # valid_to 也要纳入检查点，避免 D-B1(0-50) 这种有期限的需求在 T=50 失效后
    # 出现 0G 空档导致 predicted_load 被低估。T+20 时也包含 T=50 检查点，
    # 此时 D-B1 失效 → T-B 0G + D-A 3G + D-C 2G = 5G（说明书 T+20 行说明）。
    # 但 T+15 时窗口 [15,45] 不含 50 → 不会命中，行为正确。
    checkpoints: Set[int] = {now + lookahead}
    for tc, demands in tasks_with_demands.items():
        for d in demands:
            if now < d.valid_from_min <= now + lookahead:
                checkpoints.add(d.valid_from_min)
            if d.valid_to_min is not None and now < d.valid_to_min <= now + lookahead:
                checkpoints.add(d.valid_to_min)

    # 加载预订中的 demand 兜底（避免 0G 空档）
    pending_bookings = _pending_demand_bookings()  # {task_code: gbps}

    # 逐检查点
    sorted_cps = sorted(checkpoints)
    max_predicted = 0.0
    predicted_min = None
    future = 0.0
    for m in sorted_cps:
        total = 0.0
        for tc, demands in tasks_with_demands.items():
            for d in demands:
                in_window = d.valid_from_min <= m and (d.valid_to_min is None or m < d.valid_to_min)
                if in_window:
                    total += d.demand_gbps or 0.0
                else:
                    # 兜底：d 在 m 时刻失效，但任务有更晚的预订需求（comm_event 待兑现）
                    # 此时把预订的 gbps 计入，避免 0G 空档。
                    if (d.valid_to_min is not None and d.valid_to_min == m
                            and tc in pending_bookings):
                        total += pending_bookings[tc]
        if total > max_predicted:
            max_predicted = total
            predicted_min = m
    if sorted_cps:
        first_cp = sorted_cps[0]
        for tc, demands in tasks_with_demands.items():
            for d in demands:
                if d.valid_from_min <= first_cp and (d.valid_to_min is None or first_cp < d.valid_to_min):
                    future += d.demand_gbps or 0.0
            # 兜底：如果 first_cp 处任务所有需求都失效，但有预订需求
            if tc in pending_bookings and not any(
                d.valid_from_min <= first_cp and (d.valid_to_min is None or first_cp < d.valid_to_min)
                for d in demands
            ):
                future += pending_bookings[tc]

    # 判定
    if current > capacity:
        warn_status = "超容量"
    elif max_predicted > capacity:
        warn_status = "超容量"
    elif future > warning:
        warn_status = "超预警线"
    else:
        warn_status = "正常"

    # 回写
    previous = link.warn_status
    LinkRepo.update_loads(link_code, round(current, 2), round(future, 2), warn_status)

    # 容量超限 → 幂等写告警
    new_alarm = None
    if warn_status == "超容量":
        if not AlarmRepo.has_pending_capacity_alarm(link_code):
            from uuid import uuid4
            code = f"AL-{uuid4().hex[:8].upper()}"
            new_alarm = ResourceAlarmRow(
                alarm_code=code, link_code=link_code, alarm_type="容量超限",
                native_severity="critical", event_min=now, predicted_min=predicted_min,
            )
            AlarmRepo.insert(new_alarm)

    # 事件流水
    if new_alarm is not None:
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-OE-{datetime.now().strftime('%H%M%S%f')}",
            event_type="occupancy_exceeded", ref_type="alarm", ref_code=new_alarm.alarm_code,
            occur_min=now,
            summary=f"{link_code} 预测 T+{predicted_min - now if predicted_min else '?'} "
                    f"达 {round(max_predicted, 2)}G 超容量 {capacity}G",
        ))
    elif previous != warn_status:
        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-SC-{datetime.now().strftime('%H%M%S%f')}",
            event_type="state_changed", ref_type="link", ref_code=link_code,
            occur_min=now, summary=f"{link_code} {previous or '无'} -> {warn_status}",
        ))

    # ws 提示
    if warn_status in ("超预警线", "超容量"):
        if warn_status == "超预警线":
            message = f"{link_code} 未来占用 {round(future, 2)}G 超预警线 {warning}G"
        else:
            offset = predicted_min - now if predicted_min is not None else None
            message = f"{link_code} 预测T+{offset}达{round(max_predicted, 2)}G超容量{capacity}G"
        from app.services.engine import safe_push
        safe_push("toast", {"level": "warn" if warn_status == "超预警线" else "error", "message": message})

    return {
        "link_code": link_code,
        "current_load_gbps": round(current, 2),
        "future_load_gbps": round(future, 2),
        "predicted_load_max_gbps": round(max_predicted, 2),
        "predicted_min": predicted_min,
        "warn_status": warn_status,
    }


def recalc_all(as_of_min: Optional[int] = None) -> List[Dict[str, Any]]:
    """遍历所有 up 链路计算占用 + 触发 ACT-03。"""
    from app.db.repo import PlanRepo
    from app.services.plan import generate_plans

    links = [l for l in LinkRepo.list_all() if l.oper_state == "up"]
    results = []
    for l in links:
        r = calc_occupancy(l.link_code, as_of_min=as_of_min)
        results.append(r)

    for r in results:
        if r["warn_status"] != "超容量":
            continue
        link_code = r["link_code"]
        cap_alarm = AlarmRepo.latest_capacity_alarm(link_code)
        if cap_alarm is None:
            continue
        pending = PlanRepo.list_pending_by_alarm(cap_alarm.alarm_code)
        # 修复（major 3）：同一告警下若已存在已决方案（批准/已执行/拒绝），
        # 且没有待审批方案，则不再重复生成新方案；否则每 tick 都会追加
        # PLAN-L-X-REPAIR-02 / -03 ... 同一告警被反复推 modal_approval。
        decided = [
            p for p in PlanRepo.list_by_alarm(cap_alarm.alarm_code)
            if p.status in ("批准", "已执行", "拒绝", "已作废")
        ]
        now = as_of_min if as_of_min is not None else get_clock().now_min()
        predicted_min = r.get("predicted_min")
        imminent = predicted_min is not None and (predicted_min - now) <= 1
        if pending and not imminent:
            continue
        if decided and not pending:
            # 该告警的方案已决（无论批准/拒绝），不再重复生成；
            # 除非新一轮容量告警 episode 发生（latest_capacity_alarm 改变）才允许。
            continue
        if pending and imminent:
            for p in pending:
                PlanRepo.update_status(p.plan_code, "已作废")
            try:
                from app.services.approval import _cancel_countdown
                _cancel_countdown(cap_alarm.alarm_code)
            except Exception:
                pass
        generate_plans(alarm_link_code=link_code, as_of_min=as_of_min)
    return results


def update_link_loads() -> None:
    recalc_all()
