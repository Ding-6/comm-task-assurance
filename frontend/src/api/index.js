/**
 * 通信任务保障 · 前端接口调用层（统一出口）
 *
 * 全部按《本体规格说明书 V4.1》§7.1 / §7.2 路径实现：
 *
 * 业务能力 SK-01~SK-10：
 *   POST /api/comm/alarm      注入告警
 *   POST /api/comm/switch     链路故障切换
 *   POST /api/comm/occupancy  占用 + 前瞻
 *   POST /api/comm/plan       生成方案
 *   POST /api/comm/approve    人工审批
 *   POST /api/comm/repair     修理回执 / 完成
 *   POST /api/comm/restore    链路恢复 + 任务回迁
 *   POST /api/comm/task       添加任务
 *   POST /api/comm/link       添加链路
 *   POST /api/comm/demand     需求变更
 *
 * 查询 / 列表：
 *   GET  /api/comm/snapshot
 *   GET  /api/comm/{links,tasks,alarms,plans,approvals,repairs,events}
 *   POST /api/comm/reset
 *
 * 扩展能力（说明书未列，保留）：
 *   GET  /api/comm/forecast
 *   POST /api/comm/precheck/task
 *   GET  /api/comm/evidence/report
 *
 * 引擎控制：
 *   GET  /api/scene/state
 *   POST /api/scene/{start,pause,resume,reset,decide,input}
 *   WS   /api/scene/ws
 *
 * 统一响应：{code: 0, message: "ok", data: ...}
 * 错误：code !== 0 抛 BizError（base.js http 拦截器处理）
 */
export * from './v4';
