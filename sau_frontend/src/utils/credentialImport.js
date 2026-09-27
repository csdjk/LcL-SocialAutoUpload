// Keep credential requests out of axios interceptors (which may log request configs).
export const MAX_CREDENTIAL_BYTES = 512 * 1024

export function importDouyinCredentials(input, options = {}) {
  return importPlatformCredentials({ ...input, platform: 'douyin' }, options)
}

export async function importPlatformCredentials({ name, credentials, accountId, platform = 'douyin' }, options = {}) {
  if (!['douyin', 'bilibili'].includes(platform)) throw new Error('不支持此平台的登录态导入')
  if (name != null && (typeof name !== 'string' || name.trim().length > 80)) throw new Error('账号名称格式无效')
  if (!credentials?.trim()) throw new Error('请粘贴完整 Cookie，或选择登录态 JSON 文件')
  if (new TextEncoder().encode(credentials).length > MAX_CREDENTIAL_BYTES) throw new Error('登录态内容不能超过 512 KB')
  const baseUrl = options.baseUrl ?? import.meta.env?.VITE_API_BASE_URL ?? 'http://localhost:5409'
  const fetchImpl = options.fetchImpl || globalThis.fetch
  const controller = new AbortController()
  const abort = () => controller.abort()
  options.signal?.addEventListener('abort', abort, { once: true })
  if (options.signal?.aborted) controller.abort()
  const timer = setTimeout(abort, options.timeoutMs ?? 45000)
  try {
    const response = await fetchImpl(`${baseUrl.replace(/\/$/, '')}/accounts/import-${platform}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-SAU-Local': '1' },
      body: JSON.stringify({ name: name?.trim() || '', credentials, account_id: accountId ?? null }),
      cache: 'no-store', signal: controller.signal,
    })
    const data = await response.json()
    if (!response.ok || data.code !== 200) throw new Error(data.msg || '登录态验证失败，账号未保存')
    return data.data
  } catch (error) {
    if (controller.signal.aborted) throw new Error('验证等待已结束。请刷新账号列表确认结果后再重试')
    if (error instanceof TypeError || error instanceof SyntaxError) throw new Error('无法连接本机登录服务，请确认后端正在运行')
    throw error
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', abort)
  }
}
