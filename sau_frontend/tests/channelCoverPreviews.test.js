import test from 'node:test'
import assert from 'node:assert/strict'
import { channelCoverPreviews } from '../src/utils/channelCoverPreviews.js'

const packageData = {
  platforms: { wechat_channels: { cover_landscape: 'landscape', cover_portrait: 'portrait' } },
  assets: { landscape: { path: 'cover-4x3.jpg' }, portrait: { path: 'cover-3x4.jpg' } }
}

test('custom Channels mode exposes BOTH distinct cover assets and correct ratios', () => {
  assert.deepEqual(channelCoverPreviews(packageData).map(x => [x.key, x.label, x.ratio, x.available]), [
    ['landscape', '4:3 横版封面', '4 / 3', true],
    ['portrait', '3:4 竖版封面', '3 / 4', true]
  ])
})

test('preview uses package mappings, matching uploader instead of a fixed portrait key', () => {
  const data = { platforms: { wechat_channels: { cover_landscape: 'horizontal-custom', cover_portrait: 'vertical-custom' } },
    assets: { 'horizontal-custom': { path: 'h.jpg' }, 'vertical-custom': { path: 'v.jpg' } } }
  assert.deepEqual(channelCoverPreviews(data).map(x => x.key), ['horizontal-custom', 'vertical-custom'])
  assert.ok(channelCoverPreviews(data).every(x => x.available))
})

test('missing horizontal image never silently reuses the vertical image', () => {
  const data = { ...packageData, assets: { portrait: { path: 'only-v.jpg' } } }
  const [horizontal, vertical] = channelCoverPreviews(data)
  assert.equal(horizontal.available, false)
  assert.equal(vertical.available, true)
  assert.notEqual(horizontal.key, vertical.key)
})

test('legacy mapping fallback remains deterministic without mutating draft or package', () => {
  const data = { assets: packageData.assets, platforms: { wechat_channels: { cover_mode: 'custom' } } }
  const before = JSON.stringify(data)
  assert.ok(channelCoverPreviews(data).every(x => x.available))
  assert.equal(JSON.stringify(data), before)
  assert.equal(data.platforms.wechat_channels.cover_mode, 'custom')
})

test('empty loading state and explicit invalid mapping do not claim covers exist', () => {
  assert.ok(channelCoverPreviews(null).every(x => !x.available))
  const data = { ...packageData, platforms: { wechat_channels: { cover_landscape: 'missing', cover_portrait: 'portrait' } } }
  assert.equal(channelCoverPreviews(data)[0].available, false)
})
