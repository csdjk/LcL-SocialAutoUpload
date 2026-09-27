<template>
  <main class="task-page">
    <header class="page-heading"><div><p class="eyebrow">发布记录</p><h1>任务中心</h1><p>查看投稿进度，优先处理需要关注的任务。</p></div><el-button :loading="loading" @click="refresh()">刷新任务</el-button></header>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <div class="task-toolbar">
      <div class="state-filters" role="group" aria-label="任务状态筛选"><button v-for="option in stateOptions" :key="option.key" type="button" :aria-pressed="stateFilter === option.key" :class="{ active: stateFilter === option.key }" @click="stateFilter = option.key">{{ option.name }}<span>{{ platformTasks.filter(task => option.key === 'all' || taskBucket(task.state) === option.key).length }}</span></button></div>
      <div class="filter-row"><label>平台<el-select v-model="filter" aria-label="筛选投稿平台"><el-option label="全部平台" value="all" /><el-option v-for="p in platforms" :key="p.key" :label="p.name" :value="p.key" /></el-select></label><span>{{ groups.length }} 项内容 · {{ visible.length }} 条任务</span></div>
    </div>
    <el-empty v-if="!groups.length" :description="stateFilter !== 'all' || filter !== 'all' ? '没有匹配当前筛选的任务' : '暂无投稿记录'"><el-button v-if="stateFilter !== 'all' || filter !== 'all'" @click="stateFilter = 'all'; filter = 'all'">清除筛选</el-button><el-button v-else @click="$router.push('/')">前往今日待发布</el-button></el-empty>
    <section v-for="group in groups" :key="group.key" class="soft-panel group-card">
      <button type="button" class="group-heading" :aria-expanded="isGroupExpanded(group)" @click="toggleGroup(group)">
        <span><strong>{{ group.editionId }}</strong><small>{{ group.tasks.length }} 条投稿记录 · {{ taskTime(group.tasks[0].created_at) }}</small></span>
        <span class="group-status"><el-tag v-if="group.attention" type="warning">有任务待处理</el-tag><el-tag v-else-if="group.active">投稿进行中</el-tag><el-tag v-else-if="group.finished" type="success">已全部发布</el-tag><el-tag v-else type="info">等待审核或核对</el-tag><span aria-hidden="true">{{ isGroupExpanded(group) ? '收起' : '展开' }}</span></span>
      </button>
      <div v-if="isGroupExpanded(group)" class="group-tasks">
    <article v-for="task in group.tasks" :key="task.id" class="task-card">
      <div class="task-head"><div><h3>{{ label(task.platform) }}</h3><p :title="task.created_at">{{ taskSource(task.source) }} · {{ taskTime(task.created_at) }}</p></div><el-tag :type="tagType(task.state)">{{ stateText(task.state) }}</el-tag></div>
      <PublicationProgress v-if="task.state !== 'published'" :state="task.state" :evidence="task.evidence" :created-at="task.created_at" :now="now" />
      <p v-if="task.error" class="task-error">{{ task.error }}</p>
      <details class="task-metadata"><summary>账号、作品与核对记录</summary><p v-if="task.evidence?.note" class="detail">{{ task.evidence.note }}</p><div class="detail-grid"><span>账号：{{ task.account_id }}</span><span>作品 ID：{{ task.remote_id || '待核对' }}</span></div></details>
      <div class="actions"><a v-if="task.url" :href="task.url" target="_blank" rel="noopener noreferrer">打开作品 ↗</a><el-button :loading="busy[task.id]" :disabled="['queued', 'uploading'].includes(task.state)" @click="sync(task)">同步平台状态</el-button><el-button :disabled="['queued', 'uploading'].includes(task.state)" @click="openReconcile(task)">确认投稿状态</el-button></div>
    </article>
      </div>
    </section>
    <el-dialog v-model="dialog" class="reconcile-dialog" title="确认投稿状态" width="min(94vw, 460px)" :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving">
      <p class="dialog-note">选择你在平台看到的状态，作品信息自动保留。确认后更新记录。</p>
      <el-alert v-if="confirmError" :title="confirmError" type="error" :closable="false" show-icon />
      <el-form label-position="top" :disabled="saving" @submit.prevent="saveReconcile">
        <el-form-item label="状态"><el-select v-model="form.state" aria-label="投稿状态" style="width:100%"><el-option label="审核中" value="processing" /><el-option label="已公开发布" value="published" /><el-option label="需本人处理" value="needs_action" /><el-option label="仍不确定" value="unknown" /><el-option label="确认没有作品" value="failed" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button :disabled="saving" @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveReconcile">确认状态</el-button></template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'
import { buildStatusConfirmation } from '@/utils/dailyReconcile'
import PublicationProgress from '@/components/PublicationProgress.vue'
import { platformName, taskStateText, taskTagType } from '@/utils/platformStatus'
import { taskBucket, taskSource, taskTime } from '@/utils/taskPresentation'
const platforms = [{ key: 'bilibili', name: 'B站' }, { key: 'douyin', name: '抖音' }, { key: 'wechat_channels', name: '微信视频号' }, { key: 'youtube', name: 'YouTube' }, { key: 'toutiao', name: '今日头条' }, { key: 'kuaishou', name: '快手' }, { key: 'xiaohongshu', name: '小红书' }]
const label = key => platformName(key)
const stateText = taskStateText
const tagType = taskTagType
const tasks = ref([])
const filter = ref('all')
const stateFilter = ref('all')
const stateOptions = [{ key: 'all', name: '全部' }, { key: 'attention', name: '待处理' }, { key: 'active', name: '处理中' }, { key: 'published', name: '已发布' }]
const platformTasks = computed(() => filter.value === 'all' ? tasks.value : tasks.value.filter(task => task.platform === filter.value))
const visible = computed(() => platformTasks.value.filter(task => stateFilter.value === 'all' || taskBucket(task.state) === stateFilter.value))
const expanded = reactive({})
const groups = computed(() => {
  const byEdition = new Map()
  for (const task of visible.value) {
    const key = `${task.edition_id}\u0000${task.revision}`
    if (!byEdition.has(key)) byEdition.set(key, { key, editionId: task.edition_id, tasks: [] })
    byEdition.get(key).tasks.push(task)
  }
  return [...byEdition.values()].map(group => ({ ...group,
    finished: group.tasks.every(task => task.state === 'published'),
    attention: group.tasks.some(task => ['unknown', 'needs_action', 'failed'].includes(task.state)),
    active: group.tasks.some(task => ['queued', 'uploading'].includes(task.state))
  })).sort((a, b) => Number(b.attention) - Number(a.attention) || Number(b.active) - Number(a.active) ||
    b.tasks[0].created_at.localeCompare(a.tasks[0].created_at))
})
const isGroupExpanded = group => expanded[group.key] ?? !group.finished
const toggleGroup = group => { expanded[group.key] = !isGroupExpanded(group) }
const loading = ref(false)
const busy = reactive({})
const error = ref('')
const dialog = ref(false)
const target = ref(null)
const saving = ref(false)
const form = reactive({ state: 'unknown' })
const confirmError = ref('')
const now = ref(Date.now())
let pollTimer
let clockTimer
let refreshing = false
async function refresh(silent = false) {
  if (refreshing) return
  refreshing = true
  if (!silent) loading.value = true
  try { tasks.value = (await http.get('/daily/tasks', { limit: 100 }, { silentError: true })).data; error.value = '' }
  catch (exc) { error.value = exc.message || '任务读取失败' }
  finally { refreshing = false; if (!silent) loading.value = false }
}
async function sync(task) {
  busy[task.id] = true
  try {
    const result = await http.post('/daily/sync-result', { task_id: task.id }, { headers: { 'X-SAU-Local': '1' }, silentError: true, timeout: 65000 })
    ElMessage.info(result.data.sync.message || '已完成一次远端回读；请查看任务状态')
    await refresh()
  } catch (exc) { error.value = exc.message || '远端回读失败；原任务保留，未重投' }
  finally { busy[task.id] = false }
}
function openReconcile(task) {
  target.value = { task_id: task.id, platform: task.platform, account_id: task.account_id, updated_at: task.updated_at }
  form.state = ['processing', 'published', 'needs_action', 'unknown', 'failed'].includes(task.state) ? task.state : 'unknown'
  confirmError.value = ''
  dialog.value = true
}
async function saveReconcile() {
  if (saving.value) return
  confirmError.value = ''
  saving.value = true
  try {
    await http.post('/daily/reconcile', buildStatusConfirmation(target.value, form.state), { headers: { 'X-SAU-Local': '1' }, silentError: true })
    dialog.value = false
    ElMessage.success('核对结果已保存，未重新投稿')
    await refresh()
  } catch (exc) { confirmError.value = exc.message || '状态保存失败，请重新打开任务' }
  finally { saving.value = false }
}
onMounted(() => {
  refresh()
  pollTimer = window.setInterval(() => refresh(true), 3000)
  clockTimer = window.setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => { window.clearInterval(pollTimer); window.clearInterval(clockTimer) })
</script>

<style scoped>
.task-page { max-width: 1440px; margin: auto; padding: 8px 4px 42px; }
.page-heading, .task-head, .actions, .filter-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.page-heading { margin-bottom: 24px; }
h1 { font-size: clamp(26px, 2.4vw, 32px); margin: 0 0 6px; }
p, .filter-row span { color: var(--ui-muted); line-height: 1.6; }
.eyebrow { color: var(--el-color-primary); font-size: 12px; font-weight: 700; letter-spacing: .1em; }
.filter-row { margin: 0 0 22px; }
.filter-row label { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.filter-row .el-select { width: 180px; min-width: 0; }
.soft-panel { padding: 20px; background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: 16px; box-shadow: var(--ui-raised); }
.group-card { margin-bottom: 18px; }
.group-heading { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 0; border: 0; background: transparent; color: var(--ui-text); text-align: left; cursor: pointer; }
.group-heading strong { display: block; font-size: 20px; overflow-wrap: anywhere; }
.group-heading small { display: block; margin-top: 5px; color: var(--ui-muted); font-size: 12px; }
.group-heading:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 4px; }
.group-status { display: flex; justify-content: flex-end; align-items: center; flex-wrap: wrap; gap: 8px; color: var(--ui-muted); font-size: 12px; }
.group-tasks { margin-top: 18px; border-top: 1px solid var(--ui-border); }
.task-card { padding: 18px 0; border-bottom: 1px solid var(--ui-border); }
.task-card:last-child { border-bottom: 0; padding-bottom: 0; }
.task-head h3 { margin: 0; font-size: 18px; overflow-wrap: anywhere; }
.task-head .el-tag { align-self: flex-start; width: max-content; max-width: 100%; }
.task-head p { margin: 4px 0 0; }
.task-error { color: var(--el-color-danger); }
.detail-grid { display: flex; gap: 12px 26px; flex-wrap: wrap; color: var(--ui-muted); overflow-wrap: anywhere; }
.actions { justify-content: flex-end; flex-wrap: wrap; margin-top: 18px; }
.dialog-note { margin: 0 0 16px; line-height: 1.6; }
@media(max-width: 650px) { .page-heading, .task-head, .group-heading { align-items: stretch; flex-direction: column; } .group-status { justify-content: flex-start; } .actions { justify-content: flex-start; } .filter-row { flex-wrap: wrap; } .filter-row .el-select { width: min(180px, 56vw); } }

.task-toolbar { margin-bottom: 24px; display: grid; gap: 18px; }
.state-filters { display: flex; flex-wrap: wrap; gap: 8px; }
.state-filters button { display: flex; align-items: center; gap: 10px; padding: 10px 16px; min-height: 44px; color: var(--ui-muted); border: 1px solid transparent; border-radius: 10px; font-size: 14px; }
.state-filters button span { min-width: 22px; padding: 1px 5px; border-radius: 5px; background: var(--ui-soft); font-size: 12px; font-variant-numeric: tabular-nums; }
.state-filters button.active { color: var(--el-color-primary); background: var(--ui-surface); border-color: var(--ui-border); box-shadow: var(--ui-raised-sm); font-weight: 600; }
.filter-row { margin: 0; font-size: 13px; }
.group-heading strong { font-size: 18px; }
.group-tasks { margin-top: 16px; }
.task-head p { font-size: 13px; }
.task-metadata { color: var(--ui-muted); font-size: 13px; margin-top: 12px; }
.task-metadata summary { cursor: pointer; }
.task-metadata p { margin: 8px 0; }
.task-error { padding: 12px 14px; border-left: 3px solid var(--el-color-danger); border-radius: 0 8px 8px 0; background: var(--ui-danger-bg); font-size: 14px; }
.actions { margin-top: 14px; gap: 10px; }
.actions .el-button { margin: 0; }
.actions a { color: var(--el-color-primary); font-size: 14px; margin-right: auto; }
@media(max-width: 650px) {
 .state-filters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
 .state-filters button { justify-content: space-between; padding: 10px 12px; }
 .soft-panel { padding: 16px; }
 .task-head { flex-direction: row; align-items: flex-start; gap: 10px; }
 .task-head > div { min-width: 0; }
 .task-head .el-tag { flex: none; }
 .actions { display: grid; grid-template-columns: 1fr 1fr; }
 .actions a { grid-column: 1 / -1; min-height: 36px; display: flex; align-items: center; }
 .actions .el-button { padding-inline: 8px; }
}

</style>
