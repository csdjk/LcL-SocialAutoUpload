// Capture the pointer throughout a drag. Coalesce moves while the native bridge
// is busy so a slow response cannot replay old geometry after the pointer stops.
export function installWindowGestures(api, onError, doc = document) {
  let active = null
  async function flush(gesture) {
    if (!gesture.ready || gesture.busy) return
    gesture.busy = true
    try {
      while (gesture.pending) {
        const delta = gesture.pending
        gesture.pending = null
        await api.update_window_gesture(gesture.id, ...delta)
      }
      if (gesture.ended) await api.end_window_gesture(gesture.id)
    } catch (error) {
      gesture.ended = true
      gesture.pending = null
      onError(error)
    } finally { gesture.busy = false }
  }
  function move(event) {
    const gesture = active
    if (!gesture || event.pointerId !== gesture.pointerId) return
    const delta = [event.screenX - gesture.x, event.screenY - gesture.y]
    if (Math.max(...delta.map(Math.abs)) >= 3 || gesture.moved) {
      gesture.moved = true
      gesture.pending = delta
      flush(gesture)
    }
  }
  function end(event) {
    if (!active || event.pointerId !== active.pointerId) return
    if (event.type === 'pointerup') move(event)
    const gesture = active
    active = null
    gesture.ended = true
    if (gesture.target.hasPointerCapture(event.pointerId)) gesture.target.releasePointerCapture(event.pointerId)
    flush(gesture)
  }
  async function begin(event) {
    if (event.button !== 0 || active) return
    const target = event.target.closest('[data-window-resize], [data-window-drag]')
    if (!target || event.target.closest('button, input, select, textarea, a, [role="button"]')) return
    const gesture = { target, pointerId: event.pointerId, x: event.screenX, y: event.screenY,
                      ready: false, busy: false, ended: false, pending: null, moved: false }
    active = gesture
    target.setPointerCapture(event.pointerId)
    try {
      gesture.id = await api.begin_window_gesture(target.dataset.windowResize || 'move', event.screenX, event.screenY)
      if (gesture.id == null) { end(event); return }
      gesture.ready = true
      flush(gesture)
    } catch (error) { end(event); onError(error) }
  }
  doc.addEventListener('pointerdown', begin)
  doc.addEventListener('pointermove', move)
  doc.addEventListener('pointerup', end)
  doc.addEventListener('pointercancel', end)
  doc.addEventListener('lostpointercapture', end)
  return () => {
    if (active) end({ pointerId: active.pointerId })
    doc.removeEventListener('pointerdown', begin)
    doc.removeEventListener('pointermove', move)
    doc.removeEventListener('pointerup', end)
    doc.removeEventListener('pointercancel', end)
    doc.removeEventListener('lostpointercapture', end)
  }
}
