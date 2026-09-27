<template>
  <main class="automation-page">
    <header class="page-heading">
      <div><p class="eyebrow">本机发布</p><h1>自动排程</h1><p>发现通过检查的今日资源包后，在北京时间窗口内为已启用的平台各预约一次。</p></div>
      <el-button @click="refresh()" :loading="loading">刷新状态</el-button>
    </header>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <div class="top-grid">
      <section class="soft-panel">
        <div class="section-heading"><h2>运行规则</h2><el-switch v-model="form.enabled" aria-label="启用自动排程" /></div>
        <div class="time-grid">
          <label>开始投稿（北京时间）<el-input v-model="form.after" placeholder="06:00" aria-label="开始投稿时间" /></label>
          <label>最晚投稿（北京时间）<el-input v-model="form.deadline" placeholder="23:00" aria-label="最晚投稿时间" /></label>
        </div>
        <p class="note">到点后等待完整资源包；超过截止时间不补发昨日内容。关闭窗口后，后台仍会按设置运行。</p>
        <div class="panel-actions"><el-button type="primary" :loading="saving" @click="save">保存排程</el-button></div>
      </section>
      <section class="soft-panel">
        <div class="section-heading"><h2>后台执行器</h2><el-tag :type="worker?.running ? 'success' : 'danger'">{{ worker?.running ? '运行中' : '未运行' }}</el-tag></div>
        <p>暂停后不会领取新任务，也不会自动发现新资源；已进入平台提交阶段的任务会继续完成并保留回执。</p>
        <el-button :type="worker?.paused ? 'primary' : 'warning'" :loading="workerBusy" @click="setPaused(!worker?.paused)">{{ worker?.paused ? '恢复新任务' : '暂停新任务' }}</el-button>
      </section>
    </div>

    <section class="platform-section">
      <h2>目标平台</h2>
      <div class="platform-grid">
        <article v-for="platform in platforms" :key="platform.key" class="soft-panel platform-panel">
          <div class="section-heading"><h3>{{ platform.name }}</h3><el-switch v-model="form.platforms[platform.key].enabled" :disabled="!form.platforms[platform.key].verified" :aria-label="`自动投稿到${platform.name}`" /></div>
          <p>{{ platform.route }}</p>
          <el-checkbox class="verification-choice" v-model="form.platforms[platform.key].verified" @change="onVerified(platform.key)">已完成真实账号投稿验收</el-checkbox>
          <p class="verification-note">确认文案、封面、提交与结果核对后开启。</p>
          <p v-if="events[platform.key]" class="event" role="status">今日：{{ events[platform.key].message }}</p>
        </article>
      </div>
    </section>

    <p class="task-link">投稿执行情况与结果在<router-link to="/publish-center">任务中心</router-link>查看。</p>
  </main>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'

const platforms = [
  { key: 'bilibili', name: 'B站', route: '正式可用时优先走已配置的上传通道。' },
  { key: 'douyin', name: '抖音', route: '具备适用授权时优先官方 API；否则使用已验收的本机通道。' },
  { key: 'wechat_channels', name: '微信视频号', route: '使用本机官方后台；需要扫码或本人验证时等待处理。' },
  { key: 'youtube', name: 'YouTube', route: 'Google 授权后通过官方 API 投稿；未审核项目上传受私享限制。' },
  { key: 'toutiao', name: '今日头条', route: '头条号浏览器投稿；请先完成真实账号验收。' }
]
const form = reactive({ enabled: false, after: '06:00', deadline: '23:00', platforms: Object.fromEntries(platforms.map(p => [p.key, { enabled: false, verified: false }])) })
const events = ref({})
const worker = ref(null)
const loading = ref(false)
const saving = ref(false)
const workerBusy = ref(false)
const error = ref('')
let timer
const onVerified = key => { if (!form.platforms[key].verified) form.platforms[key].enabled = false }

async function refresh(preserveForm = false) {
  if (loading.value) return
  loading.value = true
  try {
    const [schedule, health] = await Promise.all([
      http.get('/daily/automation', undefined, { silentError: true }),
      http.get('/daily/worker', undefined, { silentError: true })
    ])
    if (!preserveForm) Object.assign(form, schedule.data.settings)
    events.value = schedule.data.events
    worker.value = health.data
    error.value = ''
  } catch (exc) { error.value = exc.message || '无法读取后台状态' }
  finally { loading.value = false }
}

async function save() {
  if (saving.value) return
  saving.value = true
  try {
    await http.put('/daily/automation', JSON.parse(JSON.stringify(form)), { headers: { 'X-SAU-Local': '1' }, silentError: true })
    ElMessage.success('排程已保存；未立即创建投稿任务')
    await refresh()
  } catch (exc) { error.value = exc.message || '排程保存失败' }
  finally { saving.value = false }
}

async function setPaused(paused) {
  workerBusy.value = true
  try {
    const result = await http.put('/daily/worker', { paused }, { headers: { 'X-SAU-Local': '1' }, silentError: true })
    worker.value = result.data
  } catch (exc) { error.value = exc.message || '后台操作失败' }
  finally { workerBusy.value = false }
}
onMounted(() => { refresh(); timer = window.setInterval(() => refresh(true), 15000) })
onUnmounted(() => window.clearInterval(timer))
</script>

<style scoped>
.automation-page { max-width: 1440px; margin: auto; padding: 8px 4px 42px; color: var(--ui-text); }
.page-heading, .section-heading, .panel-actions { display: flex; justify-content: space-between; align-items: center; gap: 18px; }
.page-heading { margin-bottom: 22px; }
.page-heading h1 { font-size: clamp(26px, 2.4vw, 32px); margin: 2px 0 6px; }
.page-heading p, .soft-panel p, .section-heading span { color: var(--ui-muted); line-height: 1.6; }
.eyebrow { color: var(--el-color-primary) !important; text-transform: uppercase; font-size: 12px; letter-spacing: .12em; font-weight: 700; }
.soft-panel { min-width: 0; padding: 22px; background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: 16px; box-shadow: var(--ui-raised); }
.soft-panel h2, .soft-panel h3 { margin: 0; font-size: 18px; }
.platform-section h2 { margin: 0 0 16px; font-size: 20px; }
.section-heading { margin-bottom: 16px; }
.top-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 18px; }
.platform-section { margin: 32px 0; }
.platform-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.time-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; margin: 12px 0; }
.time-grid > label { display: grid; gap: 8px; font-weight: 500; }
.note { font-size: 13px; }
.panel-actions { justify-content: flex-end; }
.event { overflow-wrap: anywhere; padding-top: 10px; border-top: 1px solid var(--ui-border); }
.task-link { margin-top: 22px; color: var(--ui-muted); }
.task-link a { margin: 0 4px; color: var(--el-color-primary); }
@media (max-width: 1080px) { .platform-grid { grid-template-columns: 1fr; } }
@media (max-width: 800px) { .top-grid { grid-template-columns: 1fr; } }
@media (max-width: 600px) { .page-heading { align-items: stretch; flex-direction: column; } .time-grid { grid-template-columns: 1fr; } }

.verification-choice { min-height: 44px; height: auto; margin: 12px 0 0; align-items: flex-start; width: 100%; }
.verification-choice :deep(.el-checkbox__input) { margin-top: 4px; }
.verification-choice :deep(.el-checkbox__label) { white-space: normal; font-weight: 400; font-size: 13px; padding-left: 9px; }
.verification-note { margin: 0 0 0 23px; font-size: 12px; }
.platform-panel > p { font-size: 13px; min-height: 42px; }
.platform-panel .verification-note { min-height: 0; }
.top-grid .soft-panel > .el-button { margin-top: 16px; }
@media(min-width: 801px) and (max-width: 1200px) { .platform-grid { grid-template-columns: repeat(2, minmax(0,1fr)); } }

</style>
