import test from 'node:test'
import assert from 'node:assert/strict'
import { createLoginStream } from '../src/utils/loginStream.js'

function setup() {
  let source, serial = 0
  const timers = new Map(), events = []
  class FakeSource {
    constructor() { source = this; this.handlers = {}; this.closed = false }
    close() { this.closed = true }
    addEventListener(name, callback) { this.handlers[name] = callback }
    message(data) { this.onmessage({ data }) }
    named(name, data) { this.handlers[name]({ data: JSON.stringify(data) }) }
  }
  const stream = createLoginStream('http://localhost/login', {
    onQr: data => events.push(['qr', data]),
    onStatus: data => events.push(['status', data]),
    onError: data => events.push(['error', data]),
    onSuccess: () => { assert.equal(source.closed, true); events.push(['success']) }
  }, {
    EventSourceImpl: FakeSource,
    setTimeoutImpl: (fn, delay) => { const id = ++serial; timers.set(id, { fn, delay }); return id },
    clearTimeoutImpl: id => timers.delete(id)
  })
  return { source, stream, timers, events }
}

test('success closes immediately and ignores EOF/error/reconnect', () => {
  const { source, events, timers } = setup()
  source.message('200'); source.onerror(); source.message('500')
  assert.deepEqual(events, [['success']]); assert.equal(timers.size, 0)
})
test('refreshed QR replaces the first image', () => {
  const { source, events, timers } = setup()
  source.message('data:image/png;base64,first'); source.message('data:image/png;base64,new')
  assert.equal(events.length, 2); assert.equal(events[1][1], 'data:image/png;base64,new')
  assert.equal(timers.size, 1)
})
test('named progress is not mistaken for image data', () => {
  const { source, events } = setup()
  source.named('login-status', { stage: 'scanned', message: '请确认' })
  assert.equal(events[0][0], 'status'); assert.equal(source.closed, false)
})
test('server error reason survives subsequent EOF and 500', () => {
  const { source, events } = setup()
  source.named('login-error', { message: '二维码过期' }); source.message('500'); source.onerror()
  assert.deepEqual(events, [['error', '二维码过期']]); assert.equal(source.closed, true)
})
test('QR timeout is bounded', () => {
  const { source, events, timers } = setup()
  Array.from(timers.values()).find(t => t.delay === 60000).fn()
  assert.match(events[0][1], /二维码获取超时/); assert.equal(source.closed, true)
})
test('overall timeout remains active after receiving QR', () => {
  const { source, events, timers } = setup()
  source.message('data:image/png;base64,test')
  Array.from(timers.values()).find(t => t.delay === 370000).fn()
  assert.equal(events[1][0], 'error'); assert.equal(source.closed, true)
})
test('cancel stops stream/timers and ignores stale callbacks', () => {
  const { source, stream, events, timers } = setup()
  stream.close(); source.message('200'); source.onerror()
  source.named('login-status', { message: 'stale' })
  assert.deepEqual(events, []); assert.equal(timers.size, 0); assert.equal(source.closed, true)
})
test('invalid relative QR becomes retryable failure instead of endless loading', () => {
  const { source, events } = setup()
  source.message('/connect/qrcode/test')
  assert.equal(events[0][0], 'error'); assert.equal(source.closed, true)
})
test('legacy terminal failure still works', () => {
  const { source, events } = setup()
  source.message('500')
  assert.equal(events[0][0], 'error'); assert.equal(source.closed, true)
})
test('legacy raw base64 remains supported', () => {
  const { source, events } = setup()
  source.message('A'.repeat(104))
  assert.equal(events[0][0], 'qr'); assert.match(events[0][1], /^data:image\/png;base64,/)
})
