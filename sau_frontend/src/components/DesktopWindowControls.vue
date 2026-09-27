<template>
  <template v-if="desktop">
    <div class="window-controls" role="group" aria-label="窗口操作">
      <button type="button" aria-label="最小化窗口" title="最小化" @click="invoke('minimize_window')">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5h10" /></svg>
      </button>
      <button type="button" :aria-label="maximized ? '还原窗口' : '最大化窗口'" :title="maximized ? '还原' : '最大化'" @click="invoke('toggle_maximize')">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path v-if="maximized" d="M5 5V3h8v8h-2M3 5h8v8H3z" /><path v-else d="M3 3h10v10H3z" /></svg>
      </button>
      <button class="window-close" type="button" aria-label="关闭窗口并保留后台任务" title="关闭到托盘，后台任务继续运行" @click="invoke('close_window')">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="m3 3 10 10M13 3 3 13" /></svg>
      </button>
    </div>
    <template v-if="!maximized">
      <div v-for="edge in edges" :key="edge" :class="['window-resize', `resize-${edge}`]" :data-window-resize="edge" aria-hidden="true" />
    </template>
  </template>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage } from 'element-plus'
import { installWindowGestures } from '../utils/windowGestures.js'
const emit = defineEmits(['ready'])
const desktop = ref(false)
const maximized = ref(false)
const edges = ['left', 'right', 'top', 'bottom', 'top-left', 'top-right', 'bottom-left', 'bottom-right']
let removeGestures = null
async function invoke(method, ...args) {
  try { return await window.pywebview.api[method](...args) }
  catch { ElMessage.error('窗口操作未完成，请重试') }
}
async function ready() {
  if (!window.pywebview?.api?.window_state) return
  desktop.value = true
  if (!removeGestures) removeGestures = installWindowGestures(window.pywebview.api, () => ElMessage.error('窗口拖动未完成，请重试'))
  emit('ready')
  const state = await invoke('window_state')
  if (state) maximized.value = state.maximized
}
const syncState = event => { maximized.value = event.detail.maximized }
const toggle = () => { if (desktop.value) invoke('toggle_maximize') }
onMounted(() => {
  window.addEventListener('pywebviewready', ready)
  window.addEventListener('desktop-window-state', syncState)
  window.addEventListener('desktop-toggle-maximize', toggle)
  ready()
})
onBeforeUnmount(() => {
  removeGestures?.()
  window.removeEventListener('pywebviewready', ready)
  window.removeEventListener('desktop-window-state', syncState)
  window.removeEventListener('desktop-toggle-maximize', toggle)
})
</script>

<style scoped>
.window-controls { position: relative; z-index: 4000; display: flex; align-self: stretch; margin-left: 8px; border-left: 1px solid var(--ui-border); background: var(--ui-surface); }
.window-controls button { width: 46px; height: 100%; min-height: 44px; display: grid; place-items: center; border: 0; border-radius: 0; background: transparent; color: var(--ui-muted); cursor: pointer; }
.window-controls button:hover { background: var(--el-fill-color-light); color: var(--ui-text); }
.window-controls .window-close:hover { background: #c42b1c; color: white; }
.window-controls button:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: -5px; }
.window-controls svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.3; }
.window-resize { position: fixed; z-index: 9999; touch-action: none; user-select: none; }
.resize-left, .resize-right { width: 5px; top: 10px; bottom: 10px; cursor: ew-resize; }
.resize-left { left: 0; } .resize-right { right: 0; }
.resize-top, .resize-bottom { height: 5px; left: 10px; right: 10px; cursor: ns-resize; }
.resize-top { top: 0; } .resize-bottom { bottom: 0; }
.resize-top-left, .resize-top-right, .resize-bottom-left, .resize-bottom-right { width: 10px; height: 10px; }
.resize-top-left, .resize-bottom-right { cursor: nwse-resize; }
.resize-top-right, .resize-bottom-left { cursor: nesw-resize; }
.resize-top-left { top: 0; left: 0; } .resize-top-right { top: 0; right: 0; }
.resize-bottom-left { bottom: 0; left: 0; } .resize-bottom-right { bottom: 0; right: 0; }
</style>
