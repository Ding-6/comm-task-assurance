/**
 * 通信任务保障 · 前端接口调用层（V4.1 / §7.1 重写）
 *
 * 设计原则：
 * - 路径严格对齐《本体规格说明书 V4.1》§7.1 / §7.2
 * - 统一响应 {code, message, data}：code === 0 表示成功
 * - 错误处理由 base.js 的 http 拦截器统一抛 BizError
 * - 业务接口（SK-01~SK-10）+ 引擎接口 + 扩展能力分组
 */
import http from '../utils/http';

// ============================================================
// SK-01 ACT-01 识别资源异常并自动切换
// ============================================================
export const switchLink = (payload) => http.post('/api/comm/switch', payload);

// ============================================================
// SK-02 ACT-02 计算链路占用与前瞻预警
// ============================================================
export const calcOccupancy = (payload) => http.post('/api/comm/occupancy', payload);

// ============================================================
// SK-03 ACT-03 生成处置方案
// ============================================================
export const generatePlan = (payload) => http.post('/api/comm/plan', payload);

// ============================================================
// SK-04 ACT-04 人工审批决策
// ============================================================
export const approvePlan = (payload) => http.post('/api/comm/approve', payload);

// ============================================================
// SK-05 ACT-05 修理单回执登记（含修理完成）
// ============================================================
export const receiveRepair = (payload) => http.post('/api/comm/repair', payload);

// ============================================================
// SK-06 事故注入（输入框·事故类）
// ============================================================
export const injectAlarm = (payload) => http.post('/api/comm/alarm', payload);

// ============================================================
// SK-07 ACT-06 链路恢复与任务回迁
// ============================================================
export const restoreLink = (payload) => http.post('/api/comm/restore', payload);

// ============================================================
// SK-08 添加任务（输入框·任务类）
// ============================================================
export const addTask = (payload) => http.post('/api/comm/task', payload);

// ============================================================
// SK-09 添加链路（输入框·链路类）
// ============================================================
export const addLink = (payload) => http.post('/api/comm/link', payload);

// ============================================================
// SK-10 需求变更登记（输入框·提示类·结构化）
// ============================================================
export const addDemand = (payload) => http.post('/api/comm/demand', payload);

// ============================================================
// 查询接口
// ============================================================
export const getSnapshot = () => http.get('/api/comm/snapshot');
export const listLinks = () => http.get('/api/comm/links');
export const listTasks = () => http.get('/api/comm/tasks');
export const listAlarms = () => http.get('/api/comm/alarms');
export const listPlans = () => http.get('/api/comm/plans');
export const listApprovals = () => http.get('/api/comm/approvals');
export const listRepairs = () => http.get('/api/comm/repairs');
export const listEvents = () => http.get('/api/comm/events');
export const resetCommScene = () => http.post('/api/comm/reset');

// ============================================================
// 扩展能力（说明书未列，保留）
// ============================================================
export const getForecast = (params) => http.get('/api/comm/forecast', { params });
export const precheckAddTask = (payload) => http.post('/api/comm/precheck/task', payload);
export const getEvidenceReport = () => http.get('/api/comm/evidence/report');

// ============================================================
// 引擎接口（§7.2）
// ============================================================
export const startScene = () => http.post('/api/scene/start');
export const pauseScene = () => http.post('/api/scene/pause');
export const resumeScene = () => http.post('/api/scene/resume');
export const getSceneState = () => http.get('/api/scene/state');
export const resetScene = () => http.post('/api/scene/reset');
export const decideOnScene = (payload) => http.post('/api/scene/decide', payload);
export const postSceneInput = (payload) => http.post('/api/scene/input', payload);

// ============================================================
// 业务事件流（WebSocket）—— §7.2 /api/scene/ws
// ============================================================
export const SCENE_WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/scene/ws';

// ============================================================
// 兼容旧 utils/api.js 的导出
// ============================================================
export {
    switchLink as switchTask,
    addTask as addTaskV4,
    addLink as addLinkV4,
    addDemand as addDemandV4,
};
