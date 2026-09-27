"""Daily queue bridge to the existing Kuaishou browser uploader.

Returning to content management alone does not prove public publication.
The shared ledger retains an unknown result until it is explicitly reconciled.
"""
from uploader.ks_uploader.main import KSVideo, cookie_auth, normalize_topics


async def publish(video, cover, material, cookie, progress):
    from daily_publish import UploadNotStartedError
    try:
        description, tags = normalize_topics(material['description'], material.get('tags', []))
    except ValueError as exc:
        raise UploadNotStartedError(str(exc), 'invalid_topics', 'edit_material') from exc
    try:
        authenticated = await cookie_auth(str(cookie))
    except Exception as exc:
        raise UploadNotStartedError("快手登录检查未完成，尚未上传，请检查网络后重试", "login_check_unavailable") from exc
    if not authenticated:
        raise UploadNotStartedError("快手登录已失效，请到账号管理重新登录", "login_check_failed", "relogin")
    declaration = material.get("ai_declaration", "").strip()
    if declaration and declaration not in description:
        description += "\n" + declaration
    app = KSVideo(material["title"], str(video), tags, 0, str(cookie),
                  headless=False, thumbnail_path=str(cover), desc=description,
                  single_submission=True, progress_callback=progress)
    await app.main()
    from datetime import datetime, timezone, timedelta
    from pathlib import Path
    from publishing.browser_readback import readback
    return await readback('kuaishou', Path(cookie), material,
                          datetime.now(timezone(timedelta(hours=8))).date().isoformat())
