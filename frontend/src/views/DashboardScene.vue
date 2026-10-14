<template>
  <div class="ds ds-fade">
    <!-- ====== 顶部状态条：实时本地时钟 + 相对偏移 ====== -->
    <header class="hdr">
      <span class="logo">通信任务保障演示台</span>
      <div class="clock">
        <span class="rel mono">{{ relLabel }}</span>
        <span class="wall mono">{{ wallclock }}</span>
        <span class="live">实时 1:1</span>
      </div>
      <div class="prog-wrap">
        <div class="prog" :aria-label="`保障窗口进度 ${offsetMin}/120`">
          <i :style="{ width: progress + '%' }"></i>
        </div>
        <div class="prog-lbl mono">窗口 {{ relLabel }} → {{ endLabel }} · 收尾 {{ endShort }}</div>
      </div>
      <span class="sp"></span>
      <div class="role">
        <span :class="{ on: role === 'ROLE-DUTY' }" @click="role = 'ROLE-DUTY'">值班员</span>
        <span :class="{ on: role === 'ROLE-B' }" @click="role = 'ROLE-B'">直播保障</span>
        <span :class="{ on: role === 'ROLE-APPROVER' }" @click="role = 'ROLE-APPROVER'">审批人</span>
      </div>
      <button class="btn" @click="goReport">事件流水</button>
      <button class="btn danger" @click="reset">复位</button>
    </header>

    <!-- ====== 顶部提示条 ====== -->
    <ToastBar :toasts="mergedToasts" @dismiss="onToastDismiss" />

    <!-- ====== 主区 ====== -->
    <main class="main">
      <section class="col">
        <div class="links">
          <LinkGauge v-for="(lk, i) in scene.links" :key="lk.link_code" :link="lk" :task-lookup="taskLookup" :idx="String(i+1).padStart(2,'0')" />
        </div>

        <ForecastChart
          :link-code="scene.forecast.link"
          :now="scene.forecast.now"
          :series="scene.forecast.series"
          :hit="scene.forecast.hit"
        />

        <div class="card tasks">
          <h3>
            保障任务
            <span class="sub">comm_task · 当前归属取 assigned_min 最新一行</span>
            <span class="r">{{ scene.tasks.length }} 项</span>
          </h3>
          <table class="t">
            <thead><tr><th>编码</th><th>名称</th><th>当前链路</th><th>当前需求</th><th>关键阶段</th><th>责任角色</th><th>归属说明</th></tr></thead>
            <tbody>
              <TaskCard v-for="t in scene.tasks" :key="t.task_code" :task="t" />
            </tbody>
          </table>
        </div>
      </section>

      <aside class="col col-r">
        <InputPanel
          :hint="hint"
          @submit="onSubmit"
          @open-task="taskForm = true"
          @open-link="linkForm = true"
        />
        <div class="card booking">
          <h3>
            预订事件队列
            <span class="sub">occur_min &gt; 当前即待兑现</span>
          </h3>
          <div v-for="b in scene.booking" :key="b.t" class="bk">
            <span class="when mono">{{ b.t }}</span>
            <span>{{ b.text }}</span>
            <span class="pill" :class="b.state === 'done' ? 'ok' : 'idle'">
              {{ b.state === 'done' ? '✓ 已兑现' : `◷ 待兑现 · 还有 ${b.remain} 分` }}
            </span>
          </div>
          <div v-if="!scene.booking.length" class="empty">暂无预订</div>
        </div>
        <div class="card events">
          <h3>
            事件流水
            <span class="sub">comm_event</span>
            <span class="r">{{ scene.events.length }} 条</span>
          </h3>
          <div class="scroll">
            <EventTimeline :events="scene.events" />
          </div>
        </div>
      </aside>
    </main>

    <NoticeModal :notice="scene.notice" @close="onNoticeClose" />
    <ApprovalModal
      :approval="liveApproval"
      @decide="onDecide"
      @reject-all="onRejectAll"
      @close="onApprovalClose"
    />

    <TaskFormDialog v-model="taskForm" />
    <LinkFormDialog v-model="linkForm" />
  </div>
</template>

<script setup>
import { computed, inject, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import LinkGauge from '../components/LinkGauge.vue';
import TaskCard from '../components/TaskCard.vue';
import EventTimeline from '../components/EventTimeline.vue';
import ForecastChart from '../components/ForecastChart.vue';
import ApprovalModal from '../components/ApprovalModal.vue';
import NoticeModal from '../components/NoticeModal.vue';
import ToastBar from '../components/ToastBar.vue';
import InputPanel from '../components/InputPanel.vue';
import TaskFormDialog from '../components/TaskFormDialog.vue';
import LinkFormDialog from '../components/LinkFormDialog.vue';
import { getScene, STEP_KEYS } from '../data/scenes';
import { resetScene, decideApproval, rejectAllPlans } from '../utils/api';
import { createSceneSocket } from '../utils/ws';
import { formatClock, plusMin, rel } from '../utils/time';
import { useRouter } from 'vue-router';

const { currentStep, setScene } = inject('scene');
const router = useRouter();

const tickRef = ref(new Date());
let tickTimer = null;
onMounted(() => {
  tickTimer = setInterval(() => { tickRef.value = new Date(); }, 1000);
});
onBeforeUnmount(() => tickTimer && clearInterval(tickTimer));

const offsetMin = computed(() => {
  const s = getScene(currentStep.value);
  return s.offsetMin ?? 0;
});

const wallclock = computed(() => formatClock(tickRef.value));
const relLabel = computed(() => rel(offsetMin.value));
const endLabel = computed(() => rel(120));
const endShort = computed(() => plusMin(tickRef.value, 120));
const progress = computed(() => Math.max(0, Math.min(100, (offsetMin.value / 120) * 100)));

const scene = computed(() => getScene(currentStep.value));
const taskLookup = computed(() => {
  const o = {};
  scene.value.tasks.forEach((t) => (o[t.task_code] = t));
  return o;
});

const role = ref('ROLE-DUTY');
const taskForm = ref(false);
const linkForm = ref(false);

const hint = ref('');
function jumpTo(step) {
  const idx = STEP_KEYS.indexOf(step);
  if (idx >= 0) setScene(idx);
}

watch(currentStep, (s) => {
  if (s === 'm21') hint.value = '修理单预计 55 分钟完成';
  else if (s === 'm55') hint.value = '线路 1 修好了';
  else hint.value = '';
}, { immediate: true });

function onSubmit(p) {
  if (p.parsed && p.parsed.includes('链路恢复')) jumpTo('m55');
}
function onNoticeClose() {}
function onApprovalClose() {}
function onDecide({ plan }) {
  ElMessage.success(`已决策：${plan.code}`);
  if (plan.type === 'REROUTE') jumpTo('m21');
}
async function onRejectAll() {
  // 修复（round2 minor A + E）：scene 是 computed ref，要 .value
  // 走单一后端接口 /comm/plans/reject-all，原子批拒绝一个告警下所有待审批方案
  const approval = scene.value.approval;
  if (!approval?.alarmCode) {
    ElMessage.warning('当前没有可拒绝的告警');
    return;
  }
  try {
    const res = await rejectAllPlans({
      alarm_code: approval.alarmCode,
      reason: '批量拒绝全部方案',
      approver: 'ROLE-APPROVER',
      decision_source: '人工',
    });
    const rejected = res?.data?.rejected ?? 0;
    ElMessage.warning(rejected > 0 ? `已拒绝 ${rejected} 个方案` : '没有可拒绝的待审批方案');
  } catch {/* http.js 已 ElMessage */}
}

function goReport() { router.push('/report'); }
async function reset() {
  try {
    await resetScene();
    ElMessage.success('场景已复位');
    setScene(0);
  } catch {/* */}
}

const wsRef = ref(null);
// 修复 bug2/3/9/10：实时累积 ws 推送的 toast / notice（不再走场景静态数据）
const liveToasts = ref([]); // {id, lv, ic, txt, src}
// 静态 toast dismiss 跟踪（避免自动消失逻辑被绕开）
const dismissedStaticKeys = ref(new Set());
// 切步骤时清空 dismissed 集合
watch(() => currentStep.value, () => dismissedStaticKeys.value = new Set());
// 修复：approval / notice 也走 ws 实时接收（不再走场景静态 data.approval）
const liveApproval = ref({ open: false, plans: [], alarmCode: '', alarm: '', hitTime: '', predicted: '', remainMin: 60, countdown: 60 });
let _toastIdSeq = 0;
function pushToast({ lv = 'info', ic = '', txt = '', src = '' }) {
  // 修复 toast 重入：去重（同样 lv+txt 60s 内只显示一次），
  // 避免引擎每分钟 tick 反复推同样的"超容量"toast 堆成山
  const dup = liveToasts.value.find((t) => t.lv === lv && t.txt === txt);
  if (dup) return;
  const id = ++_toastIdSeq;
  liveToasts.value = [...liveToasts.value, { id, lv, ic, txt, src }];
}
const mergedToasts = computed(() => {
  // 静态场景的 toasts（在脚本中作为占位） + 实时 ws 推送
  const staticOnes = (scene.value.toasts || [])
    .map((t, i) => ({
      id: `static-${currentStep.value}-${i}`,
      lv: t.lv || 'info', ic: t.ic || '', txt: t.txt || '', src: t.src || '',
    }))
    .filter((t) => !dismissedStaticKeys.value.has(t.id));
  return [...staticOnes, ...liveToasts.value];
});
function onToastDismiss(t) {
  // 静态 toast 也参与自动消失：按 (step+idx) 标记 dismissed，
  // 下次合并时过滤掉。简单做法：当前 step 切换时已会变 id（prefix 包含 currentStep），
  // 所以同一 step 内的静态 toast 关闭后下次合并会重新出现——这是预期（演示步骤回到该点时再次提示）。
  // 但单次合并里被 dismiss 后仍出现，违反 ToastBar 自动消失语义。
  // 修复：维护一个"本次 step 内已 dismiss 的静态 toast key 集合"。
  if (t.id && String(t.id).startsWith('static-')) {
    dismissedStaticKeys.value.add(String(t.id));
    return;
  }
  liveToasts.value = liveToasts.value.filter((x) => x.id !== t.id);
}
onMounted(() => {
  wsRef.value = createSceneSocket({
    onOpen: () => console.debug('[ws] connected'),
    onMessage: (msg) => {
      if (!msg) return;
      if (msg.type === 'snapshot' && msg.step) jumpTo(msg.step);
      // 修复 bug2/3/9/10：把后端 toast/modal_notice/alert 累积为顶部提示
      else if (msg.type === 'toast' && msg.data) {
        const d = msg.data;
        pushToast({ lv: d.level || 'info', ic: '', txt: d.message || '' });
      } else if (msg.type === 'modal_notice' && msg.data) {
        const d = msg.data;
        pushToast({ lv: d.level || 'info', ic: d.title || 'ⓘ', txt: d.message || '' });
      } else if (msg.type === 'alert' && msg.data) {
        const d = msg.data;
        pushToast({ lv: d.level || 'warn', ic: '!', txt: d.message || '' });
      } else if (msg.type === 'state' && msg.data) {
        // state 推送里也常带一个 listOfToasts（演示方）；兼容
        if (Array.isArray(msg.data.toasts)) {
          for (const t of msg.data.toasts) pushToast(t);
        }
      } else if (msg.type === 'engine_error' && msg.data) {
        pushToast({ lv: 'over', ic: '✕', txt: `引擎异常：${msg.data.error || ''}` });
      } else if (msg.type === 'modal_refresh' && msg.data) {
         // 修复 modal_refresh：审批中收到 ACT-05 修理回执，更新方案可行性（不重置倒计时）
         const d = msg.data;
         if (liveApproval.value.open) {
           liveApproval.value = {
             ...liveApproval.value,
             plans: Array.isArray(d.plans) ? d.plans : liveApproval.value.plans,
             refresh: d.fresh_message || liveApproval.value.refresh,
             fresh_message: d.fresh_message || liveApproval.value.fresh_message,
           };
         }
       } else if (msg.type === 'modal_approval' && msg.data) {
        // 修复：审批弹窗从 ws 推送打开
        const d = msg.data;
        liveApproval.value = {
          open: true,
          plans: Array.isArray(d.plans) ? d.plans : [],
          alarmCode: d.alarm_code || '',
          alarm: d.link_code ? `${d.link_code} 前瞻超限` : '容量超限',
          hitTime: d.predicted_min != null ? `T+${d.predicted_min}` : '',
          predicted: d.predicted_load != null ? `${Number(d.predicted_load).toFixed(2)}G / ${d.capacity != null ? Number(d.capacity).toFixed(2) : '?'}G` : '',
          remainMin: d.remain_min != null ? Number(d.remain_min) : 30,
          countdown: d.countdown != null ? Number(d.countdown) : 60,
          fresh_message: d.fresh_message || null,
          refresh: d.fresh_message || null,
        };
      } else if (msg.type === 'modal_close' && msg.data) {
        // 修复 bug11：modal_close 也关掉弹窗
        liveApproval.value = { ...liveApproval.value, open: false };
      }
    },
  });
});
onBeforeUnmount(() => wsRef.value?.close?.());
</script>

<style scoped>
.ds {
  display: flex; flex-direction: column;
  min-height: 100%;
  width: 100%;
  background: var(--page); color: var(--text);
}

.hdr {
  height: 56px; display: flex; align-items: center; gap: 16px;
  padding: 0 20px; border-bottom: 1px solid var(--border);
  background: var(--card2); flex: none;
}
.hdr .logo { font-weight: 700; font-size: 16px; letter-spacing: .5px; }
.hdr .clock {
  display: flex; align-items: center; gap: 10px;
  margin-left: 12px; padding: 4px 12px; border-radius: 6px;
  background: var(--chip); border: 1px solid var(--border);
}
.hdr .clock .rel { font-size: 18px; font-weight: 700; color: var(--text); }
.hdr .clock .wall { font-size: 18px; font-weight: 700; color: var(--accent); letter-spacing: .02em; }
.live { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--ok); }
.live::before {
  content: ""; width: 8px; height: 8px; border-radius: 50%;
  background: var(--ok); box-shadow: 0 0 0 3px rgba(21,128,61,.18);
}
.prog-wrap { display: flex; flex-direction: column; gap: 2px; }
.prog { width: 240px; height: 6px; background: var(--chip); border-radius: 3px; position: relative; overflow: hidden; }
.prog i { position: absolute; inset: 0 auto 0 0; background: var(--accent); border-radius: 3px; transition: width .3s; }
.prog-lbl { font-size: 11px; color: var(--sub); }
.sp { flex: 1; }

.role {
  display: flex; gap: 4px;
  background: var(--chip); border: 1px solid var(--border);
  border-radius: 6px; padding: 3px;
}
.role span { font-size: 12px; padding: 4px 10px; border-radius: 4px; color: var(--sub); cursor: pointer; }
.role span.on { background: var(--accent); color: #fff; }

.btn {
  font: inherit; font-size: 13px; padding: 7px 14px;
  border-radius: 6px; border: 1px solid var(--border);
  background: var(--card); color: var(--text); cursor: pointer;
}
.btn:hover { border-color: var(--accent); }
.btn.danger { border-color: #7f1d1d; color: #fecaca; }

.main {
  flex: 1; display: grid;
  grid-template-columns: 1fr 400px;
  gap: 16px; padding: 16px 20px;
  min-height: 0;
}
.col { display: flex; flex-direction: column; gap: 16px; min-height: 0; }
.col-r { min-height: 0; }
.links { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }

.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
.card h3 { margin: 0 0 10px; font-size: 14px; display: flex; align-items: center; gap: 8px; }
.card h3 .sub { font-weight: 400; color: var(--sub); font-size: 12px; }
.card h3 .r { margin-left: auto; font-size: 12px; color: var(--sub); }
.scroll { overflow: auto; min-height: 0; flex: 1; }
.empty { color: var(--muted); font-size: 12px; padding: 10px 0; }

table.t { width: 100%; border-collapse: collapse; font-size: 12.5px; }
table.t th { text-align: left; color: var(--sub); font-weight: 400; padding: 6px 8px; border-bottom: 1px solid var(--border); }
table.t td { padding: 7px 8px; border-bottom: 1px solid var(--border-soft); }

.bk {
  display: flex; align-items: center; gap: 8px;
  font-size: 12.5px; padding: 7px 0;
  border-bottom: 1px solid var(--border-soft);
}
.bk:last-child { border-bottom: 0; }
.bk .when { min-width: 90px; color: var(--accent); }

.events { flex: 1; display: flex; flex-direction: column; min-height: 240px; }
.events .scroll { flex: 1; }
</style>