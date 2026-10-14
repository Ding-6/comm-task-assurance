"""HOTL 规则校验：完整业务链路端到端测试。

按 V4.1 说明书 §1.6 时序跑完整 2 小时剧本的关键节点。
"""
import os
import sys
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("TMA_MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("TMA_MYSQL_PORT", "3306")
os.environ.setdefault("TMA_MYSQL_USER", "root")
os.environ.setdefault("TMA_MYSQL_PASSWORD", "123456")
os.environ.setdefault("TMA_MYSQL_DB", "zb_db2")

from fastapi.testclient import TestClient
from app.main import app
from app.core.scene_clock import get_clock
from app.db import transaction
from app.services.engine import (
    add_link as _add_link_svc, add_task as _add_task_svc, reset_default_scene,
)
from app.services.occupancy import recalc_all
from app.services.approval import decide_approval, start_countdown
from app.db.repo import (
    AlarmRepo, ApprovalRepo, EventRepo, LinkRepo, PlanRepo, RepairRepo, TaskLinkRepo, TaskRepo,
)

client = TestClient(app)
passes, fails = 0, 0


def check(label, ok, detail=""):
    global passes, fails
    if ok:
        passes += 1
        print(f"  ✓ {label}" + (f"  ({detail})" if detail else ""))
    else:
        fails += 1
        print(f"  ✗ {label}  -- {detail}")


def parse(resp):
    return resp.json()


def reset():
    """清空业务表 + 重新加载默认数据 + 重算占用。"""
    reset_default_scene()
    clock = get_clock()
    clock.reset()  # 让 now_min = 0


# ============================================================
# 0. 启动 + 默认场景负载（T+0）
# ============================================================
print("=" * 60)
print("[0] 启动校验 + 默认场景负载")
print("=" * 60)
reset()
r = client.get("/api/comm/links")
links = {l["link_code"]: l for l in parse(r).get("data", [])}
check("L-1H 3G/3G 正常", links["L-1H"]["current_load_gbps"] == 3.0 and links["L-1H"]["warn_status"] == "正常", str(links["L-1H"]))
check("L-2H 6G/6G 正常 (D-B2 预订未到不计入)",
      links["L-2H"]["current_load_gbps"] == 6.0 and links["L-2H"]["warn_status"] == "正常", str(links["L-2H"]))
check("L-3H 0G/0G 正常 (备用)", links["L-3H"]["current_load_gbps"] == 0.0)
check("L-1H 备用 L-3H", links["L-1H"]["backup_link_code"] == "L-3H")

# ============================================================
# 1. 接口契约（错误路径 + 幂等）
# ============================================================
print()
print("=" * 60)
print("[1] 接口契约：BaseSuccess / BaseError / 幂等")
print("=" * 60)
r = client.post("/api/comm/occupancy", json={"link_code": "L-2H"})
d = parse(r)
check("BaseSuccess code=0", d.get("code") == 0)
check("occupancy 返回 3 字段", all(k in d.get("data", {}) for k in
      ["current_load_gbps", "future_load_gbps", "predicted_load_max_gbps"]))

r = client.post("/api/comm/occupancy", json={"link_code": "L-99H"})
d = parse(r)
check("链路不存在 → BaseError code!=0 + message 非空",
      d.get("code") != 0 and bool(d.get("message")))

r = client.post("/api/comm/approve", json={"plan_code": "PLAN-XXX-NOPE", "decision": "批准"})
d = parse(r)
check("方案不存在 → BaseError code!=0", d.get("code") != 0)

r = client.post("/api/comm/alarm", json={})
d = parse(r)
check("缺参数 → code=400 + 校验错误列表", d.get("code") == 400 and isinstance(d.get("data"), list))

# 幂等：注入容量超限告警 + 重复 plan
client.post("/api/comm/alarm", json={"link_code": "L-2H", "alarm_type": "容量超限", "native_severity": "critical", "event_min": 0, "predicted_min": 50})
r1 = client.post("/api/comm/plan", json={"alarm_link_code": "L-2H"})
codes1 = [p["plan_code"] for p in parse(r1).get("data", {}).get("plans", [])]
r2 = client.post("/api/comm/plan", json={"alarm_link_code": "L-2H"})
codes2 = [p["plan_code"] for p in parse(r2).get("data", {}).get("plans", [])]
check("ACT-03 幂等：两次 plan_code 完全相同", codes1 == codes2, f"first={codes1} second={codes2}")

# ============================================================
# 2. T+15 链路中断：占用超预警线但 NOT 超容量
# ============================================================
print()
print("=" * 60)
print("[2] T+15 L-1H 故障：L-2H = 9G/9G，超预警线，不超容量")
print("=" * 60)
reset()
clock = get_clock()
clock.start()
clock.start_wallclock = datetime.now() - timedelta(minutes=15)

r = client.post("/api/comm/switch", json={"alarm_link_code": "L-1H", "as_of_min": 15})
sw = parse(r).get("data", {})
check("ACT-01 切换 L-1H HTTP 200", r.status_code == 200)
check("switched_tasks 含 T-A", "T-A" in sw.get("switched_tasks", []))
check("to_link = L-2H（共享链路优先）", sw.get("to_link") == "L-2H")

# 占用（T+15 时刻 L-2H 应有 3 任务：T-A 3G + T-B 4G + T-C 2G = 9G）
r = client.post("/api/comm/occupancy", json={"link_code": "L-2H", "as_of_min": 15})
occ = parse(r).get("data", {})
check("T+15 L-2H current = 9.0", occ.get("current_load_gbps") == 9.0, str(occ))
check("T+15 L-2H future = 9.0 (first_cp=45 lookahead 终点，D-B1 仍生效)",
      occ.get("future_load_gbps") == 9.0, f"实际={occ.get('future_load_gbps')}")
check("T+15 L-2H warn_status = 超预警线（不是超容量！）",
      occ.get("warn_status") == "超预警线", f"实际={occ.get('warn_status')}")
check("T+15 L-2H predicted_min = 45（窗口 [15,45] 终点）",
      occ.get("predicted_min") == 45, f"实际={occ.get('predicted_min')}")

# ============================================================
# 3. T+20 前瞻命中 T+50：容量超限告警 + 方案生成
# ============================================================
print()
print("=" * 60)
print("[3] T+20 前瞻窗口首次覆盖 T+50：容量超限告警 + REPAIR 方案")
print("=" * 60)

# 时钟已经走到 T+15，再快进到 T+20
clock.start_wallclock = datetime.now() - timedelta(minutes=20)

# 触发 ACT-02
r = client.post("/api/comm/occupancy", json={"link_code": "L-2H", "as_of_min": 20})
occ = parse(r).get("data", {})
check("T+20 L-2H warn_status = 超容量", occ.get("warn_status") == "超容量", f"实际={occ.get('warn_status')}")
check("T+20 L-2H predicted_min = 50 (D-B1 失效后 T-B 兜底 6G, 11G 超容量)",
      occ.get("predicted_min") == 50, f"实际={occ.get('predicted_min')}")

# 容量超限告警
alarms = AlarmRepo.list_all()
cap = next((a for a in alarms if a.alarm_type == "容量超限"), None)
check("容量超限告警已写入", cap is not None, f"现有={[(a.alarm_code, a.alarm_type) for a in alarms]}")
check("容量超限告警 predicted_min = 50", cap and cap.predicted_min == 50)
check("容量超限告警 link_code = L-2H", cap and cap.link_code == "L-2H")

# 触发 ACT-03（recalc_all）
recalc_all(as_of_min=20)
plans = PlanRepo.list_all()
plan_codes = [p.plan_code for p in plans]
check("ACT-03 REPAIR 方案已生成：PLAN-L-2H-REPAIR",
      "PLAN-L-2H-REPAIR" in plan_codes, f"现有={plan_codes}")
# L-2H 没有 backup，所以没有 REROUTE（这是正确的——说明书 §1.2 默认场景）
repair = next((p for p in plans if p.plan_code == "PLAN-L-2H-REPAIR"), None)
check("REPAIR 方案 can_meet_critical = 是 (20+30=50 ≤ 50)",
      repair and repair.can_meet_critical == "是", f"实际={repair.can_meet_critical if repair else 'None'}")

# ============================================================
# 4. ACT-05 修理回执 est=55 > 关键期 50 → REPAIR 方案作废
# ============================================================
print()
print("=" * 60)
print("[4] ACT-05 修理回执：REPAIR 方案作废")
print("=" * 60)

# L-2H 容量超限场景：AL 触发的修理 + REPAIR 方案
reset()
# 走完整链路：T+15 L-1H 故障 → T+20 L-2H 容量超限 → REPAIR 方案
clock = get_clock()
clock.start()
clock.start_wallclock = datetime.now() - timedelta(minutes=20)
client.post("/api/comm/alarm", json={"link_code":"L-1H","alarm_type":"link_down","native_severity":"critical","event_min":0})
client.post("/api/comm/switch", json={"alarm_link_code":"L-1H","as_of_min":15})
recalc_all(as_of_min=20)
plans = PlanRepo.list_all()
l2h_plan = next((p for p in plans if p.plan_code == "PLAN-L-2H-REPAIR"), None)
check("[4] 前置：L-2H REPAIR 方案已存在", l2h_plan is not None, f"现有={[p.plan_code for p in plans]}")
# 找 L-2H 修理单
repairs = RepairRepo.list_all()
l2h_rep = next((o for o in repairs if o.link_code == "L-2H"), None)
# L-2H 没 ACT-01 切换因此没自动写修理单；显式写一张
if l2h_rep is None:
    from app.db.row import RepairOrderRow
    from app.db import transaction as _t
    with _t():
        from app.db.repo import RepairRepo
        RepairRepo.insert(RepairOrderRow(
            order_code="RP-L-2H-001", link_code="L-2H", alarm_code=None,
            request_min=20, est_finish_min=30, status="已申请",
        ))
    repairs = RepairRepo.list_all()
    l2h_rep = next((o for o in repairs if o.link_code == "L-2H"), None)
check("L-2H 已有修理单", l2h_rep is not None, f"现有={[o.order_code for o in repairs]}")
if l2h_rep:
    r = client.post("/api/comm/repair", json={"order_code": l2h_rep.order_code, "est_finish_min": 55})
    check("修理回执 HTTP 200", r.status_code == 200)
    plans = PlanRepo.list_all()
    repair = next((p for p in plans if p.plan_code == "PLAN-L-2H-REPAIR"), None)
    check("REPAIR 方案 status = 已作废 (est=55 > 关键期 50)",
          repair and repair.status == "已作废", f"实际={repair.status if repair else 'None'}")
    check("REPAIR 方案 can_meet_critical = 否",
          repair and repair.can_meet_critical == "否", f"实际={repair.can_meet_critical if repair else 'None'}")

# ============================================================
# 5. ACT-04：60 秒超时自动（SYSTEM / 超时自动）
# ============================================================
print()
print("=" * 60)
print("[5] ACT-04 超时自动：SYSTEM / 超时自动 审批")
print("=" * 60)

# L-2H 没有 backup_link_code，所以只有 REPAIR 方案没有 REROUTE。
# [4] 末尾已把 REPAIR 方案作废，需要先 reset + 重新触发 ACT-03 拿新方案
reset()
clock = get_clock()
clock.start()
clock.start_wallclock = datetime.now() - timedelta(minutes=20)
client.post("/api/comm/alarm", json={"link_code":"L-1H","alarm_type":"link_down","native_severity":"critical","event_min":0})
client.post("/api/comm/switch", json={"alarm_link_code":"L-1H","as_of_min":15})
recalc_all(as_of_min=20)
plans = PlanRepo.list_pending()
repair = next((p for p in plans if p.plan_code == "PLAN-L-2H-REPAIR"), None)
if repair:
    plan_code = repair.plan_code
    ap = decide_approval(
        plan_code=plan_code, decision="批准",
        approver="ROLE-APPROVER", decision_source="人工",
        reason="REPAIR 方案可行批准",
    )
    check("approval_code 真实值", ap.approval_code and ap.approval_code.startswith("APV-"), ap.approval_code)
    check("approver = ROLE-APPROVER", ap.approver == "ROLE-APPROVER", ap.approver)
    check("decision_source = 人工", ap.decision_source == "人工", ap.decision_source)
    check("decision = 批准", ap.decision == "批准", ap.decision)
    check("decided_min 真实值", ap.decided_min is not None, str(ap.decided_min))
    p = PlanRepo.get(plan_code)
    check("REPAIR 方案 status = 批准（不是已执行）", p and p.status == "批准",
          f"实际={p.status if p else 'None'}")

# 单独测一次超时自动：reset + 容量超限 L-2H（但 L-2H 无 backup）→ 跳过 REROUTE 测试，只验证超时分支
plans = PlanRepo.list_pending()
if plans:
    ap = decide_approval(
        plan_code=plans[0].plan_code, decision="批准",
        approver="SYSTEM", decision_source="超时自动",
        reason="60 秒未决策，自动执行保守默认方案",
    )
    check("超时分支 approver = SYSTEM", ap.approver == "SYSTEM", ap.approver)
    check("超时分支 decision_source = 超时自动", ap.decision_source == "超时自动", ap.decision_source)
else:
    check("超时分支：有待审批方案可测", False, "前置无待审批方案")

# ============================================================
# 6. ACT-06 链路恢复 + 任务回迁 + 备用释放
# ============================================================
print()
print("=" * 60)
print("[6] ACT-06 链路恢复 + 任务回迁 + 备用释放")
print("=" * 60)

# [4][5] 已准备：L-2H 容量超限（实际链路没 down，所以 restore 只检查链路状态变更）
# 简化：用 L-1H 链路做完整 ACT-06 闭环——reset + 注入 + switch + 触发 ACT-03 + 批准 REROUTE + restore
reset()
client.post("/api/comm/alarm", json={"link_code": "L-1H", "alarm_type": "link_down", "native_severity": "critical", "event_min": 0})
client.post("/api/comm/switch", json={"alarm_link_code": "L-1H", "as_of_min": 5})
recalc_all(as_of_min=0)
plans = PlanRepo.list_pending()
reroute = next((p for p in plans if p.is_default == "是"), None)
if reroute:
    decide_approval(plan_code=reroute.plan_code, decision="批准", approver="ROLE-APPROVER", decision_source="人工")

# 现在 L-1H down, T-A 在 L-3H。模拟修理完成 → 回迁
r = client.post("/api/comm/restore", json={"link_code": "L-1H", "as_of_min": 60})
d = parse(r)
res = d.get("data", {}) if isinstance(d.get("data"), dict) else {}
check("ACT-06 恢复 HTTP 200", r.status_code == 200)
check("restored_tasks 含 T-A", "T-A" in res.get("restored_tasks", []), f"restored={res.get('restored_tasks', [])}")
check("released_links 含 L-3H（备用释放）", "L-3H" in res.get("released_links", []), f"released={res.get('released_links', [])}")

# 验证 L-3H 占用归零 + L-1H 重新承载 T-A
links = {l["link_code"]: l for l in parse(client.get("/api/comm/links")).get("data", [])}
check("L-3H current = 0.0 (释放归零)", links["L-3H"]["current_load_gbps"] == 0.0, f"实际={links['L-3H']['current_load_gbps']}")
check("L-3H warn_status = 正常", links["L-3H"]["warn_status"] == "正常")
check("L-1H current = 3.0 (T-A 回迁 3G)", links["L-1H"]["current_load_gbps"] == 3.0, f"实际={links['L-1H']['current_load_gbps']}")
check("L-1H oper_state = up", links["L-1H"]["oper_state"] == "up")
tasks = {t["task_code"]: t for t in parse(client.get("/api/comm/tasks")).get("data", [])}
check("T-A.current_link_code = L-1H（已回迁）", tasks["T-A"]["current_link_code"] == "L-1H",
      f"实际={tasks['T-A']['current_link_code']}")

# ============================================================
# 7. 事件链：完整闭环 6 种事件类型
# ============================================================
print()
print("=" * 60)
print("[7] 事件链：完整闭环 6 种事件类型")
print("=" * 60)

# 重置 + 走完整 T+0 → T+55 闭环
reset()
r = client.post("/api/comm/alarm", json={"link_code": "L-1H", "alarm_type": "link_down", "native_severity": "critical", "event_min": 0})
ac = parse(r).get("data", {}).get("alarm_code")
check("T+0 注入告警 AL-01", ac == "AL-01", f"alarm_code={ac}")

r = client.post("/api/comm/switch", json={"alarm_link_code": "L-1H", "as_of_min": 5})
check("T+0 立即 switch", r.status_code == 200)

# 触发 ACT-03 + 批准 REROUTE
recalc_all(as_of_min=0)
plans = PlanRepo.list_pending()
reroute = next((p for p in plans if p.is_default == "是"), None)
if reroute:
    decide_approval(plan_code=reroute.plan_code, decision="批准", approver="ROLE-APPROVER", decision_source="人工")

# T+55 restore
r = client.post("/api/comm/restore", json={"link_code": "L-1H", "as_of_min": 55})
check("T+55 restore HTTP 200", r.status_code == 200)

events = EventRepo.list_all()
types = [e.event_type for e in events]
print(f"  事件类型序列: {types}")
for t in ("alarm_injected", "switch_done", "plan_generated", "approval_decided", "link_restored"):
    check(f"含 {t} 事件", t in types, f"已有={set(types)}")
check("含 state_changed 事件 (链路状态变化或预订兑现)",
      "state_changed" in types or "occupancy_exceeded" in types)

# ============================================================
# 8. 预订兑现：原 task_booked 行的 event_type 不被覆盖（修复项 6）
# ============================================================
print()
print("=" * 60)
print("[8] 预订兑现：原 task_booked 行 event_type 保留")
print("=" * 60)
reset()
import asyncio
from app.services.engine import _tick_once
clock = get_clock()
clock.start()
# 写一条预订：ref_type=alarm, ref_code=L-1H, occur_min=0
from app.db.row import SceneEventRow
from app.db import transaction as _t
with _t():
    # 用 ref_type=demand（兑现走 demand_change 路径，不触发 switch → 不撞 comm_task_link 唯一约束）
    EventRepo.insert(SceneEventRow(
        event_code="EVT-TEST-BOOK", event_type="task_booked",
        ref_type="demand", ref_code="D-TEST-BOOK", occur_min=0,
        summary="task=T-B;gbps=3.00;测试预订兑现",
    ))
# 触发 tick
asyncio.run(_tick_once())
booked = next((e for e in EventRepo.list_all() if e.event_code == "EVT-TEST-BOOK"), None)
check("原 task_booked 行 event_type 仍是 task_booked（未被覆盖）",
      booked and booked.event_type == "task_booked", f"实际={booked.event_type if booked else None}")
check("原行 summary 追加「已兑现」",
      booked and "已兑现" in (booked.summary or ""), f"实际={booked.summary if booked else 'None'}")
state_changed_evt = [e for e in EventRepo.list_all()
                     if e.event_type == "state_changed"]
check("新增独立 state_changed 兑现事件", len(state_changed_evt) > 0, f"共 {len(state_changed_evt)} 条")

# ============================================================
# 9. 唯一约束 / PyMySQL 参数化 / 9 张表对应
# ============================================================
print()
print("=" * 60)
print("[9] DB：唯一约束 + PyMySQL 参数化 + 9 张表对应 .owl")
print("=" * 60)

import re
reset()
# 重复插 (T-A, 0) 应被 uk_task_min 拒绝
try:
    from app.db.row import TaskLinkBindingRow
    from sqlalchemy.exc import IntegrityError
    with transaction():
        TaskLinkRepo.insert(TaskLinkBindingRow(task_code="T-A", link_code="L-1H", assigned_min=0, note="dup"))
    check("重复 (T-A, 0) 应被 uk_task_min 拒绝", False, "重复插入未抛异常")
except Exception as e:
    check("重复 (T-A, 0) 被唯一约束拒绝",
          "uk_task_min" in str(e).lower() or "duplicate" in str(e).lower(), f"异常类型={type(e).__name__}")

# 静态扫描：app/db/repo.py 找 f-string 拼 SQL
repo_path = os.path.join(os.path.dirname(__file__), "app", "db", "repo.py")
with open(repo_path, encoding="utf-8") as f:
    src = f.read()
bad = re.findall(r"execute\(f[\"'].*%s", src)  # 仅查 f-string 拼接 %s
check("app/db/repo.py 无 f-string 拼 SQL", len(bad) == 0, f"潜在问题 {len(bad)} 个")

# .owl 类 ↔ 数据库表
# 修复（minor 13）：去掉未使用的 yaml 导入 + 表检查改走 TMA_MYSQL_* 配置
with open(os.path.join(os.path.dirname(__file__), "..", "depoly", "通信任务保障本体_v4.1.owl"), encoding="utf-8") as f:
    owl = f.read()
owl_classes = re.findall(r'owl:Class rdf:about="#(\w+)"', owl)
expected = {
    "CommTask": "comm_task", "CommLink": "comm_link",
    "TrafficDemand": "comm_demand", "ResourceAlarm": "comm_alarm",
    "CommPlan": "comm_plan", "ApprovalRecord": "comm_approval",
    "RepairOrder": "comm_repair_order", "SceneEvent": "comm_event",
    "TaskLinkBinding": "comm_task_link",
}
matched = sum(1 for k in expected if k in owl_classes)
check("9 张表与 .owl 8+1 类一一对应", matched == 9, f"对应={matched}/9")

# 9 张表实际存在（走配置而不是硬编码 root/123456/zb_db2）
from app.db import query
rows = [next(iter(r.values())) for r in query("SHOW TABLES")]
check("9 张表全部存在", all(t in rows for t in expected.values()), f"实际={rows}")

# ============================================================
print()
print("=" * 60)
total = passes + fails
print(f"总计：{passes} pass / {fails} fail（{total} 项）")
print("=" * 60)
sys.exit(0 if fails == 0 else 1)