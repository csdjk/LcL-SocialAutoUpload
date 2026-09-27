// These statements record the user's explicit confirmation, never an automated query.
export const NO_WORK_NOTE = '本人已检查当前账号的内容管理，确认没有本次作品。'
export const RECONCILE_STATES = ['processing', 'published', 'needs_action', 'unknown', 'failed']
const text = value => typeof value === 'string' ? value.trim() : ''

export function buildStatusConfirmation(target, state) {
  if (!target?.task_id || !target.updated_at) throw new Error('任务信息未加载，请重新打开状态确认窗口')
  if (!RECONCILE_STATES.includes(state)) throw new Error('请选择投稿状态')
  return { task_id: target.task_id, state, expected_updated_at: target.updated_at, confirmed: true }
}

export function isHttpsWorkUrl(value) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !!url.hostname && !url.username && !url.password
  } catch { return false }
}

export function validateReconcileForm(form) {
  const errors = {}
  if (!RECONCILE_STATES.includes(form.state)) errors.state = '请选择核对状态'
  if (['processing', 'published'].includes(form.state) && !text(form.remote_id)) {
    errors.remote_id = '请填写在平台查到的真实作品 ID'
  }
  if (form.state !== 'failed' && text(form.remote_id).length > 200) errors.remote_id = '作品 ID 不能超过 200 字'
  if (form.state === 'published' && !isHttpsWorkUrl(text(form.url))) {
    errors.url = '已发布需填写有效的 https:// 作品链接'
  } else if (form.state !== 'failed' && text(form.url) && !isHttpsWorkUrl(text(form.url))) {
    errors.url = '作品链接需为有效的 https:// 地址'
  }
  if (form.state !== 'failed' && text(form.url).length > 2048) errors.url = '作品链接过长'
  if (form.state === 'failed' && form.remote_absent !== true) {
    errors.remote_absent = '请先检查内容管理，再勾选确认没有本次作品'
  }
  if (!text(form.note)) errors.note = '请填写核对依据；确认失败时可勾选上方确认项生成说明'
  else if (text(form.note).length > 2000) errors.note = '核对依据不能超过 2000 字'
  return errors
}

export function buildReconcileRequest(target, form, checkedAt = new Date().toISOString()) {
  if (!target?.task_id || !target.platform || !target.account_id) throw new Error('任务信息未加载，请重新打开核对窗口')
  const errors = validateReconcileForm(form)
  if (Object.keys(errors).length) throw new Error(Object.values(errors)[0])
  const failed = form.state === 'failed'
  return {
    task_id: target.task_id, state: form.state, expected_updated_at: target.updated_at,
    evidence: {
      platform: target.platform, account_id: String(target.account_id),
      remote_id: failed ? '' : text(form.remote_id), url: failed ? '' : text(form.url),
      note: text(form.note), remote_absent: failed && form.remote_absent === true,
      checked_at: checkedAt
    }
  }
}
