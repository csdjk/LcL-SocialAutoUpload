<template>
  <section class="publication-progress" :class="`progress-${model.mode}`" :aria-label="`投稿进度：${model.title}`">
    <div class="progress-copy">
      <strong>{{ model.title }}</strong>
      <span v-if="timed && createdAt">{{ model.percent !== null && model.percent !== undefined ? `上传 ${model.percent}% · ` : '' }}任务已历时 {{ elapsedText(createdAt, now) }}</span>
    </div>
    <div class="phase-track" role="progressbar" :aria-valuetext="`${model.title}；${model.detail}`">
      <span v-for="(phase, index) in PHASES" :key="phase.key" class="phase-segment"
        :class="{ done: index < model.phase || model.mode === 'complete', current: index === model.phase && model.mode !== 'complete' }">
        <span class="phase-fill" :class="{ measured: index === model.phase && model.percent !== null && model.percent !== undefined }"
          :style="index === model.phase && model.percent !== null && model.percent !== undefined ? { width: `${model.percent}%` } : undefined" />
      </span>
    </div>
    <div class="phase-labels" aria-hidden="true"><span v-for="phase in PHASES" :key="phase.key">{{ phase.label }}</span></div>
    <p class="progress-detail" role="status">{{ model.detail }}</p>
  </section>
</template>

<script setup>
import { computed } from 'vue'
import { PHASES, elapsedText, progressModel } from '@/utils/publicationProgress'
const props = defineProps({ state: { type: String, required: true }, evidence: { type: Object, default: null },
  createdAt: { type: String, default: '' }, now: { type: Number, default: 0 } })
const model = computed(() => progressModel(props.state, props.evidence))
const timed = computed(() => ['active', 'waiting', 'queued'].includes(model.value.mode))
</script>

<style scoped>
.publication-progress { margin: 18px 0; padding: 14px 16px; border-radius: 14px; background: var(--ui-surface); box-shadow: var(--ui-inset); }
.progress-copy { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; color: var(--ui-text); }
.progress-copy strong { font-size: 15px; }
.progress-copy span, .progress-detail { color: var(--ui-muted); font-size: 13px; }
.phase-track { display: flex; gap: 5px; width: 100%; height: 12px; margin-top: 12px; overflow: hidden; border-radius: 9px; }
.phase-segment { flex: 1; min-width: 0; overflow: hidden; border-radius: 6px; background: var(--ui-border); }
.phase-fill { display: block; width: 0; height: 100%; background: var(--el-color-primary); }
.phase-segment.done .phase-fill, .progress-complete .phase-fill { width: 100%; }
.progress-active .phase-segment.current .phase-fill { width: 45%; animation: phase-motion 1.5s ease-in-out infinite alternate; }
.progress-active .phase-segment.current .phase-fill.measured { animation: none; }
.progress-waiting .phase-segment.current .phase-fill { width: 70%; animation: phase-motion 2.5s ease-in-out infinite alternate; }
.progress-attention .phase-segment.done .phase-fill { background: #a66b13; }
.progress-attention .phase-segment.current .phase-fill { width: 100%; background: #a66b13; }
.progress-failed .phase-segment.done .phase-fill { background: #b84b53; }
.progress-failed .phase-segment.current .phase-fill { width: 100%; background: #b84b53; }
.progress-complete .phase-fill { background: #278356; }
.phase-labels { display: flex; gap: 5px; margin-top: 7px; }
.phase-labels span { flex: 1; min-width: 0; text-align: center; font-size: 12px; color: var(--ui-muted); }
.progress-detail { margin: 9px 0 0; line-height: 1.5; overflow-wrap: anywhere; }
@keyframes phase-motion { from { transform: translateX(-85%); } to { transform: translateX(185%); } }
@media (max-width: 420px) { .publication-progress { padding: 14px 12px; } .phase-labels span { font-size: 11px; } .progress-copy { flex-wrap: wrap; } }
@media (prefers-reduced-motion: reduce) { .progress-active .phase-segment.current .phase-fill, .progress-waiting .phase-segment.current .phase-fill { animation: none; width: 65%; } }
</style>
