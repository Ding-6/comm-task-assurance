<template>
  <Teleport to="body">
    <div v-if="toasts.length" class="bars" role="status" aria-live="polite">
      <div
        v-for="t in toasts"
        :key="t.id ?? `${t.lv}-${t.txt}`"
        class="bar"
        :class="t.lv"
      >
        <b class="ic">{{ t.ic }}</b>
        <span class="txt">{{ t.txt }}</span>
        <span v-if="t.src" class="src mono">{{ t.src }}</span>
        <button
          type="button"
          class="close"
          aria-label="关闭"
          @click="dismiss(t)"
        >×</button>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
const props = defineProps({ toasts: { type: Array, default: () => [] } });
const emit = defineEmits(['dismiss']);

// 修复 bug2/3/9/10：X 关闭 + 自动消失（6 秒，错误类延 10 秒）
const timers = new Map();
function scheduleAutoDismiss(t) {
  const id = t.id ?? `${t.lv}-${t.txt}`;
  if (timers.has(id)) return;
  const ms = t.lv === 'over' || t.lv === 'warn' ? 10000 : 6000;
  const handle = setTimeout(() => {
    dismiss(t);
  }, ms);
  timers.set(id, handle);
}
function clearAutoDismiss(t) {
  const id = t.id ?? `${t.lv}-${t.txt}`;
  if (timers.has(id)) {
    clearTimeout(timers.get(id));
    timers.delete(id);
  }
}
function dismiss(t) {
  clearAutoDismiss(t);
  emit('dismiss', t);
}

// 跟踪当前 toasts 队列，对每条新 toast 启动定时器
import { watch, onBeforeUnmount } from 'vue';
watch(
  () => props.toasts,
  (curr, prev) => {
    const seen = new Set();
    for (const t of curr || []) {
      const id = t.id ?? `${t.lv}-${t.txt}`;
      if (seen.has(id)) continue;
      seen.add(id);
      scheduleAutoDismiss(t);
    }
    // 清理已不在队列的 toast 定时器
    const live = new Set((curr || []).map((t) => t.id ?? `${t.lv}-${t.txt}`));
    for (const [id, handle] of timers.entries()) {
      if (!live.has(id)) {
        clearTimeout(handle);
        timers.delete(id);
      }
    }
  },
  { immediate: true, deep: true }
);

onBeforeUnmount(() => {
  for (const handle of timers.values()) clearTimeout(handle);
  timers.clear();
});
</script>

<style scoped>
.bars {
  position: fixed; top: 72px; left: 50%; transform: translateX(-50%);
  display: flex; flex-direction: column; gap: 6px;
  z-index: 60; max-width: 92vw; pointer-events: none;
}
.bar {
  display: flex; align-items: center; gap: 10px;
  padding: 9px 12px 9px 18px; font-size: 13px;
  border: 1px solid; border-radius: 6px;
  box-shadow: 0 4px 18px rgba(0,0,0,.18);
  pointer-events: auto;
  animation: slide-in .25s ease both;
}
.bar .ic { font-size: 14px; flex: 0 0 auto; }
.bar .txt { flex: 1 1 auto; }
.bar .src { margin-left: auto; font-size: 12px; opacity: .75; }
.bar .close {
  flex: 0 0 auto;
  width: 22px; height: 22px;
  border: 0; background: transparent; cursor: pointer;
  font-size: 18px; line-height: 1; color: inherit;
  border-radius: 4px; padding: 0;
}
.bar .close:hover { background: rgba(0,0,0,.08); }
.bar.ok    { background: var(--ok-bg);     color: var(--ok-fg);     border-color: var(--ok-bd); }
.bar.warn  { background: var(--warn-bg);   color: var(--warn-fg);   border-color: var(--warn-bd); }
.bar.over  { background: var(--over-bg);   color: var(--over-fg);   border-color: var(--over-bd); }
.bar.info  { background: var(--accent-soft); color: var(--accent);   border-color: var(--accent); }
.bar.backup{ background: var(--backup-bg); color: var(--backup-fg); border-color: var(--backup-bd); }

@keyframes slide-in { from { opacity: 0; transform: translateY(-6px); } to { opacity: 1; transform: translateY(0); } }
</style>
