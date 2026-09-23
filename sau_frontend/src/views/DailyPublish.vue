<template>
  <div class="daily-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">AI 日报 · 每日交付</p>
        <h1>今日待发布</h1>
        <p class="subtle">自动投稿：B站、抖音 · 手动投稿：视频号</p>
      </div>
      <el-button @click="refresh(true)" :loading="loading">刷新状态</el-button>
    </header>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="notice" />
    <el-alert v-if="updateAvailable" title="检测到新的资源版本；当前编辑内容未被覆盖。刷新页面可切换到新版本。" type="warning" show-icon :closable="false" class="notice" />
    <el-alert v-if="bundle?.errors?.length" :title="`已跳过异常版本：${bundle.errors.join('；')}`" type="warning" show-icon :closable="false" class="notice" />
    <el-empty v-if="!loading && !bundle?.package && !error" description="今日资源尚未就绪；不会自动使用昨日视频。" />

    <template v-if="bundle?.package">
      <section class="edition-card">
        <div class="edition-copy">
          <p class="eyebrow">{{ bundle.package.date }} · {{ bundle.package.revision }}</p>
          <h2>{{ bundle.package.series }}</h2>
          <p>时长 {{ duration }} · 视频 {{ size }} · 技术检查通过 · 编辑与视觉复核通过</p>
          <p class="subtle">期次 {{ bundle.package.edition_id }} · 当前版本只读，手动编辑保存在工具草稿中。</p>
        </div>
        <video controls preload="metadata" :src="assetUrl('video')" class="video-preview" />
      </section>

      <div class="platform-grid">
        <article v-for="platform in platforms" :key="platform.key" class="platform-card">
          <div class="platform-head">
            <div>
              <p class="eyebrow">{{ platform.mode }}</p>
              <h3>{{ platform.name }}</h3>
            </div>
            <el-tag :type="statusType(bundle.status[platform.key]?.state)">{{ statusText(bundle.status[platform.key]?.state) }}</el-tag>
          </div>
          <p class="account">账号：{{ bundle.status[platform.key]?.account?.display_name || bundle.status[platform.key]?.account?.account_id || '未配置' }}</p>
          <el-alert v-if="bundle.status[platform.key]?.access_status !== 'ready'" :title="`访问状态：${bundle.status[platform.key]?.access_status}`" type="warning" :closable="false" />
          <div class="cover-row">
            <img :src="assetUrl(platform.cover)" :alt="`${platform.name}封面`" />
            <span>封面 {{ platform.cover === 'portrait' ? '3:4' : '4:3' }}</span>
          </div>
          <el-form label-position="top" v-if="drafts[platform.key]">
            <el-form-item label="标题"><el-input v-model="drafts[platform.key].title" maxlength="120" show-word-limit /></el-form-item>
            <el-form-item label="简介"><el-input v-model="drafts[platform.key].description" type="textarea" :rows="5" /></el-form-item>
            <el-form-item label="话题（逗号分隔）"><el-input v-model="tagsText[platform.key]" /></el-form-item>
            <el-form-item v-if="platform.key === 'bilibili'" label="B站分区 ID"><el-input v-model="drafts[platform.key].category" /></el-form-item>
            <el-form-item label="AI 内容声明"><el-input v-model="drafts[platform.key].ai_declaration" /></el-form-item>
          </el-form>
          <div class="platform-actions">
            <el-button @click="save(platform.key)" :disabled="!accountId(platform.key)">保存草稿</el-button>
            <el-checkbox v-model="selected[platform.key]" :disabled="!canPublish(platform.key)">选中发布</el-checkbox>
          </div>
          <p v-if="bundle.status[platform.key]?.remote_id" class="remote">远端作品：{{ bundle.status[platform.key].remote_id }}</p>
          <p v-if="bundle.status[platform.key]?.task_id" class="task-line">
            任务 {{ bundle.status[platform.key].task_id }}
            <el-button link type="primary" @click="openReconcile(platform.key)">核对远端结果</el-button>
          </p>
        </article>
      </div>

      <footer class="publish-footer">
        <p>仅提交已选且尚未投稿的平台；已提交、审核中或结果待核对的任务禁止重复发布。</p>
        <el-button type="primary" size="large" :disabled="!selectedPlatforms.length" :loading="submitting" @click="submit">
          发布到所选平台（{{ selectedPlatforms.length }}）
        </el-button>
      </footer>
    </template>

    <el-dialog v-model="reconcileVisible" title="核对平台内容管理结果" width="min(92vw, 520px)">
      <p class="subtle">只记录实际看到的远端状态。审核中与公开发布分别填写。</p>
      <el-form label-position="top">
        <el-form-item label="状态">
          <el-select v-model="reconcileForm.state" style="width:100%">
            <el-option label="已提交／审核中" value="processing" />
            <el-option label="已发布" value="published" />
            <el-option label="需要人工处理" value="needs_action" />
            <el-option label="结果待核对" value="unknown" />
            <el-option label="确认失败" value="failed" />
          </el-select>
        </el-form-item>
        <el-form-item label="作品 ID"><el-input v-model="reconcileForm.remote_id" /></el-form-item>
        <el-form-item label="作品链接"><el-input v-model="reconcileForm.url" /></el-form-item>
        <el-form-item v-if="reconcileForm.state === 'failed'" label="远端核对">
          <el-checkbox v-model="reconcileForm.remote_absent">已检查内容管理，确认没有该作品</el-checkbox>
        </el-form-item>
        <el-form-item label="核对依据"><el-input v-model="reconcileForm.note" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="reconcileVisible = false">取消</el-button><el-button type="primary" @click="reconcile">保存核对结果</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'

const platforms = [
  { key: 'bilibili', name: 'B站', mode: '06:00 任务自动投稿；可手动补发', cover: 'bilibili' },
  { key: 'douyin', name: '抖音', mode: '06:00 任务自动投稿；可手动补发', cover: 'portrait' },
  { key: 'wechat_channels', name: '视频号', mode: '仅手动投稿', cover: 'portrait' }
]
const bundle = ref(null)
const drafts = reactive({})
const tagsText = reactive({})
const selected = reactive({})
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const updateAvailable = ref(false)
const reconcileVisible = ref(false)
const reconcilePlatform = ref('')
const reconcileForm = reactive({ state: 'processing', remote_id: '', url: '', note: '', remote_absent: false })
let timer

const selectedPlatforms = computed(() => platforms.map(x => x.key).filter(key => selected[key] && canPublish(key)))
const duration = computed(() => `${Math.round(bundle.value?.package?.video?.duration_seconds || 0)} 秒`)
const size = computed(() => `${((bundle.value?.package?.video?.bytes || 0) / 1024 / 1024).toFixed(2)} MiB`)
const apiRoot = import.meta.env?.VITE_API_BASE_URL || 'http://localhost:5409'
const assetUrl = key => bundle.value?.package ? `${apiRoot}/daily/asset/${bundle.value.package.date}/${bundle.value.package.revision}/${key}` : ''
const accountId = key => String(bundle.value?.status?.[key]?.account?.account_id || '')
const canPublish = key => {
  const item = bundle.value?.status?.[key]
  return item?.access_status === 'ready' && !item?.account_mismatch &&
    !!(item?.account?.alias || (key === 'wechat_channels' && item?.account?.web_account_id)) &&
    (item.state === 'ready' || (item.state === 'failed' && !item.remote_id))
}
const statusText = state => ({ ready: '待发布', uploading: '上传中', needs_action: '待人工处理',
  processing: '已提交／审核中', published: '已发布', unknown: '结果待核对', failed: '确认失败', prepared: '历史预约待核对' })[state] || '待配置'
const statusType = state => ({ published: 'success', processing: 'warning', uploading: 'info',
  unknown: 'danger', failed: 'danger', needs_action: 'warning' })[state] || 'info'

async function refresh(force = false) {
  if (loading.value) return
  loading.value = true
  try {
    const response = await http.get('/daily/today')
    const next = response.data
    if (bundle.value?.package && next.package && bundle.value.package.revision !== next.package.revision && !force) {
      updateAvailable.value = true
      return
    }
    const first = !bundle.value?.package || force
    bundle.value = next
    error.value = ''
    if (first && next.package) {
      for (const platform of platforms) {
        const key = platform.key
        drafts[key] = { ...next.package.platforms[key], ...(next.drafts?.[key] || {}) }
        tagsText[key] = (drafts[key].tags || []).join('，')
        selected[key] = false
      }
      updateAvailable.value = false
    }
  } catch (exc) {
    error.value = exc.message || '今日资源读取失败'
  } finally {
    loading.value = false
  }
}

async function save(key) {
  try {
    const payload = { ...drafts[key], tags: tagsText[key].split(/[,，]/).map(x => x.trim().replace(/^#/, '')).filter(Boolean) }
    await http.put('/daily/draft', { package_path: bundle.value.package_path, platform: key,
      account_id: accountId(key), payload })
    drafts[key] = payload
    ElMessage.success('草稿已保存')
  } catch (exc) { ElMessage.error(exc.message || '保存草稿失败'); throw exc }
}

async function submit() {
  submitting.value = true
  try {
    for (const key of selectedPlatforms.value) await save(key)
    const response = await http.post('/daily/submit', { package_path: bundle.value.package_path, platforms: selectedPlatforms.value })
    for (const key of Object.keys(selected)) selected[key] = false
    const count = response.data.jobs.length
    if (count) ElMessage.success(`已预约 ${count} 个发布任务，请查看各平台状态`)
    for (const [key, message] of Object.entries(response.data.errors)) ElMessage.warning(`${key}：${message}`)
    await refresh()
  } catch (exc) { ElMessage.error(exc.message || '发布任务创建失败') }
  finally { submitting.value = false }
}

function openReconcile(key) {
  reconcilePlatform.value = key
  Object.assign(reconcileForm, { state: 'processing', remote_id: '', url: '', note: '', remote_absent: false })
  reconcileVisible.value = true
}
async function reconcile() {
  const key = reconcilePlatform.value
  try {
    await http.post('/daily/reconcile', { task_id: bundle.value.status[key].task_id, state: reconcileForm.state,
      evidence: { platform: key, account_id: accountId(key), remote_id: reconcileForm.remote_id,
        url: reconcileForm.url, note: reconcileForm.note, remote_absent: reconcileForm.remote_absent,
        checked_at: new Date().toISOString() } })
    reconcileVisible.value = false
    ElMessage.success('核对结果已保存')
    await refresh()
  } catch (exc) { ElMessage.error(exc.message || '保存核对结果失败') }
}
onMounted(() => { refresh(); timer = window.setInterval(() => refresh(), 8000) })
onUnmounted(() => window.clearInterval(timer))
</script>

<style scoped>
.daily-page { max-width: 1480px; margin: 0 auto; padding: 8px 4px 40px; color: #17243a; }
.page-header, .edition-card, .platform-head, .platform-actions, .publish-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.page-header { margin-bottom: 22px; }
.page-header h1 { margin: 3px 0 5px; font-size: clamp(27px, 3vw, 36px); }
.eyebrow { margin: 0; color: #4b65a4; font-weight: 700; font-size: 12px; letter-spacing: .08em; }
.subtle, .account, .remote, .task-line { color: #67738b; font-size: 13px; }
.notice { margin-bottom: 16px; }
.edition-card, .platform-card, .publish-footer { background: #fff; border: 1px solid #e1e7f0; border-radius: 16px; box-shadow: 0 8px 26px #17365e0a; }
.edition-card { padding: 22px; margin-bottom: 20px; }
.edition-copy h2 { margin: 6px 0 10px; font-size: 28px; }
.video-preview { width: min(46%, 460px); aspect-ratio: 16/9; border-radius: 10px; background: #111827; }
.platform-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.platform-card { padding: 20px; min-width: 0; }
.platform-head { align-items: flex-start; }
.platform-head h3 { margin: 4px 0 0; font-size: 23px; }
.cover-row { display: flex; align-items: center; gap: 12px; margin: 18px 0; color: #67738b; font-size: 13px; }
.cover-row img { width: 92px; height: 92px; object-fit: cover; border-radius: 8px; }
.platform-actions { flex-wrap: wrap; }
.task-line { overflow-wrap: anywhere; }
.publish-footer { margin-top: 18px; padding: 18px 22px; }
.publish-footer p { margin: 0; }
@media (max-width: 1100px) { .platform-grid { grid-template-columns: 1fr; } }
@media (max-width: 600px) {
  .page-header, .edition-card, .publish-footer { align-items: stretch; flex-direction: column; }
  .edition-card, .platform-card, .publish-footer { padding: 16px; }
  .video-preview { width: 100%; }
  .publish-footer .el-button { width: 100%; margin: 0; }
}
</style>
