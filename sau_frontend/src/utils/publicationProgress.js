export const PHASES = [
  { key: 'prepare', label: '准备' },
  { key: 'upload', label: '上传视频' },
  { key: 'details', label: '完善信息' },
  { key: 'submit', label: '提交' },
  { key: 'verify', label: '平台核对' }
]

const STAGE_PHASE = {
  checking: 0, deduplicating: 0, opening: 0, preflight: 0,
  uploading: 1, media_upload: 1,
  metadata: 2, cover: 2,
  submitting: 3,
  verifying: 4, verification: 4
}

const STAGE_TEXT = {
  checking: '检查账号与素材', deduplicating: '核对平台是否已有作品', opening: '打开投稿页面',
  uploading: '正在上传视频', media_upload: '正在上传视频', metadata: '填写文案与声明',
  cover: '设置封面', submitting: '向平台提交作品', verifying: '读取平台结果',
  verification: '等待平台身份验证', preflight: '投稿前检查'
}

export function progressModel(state, evidence = {}) {
  const stage = evidence?.stage
  if (state === 'published') return { phase: 5, title: '已公开发布', mode: 'complete', detail: '已记录平台发布结果' }
  if (state === 'processing') return { phase: 4, title: '平台已受理，等待审核或核对', mode: 'waiting', detail: evidence?.note || '受理不等于公开发布' }
  if (state === 'needs_action') return { phase: 4, title: '需要本人处理', mode: 'attention', detail: evidence?.message || '请检查平台要求的操作' }
  if (state === 'unknown') return { phase: Math.max(0, STAGE_PHASE[stage] ?? 3), title: '结果不确定，先核对原任务', mode: 'attention', detail: evidence?.message || '不会自动再次投稿' }
  if (state === 'failed') return { phase: Math.max(0, STAGE_PHASE[stage] ?? 0), title: '投稿未完成', mode: 'failed', detail: stage ? `停止于：${STAGE_TEXT[stage] || stage}` : '查看下方错误' }
  if (state === 'queued') return { phase: 0, title: '等待后台执行器领取', mode: 'queued', detail: '排队中；还未向平台上传' }
  const phase = STAGE_PHASE[stage] ?? 0
  const percent = evidence?.media_percent
  const measured = phase === 1 && Number.isInteger(percent) && percent >= 0 && percent <= 100 ? percent : null
  return { phase, title: STAGE_TEXT[stage] || '正在执行投稿任务', mode: 'active',
    percent: measured,
    detail: measured !== null ? `官方投稿页面显示上传 ${measured}%` :
      (evidence?.message || (phase === 1 ? '平台没有提供可核实的上传百分比' : '等待当前阶段完成')) }
}

export function elapsedText(createdAt, now = Date.now()) {
  const start = Date.parse(createdAt || '')
  if (!Number.isFinite(start)) return ''
  const seconds = Math.max(0, Math.floor((now - start) / 1000))
  const minutes = Math.floor(seconds / 60)
  return minutes ? `${minutes} 分 ${String(seconds % 60).padStart(2, '0')} 秒` : `${seconds} 秒`
}
