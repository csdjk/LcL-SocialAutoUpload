"""Platform identities and backwards-compatible material views."""
import copy

CORE_PLATFORMS = ("bilibili", "douyin", "wechat_channels")
PLATFORMS = (*CORE_PLATFORMS, "youtube", "toutiao", "kuaishou", "xiaohongshu")
PLATFORM_TYPES = {"bilibili": 5, "douyin": 3, "wechat_channels": 2, "youtube": 6, "toutiao": 7, "kuaishou": 4, "xiaohongshu": 1}
PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "wechat_channels": "视频号", "youtube": "YouTube", "toutiao": "今日头条", "kuaishou": "快手", "xiaohongshu": "小红书"}


def material_for(package, platform):
    """New platforms can consume old editions without rewriting their manifest/hash."""
    if platform not in PLATFORMS:
        raise ValueError("不支持的投稿平台")
    materials = package.get("platforms", {})
    if platform not in materials and platform not in ("youtube", "toutiao", "kuaishou", "xiaohongshu"):
        raise ValueError(f"缺少 {platform} 文案")
    source = materials.get(platform) or materials["bilibili" if platform == "youtube" else "douyin"]
    result = copy.deepcopy(source)
    if platform in ("youtube", "toutiao") and platform not in materials:
        result.update(cover="landscape", cover_mode="custom")
        result.pop("category", None)
        result.pop("short_title", None)
    if platform == "youtube":
        result.setdefault("visibility", "public")
        result.setdefault("made_for_kids", None if package.get("kind") == "imported" else False)
    if platform in ("kuaishou", "xiaohongshu") and platform not in materials:
        result.update(cover="portrait", cover_mode="custom")
        for key in ("category", "short_title", "cover_landscape", "cover_portrait"):
            result.pop(key, None)
    return result


def package_view(package):
    return {**package, "platforms": {key: material_for(package, key) for key in PLATFORMS}}
