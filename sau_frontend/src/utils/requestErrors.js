// Preserve structured server validation messages; never stringify request configs/credentials.
export function apiErrorMessage(error) {
  const data = error?.response?.data
  if (data && typeof data === 'object') {
    const value = [data.msg, data.message].find(item => typeof item === 'string' && item.trim())
    if (value) return value.trim()
  }
  const status = error?.response?.status
  if (status) return ({
    400: '提交内容不完整或格式有误，请检查表单',
    401: '未授权，请重新登录',
    403: '拒绝访问，请检查操作权限',
    404: '请求的任务或资源不存在',
    409: '数据已更新，请刷新后重试',
    422: '提交内容未通过校验，请检查表单',
    500: '服务器内部错误，请稍后重试',
    502: '后端服务暂不可用，请稍后重试',
    503: '后端服务暂不可用，请稍后重试'
  })[status] || (status >= 200 && status < 300 ? '请求未完成，请检查提交内容' : `请求未完成（HTTP ${status}）`)
  if (error?.code === 'ECONNABORTED' || error?.code === 'ETIMEDOUT') return '请求超时，请刷新状态确认结果后再重试'
  if (error?.code === 'ERR_CANCELED') return '请求已取消'
  return '无法连接本机服务，请确认后端正在运行'
}

export function normalizeApiError(error) {
  const normalized = error instanceof Error ? error : new Error()
  normalized.message = apiErrorMessage(error)
  return normalized
}
