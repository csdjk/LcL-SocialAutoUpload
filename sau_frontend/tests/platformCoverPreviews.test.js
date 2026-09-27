import test from 'node:test'
import assert from 'node:assert/strict'
import { platformCoverPreviews } from '../src/utils/platformCoverPreviews.js'

const pkg = { assets: { landscape: { path: 'wide.png' }, portrait: { path: 'tall.png' }, special: { path: 'chosen.png' } }, platforms: {} }
test('快手使用投稿草稿的封面资源，缺失时不允许预览', () => {
  assert.equal(platformCoverPreviews(pkg, 'kuaishou')[0].key, 'portrait')
  assert.equal(platformCoverPreviews(pkg, 'kuaishou', { cover: 'special' })[0].key, 'special')
  assert.equal(platformCoverPreviews(pkg, 'kuaishou', { cover: 'missing' })[0].available, false)
})
test('双封面与视频画面模式对应实际投稿资源', () => {
  for (const key of ['douyin', 'wechat_channels']) {
    assert.deepEqual(platformCoverPreviews(pkg, key).map(c => c.key), ['landscape', 'portrait'])
    assert.equal(platformCoverPreviews(pkg, key, { cover_landscape: 'special' })[0].key, 'special')
  }
  assert.deepEqual(platformCoverPreviews(pkg, 'wechat_channels', { cover_mode: 'video_frame' }), [])
})
test('新平台封面从交付元数据读取实际比例和分辨率', () => {
  const data = { assets: { youtube: { path:'yt.jpg', width:3840, height:2160 }, kuaishou: { path:'ks.jpg', width:1080, height:1440 } } }
  const yt = platformCoverPreviews(data, 'youtube', {cover:'youtube'})[0]
  assert.equal(yt.label, '16:9 横版封面')
  assert.equal(yt.resolution, '3840×2160')
  assert.equal(platformCoverPreviews(data, 'kuaishou', {cover:'kuaishou'})[0].orientation, 'portrait')
})
