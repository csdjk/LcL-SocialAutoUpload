import test from 'node:test'
import assert from 'node:assert/strict'
import { taskBucket, taskTime, taskSource } from '../src/utils/taskPresentation.js'

test('uncertain and failed results remain attention items, never published', () => {
  for (const state of ['unknown', 'failed', 'needs_action']) assert.equal(taskBucket(state), 'attention')
  for (const state of ['queued', 'uploading', 'processing']) assert.equal(taskBucket(state), 'active')
  assert.equal(taskBucket('published'), 'published')
  assert.equal(taskBucket('unrecognized'), 'other')
})
test('timestamps display Beijing time across UTC date boundaries', () => {
  assert.equal(taskTime('2026-09-26T16:05:00Z'), '09/27 00:05')
  assert.equal(taskTime('2026-09-27T00:05:00+08:00'), '09/27 00:05')
  assert.equal(taskTime('invalid'), '时间待确认')
})
test('source labels explain known routes and preserve unknown source names', () => {
  assert.equal(taskSource('mcp'), 'Codex 投稿')
  assert.equal(taskSource('manual'), '手动投稿')
  assert.equal(taskSource('automation'), '自动排程')
  assert.equal(taskSource('legacy'), 'legacy')
})
