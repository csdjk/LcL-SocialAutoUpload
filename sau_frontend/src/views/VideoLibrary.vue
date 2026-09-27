<template>
  <main class="library-page">
    <header class="page-heading"><div><p class="eyebrow">本机内容库</p><h1>普通视频</h1><p>导入 MP4 后编辑各平台文案，立即投稿或预约北京时间。日报仍从“今日待发布”读取。</p></div><el-button @click="refresh">刷新内容</el-button></header>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <section class="soft-panel import-panel">
      <h2>导入视频</h2>
      <div class="fields">
        <label>MP4 视频<input type="file" accept="video/mp4,.mp4" @change="video = $event.target.files?.[0] || null" /></label>
        <label>自定义封面（可选）<input type="file" accept="image/*" @change="cover = $event.target.files?.[0] || null" /></label>
        <label>标题<el-input v-model="title" maxlength="100" show-word-limit /></label>
        <label>简介<el-input v-model="description" type="textarea" :rows="3" maxlength="3000" show-word-limit /></label>
        <label>话题（用逗号分隔）<el-input v-model="tags" /></label>
        <label>AI 内容声明（适用时填写）<el-input v-model="aiDeclaration" /></label>
      </div>
      <div class="panel-actions"><span v-if="progress">已传输 {{ progress }}%</span><el-button type="primary" :loading="importing" :disabled="!video || !title.trim() || !description.trim()" @click="importNow">导入并检查</el-button></div>
    </section>
    <section class="items-section">
      <h2>已导入内容</h2>
      <el-empty v-if="!items.length" description="暂无普通视频" />
      <article v-for="item in items" :key="item.id" class="soft-panel item-card">
        <el-alert v-if="item.error" :title="`内容 ${item.id} 校验失败：${item.error}`" type="error" :closable="false" />
        <template v-else>
          <div class="item-heading"><div><h3>{{ item.title }}</h3><p>{{ item.date }} · {{ (item.video.bytes / 1048576).toFixed(2) }} MiB · {{ Math.round(item.video.duration_seconds) }} 秒</p></div><img :src="assetUrl(item.id, 'landscape')" alt="视频封面" /></div>
          <div class="schedule-row"><label>投稿时间（北京时间，留空立即执行）<input v-model="scheduled[item.id]" type="datetime-local" /></label></div>
          <section v-if="common[item.id]" class="shared-editor">
            <div class="shared-heading"><div><h4>通用文案</h4><p>修改后可一次应用到五个平台；平台专属字段仍分别保留。</p></div><el-button @click="applyCommon(item.id)">应用到五个平台</el-button></div>
            <div class="shared-fields"><label>标题<el-input v-model="common[item.id].title" maxlength="100" /></label><label>简介<el-input v-model="common[item.id].description" type="textarea" :rows="2" /></label><label>话题<el-input v-model="common[item.id].tagsText" /></label></div>
          </section>
          <div class="platform-grid">
            <section v-for="platform in platforms" :key="platform.key" class="platform-editor">
              <div class="platform-head"><h4>{{ platform.name }}</h4><el-tag :type="taskTagType(item.status[platform.key].state)">{{ taskStateText(item.status[platform.key].state) }}</el-tag></div>
              <p class="platform-title">{{ edits[item.id][platform.key].title }}</p>
              <details><summary>调整{{ platform.name }}文案</summary><div class="platform-fields">
              <label>标题<el-input v-model="edits[item.id][platform.key].title" :maxlength="platform.key === 'toutiao' ? 30 : 100" show-word-limit /></label>
              <label>简介<el-input v-model="edits[item.id][platform.key].description" type="textarea" :rows="3" /></label>
              <label>话题<el-input v-model="edits[item.id][platform.key].tagsText" /></label>
              <label v-if="platform.key === 'bilibili'">分区 ID<el-input v-model="edits[item.id][platform.key].category" /></label>
              <YouTubeOptions v-if="platform.key === 'youtube'" v-model="edits[item.id][platform.key]" />
              <label>AI 声明<el-input v-model="edits[item.id][platform.key].ai_declaration" /></label>
              </div></details>
              <el-button :disabled="item.status[platform.key].state !== 'ready' || item.status[platform.key].access_status !== 'ready' || publishing[item.id]" :loading="publishing[item.id]" @click="submit(item, [platform.key])">{{ scheduled[item.id] ? '预约' : '立即投稿到' }}{{ platform.name }}</el-button>
            </section>
          </div>
          <div class="panel-actions"><el-button type="primary" :disabled="publishing[item.id] || !available(item).length" :loading="publishing[item.id]" @click="submit(item, available(item))">{{ scheduled[item.id] ? '预约' : '立即投稿到' }}全部可用平台</el-button></div>
        </template>
      </article>
    </section>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'
import YouTubeOptions from '@/components/YouTubeOptions.vue'
import { taskStateText, taskTagType } from '@/utils/platformStatus'
const router = useRouter()
const platforms = [{ key: 'bilibili', name: 'B站' }, { key: 'douyin', name: '抖音' }, { key: 'wechat_channels', name: '微信视频号' }, { key: 'youtube', name: 'YouTube' }, { key: 'toutiao', name: '今日头条' }]
const items = ref([])
const edits = reactive({})
const common = reactive({})
const scheduled = reactive({})
const publishing = reactive({})
const video = ref(null)
const cover = ref(null)
const title = ref('')
const description = ref('')
const tags = ref('')
const aiDeclaration = ref('')
const importing = ref(false)
const progress = ref(0)
const error = ref('')
const assetUrl = (id, key) => `/daily/library/${encodeURIComponent(id)}/asset/${key}`
const available = item => platforms.map(p => p.key).filter(key => item.status[key].state === 'ready' && item.status[key].access_status === 'ready')

async function refresh() {
  try {
    const response = await http.get('/daily/library', undefined, { silentError: true })
    items.value = response.data
    for (const item of items.value) {
      if (item.error || edits[item.id]) continue
      edits[item.id] = Object.fromEntries(platforms.map(({ key }) => [key, {
        title: item.platforms[key].title,
        description: item.platforms[key].description,
        tagsText: (item.platforms[key].tags || []).join('，'),
        category: String(item.platforms[key].category || ''),
        ai_declaration: item.platforms[key].ai_declaration || '',
        visibility: item.platforms[key].visibility, made_for_kids: item.platforms[key].made_for_kids
      }]))
      common[item.id] = { title: item.title, description: item.platforms.bilibili.description,
        tagsText: (item.platforms.bilibili.tags || []).join('，') }
    }
    error.value = ''
  } catch (exc) { error.value = exc.message || '内容库读取失败' }
}

function applyCommon(id) {
  const value = common[id]
  for (const { key } of platforms) Object.assign(edits[id][key], value)
  ElMessage.success('通用标题、简介和话题已应用到五个平台')
}

async function importNow() {
  if (importing.value || !video.value) return
  importing.value = true
  progress.value = 0
  try {
    const form = new FormData()
    form.append('video', video.value)
    if (cover.value) form.append('cover', cover.value)
    form.append('title', title.value.trim())
    form.append('description', description.value.trim())
    form.append('tags', tags.value)
    form.append('ai_declaration', aiDeclaration.value.trim())
    const result = await http.post('/daily/library/import', form, {
      headers: { 'X-SAU-Local': '1', 'Content-Type': 'multipart/form-data' }, silentError: true,
      onUploadProgress: event => { if (event.total) progress.value = Math.round(event.loaded / event.total * 100) }
    })
    ElMessage.success(result.data.existing ? '该视频已导入，已显示原有内容' : '视频已导入并检查；尚未投稿')
    await refresh()
  } catch (exc) { error.value = exc.message || '导入失败' }
  finally { importing.value = false }
}

async function submit(item, keys) {
  if (publishing[item.id] || !keys.length) return
  publishing[item.id] = true
  try {
    const payloads = Object.fromEntries(keys.map(key => [key, {
      title: edits[item.id][key].title.trim(), description: edits[item.id][key].description.trim(),
      tags: edits[item.id][key].tagsText.split(/[,，]/).map(x => x.trim().replace(/^#/, '')).filter(Boolean),
      category: edits[item.id][key].category.trim(), ai_declaration: edits[item.id][key].ai_declaration.trim(),
      ...(key === 'youtube' ? { visibility: edits[item.id][key].visibility, made_for_kids: edits[item.id][key].made_for_kids } : {})
    }]))
    const scheduled_for = scheduled[item.id] ? `${scheduled[item.id]}:00+08:00` : null
    const result = await http.post('/daily/library/submit', { id: item.id, platforms: keys, payloads, scheduled_for },
      { headers: { 'X-SAU-Local': '1' }, silentError: true })
    await refresh()
    if (result.data.jobs.length) ElMessage.success(`${result.data.jobs.length} 个平台已${scheduled_for ? '预约' : '入队'}；请在任务中心查看进度`)
    if (Object.keys(result.data.errors).length) error.value = Object.values(result.data.errors).join('；')
    else if (result.data.jobs.length) await router.push('/publish-center')
  } catch (exc) { error.value = exc.message || '预约失败；请刷新账本核对' }
  finally { publishing[item.id] = false }
}
onMounted(refresh)
</script>

<style scoped>
.library-page { max-width: 1480px; margin: auto; padding: 8px 4px 42px; }
.page-heading, .item-heading, .platform-head, .panel-actions { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.page-heading { margin-bottom: 22px; }
h1 { font-size: clamp(26px, 2.4vw, 32px); margin: 0 0 6px; }
.eyebrow { color: var(--el-color-primary); font-size: 12px; font-weight: 700; letter-spacing: .12em; }
p { color: var(--ui-muted); line-height: 1.55; }
.soft-panel { padding: 22px; background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: 16px; box-shadow: var(--ui-raised); }
.fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
label { display: grid; align-content: start; gap: 8px; font-weight: 600; }
input[type=file], input[type=datetime-local] { width: 100%; min-width: 0; min-height: 42px; padding: 8px; color: var(--ui-text); background: var(--ui-surface); border: 1px solid var(--ui-border); border-radius: 12px; box-shadow: var(--ui-inset); }
.panel-actions { justify-content: flex-end; margin-top: 20px; }
.items-section { margin-top: 32px; }
.item-card { margin-top: 18px; }
.item-heading img { width: 180px; aspect-ratio: 16/9; object-fit: cover; border-radius: 12px; }
.schedule-row { max-width: 360px; margin: 18px 0; }
.shared-editor { padding: 18px; margin: 18px 0; border-radius: 14px; box-shadow: var(--ui-inset); }
.shared-heading { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
.shared-heading h4 { margin: 0; }
.shared-heading p { margin: 5px 0; font-size: 13px; }
.shared-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 14px; }
.shared-fields label:last-child { grid-column: 1 / -1; }
.platform-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 14px; }
.platform-editor { min-width: 0; padding: 16px; border-radius: 14px; box-shadow: var(--ui-inset); display: grid; align-content: start; gap: 13px; }
.platform-editor h4 { margin: 0; }
.platform-title { margin: 0; overflow-wrap: anywhere; }
.platform-editor summary { color: var(--el-color-primary); cursor: pointer; font-weight: 600; }
.platform-fields { display: grid; gap: 13px; padding-top: 12px; }
@media(max-width: 980px) { .platform-grid { grid-template-columns: 1fr; } }
@media(max-width: 700px) { .page-heading, .item-heading { align-items: stretch; flex-direction: column; } .fields, .shared-fields { grid-template-columns: 1fr; } .shared-fields label:last-child { grid-column: auto; } .item-heading img { width: 100%; } }

.import-panel h2 { font-size: 20px; margin: 0 0 18px; }
.fields label { font-size: 14px; font-weight: 500; }
.items-section > h2 { font-size: 20px; }
input[type=file]::file-selector-button { border: 1px solid var(--ui-border); border-radius: 6px; padding: 6px 10px; margin-right: 10px; background: var(--ui-surface); color: var(--ui-text); font: inherit; cursor: pointer; }

</style>
