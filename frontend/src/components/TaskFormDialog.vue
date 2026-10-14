<template>
  <Teleport to="body">
    <div v-if="modelValue" class="mask">
      <article class="fm" role="dialog" aria-label="添加任务">
        <header class="mh">
          添加任务 <span class="muted sub">SK-08 · POST /api/comm/tasks</span>
        </header>
        <div class="mb">
          <div class="fd">
            <label>任务编码<em>*</em></label>
            <div class="in" :class="{ err: errors.code }"><input v-model="form.code" class="mono" placeholder="T-D" /><span class="u">主键</span></div>
            <div v-if="errors.code" class="msg">✕ {{ errors.code }}</div>
          </div>
          <div class="fd">
            <label>任务名称<em>*</em></label>
            <div class="in"><input v-model="form.name" placeholder="临时会商保障" /></div>
          </div>
          <div class="fd">
            <label>任务持续时间<em>*</em></label>
            <div class="in"><input v-model.number="form.duration_min" type="number" class="mono" /><span class="u">分钟</span></div>
            <div class="help">2h = 120 分钟，写入 duration_min</div>
          </div>
          <div class="fd">
            <label>初始链路<em>*</em></label>
            <div class="seg">
              <span v-for="o in linkOpts" :key="o" :class="{ on: form.link === o }" @click="form.link = o">{{ o }}</span>
            </div>
            <div class="help">只列出已登记且 up 的链路</div>
          </div>
          <div class="fd">
            <label>需求值<em>*</em></label>
            <div class="in"><input v-model.number="form.gbps" type="number" step="0.01" class="mono" /><span class="u">G</span></div>
          </div>
          <div class="fd">
            <label>关键阶段</label>
            <div class="in"><input v-model.number="form.critical_min" type="number" placeholder="选填，如 50" class="mono" /><span class="u">分钟</span></div>
          </div>
          <div v-if="preview" class="parse" :class="preview.tone">{{ preview.text }}</div>
        </div>
        <footer class="mf">
          <button class="btn" @click="$emit('update:modelValue', false)">取消</button>
          <button class="btn pri" :disabled="!canSubmit" @click="submit">登记任务</button>
        </footer>
      </article>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue';
import { ElMessage } from 'element-plus';
import { addTask } from '../utils/api';

const props = defineProps({ modelValue: { type: Boolean, default: false } });
const emit = defineEmits(['update:modelValue', 'submit']);

const linkOpts = ['L-1H', 'L-2H', 'L-3H'];

const form = reactive({ code: '', name: '', duration_min: 120, link: 'L-2H', gbps: 5, critical_min: null });
const errors = ref({});

watch(() => props.modelValue, (v) => {
  if (v) { Object.assign(form, { code: '', name: '', duration_min: 120, link: 'L-2H', gbps: 5, critical_min: null }); errors.value = {}; }
});

const preview = computed(() => {
  if (!form.gbps) return null;
  // 演示版预检：取 L-2H 当前 6G + gbps
  const cur = 6;
  if (cur + Number(form.gbps) > 10) {
    return { tone: 'warn', text: `▲ 预检：L-2H 当前 6.00G + ${form.gbps.toFixed(2)}G = ${(cur + Number(form.gbps)).toFixed(2)}G > 10.00G。可以登记，登记后会进入方案审批流程。` };
  }
  return { tone: 'ok', text: `✓ 预检通过：L-2H 登记后 ${(cur + Number(form.gbps)).toFixed(2)}G，压预警线下 8G。` };
});

const canSubmit = computed(() => form.code && form.name && form.gbps && form.link);

async function submit() {
  errors.value = {};
  if (!form.code) errors.value.code = '编码必填';
  if (!/^T-[A-Z0-9]+$/i.test(form.code)) errors.value.code = '编码格式应为 T-XXX';
  if (Object.keys(errors.value).length) return;
  try {
    // 修复（major 10）：内部字段映射到后端 TaskAddIn schema
    // code/name/link/gbps → task_code/task_name/link_code/demand_gbps
    await addTask({
      task_code: form.code,
      task_name: form.name,
      duration_min: form.duration_min,
      link_code: form.link,
      demand_gbps: form.gbps,
      critical_min: form.critical_min,
    });
    ElMessage.success(`已登记任务：${form.code}`);
    emit('submit', form);
    emit('update:modelValue', false);
  } catch {/* http.js 已处理 */}
}
</script>

<style scoped>
/* 修复 bug8：原样式 background: #10213a 是深色，与全局浅色主题冲突，label/input 不可见
   现统一用浅色卡片 + chip 输入框底 + sub 文字（对比度足够） */
.mask { position: fixed; inset: 0; background: rgba(30,40,60,.40); display: flex; align-items: center; justify-content: center; z-index: 110; }
.fm { width: 520px; max-width: 96vw; background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 20px 60px rgba(20,20,30,.18); }
.mh { padding: 14px 18px; border-bottom: 1px solid var(--border); font-weight: 700; color: var(--text); display: flex; gap: 10px; align-items: baseline; }
.mh .sub { font-weight: 400; color: var(--sub); font-size: 12px; }
.mb { padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; }
.mf { padding: 12px 18px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end; gap: 8px; }
.fd label { display: block; font-size: 12.5px; color: var(--text); font-weight: 500; margin-bottom: 4px; }
.fd label em { color: var(--over); font-style: normal; margin-left: 2px; }
.fd .in { display: flex; align-items: center; background: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 0 10px; height: 34px; font-size: 13px; }
.fd .in input { flex: 1; background: transparent; border: 0; color: var(--text); font: inherit; outline: none; caret-color: var(--accent); }
.fd .in input::placeholder { color: var(--muted); }
.fd .in .u { margin-left: auto; color: var(--muted); font-size: 12px; }
.fd .in.err { border-color: var(--over); }
.fd .msg { font-size: 11.5px; color: var(--over); margin-top: 4px; }
.fd .help { font-size: 11.5px; color: var(--sub); margin-top: 4px; }
.seg { display: flex; gap: 6px; }
.seg span { flex: 1; text-align: center; padding: 7px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: #fff; color: var(--text); cursor: pointer; }
.seg span.on { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.parse { font-size: 12px; padding: 8px 10px; border-radius: 6px; }
.parse.warn { background: var(--warn-bg); border: 1px solid var(--warn-bd); color: var(--warn-fg); }
.parse.ok { background: var(--ok-bg); border: 1px solid var(--ok-bd); color: var(--ok-fg); }
.btn { font: inherit; font-size: 13px; padding: 7px 14px; border-radius: 6px; border: 1px solid var(--border); background: var(--card); color: var(--text); cursor: pointer; }
.btn.pri { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn:hover { border-color: var(--accent); }
.btn[disabled] { opacity: .4; cursor: not-allowed; }
</style>