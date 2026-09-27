// Login SSE lifecycle kept separate from the dialog for deterministic testing.
export function createLoginStream(url, callbacks, options = {}) {
  const Source = options.EventSourceImpl || globalThis.EventSource
  const schedule = options.setTimeoutImpl || globalThis.setTimeout
  const unschedule = options.clearTimeoutImpl || globalThis.clearTimeout
  let active = true
  let source = null
  let qrTimer = null
  let loginTimer = null

  const close = () => {
    active = false
    if (qrTimer !== null) unschedule(qrTimer)
    if (loginTimer !== null) unschedule(loginTimer)
    qrTimer = loginTimer = null
    source?.close()
  }
  const fail = (message, details) => {
    if (!active) return
    close()
    callbacks.onError?.(message, details)
  }
  const parseEvent = (event) => {
    try { return JSON.parse(event.data) } catch { return null }
  }

  try {
    source = new Source(url)
    qrTimer = schedule(() => fail(options.mode === 'browser' ? '浏览器打开超时，请检查 Edge 后重试' : '二维码获取超时，请检查网络后重试'), options.qrTimeoutMs ?? 60000)
    loginTimer = schedule(() => fail(options.mode === 'browser' ? '登录等待超时，请重新打开浏览器并完成平台验证' : '登录等待超时，请重新扫码并在手机上确认'), options.loginTimeoutMs ?? 370000)
    source.onmessage = (event) => {
      if (!active) return
      const data = event.data
      if (data === '200') {
        // Close synchronously: the server EOF must not turn success into error.
        close()
        callbacks.onSuccess?.()
      } else if (data === '500') {
        fail('登录失败，请重新扫码或查看后端日志')
      } else {
        let image = ''
        if (data.startsWith('data:image/')) image = data
        else if (data.length > 100 && /^[A-Za-z0-9+/]+={0,2}$/.test(data)) image = `data:image/png;base64,${data}`
        else {
          fail('二维码数据无效，请重启后端服务后重试')
          return
        }
        if (qrTimer !== null) unschedule(qrTimer)
        qrTimer = null
        // Replacement is intentional: a refreshed QR code must replace the old one.
        callbacks.onQr?.(image)
      }
    }
    source.addEventListener('login-status', (event) => {
      if (!active) return
      const payload = parseEvent(event)
      if (payload?.stage === 'browser_open' && qrTimer !== null) {
        unschedule(qrTimer)
        qrTimer = null
      }
      if (payload && typeof payload.message === 'string') callbacks.onStatus?.(payload)
    })
    source.addEventListener('login-error', (event) => {
      if (!active) return
      const payload = parseEvent(event)
      fail(typeof payload?.message === 'string' ? payload.message : '登录失败，请重试', payload)
    })
    source.onerror = () => fail('登录连接已断开，请确认后端服务正常后重试')
  } catch {
    fail('无法连接登录服务，请确认后端已启动')
  }
  return { close }
}
