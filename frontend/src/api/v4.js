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
export const switchLink = (payload) => http.post('/comm/switch', payload);

// ============================================================
// SK-02 ACT-02 计算链路占用与前瞻预警
// ============================================================
export const calcOccupancy = (payload) => http.post('/comm/occupancy', payload);

// ============================================================
// SK-03 ACT-03 生成处置方案
// ============================================================
export const generatePlan = (payload) => http.post('/comm/plan', payload);

// ============================================================
// SK-04 ACT-04 人工审批决策
// ============================================================
export const approvePlan = (payload) => http.post('/comm/approve', payload);
// 修复（round2 minor E）：批量拒绝改走单一接口，避免循环 /approve
// 第 2 次起被后端 _do_decide 标"已作废" → 400 toast 噪音
export const rejectAllPlans = (payload) => http.post('/comm/plans/reject-all', payload);

// ============================================================
// SK-05 ACT-05 修理单回执登记（含修理完成）
// ============================================================
export const receiveRepair = (payload) => http.post('/comm/repair', payload);

// ============================================================
// SK-06 事故注入（输入框·事故类）
// ============================================================
export const injectAlarm = (payload) => http.post('/comm/alarm', payload);

// ============================================================
// SK-07 ACT-06 链路恢复与任务回迁
// ============================================================
export const restoreLink = (payload) => http.post('/comm/restore', payload);

// ============================================================
// SK-08 添加任务（输入框·任务类）
// ============================================================
export const addTask = (payload) => http.post('/comm/task', payload);

// ============================================================
// SK-09 添加链路（输入框·链路类）
// ============================================================
export const addLink = (payload) => http.post('/comm/link', payload);

// ============================================================
// SK-10 需求变更登记（输入框·提示类·结构化）
// ============================================================
export const addDemand = (payload) => http.post('/comm/demand', payload);

// ============================================================
// 查询接口
// ============================================================
export const getSnapshot = () => http.get('/comm/snapshot');
export const listLinks = () => http.get('/comm/links');
export const listTasks = () => http.get('/comm/tasks');
export const listAlarms = () => http.get('/comm/alarms');
export const listPlans = () => http.get('/comm/plans');
export const listApprovals = () => http.get('/comm/approvals');
export const listRepairs = () => http.get('/comm/repairs');
export const listEvents = () => http.get('/comm/events');
export const resetCommScene = () => http.post('/comm/reset');

// ============================================================
// 扩展能力（说明书未列，保留）
// ============================================================
export const getForecast = (params) => http.get('/comm/forecast', { params });
export const precheckAddTask = (payload) => http.post('/comm/precheck/task', payload);
export const getEvidenceReport = () => http.get('/comm/evidence/report');

// ============================================================
// 引擎接口（§7.2）
// ============================================================
export const startScene = () => http.post('/scene/start');
export const pauseScene = () => http.post('/scene/pause');
export const resumeScene = () => http.post('/scene/resume');
export const getSceneState = () => http.get('/scene/state');
export const resetScene = () => http.post('/scene/reset');
export const decideOnScene = (payload) => http.post('/scene/decide', payload);
export const postSceneInput = (payload) => http.post('/scene/input', payload);

// ============================================================
// 业务事件流（WebSocket）—— §7.2 /api/scene/ws
// ============================================================
export const SCENE_WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/api/scene/ws';
// 注：WS URL 保留 /api 前缀——浏览器原生 WebSocket 不走 http baseURL，必须给完整路径

// ============================================================
// 兼容旧 utils/api.js 的导出
// ============================================================
export {
    switchLink as switchTask,
    addTask as addTaskV4,
    addLink as addLinkV4,
    addDemand as addDemandV4,
};
