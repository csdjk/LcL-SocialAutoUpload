<template>
  <section class="youtube-setup" aria-label="YouTube 官方授权配置">
    <strong>{{ configured ? '客户端已配置，可以授权频道' : '首次使用：配置 Google 官方授权' }}</strong>
    <p>授权会打开系统默认浏览器。成功后自动读取频道名、保存授权并续期，无需扩展。</p>
    <details :open="!configured">
      <summary>首次配置步骤</summary>
      <ol>
        <li>打开 <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noopener noreferrer">Google Cloud 控制台</a>，创建并选中一个项目。</li>
        <li>在「API 和服务 → 库」搜索 <b>YouTube Data API v3</b>，点击「启用」。</li>
        <li>进入「Google Auth Platform」，完成品牌信息；受众选择「外部」，在测试用户中添加你自己的 Google 邮箱。</li>
        <li>在「客户端」创建 OAuth 客户端 ID，应用类型选 <b>桌面应用</b>，下载 JSON 文件。</li>
        <li>在下方选择该文件，再点击「Google 授权」。授权时选投稿频道，并允许读取频道和上传视频。</li>
      </ol>
    </details>
    <label class="file-label">{{ configured ? '更换 OAuth 客户端 JSON' : '选择 OAuth 客户端 JSON' }}
      <input type="file" accept=".json,application/json" :disabled="busy" @change="selectFile" aria-label="选择 OAuth 客户端 JSON" />
    </label>
    <p class="privacy-note">配置仅保存在本机。不要把 JSON 内容发到聊天或提交到代码仓库。</p>
    <p class="api-note">未通过 YouTube API 审核的项目，上传视频受私享限制。测试模式授权也可能需要定期重新确认。</p>
    <p v-if="error" class="setup-error" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">正在保存客户端配置…</p>
  </section>
</template>
<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { http } from '@/utils/request'
const emit = defineEmits(['ready', 'busy'])
const configured = ref(false)
const busy = ref(false)
const error = ref('')
const controller = new AbortController()
let disposed = false
const options = { headers: { 'X-SAU-Local': '1' }, silentError: true, signal: controller.signal }
const setReady = value => { if (!disposed) { configured.value = value; emit('ready', value) } }
onMounted(async () => {
  emit('ready', false)
  try { setReady((await http.get('/accounts/youtube-oauth/config', {}, options)).data.configured) }
  catch (e) { if (!disposed) error.value = e.message || '无法读取客户端配置' }
})
onBeforeUnmount(() => { disposed = true; controller.abort() })
async function selectFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  error.value = ''
  if (file.size > 65536 || !file.name.toLowerCase().endsWith('.json')) {
    error.value = '请选择小于 64 KiB 的 OAuth 客户端 JSON 文件'
    return
  }
  busy.value = true; emit('busy', true)
  try {
    let data
    try { data = JSON.parse((await file.text()).replace(/^\uFEFF/, '')) }
    catch { throw new Error('JSON 文件格式无效，请选择 Google Cloud 下载的客户端文件') }
    if (disposed) return
    setReady((await http.post('/accounts/youtube-oauth/config', data, options)).data.configured)
  } catch (e) { if (!disposed) error.value = e.message || '配置保存失败' }
  finally { if (!disposed) { busy.value = false; emit('busy', false) } }
}
</script>
<style scoped>
.youtube-setup { padding: 16px; border: 1px solid var(--el-border-color); border-radius: 12px; background: var(--el-fill-color-light); font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; }
.youtube-setup strong { font-size: 15px; color: var(--el-text-color-primary); }
.youtube-setup p { margin: 10px 0; }
summary { cursor: pointer; color: var(--el-color-primary); font-weight: 600; }
ol { padding-left: 20px; margin: 10px 0 16px; list-style: decimal; }
li + li { margin-top: 8px; }
a { color: var(--el-color-primary); text-decoration: underline; }
.file-label { display: grid; gap: 8px; margin-top: 12px; font-weight: 600; }
input { width: 100%; min-width: 0; font-size: 12px; }
input::file-selector-button { padding: 8px 10px; margin-right: 8px; border: 1px solid var(--el-border-color); border-radius: 6px; color: var(--el-text-color-primary); background: var(--el-bg-color); cursor: pointer; }
.privacy-note { font-size: 12px; color: var(--el-text-color-secondary); }
.api-note { padding: 10px; border: 1px solid rgba(230,162,60,.35); border-radius: 8px; color: var(--el-text-color-primary); background: rgba(230,162,60,.1); }
.setup-error { color: var(--el-color-danger); }
@media (max-width: 480px) { .youtube-setup { padding: 12px; } }
</style>
