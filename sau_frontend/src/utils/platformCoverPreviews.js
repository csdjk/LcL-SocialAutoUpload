// Match the assets consumed by each uploader, including both Douyin covers.
export function platformCoverPreviews(packageData, platform, draft = {}) {
  const material = { ...packageData?.platforms?.[platform], ...draft }
  if (platform === 'wechat_channels' && material.cover_mode === 'video_frame') return []
  const dual = platform === 'wechat_channels' || platform === 'douyin'
  const keys = dual ? [material.cover_landscape || 'landscape', material.cover_portrait || 'portrait']
    : [material.cover || (platform === 'bilibili' ? 'bilibili' : ['kuaishou', 'xiaohongshu'].includes(platform) ? 'portrait' : 'landscape')]
  return keys.map((key, index) => {
    const asset = packageData?.assets?.[key]
    const dimensions = Number.isInteger(asset?.width) && Number.isInteger(asset?.height) && asset.width > 0 && asset.height > 0
    const portrait = dimensions ? asset.height > asset.width : dual ? index === 1 : key === 'portrait'
    const ratio = dimensions ? `${asset.width} / ${asset.height}` : portrait ? '3 / 4' : '4 / 3'
    const gcd = (a, b) => b ? gcd(b, a % b) : a
    const divisor = dimensions ? gcd(asset.width, asset.height) : 1
    const ratioLabel = dimensions ? `${asset.width / divisor}:${asset.height / divisor}` : portrait ? '3:4' : '4:3'
    return { key, orientation: portrait ? 'portrait' : 'landscape',
      ratio, label: `${ratioLabel} ${portrait ? '竖版' : '横版'}封面`,
      resolution: dimensions ? `${asset.width}×${asset.height}` : '',
      available: !!asset?.path }
  })
}
