export const PLATFORM_NAMES = { bilibili: 'B站', douyin: '抖音', wechat_channels: '视频号', youtube: 'YouTube', toutiao: '今日头条', kuaishou: '快手', xiaohongshu: '小红书' }
export const TASK_STATES = {
  ready: '待投稿', queued: '排队中', uploading: '投稿中', needs_action: '需要处理',
  processing: '审核或核对中', published: '已公开发布', unknown: '结果待核对', failed: '未完成'
}
export const platformName = key => PLATFORM_NAMES[key] || key
export const taskStateText = state => TASK_STATES[state] || state
export const taskTagType = state => state === 'published' ? 'success' :
  ['unknown', 'failed'].includes(state) ? 'danger' :
  ['processing', 'needs_action'].includes(state) ? 'warning' : 'info'
