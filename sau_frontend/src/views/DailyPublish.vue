<template>
  <div class="daily-page">
    <header class="page-header">
      <div>
        <p class="eyebrow">AI 日报 · 每日交付</p>
        <h1>今日待发布</h1>
        <p class="subtle">自动读取今日视频与文案，选择平台后即可投稿。</p>
      </div>
      <div class="header-actions">
        <el-button @click="refresh(true)" :loading="loading">刷新状态</el-button>
        <el-button type="primary" :disabled="submitting || !selectedReady.length" :loading="submitting" @click="publishAll" data-testid="publish-all">
          发布到已选平台（{{ selectedReady.length }}）
        </el-button>
      </div>
    </header>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="notice" />
    <el-alert v-if="updateAvailable" title="检测到新的资源版本；当前编辑内容未被覆盖。刷新页面可切换到新版本。" type="warning" show-icon :closable="false" class="notice" />
    <el-alert v-if="bundle?.errors?.length" :title="`已跳过异常版本：${bundle.errors.join('；')}`" type="warning" show-icon :closable="false" class="notice" />
    <el-empty v-if="!loading && !bundle?.package && !error" description="今日资源尚未就绪"><p class="subtle">资源就绪后会自动出现在这里，也可以先导入普通视频。</p><el-button @click="$router.push('/content')">前往内容库</el-button></el-empty>

    <template v-if="bundle?.package">
      <section class="today-summary" aria-label="今日发布概况">
        <strong>今日内容已就绪</strong>
        <span>{{ availablePlatforms.length }} 个平台可投稿</span>
        <router-link v-if="attentionCount" to="/publish-center">{{ attentionCount }} 个平台任务需处理</router-link>
        <span v-else>选择平台并核对账号</span>
      </section>
      <section class="edition-card">
        <div class="edition-copy">
          <p class="eyebrow">{{ bundle.package.date }} · {{ bundle.package.revision }}</p>
          <h2>{{ bundle.package.series }}</h2>
          <p class="edition-title">{{ bundle.package.platforms.bilibili.title }}</p>
          <p class="edition-meta"><span>{{ duration }}</span><span>{{ size }}</span><span>资源检查通过</span></p>
          <details class="edition-details"><summary>资源版本与检查详情</summary><p class="subtle">技术检查、编辑与视觉复核通过。期次 {{ bundle.package.edition_id }} · 当前版本只读，手动编辑保存在工具草稿中。</p></details>
        </div>
        <video controls preload="metadata" :poster="assetUrl('landscape')" :src="assetUrl('video')" class="video-preview" />
      </section>

      <div class="platform-grid" aria-label="选择投稿平台">
        <article v-for="platform in platforms" :key="platform.key" class="platform-card" :data-platform="platform.key">
          <div class="platform-head">
            <div>
              <p class="eyebrow">{{ bundle.status[platform.key]?.transport?.label || platform.mode }}</p>
              <h3>{{ platform.name }}</h3>
            </div>
            <div class="platform-state"><el-tag :type="statusType(bundle.status[platform.key])">{{ statusText(bundle.status[platform.key]) }}</el-tag><el-checkbox :model-value="selectedPlatforms.includes(platform.key)" :disabled="submitting || !canPublish(platform.key)" :aria-label="`选择${platform.name}投稿`" @change="toggleSelected(platform.key, $event)">投稿</el-checkbox></div>
          </div>
          <div class="account-binding-row">
            <p class="account">{{ accountLabel(platform.key) }}</p>
            <el-button link type="primary"
              :disabled="submitting || ['queued', 'uploading'].includes(bundle.status[platform.key]?.state)" @click="openAccountBinding(platform.key)">
              {{ !accountId(platform.key) || bundle.status[platform.key]?.access_status === 'binding_missing' ? '选择账号' : '更换账号' }}
            </el-button>
          </div>
          <p v-if="bundle.status[platform.key]?.account_mismatch" class="account">本期已有其他账号的任务，请先核对远端结果。</p>
          <el-alert v-if="bundle.status[platform.key]?.state !== 'published' && dailyTaskNotice(bundle.status[platform.key])"
            :title="dailyTaskNotice(bundle.status[platform.key])"
            :type="dailyTaskNoticeType(bundle.status[platform.key])"
            :closable="false" show-icon data-testid="task-failure-reason" />
          <el-alert v-else-if="bundle.status[platform.key]?.state !== 'published' && bundle.status[platform.key]?.access_status !== 'ready'" :title="accessText(platform.key)" type="warning" :closable="false" />
          <el-button v-if="needsDailyRelogin(bundle.status[platform.key])"
            link type="primary" @click="$router.push('/account-management')" class="login-recovery">去账号管理重新登录</el-button>
          <div class="cover-row">
            <template v-if="platform.key === 'wechat_channels' && drafts[platform.key]?.cover_mode !== 'custom'">
              <div class="frame-cover-preview">视频画面</div>
              <span>使用平台生成的视频画面封面</span>
            </template>
            <div v-else class="cover-thumbnails" :data-testid="platform.key === 'wechat_channels' ? 'channels-dual-cover' : undefined">
              <figure v-for="cover in coversFor(platform)" :key="cover.key" :data-testid="`cover-${cover.orientation}`">
                <button class="cover-image-box" type="button" :style="{ aspectRatio: cover.ratio }"
                  :disabled="!cover.available" :aria-label="`放大预览${platform.name}${cover.label}`" @click="openCover(platform, cover)">
                  <img v-if="cover.available" :src="assetUrl(cover.key)" :alt="`${platform.name}${cover.label}`" />
                  <span v-else class="missing-cover">缺少封面</span>
                  <span v-if="cover.available" class="cover-zoom" aria-hidden="true">放大</span>
                </button>
                <figcaption>{{ cover.label }}</figcaption>
              </figure>
            </div>
            <div class="cover-copy"><p class="material-title">{{ drafts[platform.key]?.title }}</p><span>封面可点击放大</span></div>
          </div>
          <details class="advanced-publish"><summary>编辑文案与封面设置</summary>
          <p class="subtle transport-reason">{{ bundle.status[platform.key]?.transport?.reason }}</p>
          <el-form label-position="top" v-if="drafts[platform.key]">
            <el-form-item label="标题"><el-input v-model="drafts[platform.key].title" :maxlength="platform.key === 'xiaohongshu' ? 20 : platform.key === 'toutiao' ? 30 : platform.key === 'youtube' ? 100 : 120" show-word-limit /></el-form-item>
            <el-form-item v-if="platform.key === 'wechat_channels'" label="封面方式">
              <el-radio-group v-model="drafts[platform.key].cover_mode" aria-label="视频号封面方式">
                <el-radio value="custom">资源包双封面（4:3＋3:4）</el-radio>
                <el-radio value="video_frame">视频画面</el-radio>
              </el-radio-group>
              <p v-if="drafts[platform.key].cover_mode === 'custom'" class="dual-cover-note">分别使用横版、竖版素材；两张封面都保存成功后才投稿。</p>
            </el-form-item>
            <el-form-item label="简介"><el-input v-model="drafts[platform.key].description" type="textarea" :rows="5" /></el-form-item>
            <el-form-item label="话题（逗号分隔）"><el-input v-model="tagsText[platform.key]" /></el-form-item>
            <el-form-item v-if="platform.key === 'bilibili'" label="B站分区">
              <el-select v-model="drafts[platform.key].category" filterable placeholder="选择分区，下次自动沿用" :loading="categoriesLoading" style="width:100%">
                <el-option v-for="choice in biliCategories" :key="choice.value" :value="choice.value" :label="choice.label" />
              </el-select>
              <p v-if="categoriesError" class="subtle">{{ categoriesError }} <el-button link @click="loadCategories">重新读取分区</el-button></p>
            </el-form-item>
            <YouTubeOptions v-if="platform.key === 'youtube'" v-model="drafts[platform.key]" />
            <el-form-item v-if="platform.key !== 'bilibili'" :label="['kuaishou', 'xiaohongshu'].includes(platform.key) ? 'AI 内容声明（追加到简介）' : 'AI 内容声明'"><el-input v-model="drafts[platform.key].ai_declaration" /></el-form-item>
          </el-form>
          <el-button @click="save(platform.key)" :disabled="!accountId(platform.key) || actionBusy[platform.key]">保存此平台草稿</el-button>
          </details>
          <el-alert v-if="actionErrors[platform.key]" :title="actionErrors[platform.key]" type="error" :closable="false" show-icon class="notice" />
          <PublicationProgress v-if="bundle.status[platform.key]?.task_id && bundle.status[platform.key]?.state !== 'published'"
            :state="bundle.status[platform.key].state"
            :evidence="{ stage: bundle.status[platform.key].progress_stage, message: bundle.status[platform.key].progress_message, media_percent: bundle.status[platform.key].media_percent }"
            :created-at="bundle.status[platform.key].task_created_at" :now="now" />
          <div v-if="['wechat_channels', 'kuaishou', 'xiaohongshu'].includes(platform.key) || ['unknown', 'processing'].includes(bundle.status[platform.key]?.state)" class="platform-actions">
            <el-button v-if="['bilibili', 'douyin', 'wechat_channels', 'youtube', 'toutiao', 'kuaishou', 'xiaohongshu'].includes(platform.key) && ['unknown', 'processing'].includes(bundle.status[platform.key]?.state)"
              type="primary" :loading="actionBusy[platform.key]" @click="syncResult(platform.key)" data-testid="sync-wechat_channels">同步后台结果</el-button>
            <a v-if="platform.key === 'xiaohongshu'" href="https://creator.xiaohongshu.com/new/note-manager" target="_blank" rel="noopener noreferrer" class="backend-link">查看小红书后台</a>
            <a v-if="platform.key === 'kuaishou'" href="https://cp.kuaishou.com/article/manage/video" target="_blank" rel="noopener noreferrer" class="backend-link">查看快手后台</a>
            <a v-if="platform.key === 'wechat_channels'" href="https://channels.weixin.qq.com/platform/post/list" target="_blank" rel="noopener noreferrer" class="backend-link">查看视频号后台</a>
          </div>
          <details v-if="bundle.status[platform.key]?.task_id || bundle.status[platform.key]?.remote_id" class="publication-details">
            <summary>投稿记录<span v-if="bundle.status[platform.key]?.state === 'published'" class="published-note"> · 已确认发布</span></summary>
          <p v-if="bundle.status[platform.key]?.remote_id" class="remote">远端作品：{{ bundle.status[platform.key].remote_id }}</p>
          <details v-if="isUnuploadedFailure(bundle.status[platform.key])" class="previous-failure">
            <summary>上次任务记录（未上传）</summary>
            <p>{{ bundle.status[platform.key].error }}</p>
            <p v-if="bundle.status[platform.key].task_updated_at">记录时间：{{ bundle.status[platform.key].task_updated_at }}</p>
          </details>
          <p v-if="bundle.status[platform.key]?.task_id" class="task-line">
            {{ isUnuploadedFailure(bundle.status[platform.key]) ? '上次任务' : '任务' }} {{ bundle.status[platform.key].task_id }}
            <el-button v-if="!isUnuploadedFailure(bundle.status[platform.key]) && bundle.status[platform.key]?.submission_started !== false" link type="info" @click="openReconcile(platform.key)">人工核对（备用）</el-button>
          </p>
          </details>
        </article>
      </div>

      <footer class="publish-footer">
        <p>每 3 秒更新今日内容与任务进度。点击发布会保存选中平台的文案并入队。</p>
      </footer>
    </template>

    <el-dialog v-model="coverVisible" :title="coverPreview.title" width="min(94vw, 920px)" align-center
      class="cover-preview-dialog" append-to-body @closed="coverPreview = {}">
      <div class="cover-preview-stage"><img v-if="coverPreview.src" :src="coverPreview.src" :alt="coverPreview.title" /></div>
      <p class="cover-preview-caption">{{ coverPreview.label }} · 按 Esc 或点击遮罩关闭</p>
    </el-dialog>

    <el-dialog v-model="bindingVisible" :title="`选择${bindingName}发布账号`" width="min(92vw, 520px)"
      :close-on-click-modal="false" :close-on-press-escape="!bindingSaving" :show-close="!bindingSaving">
      <p class="subtle">当前绑定：{{ bindingInfo?.account?.display_name || '未配置' }}
        <span v-if="bindingInfo?.account?.web_account_id">（#{{ bindingInfo.account.web_account_id }}）</span>
      </p>
      <el-alert v-if="bindingInfo?.access_status === 'binding_missing'" type="warning" :closable="false"
        title="原账号记录已不存在，请从账号管理中已有的账号选择一个。" />
      <el-select v-model="bindingChoice" :loading="bindingLoading" :disabled="bindingSaving" :placeholder="`选择已有${bindingName}账号`"
        style="width:100%;margin:16px 0" :aria-label="`${bindingName}发布账号`">
        <el-option v-for="account in bindingInfo?.accounts || []" :key="account.id" :value="account.id"
          :disabled="!account.selectable" :label="`${account.name}（#${account.id}）${account.selectable ? '' : ' · 请先登录'}`" />
      </el-select>
      <p class="subtle">请核对账号名称。这里只更换后续投稿目标，不会发送视频，当前编辑文案也会保留。账号列表显示上次保存的状态，提交前会再次检查登录态。</p>
      <p v-if="!bindingLoading && !(bindingInfo?.accounts?.length)" class="subtle">没有{{ bindingName }}账号，请先到账号管理添加。</p>
      <template #footer>
        <div class="binding-dialog-actions">
        <el-button :disabled="bindingSaving" @click="loadAccountBinding" :loading="bindingLoading">刷新列表</el-button>
        <el-button :disabled="bindingSaving" @click="bindingVisible = false">取消</el-button>
        <el-button type="primary" :loading="bindingSaving" :disabled="!bindingChoice || bindingLoading || bindingSaving"
          @click="confirmAccountBinding">绑定此账号</el-button>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="reconcileVisible" class="reconcile-dialog" title="确认投稿状态" width="min(94vw, 460px)"
      :close-on-click-modal="false" :close-on-press-escape="!reconcileSaving" :show-close="!reconcileSaving"
      @closed="resetReconcileDialog">
      <p class="subtle">选择你在平台看到的状态，作品信息自动保留。确认后更新记录。</p>
      <el-alert v-if="reconcileError" :title="reconcileError" type="error" :closable="false" show-icon data-testid="reconcile-error" />
      <p v-if="reconcileLoading" class="subtle">正在读取任务信息…</p>
      <el-form :model="reconcileForm" label-position="top" :disabled="reconcileSaving || reconcileLoading || !reconcileTarget" @submit.prevent="reconcile">
        <el-form-item label="状态">
          <el-select v-model="reconcileForm.state" style="width:100%" aria-label="投稿状态">
            <el-option label="审核中" value="processing" />
            <el-option label="已公开发布" value="published" />
            <el-option label="需本人处理" value="needs_action" />
            <el-option label="仍不确定" value="unknown" />
            <el-option label="确认没有作品" value="failed" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button :disabled="reconcileSaving" @click="reconcileVisible = false">取消</el-button>
        <el-button type="primary" :loading="reconcileSaving" :disabled="reconcileLoading || !reconcileTarget" @click="reconcile">确认状态</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'
import { platformCoverPreviews } from '@/utils/platformCoverPreviews'
import { channelCoverPreviews } from '@/utils/channelCoverPreviews'
import { RECONCILE_STATES, buildStatusConfirmation } from '@/utils/dailyReconcile'
import { dailyAccountLabel, dailyAccessText, canPublishDailyPlatform, isUnuploadedFailure, dailyTaskStatusText, dailyTaskNotice, isRecoveredLoginFailure, needsDailyRelogin, dailyTaskNoticeType } from '@/utils/dailyPublishState'
import YouTubeOptions from '@/components/YouTubeOptions.vue'
import PublicationProgress from '@/components/PublicationProgress.vue'

const platforms = [
  { key: 'bilibili', name: 'B站', mode: 'API 优先', cover: 'bilibili' },
  { key: 'douyin', name: '抖音', mode: 'API 优先', cover: 'portrait' },
  { key: 'wechat_channels', name: '视频号', mode: '本机浏览器', cover: 'portrait' },
  { key: 'youtube', name: 'YouTube', mode: '官方 API', cover: 'landscape' },
  { key: 'toutiao', name: '今日头条', mode: '头条号后台', cover: 'landscape' },
  { key: 'kuaishou', name: '快手', mode: '本机浏览器', cover: 'portrait' },
  { key: 'xiaohongshu', name: '小红书', mode: '本机浏览器', cover: 'portrait' }
]
const bundle = ref(null)
const coverVisible = ref(false)
const coverPreview = ref({})
const coversFor = platform => platformCoverPreviews(bundle.value?.package, platform.key, drafts[platform.key])
function openCover(platform, cover) {
  coverPreview.value = { src: assetUrl(cover.key), title: `${platform.name} · ${cover.label}`, label: cover.resolution || '完整封面预览' }
  coverVisible.value = true
}
const biliCategories = ref([])
const categoriesLoading = ref(false)
const categoriesError = ref('')
async function loadCategories() {
  if (categoriesLoading.value || !accountId('bilibili')) return
  categoriesLoading.value = true
  categoriesError.value = ''
  const account = accountId('bilibili')
  try {
    const response = await http.get('/daily/bilibili/categories', undefined, { timeout: 45000, silentError: true })
    if (account !== accountId('bilibili') || response.data.account_id !== account) return
    biliCategories.value = response.data.options
    if (drafts.bilibili && !drafts.bilibili.category) drafts.bilibili.category = response.data.category
  } catch (exc) { categoriesError.value = exc.message || '分区读取失败，请重试' }
  finally { categoriesLoading.value = false }
}
const selectedPlatforms = ref([])
const customChannelCovers = computed(() => channelCoverPreviews(bundle.value?.package))
const drafts = reactive({})
const tagsText = reactive({})
const loading = ref(false)
const submitting = ref(false)
const error = ref('')
const now = ref(Date.now())
const actionBusy = reactive({})
const actionErrors = reactive({})
const draftBaseline = reactive({})
const updateAvailable = ref(false)
const bindingVisible = ref(false)
const bindingInfo = ref(null)
const bindingChoice = ref(null)
const bindingLoading = ref(false)
const bindingSaving = ref(false)
const bindingPlatform = ref('wechat_channels')
const bindingName = computed(() => platforms.find(x => x.key === bindingPlatform.value)?.name || '平台')
const bindingUrl = computed(() => `/daily/accounts/${bindingPlatform.value}`)

async function loadAccountBinding() {
  if (bindingLoading.value) return
  bindingLoading.value = true
  try {
    const response = await http.get(bindingUrl.value, undefined, { timeout: 10000 })
    bindingInfo.value = response.data
    const candidates = response.data.accounts.filter(a => a.selectable)
    const bound = candidates.find(a => a.id === response.data.account?.web_account_id)
    bindingChoice.value = bound?.id ?? (candidates.length === 1 ? candidates[0].id : null)
  } catch (exc) {
    ElMessage.error(exc.response?.data?.msg || '无法加载平台账号列表')
  } finally { bindingLoading.value = false }
}
async function openAccountBinding(platform) {
  bindingPlatform.value = platform
  bindingVisible.value = true
  bindingInfo.value = null
  bindingChoice.value = null
  await loadAccountBinding()
}
async function confirmAccountBinding() {
  if (bindingSaving.value || !bindingChoice.value || !bindingInfo.value?.revision) return
  bindingSaving.value = true
  try {
    await http.post(bindingUrl.value, { web_account_id: bindingChoice.value, revision: bindingInfo.value.revision },
      { headers: { 'X-SAU-Local': '1' }, timeout: 10000 })
    bindingVisible.value = false
    ElMessage.success('发布账号已绑定，尚未上传或投稿')
    // Keep the user's unsaved text; switching account is not a page reset.
    await refresh()
    if (bindingPlatform.value === 'bilibili') {
      biliCategories.value = []
      drafts.bilibili.category = null
      await loadCategories()
    }
  } catch (exc) {
    ElMessage.error(exc.response?.data?.msg || '账号绑定未完成，请刷新列表后重试')
  } finally { bindingSaving.value = false }
}

const reconcileVisible = ref(false)
const reconcileTarget = ref(null)
const reconcileLoading = ref(false)
const reconcileSaving = ref(false)
const reconcileError = ref('')
let reconcileGeneration = 0
const reconcileForm = reactive({ state: 'processing' })
let timer

const duration = computed(() => `${Math.round(bundle.value?.package?.video?.duration_seconds || 0)} 秒`)
const size = computed(() => `${((bundle.value?.package?.video?.bytes || 0) / 1024 / 1024).toFixed(2)} MiB`)
const apiRoot = import.meta.env?.VITE_API_BASE_URL || (import.meta.env?.DEV ? 'http://localhost:5409' : window.location.origin)
const assetUrl = key => bundle.value?.package ? `${apiRoot}/daily/asset/${bundle.value.package.date}/${bundle.value.package.revision}/${key}` : ''
const accountId = key => String(bundle.value?.status?.[key]?.account?.account_id || '')
const accountLabel = key => dailyAccountLabel(key, bundle.value?.status?.[key])
const accessText = key => dailyAccessText(bundle.value?.status?.[key])
const canPublish = key => canPublishDailyPlatform(key, bundle.value?.status?.[key]) &&
  (key !== 'wechat_channels' || drafts[key]?.cover_mode !== 'custom' || customChannelCovers.value.every(cover => cover.available))
const availablePlatforms = computed(() => platforms.map(x => x.key).filter(canPublish))
const selectedReady = computed(() => selectedPlatforms.value.filter(canPublish))
function toggleSelected(key, checked) {
  selectedPlatforms.value = checked ? [...new Set([...selectedPlatforms.value, key])] : selectedPlatforms.value.filter(value => value !== key)
}
const attentionCount = computed(() => platforms.filter(({ key }) =>
  ['unknown', 'needs_action', 'failed'].includes(bundle.value?.status?.[key]?.state)).length)
const draftSnapshot = key => JSON.stringify({ draft: drafts[key], tags: tagsText[key] })
const statusText = dailyTaskStatusText
const statusType = item => {
  if (item?.remote_verified) return 'success'
  if (isRecoveredLoginFailure(item)) return 'info'
  if (item?.access_status === 'binding_missing' && isUnuploadedFailure(item)) return 'warning'
  if (item?.state === 'ready' && item.access_status !== 'ready') return 'warning'
  return ({ published: 'success', processing: 'warning', uploading: 'info',
    unknown: 'danger', failed: 'danger', needs_action: 'warning' })[item?.state] || 'info'
}

async function refresh(force = false) {
  if (loading.value) return
  loading.value = true
  try {
    const response = await http.get('/daily/today')
    const next = response.data
    const changed = bundle.value?.package && next.package &&
      (bundle.value.package.date !== next.package.date || bundle.value.package.revision !== next.package.revision)
    const dirty = platforms.some(({ key }) => draftBaseline[key] && draftBaseline[key] !== draftSnapshot(key))
    if (bundle.value?.package && next.package &&
        changed && dirty && !force) {
      updateAvailable.value = true
      return
    }
    const first = !bundle.value?.package || force || changed
    bundle.value = next
    error.value = ''
    if (first && next.package) {
      for (const platform of platforms) {
        const key = platform.key
        drafts[key] = { cover_mode: 'custom', ...next.package.platforms[key], ...(next.drafts?.[key] || {}) }
        if (key === 'bilibili') drafts[key].category = Number(drafts[key].category || next.status[key]?.category) || null
        tagsText[key] = (drafts[key].tags || []).join('，')
        draftBaseline[key] = draftSnapshot(key)
      }
      selectedPlatforms.value = availablePlatforms.value
      updateAvailable.value = false
      loadCategories()
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
    draftBaseline[key] = draftSnapshot(key)
    ElMessage.success('草稿已保存')
  } catch (exc) { ElMessage.error(exc.message || '保存草稿失败'); throw exc }
}

async function publishAll() {
  const keys = [...selectedReady.value]
  if (submitting.value || !keys.length) return
  submitting.value = true
  for (const key of keys) { actionBusy[key] = true; actionErrors[key] = '' }
  const targets = keys.map(key => ({ platform: key, account_id: accountId(key),
    payload: { ...drafts[key], tags: tagsText[key].split(/[,，]/).map(x => x.trim().replace(/^#/, '')).filter(Boolean) } }))
  try {
    const result = await http.post('/daily/publish-batch', { package_path: bundle.value.package_path, targets },
      { headers: { 'X-SAU-Local': '1' }, timeout: 60000, silentError: true })
    for (const key of keys) {
      actionErrors[key] = result.data.errors[key] || result.data.jobs.find(x => x.platform === key)?.warning || ''
      if (!result.data.errors[key]) draftBaseline[key] = draftSnapshot(key)
    }
    await refresh()
  } catch (exc) { error.value = exc.message || '批量投稿未完成，请刷新状态核对已入队任务' }
  finally { submitting.value = false; for (const key of keys) actionBusy[key] = false }
}

async function syncResult(key) {
  if (actionBusy[key]) return
  actionBusy[key] = true
  actionErrors[key] = ''
  try {
    const result = await http.post('/daily/sync-result', { task_id: bundle.value.status[key].task_id },
      { headers: { 'X-SAU-Local': '1' }, timeout: 65000, silentError: true })
    if (result.data.sync.status !== 'found') actionErrors[key] = result.data.sync.message
    await refresh()
  } catch (exc) { actionErrors[key] = exc.message || '后台结果暂时无法读取；没有重复投稿' }
  finally { actionBusy[key] = false }
}

function resetReconcileDialog() {
  ++reconcileGeneration
  reconcileTarget.value = null
  reconcileError.value = ''
}
async function openReconcile(key) {
  if (reconcileSaving.value) return
  const taskId = bundle.value?.status?.[key]?.task_id
  if (!taskId) { ElMessage.warning('该平台没有可核对任务，请刷新状态'); return }
  const generation = ++reconcileGeneration
  reconcileTarget.value = null
  reconcileError.value = ''
  reconcileLoading.value = true
  reconcileForm.state = 'unknown'
  reconcileVisible.value = true
  try {
    const response = await http.get(`/daily/task/${encodeURIComponent(taskId)}`, undefined, { silentError: true, timeout: 10000 })
    if (!reconcileVisible.value || generation !== reconcileGeneration) return
    const task = response.data
    if (!task || task.id !== taskId || task.platform !== key || !task.account_id || !task.updated_at) {
      throw new Error('任务信息不一致，请刷新后重新打开核对窗口')
    }
    // Keep the ORIGINAL task/account/version even if the page's 8s polling
    // later displays another task or a newly bound account.
    reconcileTarget.value = { task_id: task.id, platform: task.platform, account_id: String(task.account_id), updated_at: task.updated_at }
    reconcileForm.state = RECONCILE_STATES.includes(task.state) ? task.state : 'unknown'
  } catch (exc) {
    if (generation === reconcileGeneration) reconcileError.value = exc.message || '任务读取失败，请重新打开窗口'
  } finally {
    if (generation === reconcileGeneration) reconcileLoading.value = false
  }
}
async function reconcile() {
  if (reconcileSaving.value || reconcileLoading.value || !reconcileTarget.value) return
  reconcileError.value = ''
  reconcileSaving.value = true
  try {
    const body = buildStatusConfirmation(reconcileTarget.value, reconcileForm.state)
    await http.post('/daily/reconcile', body, { silentError: true, timeout: 15000 })
    reconcileVisible.value = false
    ElMessage.success('核对结果已保存，未重新发布视频')
    await refresh()
  } catch (exc) {
    reconcileError.value = exc.message || '保存核对结果失败，请重试'
  } finally { reconcileSaving.value = false }
}
onMounted(() => { refresh(); timer = window.setInterval(() => { now.value = Date.now(); refresh() }, 3000) })
onUnmounted(() => window.clearInterval(timer))
</script>

<style scoped>
.daily-page { max-width: 1480px; margin: 0 auto; padding: 2px 0 24px; color: var(--ui-text); }
.page-header, .edition-card, .platform-head, .platform-actions, .publish-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.page-header { margin-bottom: 14px; }
.header-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; flex-shrink: 0; }
.header-actions .el-button { margin: 0; }
.today-summary { display: flex; align-items: center; gap: 10px 18px; flex-wrap: wrap; padding: 10px 14px; margin-bottom: 12px; border: 1px solid var(--ui-border); border-radius: 14px; background: var(--ui-surface); box-shadow: var(--ui-inset); color: var(--ui-muted); }
.today-summary strong { color: var(--ui-text); }
.today-summary a { color: var(--el-color-primary); }
.transport-reason { line-height: 1.6; }
.binding-dialog-actions { display: flex; justify-content: flex-end; gap: 8px; }
.binding-dialog-actions .el-button { margin: 0; }
.page-header h1 { margin: 3px 0 5px; font-size: clamp(26px, 2.4vw, 32px); }
.eyebrow { margin: 0; color: var(--el-color-primary); font-weight: 700; font-size: 12px; letter-spacing: .08em; }
.subtle, .account, .remote, .task-line { color: var(--ui-muted); font-size: 13px; }
.notice { margin-bottom: 16px; }
.edition-card, .platform-card, .publish-footer { background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: 16px; box-shadow: var(--ui-raised); }
.edition-card { padding: 16px; margin-bottom: 14px; }
.edition-copy h2 { margin: 6px 0 10px; font-size: 28px; }
.edition-details { margin-top: 10px; font-size: 13px; color: var(--ui-muted); }
.edition-details summary { cursor: pointer; }
.video-preview { width: min(28%, 240px); aspect-ratio: 16/9; border-radius: 10px; background: #111827; }
.platform-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 14px; }
.platform-card { padding: 16px; min-width: 0; }
.platform-head { align-items: flex-start; }
.platform-state { display: grid; justify-items: end; gap: 4px; }
.platform-head h3 { margin: 4px 0 0; font-size: 21px; }
.cover-row { display: flex; align-items: center; gap: 14px; margin: 12px 0; color: var(--ui-muted); font-size: 13px; }

.platform-actions { flex-wrap: wrap; margin-top: 10px; gap: 12px; }
.advanced-publish { margin-top: 8px; }
.reconcile-dialog .subtle { margin: 0 0 16px; line-height: 1.6; }
.advanced-publish summary { cursor: pointer; color: var(--el-color-primary); font-weight: 600; margin-bottom: 14px; }
.frame-cover-preview { width: 92px; height: 70px; display: grid; place-items: center; border: 1px dashed var(--el-border-color); border-radius: 8px; }
.backend-link { color: var(--el-color-primary); font-size: 13px; text-decoration: none; }
.task-line, .remote { overflow-wrap: anywhere; }
.publication-details { margin-top: 10px; color: var(--el-text-color-secondary); font-size: 12px; }
.publication-details summary { cursor: pointer; }
.login-recovery { margin-top: 8px; }
.account-binding-row { display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap; }
.previous-failure { margin-top:14px;font-size:12px;color:var(--el-text-color-secondary);overflow-wrap:anywhere; }
.previous-failure summary { cursor:pointer; }
.previous-failure p { margin:6px 0; }
.publish-footer { margin-top: 22px; padding: 12px 0; box-shadow: none; border: 0; background: transparent; font-size: 13px; color: var(--ui-muted); }
.publish-footer p { margin: 0; }
@media (max-width: 980px) { .platform-grid { grid-template-columns: 1fr; } }
@media (max-width: 600px) {
  .binding-dialog-actions { display: grid; grid-template-columns: 1fr 1fr; }
  .binding-dialog-actions .el-button { height: 40px; }
  .binding-dialog-actions .el-button--primary { grid-column: 1 / -1; width: 100%; }
  .page-header, .edition-card, .publish-footer { align-items: stretch; flex-direction: column; }
  .edition-card, .platform-card, .publish-footer { padding: 16px; }
  .video-preview { width: 100%; }
  .publish-footer .el-button { width: 100%; margin: 0; }
}
.dual-cover-note { margin: 5px 0 0; line-height: 1.6; color: var(--el-text-color-secondary); font-size: 12px; }
.missing-cover { color: var(--el-color-danger); font-size: 12px; }

.edition-copy { min-width: 0; flex: 1; }
.edition-copy h2 { font-size: 22px; margin: 5px 0 8px; }
.edition-title { max-width: 680px; font-size: 15px; line-height: 1.7; margin: 6px 0 10px; }
.edition-meta { display: flex; gap: 8px; flex-wrap: wrap; color: var(--ui-muted); font-size: 12px; }
.edition-meta span { padding: 4px 9px; background: var(--ui-soft); border-radius: 6px; }
.account-binding-row { margin: 10px 0; padding: 6px 10px; border-radius: 9px; background: var(--ui-soft); }
.advanced-publish { border-top: 1px solid var(--ui-border); padding-top: 8px; }
.advanced-publish:not([open]) summary { margin-bottom: 0; }

.platform-actions:empty { display: none; }
@media(max-width: 700px) { .page-header { align-items: stretch; flex-direction: column; } .header-actions { justify-content: flex-start; } .header-actions .el-button--primary { flex: 1; } }


.page-header .subtle { margin: 6px 0 0; }
.account-binding-row .account { margin: 4px 0; overflow-wrap: anywhere; }
.cover-thumbnails { display: flex; align-items: flex-start; gap: 10px; flex-shrink: 0; }
.cover-thumbnails figure { margin: 0; }
.cover-image-box { position: relative; display: block; height: 84px; padding: 0; border: 1px solid var(--ui-border); border-radius: 7px; background: var(--ui-soft); cursor: zoom-in; overflow: hidden; }
.cover-image-box img { display: block; width: 100%; height: 100%; object-fit: contain; }
.cover-image-box:focus-visible { outline: 3px solid var(--el-color-primary); outline-offset: 3px; }
.cover-image-box:disabled { cursor: default; }
.cover-zoom { position: absolute; right: 3px; bottom: 3px; border-radius: 4px; background: #102033d9; color: #fff; font-size: 10px; padding: 2px 5px; }
.cover-thumbnails figcaption { font-size: 11px; margin-top: 5px; color: var(--ui-muted); }
.cover-copy { min-width: 0; font-size: 11px; color: var(--ui-muted); }
.material-title { font-size: 13px; color: var(--ui-text); line-height: 1.6; margin: 0 0 8px; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.published-note { color: var(--ui-muted); font-size: 12px; }
.cover-preview-stage { display: flex; justify-content: center; align-items: center; background: var(--ui-soft); border-radius: 8px; overflow: hidden; }
.cover-preview-stage img { display: block; max-width: 100%; max-height: 70vh; object-fit: contain; }
.cover-preview-caption { text-align: center; color: var(--ui-muted); margin: 10px 0 0; font-size: 12px; }
@media (min-width: 1280px) { .platform-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 600px) {
  .cover-row { flex-wrap: wrap; gap: 8px; }
  .cover-copy { flex-basis: 100%; }
  .cover-copy > span { display: none; }
  .material-title { -webkit-line-clamp: 2; margin-bottom: 0; }
  .cover-preview-stage img { max-height: 64vh; }
}
</style>
