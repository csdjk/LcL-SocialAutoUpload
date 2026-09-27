"""Douyin's documented upload/create endpoints. No automatic POST retries.

AI labels and dual custom covers are intentionally not invented. The selector
uses the browser adapter when those capabilities are required by a package.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import requests

ORIGIN = "https://open.douyin.com"
UPLOAD = "/api/douyin/v1/video/upload_video/"
CREATE = "/api/douyin/v1/video/create_video/"
QUERY = "/api/douyin/v1/video/video_basic_info/"
MAX_SINGLE_UPLOAD_BYTES = 300_000_000


def read_credentials(path, account):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError()
        for key in ("access_token", "open_id", "expires_at"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError()
        expires = datetime.fromisoformat(data["expires_at"])
        if expires.tzinfo is None:
            raise ValueError()
    except (OSError, ValueError, TypeError):
        raise ValueError("抖音 API 授权文件无效，需要 access_token、open_id、带时区的 expires_at") from None
    if not account.get("open_id") or account["open_id"] != data["open_id"]:
        raise ValueError("抖音 API 授权账号与绑定的 open_id 不一致")
    if expires <= datetime.now(timezone.utc):
        raise ValueError("抖音 API 授权已过期，请更新授权文件")
    scopes = data.get("scopes", [])
    if not isinstance(scopes, list) or "video.create.bind" not in scopes:
        raise ValueError("抖音 API 授权缺少 video.create.bind 权限")
    return data


def _post(session, endpoint, credentials, **kwargs):
    try:
        response = session.post(ORIGIN + endpoint,
            params={"open_id": credentials["open_id"]},
            headers={"access-token": credentials["access_token"]},
            timeout=(15, 180), allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise RuntimeError(f"抖音 API HTTP {response.status_code}；请核对任务结果")
        body = response.json()
        data = body.get("data")
        if not isinstance(data, dict) or data.get("error_code") != 0:
            code = data.get("error_code") if isinstance(data, dict) else "invalid_response"
            # Do not put a response body/token/URL in logs or MCP responses.
            raise RuntimeError(f"抖音 API 返回错误码 {code}；请检查应用权限及授权")
        return data
    except (requests.RequestException, ValueError):
        raise RuntimeError("抖音 API 请求未得到有效回执；不会自动重试投稿") from None


def publish(video, material, credentials, progress):
    from uploader.tencent_uploader.flow import SubmissionNotStarted
    if material.get("ai_declaration") or material.get("cover_mode") != "video_frame":
        raise SubmissionNotStarted("API 通道无法完整表达此视频声明或封面", stage="preflight", upload_started=False)
    if Path(video).stat().st_size > MAX_SINGLE_UPLOAD_BYTES:
        raise SubmissionNotStarted("当前 API 通道单文件上限为 286.10 MiB（300000000 字节）", stage="preflight", upload_started=False)
    text = "\n".join([material["title"], material["description"],
                       " ".join("#" + tag.lstrip("#") for tag in material.get("tags", []))]).strip()
    if len(text) > 1000:
        raise SubmissionNotStarted("抖音 API 发布文案超过 1000 字", stage="preflight", upload_started=False)
    with requests.Session() as session:
        progress({"stage": "media_upload", "message": "正在通过官方 API 上传视频", "submission_started": False})
        try:
            with Path(video).open("rb") as stream:
                result = _post(session, UPLOAD, credentials, files={"video": (Path(video).name, stream, "video/mp4")})
            uploaded = result.get("video", {}).get("video_id")
            if not uploaded:
                raise ValueError("上传回执缺少 video_id")
        except Exception:
            raise SubmissionNotStarted("API 媒体上传未完成，尚未创建投稿", stage="media_upload", upload_started=True) from None
        progress({"stage": "submitting", "message": "正在提交官方 API 投稿请求", "submission_started": True})
        # After this boundary, even an HTTP timeout must remain unknown.
        result = _post(session, CREATE, credentials, json={"video_id": uploaded, "text": text, "private_status": 0})
        remote_id = result.get("video_id") or result.get("item_id")
        if not remote_id:
            raise RuntimeError("抖音已返回但缺少作品 ID，请核对平台内容管理")
        return {"status": "found", "remote_id": str(remote_id), "item_id": result.get("item_id"),
                "source": "official_api_receipt", "note": "官方创建视频接口返回作品 ID，仍需平台审核。"}


def readback(remote_id, credentials):
    if "posting.behavior" not in credentials.get("scopes", []):
        return {"status": "unavailable", "message": "自动回读需要 posting.behavior 权限；原投稿回执仍保留"}
    key = "video_ids" if str(remote_id).isdigit() else "item_ids"
    with requests.Session() as session:
        data = _post(session, QUERY, credentials, json={key: [remote_id]})
    rows = data.get("list", [])
    matched = [row for row in rows if str(row.get("video_id")) == remote_id or row.get("item_id") == remote_id]
    if len(matched) != 1:
        return {"status": "unavailable", "message": "未查到唯一匹配作品；保留原任务，不自动重发"}
    return {"status": "found", "remote_id": remote_id, "platform_status": matched[0].get("video_status"),
            "source": "official_api_query", "message": "官方接口已找到作品；未将状态码推断为公开发布"}
