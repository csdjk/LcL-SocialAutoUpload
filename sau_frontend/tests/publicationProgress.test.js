import test from 'node:test'
import assert from 'node:assert/strict'
import { elapsedText, progressModel } from '../src/utils/publicationProgress.js'

test('shows actual task phase and preserves the platform review boundary', () => {
  assert.deepEqual(progressModel('queued').phase, 0)
  assert.equal(progressModel('queued').mode, 'queued')
  assert.equal(progressModel('uploading', { stage: 'cover' }).phase, 2)
  assert.equal(progressModel('uploading', { stage: 'media_upload' }).phase, 1)
  assert.equal(progressModel('processing').mode, 'waiting')
  assert.equal(progressModel('published').mode, 'complete')
  assert.equal(progressModel('unknown').mode, 'attention')
  assert.equal(progressModel('failed', { stage: 'cover' }).detail, '停止于：设置封面')
})

test('elapsed time comes from task creation, not an invented upload percent', () => {
  assert.equal(elapsedText('2026-09-25T10:00:00+08:00', Date.parse('2026-09-25T10:01:09+08:00')), '1 分 09 秒')
  assert.equal(elapsedText('invalid'), '')
  assert.equal(progressModel('uploading', { stage: 'uploading' }).percent, null)
  assert.equal(progressModel('uploading', { stage: 'uploading', media_percent: 42 }).percent, 42)
  assert.equal(progressModel('uploading', { stage: 'cover', media_percent: 42 }).percent, null)
  assert.equal(progressModel('uploading', { stage: 'uploading', media_percent: 120 }).percent, null)
})
