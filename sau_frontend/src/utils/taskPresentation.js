export const taskBucket = state => ['queued', 'uploading', 'processing'].includes(state) ? 'active' :
  ['failed', 'unknown', 'needs_action'].includes(state) ? 'attention' : state === 'published' ? 'published' : 'other'

export const taskSource = source => ({ mcp: 'Codex 投稿', manual: '手动投稿', automation: '自动排程' })[source] || source

export function taskTime(value) {
  const date = new Date(value)
  if (!value || !Number.isFinite(date.getTime())) return '时间待确认'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date)
}
