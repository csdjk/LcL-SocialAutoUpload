<template>
  <div class="credential-import" :data-testid="`${platform}-credential-import`">
    <el-radio-group v-model="inputMode" size="small" :disabled="busy" aria-label="登录态来源" @change="clearCredentials">
      <el-radio-button value="paste">粘贴 Cookie</el-radio-button>
      <el-radio-button value="file">JSON 文件</el-radio-button>
    </el-radio-group>
    <div v-if="inputMode === 'paste'" class="paste-section">
      <p class="hint">从已登录{{ platformName }}创作中心的 Edge 复制完整 Cookie，直接粘贴，不用转换 JSON。</p>
      <el-input v-model="credentials" type="password" :disabled="busy" autocomplete="off" :spellcheck="false"
        placeholder="粘贴完整 Cookie（不是单个 Token）" :aria-label="`${platformName} Cookie`" @input="error = ''" />
      <details class="edge-help">
        <summary>Edge 中怎么复制？</summary>
        <p>在已登录的{{ platformName }}创作中心按 F12 → 网络（Network），然后刷新页面。</p>
        <p>在筛选栏输入 <code>{{ cookieFilter }}</code>，选择一个带 Cookie 的请求 → 标头（Headers）→ 请求标头（Request Headers）。</p>
        <p>右键 Cookie 的值，选择“复制值”（Copy value），粘贴到上方。只复制 Cookie，不要复制整段请求、cURL 或截图。</p>
      </details>
    </div>
    <div v-else class="file-section">
      <p class="hint">选择不超过 512 KB 的登录态 JSON，支持本工具保存的登录态和浏览器导出的 Cookie 数组。</p>
      <input ref="fileInput" type="file" accept=".json,application/json" :disabled="busy" @change="readFile" :aria-label="`选择${platformName}登录态文件`" />
    </div>
    <p class="hint">只提交到本机服务验证，不记录凭据内容。不要发到聊天或代码仓库；导入不能跳过平台安全验证。</p>
    <p v-if="platform === 'bilibili'" class="hint">导入后会使用此登录态授权本机 B 站投稿客户端；不会自动投稿。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <div class="submit-row">
      <span v-if="busy" class="hint">正在验证，请稍候…</span>
      <el-button type="primary" :loading="busy" :disabled="busy || !credentials.trim()" @click="submit">
        {{ busy ? '验证中' : accountId ? '验证并更新账号' : '验证并添加账号' }}
      </el-button>
    </div>
  </div>
</template>
<script setup>
import { ref, computed, onBeforeUnmount } from 'vue'
import { importPlatformCredentials, MAX_CREDENTIAL_BYTES } from '@/utils/credentialImport'
const props = defineProps({ name: { type: String, default: '' }, accountId: { type: Number, default: null }, platform: { type: String, default: 'douyin' } })
const platformName = computed(() => props.platform === 'bilibili' ? 'B站' : '抖音')
const cookieFilter = computed(() => props.platform === 'bilibili' ? 'domain:member.bilibili.com' : 'domain:creator.douyin.com')
const emit = defineEmits(['busy', 'imported'])
const inputMode = ref('paste')
const credentials = ref('')
const fileInput = ref(null)
const busy = ref(false)
const error = ref('')
let controller = null
let disposed = false
let fileRevision = 0
const clearCredentials = () => {
  ++fileRevision
  credentials.value = ''
  error.value = ''
  if (fileInput.value) fileInput.value.value = ''
}
const readFile = async (event) => {
  const revision = ++fileRevision
  const file = event.target.files?.[0]
  credentials.value = ''
  error.value = ''
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.json') || file.size > MAX_CREDENTIAL_BYTES) {
    error.value = '请选择不超过 512 KB 的 JSON 文件'
    event.target.value = ''
    return
  }
  try {
    const text = await file.text()
    JSON.parse(text.replace(/^\uFEFF/, ''))
    if (!disposed && revision === fileRevision) credentials.value = text
  } catch {
    if (!disposed && revision === fileRevision) error.value = '文件不是有效的 JSON，请重新选择'
  }
}
const submit = async () => {
  if (busy.value) return
  busy.value = true
  emit('busy', true)
  error.value = ''
  controller = new AbortController()
  try {
    const row = await importPlatformCredentials({ name: props.name, credentials: credentials.value,
      accountId: props.accountId, platform: props.platform }, { signal: controller.signal })
    clearCredentials()
    busy.value = false
    emit('busy', false)
    if (!disposed) emit('imported', row)
  } catch (failure) {
    if (!disposed) error.value = failure.message || '导入失败，请重试'
  } finally {
    controller = null
    busy.value = false
    emit('busy', false)
  }
}
onBeforeUnmount(() => {
  disposed = true
  controller?.abort()
  clearCredentials()
})
</script>
<style scoped>
.credential-import { margin-top: 12px; }
.hint { font-size: 12px; line-height: 1.6; color: var(--el-text-color-secondary); }
input { max-width: 100%; font-size: 13px; }
.edge-help { margin-top: 10px; font-size: 12px; line-height: 1.7; color: var(--el-text-color-secondary); }
.edge-help summary { cursor: pointer; color: var(--el-color-primary); }
.edge-help p { margin: 6px 0; }
.edge-help code { overflow-wrap: anywhere; }
.submit-row { display: flex; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 14px; }
</style>
