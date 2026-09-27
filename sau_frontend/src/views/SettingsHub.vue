<template>
  <div class="hub-page">
    <nav class="hub-tabs" aria-label="设置分类">
      <button type="button" :aria-current="tab === 'automation' ? 'page' : undefined" :class="{ active: tab === 'automation' }" @click="select('automation')">自动排程</button>
      <button type="button" :aria-current="tab === 'about' ? 'page' : undefined" :class="{ active: tab === 'about' }" @click="select('about')">关于工具</button>
    </nav>
    <KeepAlive><component :is="tab === 'about' ? About : Automation" /></KeepAlive>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Automation from './Automation.vue'
import About from './About.vue'
const route = useRoute()
const router = useRouter()
const tab = computed(() => route.query.tab === 'about' ? 'about' : 'automation')
const select = value => router.replace({ path: '/settings', query: value === 'about' ? { tab: 'about' } : {} })
</script>

<style scoped>
.hub-page { max-width: 1440px; margin: auto; }
.hub-tabs { display: flex; gap: 8px; margin: 0 4px 20px; padding: 6px; width: max-content; max-width: 100%; border-radius: 15px; background: var(--ui-surface); box-shadow: var(--ui-inset); }
.hub-tabs button { min-height: 44px; padding: 8px 18px; border: 0; border-radius: 11px; background: transparent; color: var(--ui-muted); cursor: pointer; }
.hub-tabs button.active { color: var(--ui-text); font-weight: 700; background: var(--ui-surface); box-shadow: var(--ui-raised-sm); }
.hub-tabs button:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
</style>
