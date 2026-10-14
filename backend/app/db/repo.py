"""9 张表的原生 SQL 仓库（CRUD）。

设计原则：
- 全部走 app.db.raw 的 query / execute；
- 每张表一个 Repo 类，按业务需求暴露 list_/get_/insert_/update_/delete_ 方法；
- 字段名直接用 9 张表真实列名（参考 .owl / §6 数据库结构）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from app.db import execute, get_conn, query, query_one, query_scalar
from app.db.row import (
    ApprovalRecordRow,
    CommLinkRow,
    CommPlanRow,
    CommTaskRow,
    RepairOrderRow,
    ResourceAlarmRow,
    SceneEventRow,
    TaskLinkBindingRow,
    TrafficDemandRow,
)


def _row(cls, d):
    return cls.from_dict(d) if d is not None else None


def _rows(cls, ds):
    return [_row(cls, d) for d in ds]


# ============================================================
# comm_task
# ============================================================

class TaskRepo:
    TABLE = "comm_task"

    @staticmethod
    def list_all() -> List[CommTaskRow]:
        return _rows(CommTaskRow, query(f"SELECT * FROM {TaskRepo.TABLE} ORDER BY task_code"))

    @staticmethod
    def get(task_code: str) -> Optional[CommTaskRow]:
        return _row(CommTaskRow, query_one(
            f"SELECT * FROM {TaskRepo.TABLE} WHERE task_code=%s", (task_code,)
        ))

    @staticmethod
    def insert(t: CommTaskRow) -> None:
        execute(
            f"INSERT INTO {TaskRepo.TABLE} (task_code, task_name, duration_min, "
            f"initial_demand_gbps, critical_min, owner_role, current_link_code, lookahead_min) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (t.task_code, t.task_name, t.duration_min, t.initial_demand_gbps,
             t.critical_min, t.owner_role, t.current_link_code, t.lookahead_min),
        )

    @staticmethod
    def update_current_link(task_code: str, link_code: str) -> None:
        execute(
            f"UPDATE {TaskRepo.TABLE} SET current_link_code=%s WHERE task_code=%s",
            (link_code, task_code),
        )

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {TaskRepo.TABLE}")


# ============================================================
# comm_link
# ============================================================

class LinkRepo:
    TABLE = "comm_link"

    @staticmethod
    def list_all() -> List[CommLinkRow]:
        return _rows(CommLinkRow, query(f"SELECT * FROM {LinkRepo.TABLE} ORDER BY link_code"))

    @staticmethod
    def get(link_code: str) -> Optional[CommLinkRow]:
        return _row(CommLinkRow, query_one(
            f"SELECT * FROM {LinkRepo.TABLE} WHERE link_code=%s", (link_code,)
        ))

    @staticmethod
    def insert(l: CommLinkRow) -> None:
        execute(
            f"INSERT INTO {LinkRepo.TABLE} (link_code, link_name, capacity_gbps, "
            f"warning_gbps, oper_state, avg_repair_min, lookahead_min, backup_link_code, "
            f"current_load_gbps, future_load_gbps, warn_status) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (l.link_code, l.link_name, l.capacity_gbps, l.warning_gbps, l.oper_state,
             l.avg_repair_min, l.lookahead_min, l.backup_link_code,
             l.current_load_gbps, l.future_load_gbps, l.warn_status),
        )

    @staticmethod
    def update_oper_state(link_code: str, oper_state: str) -> None:
        execute(
            f"UPDATE {LinkRepo.TABLE} SET oper_state=%s WHERE link_code=%s",
            (oper_state, link_code),
        )

    @staticmethod
    def update_loads(link_code: str, current: float, future: float, warn_status: str) -> None:
        execute(
            f"UPDATE {LinkRepo.TABLE} SET current_load_gbps=%s, future_load_gbps=%s, "
            f"warn_status=%s WHERE link_code=%s",
            (current, future, warn_status, link_code),
        )

    @staticmethod
    def zero_loads(link_code: str) -> None:
        execute(
            f"UPDATE {LinkRepo.TABLE} SET current_load_gbps=0, future_load_gbps=0, "
            f"warn_status='正常' WHERE link_code=%s",
            (link_code,),
        )

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {LinkRepo.TABLE}")


# ============================================================
# comm_demand
# ============================================================

class DemandRepo:
    TABLE = "comm_demand"

    @staticmethod
    def list_all() -> List[TrafficDemandRow]:
        return _rows(TrafficDemandRow, query(
            f"SELECT * FROM {DemandRepo.TABLE} ORDER BY task_code, valid_from_min"
        ))

    @staticmethod
    def get(demand_code: str) -> Optional[TrafficDemandRow]:
        return _row(TrafficDemandRow, query_one(
            f"SELECT * FROM {DemandRepo.TABLE} WHERE demand_code=%s", (demand_code,)
        ))

    @staticmethod
    def list_by_task(task_code: str) -> List[TrafficDemandRow]:
        return _rows(TrafficDemandRow, query(
            f"SELECT * FROM {DemandRepo.TABLE} WHERE task_code=%s ORDER BY valid_from_min",
            (task_code,),
        ))

    @staticmethod
    def insert(d: TrafficDemandRow) -> None:
        execute(
            f"INSERT INTO {DemandRepo.TABLE} (demand_code, task_code, valid_from_min, "
            f"demand_gbps, valid_to_min) VALUES (%s,%s,%s,%s,%s)",
            (d.demand_code, d.task_code, d.valid_from_min, d.demand_gbps, d.valid_to_min),
        )

    @staticmethod
    def count() -> int:
        return int(query_scalar(f"SELECT COUNT(*) FROM {DemandRepo.TABLE}") or 0)

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {DemandRepo.TABLE}")


# ============================================================
# comm_alarm
# ============================================================

class AlarmRepo:
    TABLE = "comm_alarm"

    @staticmethod
    def list_all() -> List[ResourceAlarmRow]:
        return _rows(ResourceAlarmRow, query(
            f"SELECT * FROM {AlarmRepo.TABLE} ORDER BY event_min, alarm_code"
        ))

    @staticmethod
    def get(alarm_code: str) -> Optional[ResourceAlarmRow]:
        return _row(ResourceAlarmRow, query_one(
            f"SELECT * FROM {AlarmRepo.TABLE} WHERE alarm_code=%s", (alarm_code,)
        ))

    @staticmethod
    def latest_capacity_alarm(link_code: str) -> Optional[ResourceAlarmRow]:
        return _row(ResourceAlarmRow, query_one(
            f"SELECT * FROM {AlarmRepo.TABLE} "
            f"WHERE link_code=%s AND alarm_type='容量超限' "
            f"ORDER BY event_min DESC LIMIT 1",
            (link_code,),
        ))

    @staticmethod
    def has_pending_capacity_alarm(link_code: str) -> bool:
        return int(query_scalar(
            f"SELECT COUNT(*) FROM {AlarmRepo.TABLE} "
            f"WHERE link_code=%s AND alarm_type='容量超限'",
            (link_code,),
        ) or 0) > 0

    @staticmethod
    def latest_any(link_code: str) -> Optional[ResourceAlarmRow]:
        return _row(ResourceAlarmRow, query_one(
            f"SELECT * FROM {AlarmRepo.TABLE} WHERE link_code=%s "
            f"ORDER BY event_min DESC LIMIT 1",
            (link_code,),
        ))

    @staticmethod
    def insert(a: ResourceAlarmRow) -> None:
        execute(
            f"INSERT INTO {AlarmRepo.TABLE} (alarm_code, link_code, alarm_type, "
            f"native_severity, event_min, predicted_min) VALUES (%s,%s,%s,%s,%s,%s)",
            (a.alarm_code, a.link_code, a.alarm_type, a.native_severity,
             a.event_min, a.predicted_min),
        )

    @staticmethod
    def count() -> int:
        return int(query_scalar(f"SELECT COUNT(*) FROM {AlarmRepo.TABLE}") or 0)

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {AlarmRepo.TABLE}")


# ============================================================
# comm_task_link
# ============================================================

class TaskLinkRepo:
    TABLE = "comm_task_link"

    @staticmethod
    def list_all() -> List[TaskLinkBindingRow]:
        return _rows(TaskLinkBindingRow, query(
            f"SELECT * FROM {TaskLinkRepo.TABLE} "
            f"ORDER BY task_code, assigned_min, id"
        ))

    @staticmethod
    def list_by_link(link_code: str) -> List[TaskLinkBindingRow]:
        return _rows(TaskLinkBindingRow, query(
            f"SELECT * FROM {TaskLinkRepo.TABLE} WHERE link_code=%s "
            f"ORDER BY assigned_min DESC, id DESC",
            (link_code,),
        ))

    @staticmethod
    def latest_per_task_global() -> Dict[str, TaskLinkBindingRow]:
        """每个 task_code 取 (assigned_min DESC, id DESC) 最新一行。"""
        ds = query(
            f"SELECT t.* FROM {TaskLinkRepo.TABLE} t "
            f"JOIN (SELECT task_code, MAX(assigned_min) AS m FROM {TaskLinkRepo.TABLE} "
            f"      GROUP BY task_code) g "
            f"  ON t.task_code=g.task_code AND t.assigned_min=g.m "
            f"ORDER BY t.id DESC"
        )
        # 同 (task_code, assigned_min) 取 surrogate id 最新
        out: Dict[str, TaskLinkBindingRow] = {}
        for d in ds:
            t = TaskLinkBindingRow.from_dict(d)
            key = t.task_code
            if key not in out or (out[key].id or 0) < (t.id or 0):
                out[key] = t
        return out

    @staticmethod
    def insert(b: TaskLinkBindingRow) -> None:
        """插入新归属行；触发 uk_task_min 唯一约束时由调用方捕获。"""
        execute(
            f"INSERT INTO {TaskLinkRepo.TABLE} (task_code, link_code, assigned_min, note) "
            f"VALUES (%s,%s,%s,%s)",
            (b.task_code, b.link_code, b.assigned_min, b.note),
        )

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {TaskLinkRepo.TABLE}")


# ============================================================
# comm_approval
# ============================================================

class ApprovalRepo:
    TABLE = "comm_approval"

    @staticmethod
    def list_all() -> List[ApprovalRecordRow]:
        return _rows(ApprovalRecordRow, query(
            f"SELECT * FROM {ApprovalRepo.TABLE} ORDER BY decided_min"
        ))

    @staticmethod
    def get(approval_code: str) -> Optional[ApprovalRecordRow]:
        return _row(ApprovalRecordRow, query_one(
            f"SELECT * FROM {ApprovalRepo.TABLE} WHERE approval_code=%s", (approval_code,)
        ))

    @staticmethod
    def next_seq(plan_code: str) -> int:
        prefix = f"APV-{plan_code}-"
        n = query_scalar(
            f"SELECT COUNT(*) FROM {ApprovalRepo.TABLE} WHERE approval_code LIKE %s",
            (f"{prefix}%",),
        )
        return int(n or 0) + 1

    @staticmethod
    def insert(a: ApprovalRecordRow) -> None:
        execute(
            f"INSERT INTO {ApprovalRepo.TABLE} (approval_code, plan_code, decision, "
            f"reason, approver, decision_source, decided_min) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (a.approval_code, a.plan_code, a.decision, a.reason, a.approver,
             a.decision_source, a.decided_min),
        )

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {ApprovalRepo.TABLE}")


# ============================================================
# comm_plan
# ============================================================

class PlanRepo:
    TABLE = "comm_plan"

    @staticmethod
    def list_all() -> List[CommPlanRow]:
        return _rows(CommPlanRow, query(
            f"SELECT * FROM {PlanRepo.TABLE} ORDER BY generated_min"
        ))

    @staticmethod
    def list_pending() -> List[CommPlanRow]:
        return _rows(CommPlanRow, query(
            f"SELECT * FROM {PlanRepo.TABLE} WHERE status='待审批'"
        ))

    @staticmethod
    def list_pending_by_link(link_code: str) -> List[CommPlanRow]:
        return _rows(CommPlanRow, query(
            f"SELECT * FROM {PlanRepo.TABLE} "
            f"WHERE link_code=%s AND status='待审批'",
            (link_code,),
        ))

    @staticmethod
    def list_pending_by_alarm(alarm_code: str) -> List[CommPlanRow]:
        return _rows(CommPlanRow, query(
            f"SELECT * FROM {PlanRepo.TABLE} "
            f"WHERE alarm_code=%s AND status='待审批'",
            (alarm_code,),
        ))

    @staticmethod
    def list_by_link(link_code: str) -> List[CommPlanRow]:
        return _rows(CommPlanRow, query(
            f"SELECT * FROM {PlanRepo.TABLE} WHERE link_code=%s ORDER BY generated_min",
            (link_code,),
        ))

    @staticmethod
    def get(plan_code: str) -> Optional[CommPlanRow]:
        return _row(CommPlanRow, query_one(
            f"SELECT * FROM {PlanRepo.TABLE} WHERE plan_code=%s", (plan_code,)
        ))

    @staticmethod
    def insert(p: CommPlanRow) -> None:
        execute(
            f"INSERT INTO {PlanRepo.TABLE} (plan_code, alarm_code, plan_type, link_code, "
            f"target_link_code, est_repair_min, predicted_load_after_gbps, can_meet_critical, "
            f"status, is_default, recommendation, generated_min) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (p.plan_code, p.alarm_code, p.plan_type, p.link_code, p.target_link_code,
             p.est_repair_min, p.predicted_load_after_gbps, p.can_meet_critical,
             p.status, p.is_default, p.recommendation, p.generated_min),
        )

    @staticmethod
    def update_status(plan_code: str, status: str) -> None:
        execute(
            f"UPDATE {PlanRepo.TABLE} SET status=%s WHERE plan_code=%s",
            (status, plan_code),
        )

    @staticmethod
    def update_can_meet_critical(plan_code: str, can: str, status: Optional[str] = None) -> None:
        if status is None:
            execute(
                f"UPDATE {PlanRepo.TABLE} SET can_meet_critical=%s WHERE plan_code=%s",
                (can, plan_code),
            )
        else:
            execute(
                f"UPDATE {PlanRepo.TABLE} SET can_meet_critical=%s, status=%s WHERE plan_code=%s",
                (can, status, plan_code),
            )

    @staticmethod
    def cancel_pending_for_alarm(alarm_code: str) -> None:
        execute(
            f"UPDATE {PlanRepo.TABLE} SET status='已作废' "
            f"WHERE alarm_code=%s AND status='待审批'",
            (alarm_code,),
        )

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {PlanRepo.TABLE}")


# ============================================================
# comm_repair_order
# ============================================================

class RepairRepo:
    TABLE = "comm_repair_order"

    @staticmethod
    def list_all() -> List[RepairOrderRow]:
        return _rows(RepairOrderRow, query(
            f"SELECT * FROM {RepairRepo.TABLE} ORDER BY request_min"
        ))

    @staticmethod
    def get(order_code: str) -> Optional[RepairOrderRow]:
        return _row(RepairOrderRow, query_one(
            f"SELECT * FROM {RepairRepo.TABLE} WHERE order_code=%s", (order_code,)
        ))

    @staticmethod
    def list_by_link(link_code: str) -> List[RepairOrderRow]:
        return _rows(RepairOrderRow, query(
            f"SELECT * FROM {RepairRepo.TABLE} WHERE link_code=%s ORDER BY request_min",
            (link_code,),
        ))

    @staticmethod
    def active_by_link(link_code: str) -> Optional[RepairOrderRow]:
        """返回该链路 status != '已完成' 的最新一条修理单。"""
        return _row(RepairOrderRow, query_one(
            f"SELECT * FROM {RepairRepo.TABLE} "
            f"WHERE link_code=%s AND status<>'已完成' "
            f"ORDER BY request_min DESC, order_code DESC LIMIT 1",
            (link_code,),
        ))

    @staticmethod
    def next_seq(link_code: str) -> int:
        n = query_scalar(
            f"SELECT COUNT(*) FROM {RepairRepo.TABLE} WHERE link_code=%s",
            (link_code,),
        )
        return int(n or 0) + 1

    @staticmethod
    def insert(r: RepairOrderRow) -> None:
        execute(
            f"INSERT INTO {RepairRepo.TABLE} (order_code, link_code, alarm_code, "
            f"request_min, est_finish_min, actual_finish_min, status) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (r.order_code, r.link_code, r.alarm_code, r.request_min,
             r.est_finish_min, r.actual_finish_min, r.status),
        )

    @staticmethod
    def update_receipt(order_code: str, est_finish_min: int, status: str) -> None:
        execute(
            f"UPDATE {RepairRepo.TABLE} SET est_finish_min=%s, status=%s WHERE order_code=%s",
            (est_finish_min, status, order_code),
        )

    @staticmethod
    def mark_completed(order_code: str, actual_finish_min: int) -> None:
        execute(
            f"UPDATE {RepairRepo.TABLE} SET actual_finish_min=%s, status='已完成' "
            f"WHERE order_code=%s",
            (actual_finish_min, order_code),
        )

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {RepairRepo.TABLE}")


# ============================================================
# comm_event
# ============================================================

class EventRepo:
    TABLE = "comm_event"

    @staticmethod
    def list_all() -> List[SceneEventRow]:
        return _rows(SceneEventRow, query(
            f"SELECT * FROM {EventRepo.TABLE} ORDER BY occur_min, event_code"
        ))

    @staticmethod
    def list_booked_pending(now_min: int) -> List[SceneEventRow]:
        return _rows(SceneEventRow, query(
            f"SELECT * FROM {EventRepo.TABLE} "
            f"WHERE event_type='task_booked' AND occur_min<=%s "
            f"ORDER BY occur_min",
            (now_min,),
        ))

    @staticmethod
    def list_booked_future(now_min: int) -> List[SceneEventRow]:
        return _rows(SceneEventRow, query(
            f"SELECT * FROM {EventRepo.TABLE} "
            f"WHERE event_type='task_booked' AND occur_min>%s "
            f"ORDER BY occur_min",
            (now_min,),
        ))

    @staticmethod
    def list_recent(limit: int = 20) -> List[SceneEventRow]:
        return _rows(SceneEventRow, query(
            f"SELECT * FROM {EventRepo.TABLE} ORDER BY occur_min DESC LIMIT %s",
            (limit,),
        ))

    @staticmethod
    def insert(e: SceneEventRow) -> None:
        execute(
            f"INSERT INTO {EventRepo.TABLE} (event_code, event_type, ref_type, ref_code, "
            f"occur_min, summary) VALUES (%s,%s,%s,%s,%s,%s)",
            (e.event_code, e.event_type, e.ref_type, e.ref_code, e.occur_min, e.summary),
        )

    @staticmethod
    def update_summary(event_code: str, summary: str) -> None:
        execute(
            f"UPDATE {EventRepo.TABLE} SET summary=%s WHERE event_code=%s",
            (summary, event_code),
        )

    @staticmethod
    def delete_all() -> None:
        execute(f"DELETE FROM {EventRepo.TABLE}")
