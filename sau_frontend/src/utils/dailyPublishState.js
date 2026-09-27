// A ready status permits a manual attempt; it is not proof of remote publication.
export function dailyAccountLabel(platform, item) {
  const account = item?.account || {}
  const name = account.display_name || account.alias || account.account_id || '未配置'
  return `${account.web_account_id ? '工具账号' : '账号'}：${name}`
}

export function dailyAccessText(item) {
  if (item?.access_message) return item.access_message
  const status = item?.access_status
  return ({
    ready: '',
    needs_account: '请先选择发布账号。',
    binding_missing: '原发布账号记录已不存在，请选择该平台当前已登录的账号；无需反复重新登录。',
    needs_login: '发布账号未登录、登录已失效或登录文件缺失，请到账号管理重新登录。',
    cookie_missing: '发布账号的登录文件缺失，请到账号管理重新登录。',
    disabled: '该平台已在工具配置中停用。'
  })[status] ?? `账号状态尚未就绪：${status || '未配置'}`
}

export function canPublishDailyPlatform(platform, item) {
  if (item?.access_status !== 'ready' || item.account_mismatch || !item.account?.account_id) return false
  if (!(item.account.alias || (platform === 'douyin' && item.account.open_id) || item.account.web_account_id)) return false
  return item.state === 'ready' || (item.state === 'failed' && !item.remote_id && item.retry_allowed === true)
}

// Only explicit preflight evidence makes a failure safely distinguishable.
export function isUnuploadedFailure(item) {
  return item?.state === 'failed' && !item.remote_id && item.failure_stage === 'preflight' && item.upload_started === false
}

export function isRecoveredLoginFailure(item) {
  return isUnuploadedFailure(item) && item.failure_code === 'login_check_failed'
    && item.access_status === 'ready' && !item.account_mismatch && item.retry_allowed === true
}

export function needsDailyRelogin(item) {
  if (item?.access_status === 'binding_missing') return false
  return item?.access_status === 'needs_login' || item?.access_status === 'cookie_missing'
    || (item?.next_action === 'relogin' && item.access_status !== 'ready')
}

export function dailyTaskNoticeType(item) {
  if (item?.state === 'published') return 'success'
  if (item?.remote_verified) return 'info'
  if (item?.access_status === 'binding_missing' && (item.state === 'ready' || isUnuploadedFailure(item))) return 'warning'
  if (isRecoveredLoginFailure(item)) return 'info'
  return item?.state === 'failed' ? 'error' : 'warning'
}

export function dailyTaskStatusText(item) {
  if (item?.state === 'published') return '已发布'
  if (item?.remote_verified) return '后台已有作品'
  if (item?.state === 'uploading' && item.progress_message) return item.progress_message
  if (item?.state === 'failed' && item.submission_started === false && !item.remote_id) return '未提交，可重试' 
  if (item?.access_status === 'binding_missing' && (item.state === 'ready' || isUnuploadedFailure(item))) return '请选择发布账号'
  if (isRecoveredLoginFailure(item)) return '待重新提交'
  if (isUnuploadedFailure(item)) return item.failure_code === 'login_check_failed' ? '登录校验失败' : '未上传'
  if (item?.state === 'ready' && item.access_status !== 'ready') return '暂不可发布'
  return ({ ready: '待发布', queued: '已排队', uploading: '上传中', needs_action: '待人工处理',
    processing: '已提交／审核中', published: '已发布', unknown: '结果待核对',
    failed: '确认失败', prepared: '历史预约待核对' })[item?.state] || '待配置'
}

export function dailyTaskNotice(item) {
  if (item?.state === 'published') return '已核对平台发布成功，无需再次投稿。'
  if (item?.state === 'queued') return '任务已保存到本机队列，等待后台执行；无需再次投稿。'
  if (item?.remote_verified) return '已从官方后台找到本期同内容作品，已阻止重复投稿。'
  if (item?.state === 'uploading') return item.progress_message || '投稿任务正在执行'
  if (item?.state === 'failed' && item.submission_started === false && !item.remote_id)
    return `${item.error || '投稿准备未完成'} 本次未执行最终发表，无需填写核对表，修复后可直接重试。` 
  if (item?.access_status === 'binding_missing' && (item.state === 'ready' || isUnuploadedFailure(item))) return dailyAccessText(item)
  if (isRecoveredLoginFailure(item)) return '上次任务未上传。当前账号记录已就绪，可手动重新提交；提交前仍会检查登录态。'
  if (isUnuploadedFailure(item)) return item.error || '本次任务在上传前终止，未产生稿件。修复后可手动重试。'
  if (item?.state === 'unknown') return item.error || '投稿结果尚未确认，请先到平台内容管理检查，再记录核对结果。'
  if (item?.state === 'failed' || item?.state === 'needs_action') return item.error || ''
  return ''
}
