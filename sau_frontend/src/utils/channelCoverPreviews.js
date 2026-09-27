// Resolve the same immutable package asset keys used by the video uploader.
export function channelCoverPreviews(packageData) {
  const material = packageData?.platforms?.wechat_channels || {}
  const assets = packageData?.assets || {}
  return [
    { orientation: 'landscape', key: material.cover_landscape || 'landscape', label: '4:3 横版封面', ratio: '4 / 3' },
    { orientation: 'portrait', key: material.cover_portrait || 'portrait', label: '3:4 竖版封面', ratio: '3 / 4' }
  ].map(cover => ({ ...cover, available: Object.hasOwn(assets, cover.key) && !!assets[cover.key]?.path }))
}
