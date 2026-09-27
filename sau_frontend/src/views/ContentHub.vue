<template>
  <div class="hub-page">
    <nav class="hub-tabs" aria-label="内容库分类">
      <button type="button" :aria-current="tab === 'videos' ? 'page' : undefined" :class="{ active: tab === 'videos' }" @click="select('videos')">普通视频</button>
      <button type="button" :aria-current="tab === 'materials' ? 'page' : undefined" :class="{ active: tab === 'materials' }" @click="select('materials')">素材文件</button>
    </nav>
    <KeepAlive><component :is="tab === 'materials' ? MaterialManagement : VideoLibrary" /></KeepAlive>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import VideoLibrary from './VideoLibrary.vue'
import MaterialManagement from './MaterialManagement.vue'
const route = useRoute()
const router = useRouter()
const tab = computed(() => route.query.tab === 'materials' ? 'materials' : 'videos')
const select = value => router.replace({ path: '/content', query: value === 'materials' ? { tab: 'materials' } : {} })
</script>

<style scoped>
.hub-page { max-width: 1480px; margin: auto; }
.hub-tabs { display: flex; gap: 8px; margin: 0 4px 20px; padding: 6px; width: max-content; max-width: 100%; border-radius: 15px; background: var(--ui-surface); box-shadow: var(--ui-inset); }
.hub-tabs button { min-height: 44px; padding: 8px 18px; border: 0; border-radius: 11px; background: transparent; color: var(--ui-muted); cursor: pointer; }
.hub-tabs button.active { color: var(--ui-text); font-weight: 700; background: var(--ui-surface); box-shadow: var(--ui-raised-sm); }
.hub-tabs button:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
</style>
