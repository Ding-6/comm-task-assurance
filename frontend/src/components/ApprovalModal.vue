<template>
  <Teleport to="body">
    <div
      v-if="approval.open"
      class="mask"
      role="dialog"
      aria-modal="true"
      @click.self="$emit('close')"
    >
      <article class="modal">
        <!-- 标题区 -->
        <header class="mh">
          <span class="pill over">✕ 容量超限</span>
          <h3>处置方案审批 · {{ approval.alarm || 'L-1H 故障引发 L-2H 前瞻超限' }}</h3>
          <button type="button" class="x" aria-label="关闭" @click="$emit('close')">×</button>
        </header>

        <div class="mb-body">
          <!-- 上下文四格 -->
          <div class="ctx">
            <div>关联告警<b class="mono">{{ approval.alarmCode || 'AL-02 @ L-2H' }}</b></div>
            <div>预计超限时刻<b class="mono">{{ approval.hitTime || 'T+50' }}</b></div>
            <div>预测占用 / 容量<b class="mono red">{{ approval.predicted || '11.00G / 10.00G' }}</b></div>
            <div>距超限余量<b class="mono">{{ approval.remainMin || 30 }} 分钟</b></div>
          </div>

          <!-- ACT-05 刷新条 -->
          <div v-if="approval.refresh" class="fresh">
            ◆ 修理单回执已到达：{{ approval.refresh }}
            <span class="mla">倒计时不重置</span>
          </div>

          <!-- 倒计时 -->
          <div class="cd" :class="{ hot: remain <= 10 }" aria-live="polite">
            <span class="n mono">{{ remain }}</span>
            <div class="track"><i :style="{ width: (remain/60*100) + '%' }"></i></div>
            <small>秒后自动执行「保守默认」方案</small>
          </div>

          <!-- 方案 -->
          <div class="plans">
            <div v-for="p in approval.plans" :key="p.code" class="plan" :class="planCls(p)">
              <h4>
                {{ p.name }}
                <span v-if="p.voided" class="pill over">✕ 已作废</span>
                <span v-else class="pill idle">待审批</span>
              </h4>
              <div class="code mono">{{ p.code }}</div>
              <dl>
                <dt>故障链路</dt><dd class="mono">{{ p.fault || 'L-1H' }}</dd>
                <dt>预计修复</dt><dd class="mono">{{ p.estRepair }}</dd>
                <dt>能否赶上关键期</dt>
                <dd :class="p.canMeet ? 'ok' : 'bad'">
                  {{ p.canMeet ? '✓ 是' : '✕ 否' }} · {{ p.canMeetNote }}
                </dd>
                <dt>处置后</dt><dd>{{ p.after || '修好后 T-A 回迁 L-1H' }}</dd>
              </dl>
              <div class="rec">{{ p.rec }}</div>
              <!-- 修复 bug5：两按钮同一水平线（justify-content: space-between 也会让一个按钮靠左，这里用 grid 强制并列） -->
              <div class="acts">
                <button class="btn" :disabled="p.voided || busy" @click="decide(p, 'reject')">拒绝 {{ p.type }}</button>
                <button class="btn pri" :disabled="p.voided || busy" @click="decide(p, 'approve')">批准 {{ p.type }}</button>
              </div>
              <span v-if="p.isDefault" class="badge">保守默认</span>
            </div>
          </div>

          <!-- 修复 bug7：默认方案说明移到审批理由上方；bug6：去掉括号元信息，给出"将自动执行 XXX 方案"具体描述 -->
          <div v-if="defaultNote" class="auto-note">
            <b>◆ 自动执行说明</b>：{{ defaultNote }}
          </div>

          <div class="reason">
            <label>审批理由（选填）</label>
            <textarea v-model="reason" placeholder="例如：修复耗时过长，切换备用"></textarea>
          </div>
        </div>

        <footer class="mf">
          <span class="muted">拒绝 = 维持现状，持续预警监测</span>
          <button class="btn danger" :disabled="busy" @click="$emit('reject-all')">拒绝全部方案</button>
          <button class="btn ghost" :disabled="busy" @click="$emit('close')">关闭</button>
        </footer>
      </article>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { decideApproval } from '../utils/api';

const props = defineProps({ approval: { type: Object, required: true } });
const emit = defineEmits(['decide', 'close', 'reject-all']);

const reason = ref('');
const remain = ref(60);
const busy = ref(false);
let timer = null;

function start() {
  if (timer) clearInterval(timer);
  remain.value = props.approval.countdown ?? 60;
  timer = setInterval(() => {
    if (remain.value > 0) remain.value -= 1;
    else { clearInterval(timer); timer = null; autoExecute(); }
  }, 1000);
}
function stop() { if (timer) { clearInterval(timer); timer = null; } }

// 修复 bug11：监听 Esc 关闭 + open 变化 stop/start
function onKey(e) { if (e.key === 'Escape' && props.approval.open) emit('close'); }
onMounted(() => window.addEventListener('keydown', onKey));
onBeforeUnmount(() => { stop(); window.removeEventListener('keydown', onKey); });

watch(() => props.approval.open, (v) => { v ? start() : stop(); });
watch(() => props.approval.countdown, (v) => {
  // 外部刷新方案时倒计时不重置（设计稿硬约束）
  if (v != null && remain.value <= 0) remain.value = v;
});

function planCls(p) {
  if (p.voided) return 'void';
  if (p.isDefault) return 'def';
  return '';
}

// 修复 bug6：把"60 秒未决策将自动执行..."从元信息括号里改成具体方案描述
const defaultNote = computed(() => {
  const def = (props.approval.plans || []).find((x) => x.isDefault);
  if (!def) return null;
  // 友好描述：如"将自动执行 转备用 L-3H 方案"
  if (def.type === 'REROUTE' || def.type === 'reroute') {
    const tgt = def.target || def.targetLinkCode || '备用链路';
    return `60 秒未决策将自动执行 ${def.name || 'REROUTE'} 方案：把 ${def.fault || '故障链路'} 上的任务迁至 ${tgt}，立即解除容量风险。`;
  }
  if (def.type === 'REPAIR' || def.type === 'repair') {
    return `60 秒未决策将自动执行 ${def.name || 'REPAIR'} 方案：保持现状等修理（预计 ${def.estRepair || def.estRepairMin || ''} 分钟修复）。`;
  }
  return `60 秒未决策将自动执行 ${def.name || '默认'} 方案。`;
});

async function decide(p, kind) {
  if (busy.value) return;
  busy.value = true;
  try {
    // 修复（major 9）：后端 decision 只接受 '批准' / '拒绝'
    const decisionCn = kind === 'approve' ? '批准' : '拒绝';
    await decideApproval({
      plan_code: p.code,
      decision: decisionCn,
      reason: reason.value,
      approver: 'ROLE-APPROVER',
      decision_source: '人工',
    });
    ElMessage.success(`已${decisionCn}：${p.code}`);
    emit('decide', { plan: p, kind, reason: reason.value });
    stop();
  } catch (e) {
    // 修复 bug4：把后端 BizException 反馈给用户（http.js 已经 ElMessage，这里再加一条更具体）
    const msg = e?.response?.data?.message || e?.message || '审批失败';
    ElMessage.error(`审批失败：${msg}`);
  } finally {
    busy.value = false;
  }
}

async function autoExecute() {
  const def = (props.approval.plans || []).find((x) => x.isDefault);
  if (!def) return;
  if (busy.value) return;
  busy.value = true;
  try {
    await decideApproval({
      plan_code: def.code,
      decision: '批准',
      reason: '60 秒未决策，自动执行保守默认方案',
      approver: 'SYSTEM',
      decision_source: '超时自动',
    });
    ElMessage.warning(`超时自动执行：${def.code}`);
    emit('decide', { plan: def, kind: 'approve', reason: '超时自动' });
  } catch (e) {
    // 即使超时自动失败也要让前端能关闭弹窗
    emit('decide', { plan: def, kind: 'timeout_failed', reason: '超时自动' });
  } finally {
    busy.value = false;
  }
}
</script>

<style scoped>
.mask {
  position: fixed; inset: 0;
  background: rgba(30,40,60,.30);
  display: flex; align-items: center; justify-content: center;
  z-index: 100; padding: 20px;
}
.modal {
  width: 880px; max-width: 96vw;
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; box-shadow: 0 20px 60px rgba(20,20,30,.18);
}
.mh {
  padding: 16px 20px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px;
}
.mh h3 { margin: 0; font-size: 16px; flex: 1 1 auto; }
.mh .x {
  width: 28px; height: 28px; border: 0; background: transparent;
  font-size: 22px; line-height: 1; cursor: pointer; color: var(--sub);
  border-radius: 4px;
}
.mh .x:hover { background: var(--chip); color: var(--text); }
.mb-body { padding: 16px 20px; }

.ctx { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.ctx div { background: var(--chip); border-radius: 6px; padding: 8px 10px; font-size: 12px; color: var(--sub); }
.ctx b { display: block; color: var(--text); font-size: 15px; margin-top: 2px; }
.ctx .red { color: var(--over) !important; }

.cd { display: flex; align-items: center; gap: 12px; padding: 10px 12px; background: var(--chip); border-radius: 8px; margin-bottom: 14px; }
.cd .n { font-size: 28px; font-weight: 700; min-width: 64px; }
.cd .track { flex: 1; height: 8px; border-radius: 4px; background: #e3dfd3; overflow: hidden; }
.cd .track i { display: block; height: 100%; background: var(--accent); border-radius: 4px; transition: width 1s linear; }
.cd.hot .n { color: var(--over); }
.cd.hot .track i { background: var(--over); }
.cd.hot { box-shadow: 0 0 0 1px var(--over) inset; }
.cd small { color: var(--sub); font-size: 12px; }

.fresh {
  display: flex; gap: 8px; align-items: center;
  font-size: 12.5px; padding: 8px 12px; border-radius: 6px;
  background: var(--backup-bg); color: var(--backup-fg); margin-bottom: 12px;
  border: 1px solid var(--backup-bd);
}
.fresh b { color: var(--text); }
.fresh .mla { margin-left: auto; font-size: 12px; }

.plans { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.plan {
  position: relative;
  border: 1px solid var(--border); border-radius: 10px;
  padding: 14px; background: var(--card);
}
.plan.def { border-color: var(--backup); background: linear-gradient(180deg, var(--backup-bg) 0%, var(--card) 60%); }
.plan.void { opacity: .55; background: repeating-linear-gradient(135deg, #f1ede3 0 10px, #ede8da 10px 20px); }
.plan h4 { margin: 0 0 4px; font-size: 14px; display: flex; align-items: center; gap: 8px; color: var(--text); }
.plan .code { font-size: 11px; color: var(--sub); }
.plan dl { display: grid; grid-template-columns: 110px 1fr; gap: 4px 8px; font-size: 12.5px; margin: 10px 0; }
.plan dt { color: var(--sub); }
.plan dd { margin: 0; color: var(--text); }
.plan dd.ok { color: var(--ok); }
.plan dd.bad { color: var(--over); }
.plan .rec {
  font-size: 12px; color: var(--text);
  background: var(--chip); border-radius: 6px; padding: 8px 10px; line-height: 1.6;
}
/* 修复 bug5：两个按钮用 grid 强制同一行 */
.plan .acts {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
  margin-top: 12px;
}
.badge {
  position: absolute; top: 12px; right: 12px;
  font-size: 11px; padding: 2px 8px; border-radius: 10px;
  background: var(--backup); color: #fff; font-weight: 700;
}

/* 修复 bug7：默认方案说明，移到审批理由上面 */
.auto-note {
  margin-top: 12px;
  padding: 8px 12px;
  font-size: 12.5px;
  background: var(--warn-bg);
  color: var(--warn-fg);
  border: 1px solid var(--warn-bd);
  border-radius: 6px;
}
.auto-note b { color: var(--text); margin-right: 4px; }

.btn {
  font: inherit; font-size: 13px; padding: 7px 14px;
  border-radius: 6px; border: 1px solid var(--border);
  background: var(--card); color: var(--text); cursor: pointer;
}
.btn:hover { border-color: var(--accent); }
.btn.pri { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.danger { border-color: var(--over); color: var(--over); }
.btn.ghost { background: transparent; }
.btn[disabled] { opacity: .4; cursor: not-allowed; }

.reason { margin-top: 12px; }
.reason label { display: block; font-size: 12px; color: var(--sub); margin-bottom: 4px; }
.reason textarea {
  width: 100%; height: 46px;
  background: var(--chip); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); font: inherit; font-size: 12.5px; padding: 6px 10px; resize: none;
}

.mf { padding: 12px 20px; border-top: 1px solid var(--border); display: flex; gap: 10px; align-items: center; }
</style>
