<template>
  <main class="overview-page">
    <header class="page-heading"><div><p class="eyebrow">本机发布状态</p><h1>概览</h1><p>今日资源、后台和三个平台任务各自显示真实状态。</p></div><el-button @click="refresh" :loading="loading">刷新概览</el-button></header>
    <el-alert v-if="error" :title="error" type="warning" show-icon :closable="false" />
    <div class="stat-grid">
      <section class="soft-panel"><p>后台执行器</p><strong>{{ worker?.running ? (worker.paused ? '已暂停' : '运行中') : '未运行' }}</strong><router-link to="/automation">查看排程</router-link></section>
      <section class="soft-panel"><p>自动排程</p><strong>{{ automation?.enabled ? '已启用' : '未启用' }}</strong><span>{{ enabledPlatforms }} 个目标平台</span></section>
      <section class="soft-panel"><p>今日正式资源包</p><strong>{{ today?.package ? `${today.package.date} · ${today.package.revision}` : '待生成' }}</strong><router-link to="/">查看今日待发布</router-link></section>
      <section class="soft-panel"><p>待处理任务</p><strong>{{ needsAction }}</strong><router-link to="/publish-center">查看任务中心</router-link></section>
    </div>
    <section class="soft-panel recent"><div class="section-heading"><h2>最近任务</h2><router-link to="/publish-center">查看全部</router-link></div><el-empty v-if="!tasks.length" description="暂无投稿任务" /><div v-for="task in tasks.slice(0, 6)" :key="task.id" class="task-row"><strong>{{ names[task.platform] || task.platform }}</strong><span>{{ task.edition_id }}</span><el-tag :type="task.state === 'published' ? 'success' : task.state === 'unknown' || task.state === 'failed' ? 'danger' : 'info'">{{ task.state }}</el-tag></div></section>
    <section class="actions"><router-link to="/">今日待发布</router-link><router-link to="/video-library">导入普通视频</router-link><router-link to="/account-management">账号管理</router-link></section>
  </main>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { http } from '@/utils/request'
const names = { bilibili: 'B站', douyin: '抖音', wechat_channels: '视频号' }
const tasks = ref([])
const worker = ref(null)
const automation = ref(null)
const today = ref(null)
const error = ref('')
const loading = ref(false)
const needsAction = computed(() => tasks.value.filter(task => ['unknown', 'needs_action', 'failed'].includes(task.state)).length)
const enabledPlatforms = computed(() => Object.values(automation.value?.platforms || {}).filter(item => item.enabled).length)
async function refresh() {
  loading.value = true
  const results = await Promise.allSettled([
    http.get('/daily/tasks', { limit: 100 }, { silentError: true }),
    http.get('/daily/worker', undefined, { silentError: true }),
    http.get('/daily/automation', undefined, { silentError: true }),
    http.get('/daily/today', undefined, { silentError: true })
  ])
  if (results[0].status === 'fulfilled') tasks.value = results[0].value.data
  if (results[1].status === 'fulfilled') worker.value = results[1].value.data
  if (results[2].status === 'fulfilled') automation.value = results[2].value.data.settings
  if (results[3].status === 'fulfilled') today.value = results[3].value.data
  error.value = results.some(result => result.status === 'rejected') ? '部分状态暂时无法读取，请进入对应页面查看原因' : ''
  loading.value = false
}
onMounted(refresh)
</script>

<style scoped>
.overview-page { max-width: 1440px; margin: auto; padding: 8px 4px 42px; }
.page-heading, .section-heading, .actions, .task-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.page-heading { margin-bottom: 24px; }
h1 { font-size: clamp(28px, 3vw, 36px); margin: 0 0 6px; }
p, span { color: var(--ui-muted); line-height: 1.6; }
.eyebrow { color: var(--el-color-primary); font-size: 12px; font-weight: 700; letter-spacing: .1em; }
.stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; }
.soft-panel { min-width: 0; padding: 22px; background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: 16px; box-shadow: var(--ui-raised); }
.stat-grid .soft-panel { display: grid; align-content: space-between; min-height: 164px; gap: 8px; }
.stat-grid p { margin: 0; }
.stat-grid strong { font-size: 24px; overflow-wrap: anywhere; }
a { color: var(--el-color-primary); font-weight: 600; }
.recent { margin-top: 32px; }
.task-row { padding: 12px 0; border-bottom: 1px solid var(--ui-border); }
.task-row span { overflow-wrap: anywhere; }
.actions { justify-content: flex-start; flex-wrap: wrap; margin-top: 28px; }
.actions a { display: inline-flex; align-items: center; min-height: 44px; padding: 10px 18px; border-radius: 14px; box-shadow: var(--ui-raised-sm); }
@media(max-width: 1100px) { .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media(max-width: 600px) { .page-heading { align-items: stretch; flex-direction: column; } .stat-grid { grid-template-columns: 1fr; } .task-row { flex-wrap: wrap; } }
</style>
