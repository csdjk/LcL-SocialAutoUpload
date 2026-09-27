import test from 'node:test'
import assert from 'node:assert/strict'
import { installWindowGestures } from '../src/utils/windowGestures.js'

const tick = () => new Promise(resolve => setImmediate(resolve))
function setup(overrides = {}) {
  const handlers = new Map(), calls = [], errors = []
  const doc = { addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener: name => handlers.delete(name) }
  let captured = false
  const target = { dataset: { windowResize: 'bottom-right' },
    setPointerCapture: () => { captured = true }, hasPointerCapture: () => captured,
    releasePointerCapture: () => { captured = false },
    closest: selector => selector.startsWith('[data-window') ? target : null }
  const api = {
    begin_window_gesture: async (...args) => { calls.push(['begin', ...args]); return 1 },
    update_window_gesture: async (...args) => calls.push(['update', ...args]),
    end_window_gesture: async (...args) => calls.push(['end', ...args]), ...overrides }
  const dispose = installWindowGestures(api, e => errors.push(e), doc)
  const send = (type, x, y, extra = {}) => handlers.get(type)?.({ type, pointerId: 7, button: 0, screenX: x, screenY: y, target, ...extra })
  return { calls, errors, send, dispose, handlers, target }
}

test('release flushes final pointer position before ending drag', async () => {
  const s = setup()
  await s.send('pointerdown',100,100)
  s.send('pointermove',140,120)
  s.send('pointerup',150,125)
  await tick()
  assert.deepEqual(s.calls,[['begin','bottom-right',100,100],['update',1,40,20],['update',1,50,25],['end',1]])
})

test('slow native bridge coalesces intermediate moves and never replays them after end', async () => {
  let resolveMove
  const updates=[]
  const s=setup({ update_window_gesture: async (...args) => {
    updates.push(args)
    if (updates.length===1) await new Promise(resolve=>{resolveMove=resolve})
  } })
  await s.send('pointerdown',0,0)
  s.send('pointermove',10,10)
  s.send('pointermove',20,20)
  s.send('pointermove',30,30)
  s.send('pointerup',40,40)
  resolveMove();await tick()
  assert.deepEqual(updates,[[1,10,10],[1,40,40]])
  assert.deepEqual(s.calls.at(-1),['end',1])
})

test('release before native begin resolves still ends without leaving a captured drag', async () => {
  let begin
  const s=setup({begin_window_gesture:()=>new Promise(resolve=>{begin=resolve})})
  s.send('pointerdown',10,10)
  s.send('pointerup',10,10)
  begin(4);await tick()
  assert.deepEqual(s.calls,[['end',4]])
})

test('buttons do not start window movement and teardown removes handlers', async () => {
  const s=setup()
  const button={closest:selector=>selector.startsWith('[data-window')?s.target:button}
  await s.send('pointerdown',0,0,{target:button})
  assert.deepEqual(s.calls,[])
  s.dispose();assert.equal(s.handlers.size,0)
})

test('small click movement does not move the window or prevent double click', async () => {
  const s=setup()
  await s.send('pointerdown',100,100)
  s.send('pointerup',101,101);await tick()
  assert.deepEqual(s.calls,[['begin','bottom-right',100,100],['end',1]])
})
