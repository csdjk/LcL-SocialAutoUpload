import test from 'node:test'
import assert from 'node:assert/strict'
import { dailyAccountLabel, dailyAccessText, canPublishDailyPlatform, sortDailyPlatforms } from '../src/utils/dailyPublishState.js'
import { dailyTaskStatusText, dailyTaskNotice, dailyTaskNoticeType } from '../src/utils/dailyPublishState.js'

test('unpublished platforms precede confirmed publications without changing within-group order or the input', () => {
  const platforms = ['youtube', 'douyin', 'bilibili', 'wechat_channels', 'kuaishou', 'toutiao', 'xiaohongshu']
    .map(key => Object.freeze({ key }))
  Object.freeze(platforms)
  const status = {
    youtube: { state: 'published' }, douyin: { state: 'processing', remote_id: 'submitted' },
    bilibili: { state: 'failed' }, wechat_channels: { state: 'published' },
    kuaishou: { state: 'uploading' }, toutiao: { state: 'unknown', remote_verified: true }
  }
  assert.deepEqual(sortDailyPlatforms(platforms, status).map(item => item.key),
    ['douyin', 'bilibili', 'kuaishou', 'toutiao', 'xiaohongshu', 'youtube', 'wechat_channels'])
  assert.deepEqual(platforms.map(item => item.key),
    ['youtube', 'douyin', 'bilibili', 'wechat_channels', 'kuaishou', 'toutiao', 'xiaohongshu'])
})

test('refreshing a confirmed publication moves it behind pending platforms while preserving platform identity', () => {
  const platforms = [{ key: 'bilibili' }, { key: 'kuaishou' }, { key: 'youtube' }]
  const status = { bilibili: { state: 'ready' }, kuaishou: { state: 'queued' }, youtube: { state: 'published' } }
  assert.deepEqual(sortDailyPlatforms(platforms, status), platforms)
  status.bilibili.state = 'published'
  const ordered = sortDailyPlatforms(platforms, status)
  assert.deepEqual(ordered.map(item => item.key), ['kuaishou', 'bilibili', 'youtube'])
  assert.equal(ordered[0], platforms[1])
  assert.deepEqual(sortDailyPlatforms(platforms), platforms)
})

test('confirmed publication takes precedence over generic existing-work evidence', () => {
  const item = { state: 'published', remote_verified: true }
  assert.equal(dailyTaskStatusText(item), '已发布')
  assert.equal(dailyTaskNoticeType(item), 'success')
  assert.match(dailyTaskNotice(item), /已核对平台发布成功/)
  assert.equal(dailyTaskStatusText({ state: 'processing', remote_verified: true }), '后台已有作品')
})

const channels = { account: { web_account_id: 1, account_id: 'web:wechat_channels:1', display_name: '测试视频号', identity_source: 'tool' },
  state: 'ready', access_status: 'ready', account_mismatch: false, remote_id: null }

test('bound tool account is selectable without inventing a remote platform identity', () => {
  assert.equal(canPublishDailyPlatform('wechat_channels', channels), true)
  assert.equal(dailyAccountLabel('wechat_channels', channels), '工具账号：测试视频号')
  assert.equal(dailyAccessText(channels), '')
})

test('missing login, binding and explicit disabled states still block selection', () => {
  for (const access_status of ['needs_login', 'needs_account', 'cookie_missing', 'disabled', 'verification_required']) {
    const item = { ...channels, access_status }
    assert.equal(canPublishDailyPlatform('wechat_channels', item), false)
    assert.ok(dailyAccessText(item))
  }
  assert.equal(canPublishDailyPlatform('wechat_channels', { ...channels, account: {} }), false)
})

test('submitted, uncertain and other-account results remain blocked', () => {
  for (const state of ['uploading', 'processing', 'published', 'unknown', 'prepared', 'needs_action']) {
    assert.equal(canPublishDailyPlatform('wechat_channels', { ...channels, state }), false)
  }
  assert.equal(canPublishDailyPlatform('wechat_channels', { ...channels, account_mismatch: true }), false)
})

test('failed results require explicit verified remote absence before retry', () => {
  assert.equal(canPublishDailyPlatform('wechat_channels', { ...channels, state: 'failed' }), false)
  assert.equal(canPublishDailyPlatform('wechat_channels', { ...channels, state: 'failed', retry_allowed: true }), true)
  assert.equal(canPublishDailyPlatform('wechat_channels', { ...channels, state: 'failed', retry_allowed: true, remote_id: 'existing' }), false)
})

test('other platform aliases retain their selection behavior', () => {
  const item = { ...channels, account: { alias: 'test', account_id: 'known-id', display_name: '测试账号' } }
  assert.equal(canPublishDailyPlatform('douyin', item), true)
  assert.equal(dailyAccountLabel('douyin', item), '账号：测试账号')
  assert.equal(canPublishDailyPlatform('bilibili', { ...item, access_status: 'needs_login' }), false)
})
