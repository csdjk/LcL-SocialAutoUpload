"""Choose a supported delivery channel before reserving a publication.

Capability selection is local and never logs in or publishes. Configured API
failures are surfaced; a network failure must never trigger browser resubmission.
"""
import hashlib
import json
from pathlib import Path

LABELS = {"biliup_api": "B站接口 · biliup", "douyin_api": "抖音官方 API", "youtube_api": "YouTube 官方 API",
          "browser": "本机浏览器"}


def credentials_path(config, base):
    value = config.get("credentials_file")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("尚未配置抖音 API 授权文件")
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(base) / path


def resolve(platform, material, config, base):
    options = config.get("platforms", {}).get(platform, {})
    mode = options.get("transport", "auto")
    allowed = {"bilibili": {"auto", "biliup_api"},
               "douyin": {"auto", "douyin_api", "browser"},
               "wechat_channels": {"auto", "browser"}, "youtube": {"auto", "youtube_api"},
               "toutiao": {"auto", "browser"}, "kuaishou": {"auto", "browser"}, "xiaohongshu": {"auto", "browser"}}[platform]
    if mode not in allowed:
        raise ValueError(f"{platform} 不支持投稿通道 {mode}")
    reason = ""
    if platform == "bilibili":
        selected = "biliup_api"
        reason = "通过 biliup 调用 B站上传接口；不是开放平台应用授权。"
    elif platform == "youtube":
        from publishing.youtube_api import account_path, read_credentials
        youtube_channel = read_credentials(account_path(base, config.get('accounts', {}).get(platform, {})))['channel_id']
        selected = 'youtube_api'
        reason = '通过 Google 授权和 YouTube Data API 投稿；未审核的 API 项目上传受私享限制。'
    elif platform == "xiaohongshu":
        selected = "browser"
        reason = "使用小红书创作后台与自定义竖版封面；提交后回查原账号笔记状态。"
    elif platform == "kuaishou":
        selected = "browser"
        reason = "使用快手创作者后台和竖版封面；提交后回查原账号作品与审核状态。"
    elif platform == "toutiao":
        selected = "browser"
        reason = "使用头条号创作后台；登录或安全验证由本人完成。"
    elif platform == "wechat_channels":
        selected = "browser"
        reason = "当前适配官方创作者网页；未接入通用视频号投稿 API。"
    else:
        api = options.get("api", {})
        configured = bool(api.get("credentials_file"))
        limitation = ""
        if material.get("ai_declaration"):
            limitation = "当前已核实的官方 API 未提供此资源包所需的 AI 声明字段"
        elif material.get("cover_mode") != "video_frame":
            limitation = "官方 API 通道目前支持视频帧封面；自定义双封面使用浏览器"
        if mode == "douyin_api" and (not configured or limitation):
            raise ValueError(limitation or "请配置抖音 API 授权文件")
        if mode == "browser" or limitation or not configured:
            selected = "browser"
            reason = ("已明确选择浏览器。" if mode == "browser" else
                      (limitation + "，使用浏览器。" if limitation else "尚未配置官方 API 授权，使用浏览器。"))
        else:
            selected = "douyin_api"
            # A configured but broken credential is an error, not a fallback.
            from publishing.douyin_api import read_credentials
            account = config.get("accounts", {}).get(platform, {})
            read_credentials(credentials_path(api, base), account)
            reason = "应用需取得 video.create.bind 权限及目标账号授权。"
    # Freeze account binding and delivery settings, never credentials themselves.
    snapshot = {"platform": platform, "transport": selected,
                "account": config.get("accounts", {}).get(platform, {}), "options": options}
    if platform == 'youtube':
        snapshot['youtube_channel'] = youtube_channel
    fingerprint = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"id": selected, "label": LABELS[selected], "reason": reason, "fingerprint": fingerprint}
