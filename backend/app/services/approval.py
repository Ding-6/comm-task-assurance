"""ACT-04 人工审批决策 + 60 秒超时自动执行（PyMySQL 原生版，无 ORM）。

修复项 3：decision_source / approver 由入参透传，超时分支硬编码 SYSTEM / 超时自动。
修复项 4：返回从 session 重新读取的 ApprovalRecord（真实持久化对象），不再 new 空对象。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.common.ws import get_hub
from app.core.scene_clock import get_clock
from app.db import get_conn
from app.db.repo import (
    ApprovalRepo, EventRepo, PlanRepo, TaskLinkRepo, TaskRepo,
)
from app.db.row import (
    ApprovalRecordRow, CommPlanRow, SceneEventRow, TaskLinkBindingRow,
)


_DECIDE_LOCK = asyncio.Lock()
_COUNTDOWN_LOCK = asyncio.Lock()
_COUNTDOWN_TASKS: Dict[str, asyncio.Task[None]] = {}
_MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _set_main_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    global _MAIN_LOOP
    _MAIN_LOOP = loop


# 让 main.lifespan 可以注册
_set_main_loop_loop = _set_main_loop


async def _pop_countdown_locked(alarm_code: str) -> Optional[asyncio.Task[None]]:
    async with _COUNTDOWN_LOCK:
        return _COUNTDOWN_TASKS.pop(alarm_code, None)


async def _set_countdown_locked(alarm_code: str, task: asyncio.Task[None]) -> None:
    async with _COUNTDOWN_LOCK:
        _COUNTDOWN_TASKS[alarm_code] = task


def _cancel_countdown(alarm_code: str) -> None:
    async def _do_cancel() -> None:
        task = await _pop_countdown_locked(alarm_code)
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return
    try:
        future = asyncio.run_coroutine_threadsafe(_do_cancel(), loop)
        future.result(timeout=0)
    except Exception:
        pass


async def _cancel_countdown_async(alarm_code: str) -> None:
    task = await _pop_countdown_locked(alarm_code)
    if task is None or task.done():
        return
    # 关键：不要取消调用方自己的任务（_runner 内部用），否则后续 await 会 CancelledError
    if task is asyncio.current_task():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# ============================================================
# 决定执行（同步 + 异步）
# ============================================================

def decide_sync(
    plan_code: str, decision: str,
    reason: Optional[str] = None,
    approver: str = "ROLE-APPROVER",
    decision_source: str = "人工",
) -> Dict[str, Any]:
    """同步入口。HTTP 路由 / 单元测试使用。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = _MAIN_LOOP

    if loop is None or loop.is_closed():
        return _do_decide_sync(plan_code, decision, reason, approver, decision_source)

    future = asyncio.run_coroutine_threadsafe(
        _do_decide(plan_code, decision, reason, approver, decision_source), loop
    )
    return future.result()


# 辅助：业务错误转 BizException，让路由能通过 @app.exception_handler(BizException) 统一返回 code!=0 / 200
def _as_biz(exc: Exception, code: int = 400) -> "BizException":
    from app.common.exceptions import BizException
    if isinstance(exc, BizException):
        return exc
    return BizException(message=str(exc) or exc.__class__.__name__, code=code)


def _do_decide_sync(plan_code, decision, reason, approver, decision_source) -> Dict[str, Any]:
    clock = get_clock()
    now = clock.now_min()
    plan = PlanRepo.get(plan_code)
    if plan is None or plan.status != "待审批":
        raise ValueError("方案不存在或状态非待审批")
    if approver not in ("ROLE-APPROVER", "SYSTEM"):
        raise ValueError("Agent 无权审批")

    _cancel_countdown(plan.alarm_code)
    seq = ApprovalRepo.next_seq(plan_code)
    approval_code = f"APV-{plan_code}-{seq:02d}"
    ApprovalRepo.insert(ApprovalRecordRow(
        approval_code=approval_code, plan_code=plan_code, decision=decision,
        reason=reason, approver=approver, decision_source=decision_source, decided_min=now,
    ))

    executed = False
    if decision == "批准":
        if plan.plan_type == "reroute":
            _execute_reroute(plan, now)
            PlanRepo.update_status(plan_code, "已执行")
            executed = True
        elif plan.plan_type == "repair":
            PlanRepo.update_status(plan_code, "批准")
            try:
                request_repair(alarm_code=plan.alarm_code, plan_code=plan_code)
            except Exception:
                pass
    else:
        PlanRepo.update_status(plan_code, "拒绝")

    siblings = PlanRepo.list_pending_by_alarm(plan.alarm_code)
    for sib in siblings:
        if sib.plan_code != plan_code:
            PlanRepo.update_status(sib.plan_code, "已作废")

    EventRepo.insert(SceneEventRow(
        event_code=f"EVT-APV-{datetime.now().strftime('%H%M%S%f')}",
        event_type="approval_decided", ref_type="approval", ref_code=approval_code,
        occur_min=now, summary=f"{plan_code} {decision} ({decision_source})",
    ))

    from app.services.occupancy import recalc_all
    recalc_all(as_of_min=now)

    # 重新读出真实持久化对象（修复项 4）
    ap = ApprovalRepo.get(approval_code)
    return {"approval_code": approval_code, "plan": plan, "approval": ap, "executed": executed}


async def _do_decide(plan_code, decision, reason, approver, decision_source) -> Dict[str, Any]:
    async with _DECIDE_LOCK:
        clock = get_clock()
        now = clock.now_min()
        plan = PlanRepo.get(plan_code)
        if plan is None or plan.status != "待审批":
            raise ValueError("方案不存在或状态非待审批")
        if plan.status == "已作废":
            raise ValueError("方案已作废")
        if approver not in ("ROLE-APPROVER", "SYSTEM"):
            raise ValueError("Agent 无权审批")

        await _cancel_countdown_async(plan.alarm_code)
        seq = ApprovalRepo.next_seq(plan_code)
        approval_code = f"APV-{plan_code}-{seq:02d}"
        ApprovalRepo.insert(ApprovalRecordRow(
            approval_code=approval_code, plan_code=plan_code, decision=decision,
            reason=reason, approver=approver, decision_source=decision_source, decided_min=now,
        ))

        siblings = PlanRepo.list_pending_by_alarm(plan.alarm_code)
        executed = False
        if decision == "批准":
            if plan.plan_type == "reroute":
                _execute_reroute(plan, now)
                PlanRepo.update_status(plan_code, "已执行")
                executed = True
            elif plan.plan_type == "repair":
                PlanRepo.update_status(plan_code, "批准")
                try:
                    request_repair(alarm_code=plan.alarm_code, plan_code=plan_code)
                except Exception:
                    pass
        else:
            PlanRepo.update_status(plan_code, "拒绝")
        for sib in siblings:
            if sib.plan_code != plan_code:
                PlanRepo.update_status(sib.plan_code, "已作废")

        EventRepo.insert(SceneEventRow(
            event_code=f"EVT-APV-{datetime.now().strftime('%H%M%S%f')}",
            event_type="approval_decided", ref_type="approval", ref_code=approval_code,
            occur_min=now, summary=f"{plan_code} {decision} ({decision_source})",
        ))

        from app.services.occupancy import recalc_all
        recalc_all(as_of_min=now)

        ap = ApprovalRepo.get(approval_code)
        await get_hub().broadcast("modal_close", {
            "approval_code": approval_code, "plan_code": plan_code,
            "decision": decision, "decision_source": decision_source, "executed": executed,
        })
        return {"approval_code": approval_code, "plan": plan, "approval": ap, "executed": executed}


def _execute_reroute(plan, now: int) -> None:
    fault = plan.link_code
    target = plan.target_link_code
    if not target:
        raise ValueError(f"reroute 方案无 target_link_code: {plan.plan_code}")
    # 备用启用
    LinkRepo_update_oper_state(target, "up")
    rows = TaskLinkRepo.list_by_link(fault)
    latest: Dict[str, Any] = {}
    for b in rows:
        latest.setdefault(b.task_code, b)
    for b in latest.values():
        TaskLinkRepo.insert(TaskLinkBindingRow(
            task_code=b.task_code, link_code=target, assigned_min=now,
            note=f"{fault} 批准转备用，由 {now} 执行",
        ))
        TaskRepo.update_current_link(b.task_code, target)


# 内部小工具：避免在 _execute_reroute 顶部多一个 import
from app.db.repo import LinkRepo  # noqa: E402


def LinkRepo_update_oper_state(link_code: str, state: str) -> None:
    LinkRepo.update_oper_state(link_code, state)


# ============================================================
# 对外主接口
# ============================================================

def decide_approval(
    plan_code: str, decision: str,
    approver: str = "ROLE-APPROVER",
    decision_source: str = "人工",
    reason: Optional[str] = None,
) -> ApprovalRecordRow:
    """兼容 api/comm.py 的 decide_approval 签名。"""
    result = decide_sync(
        plan_code=plan_code, decision=decision,
        reason=reason, approver=approver, decision_source=decision_source,
    )
    ap = result.get("approval")
    if ap is None:
        raise ValueError("审批写入失败：未读到持久化记录")
    return ap


# ============================================================
# 60 秒倒计时
# ============================================================

def request_repair(alarm_code: str, plan_code: Optional[str] = None):
    from app.services.repair import request_repair as _rr
    return _rr(alarm_code, plan_code)


def start_countdown(alarm_code: str, plan_code: str, timeout_sec: int = 60) -> None:
    _cancel_countdown(alarm_code)

    async def _runner() -> None:
        try:
            await asyncio.sleep(timeout_sec)
        except asyncio.CancelledError:
            return
        from app.db import get_conn
        with get_conn() as conn:
            pass  # 占位（实际 SELECT 走 ApprovalRepo / PlanRepo）
        # 重新查一次
        pending = PlanRepo.list_pending_by_alarm(alarm_code)
        if not pending:
            return
        default = next((p for p in pending if p.is_default == "是"), None)
        if default is None:
            return
        # 关键修复：不要在 _DECIDE_LOCK 内再调用 _do_decide——
        # _do_decide 内部也会请求同一把锁（asyncio.Lock 非可重入），
        # 那样会导致 60 秒超时路径死锁。直接调用，依赖 _do_decide 自身的锁。
        await _do_decide(
            plan_code=default.plan_code, decision="批准",
            reason="60 秒未决策，自动执行保守默认方案",
            approver="SYSTEM", decision_source="超时自动",
        )

    # 修复（major 8）：HTTP 路由在 worker 线程里跑，没有 running loop，
    # get_running_loop() 会 RuntimeError；之前 except: pass 静默吞掉，
    # 60 秒超时自动路径只在引擎 tick 里能跑起来，HTTP 触发的方案超时永远不生效。
    # 现在：先尝试当前线程 loop；没有则把 _runner 调度到 _MAIN_LOOP（lifespan 注册）。
    started = False
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_runner())
        _set_main_loop(loop)
        loop.create_task(_set_countdown_locked(alarm_code, task))
        started = True
    except RuntimeError:
        pass

    if not started:
        main_loop = _MAIN_LOOP
        if main_loop is not None and not main_loop.is_closed():
            try:
                async def _start() -> None:
                    task = main_loop.create_task(_runner())
                    await _set_countdown_locked(alarm_code, task)

                asyncio.run_coroutine_threadsafe(_start(), main_loop)
                started = True
            except Exception:
                pass

    if not started:
        # 实在没办法（_MAIN_LOOP 也没注册），前端超时计时器是最后兜底
        pass
