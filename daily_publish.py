"""AI 日报资源包、发布预约与统一任务账本。自动化和 Web 共用此入口。"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
import uuid
import hashlib

from conf import BASE_DIR

PLATFORMS = ("bilibili", "douyin", "wechat_channels")
BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
ACTIVE = {"uploading", "needs_action", "processing", "published", "unknown"}
DB_PATH = Path(BASE_DIR) / "db" / "daily-jobs.db"
SETTINGS_PATH = Path(BASE_DIR) / "db" / "daily-settings.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def settings() -> dict:
    if not SETTINGS_PATH.is_file():
        return {"output_root": "", "accounts": {}, "platforms": {}}
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def output_root() -> Path:
    value = settings().get("output_root")
    if not value:
        raise ValueError("请先在 db/daily-settings.json 配置 output_root")
    root = Path(value).resolve()
    if not root.is_dir():
        raise ValueError("每日资源目录不存在")
    return root


def load_package(package_path: Path, root: Path | None = None) -> dict:
    root = (root or output_root()).resolve()
    path = package_path.resolve()
    if not path.is_relative_to(root) or not re.fullmatch(r"r\d{3}", path.parent.name):
        raise ValueError("资源包不在配置的正式版本目录")
    if path.name != "package.json" or not path.is_file():
        raise ValueError("缺少 package.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("timezone") != "Asia/Shanghai":
        raise ValueError("资源包版本或时区不受支持")
    if data.get("date") != path.parent.parent.name or data.get("edition_id") != data["date"] or data.get("revision") != path.parent.name:
        raise ValueError("资源包期次、日期或版本不一致")
    if not data.get("production", {}).get("technical_passed") or not data.get("cover_review"):
        raise ValueError("资源包没有通过完整检查")
    production = data["production"]
    for kind in ("editorial", "visual"):
        review = production.get(f"{kind}_review", {})
        if review.get("status") != "pass" or review.get("input_hash") != production.get("input_hash"):
            raise ValueError(f"{kind} 复核不匹配")
    if production["visual_review"].get("video_hash") != production.get("video_hash"):
        raise ValueError("视觉复核与成片哈希不匹配")
    assets = data.get("assets")
    if not isinstance(assets, dict) or not all(key in assets for key in ("video", "subtitles", "chapters", "sources", "bilibili", "landscape", "portrait")):
        raise ValueError("资源包文件清单不完整")
    for item in assets.values():
        relative = item["path"]
        asset = (path.parent / relative).resolve()
        if Path(relative).is_absolute() or not asset.is_relative_to(path.parent) or not asset.is_file():
            raise ValueError(f"资源路径无效：{relative}")
        if asset.stat().st_size != item["bytes"] or _sha(asset) != item["sha256"]:
            raise ValueError(f"资源哈希或大小不一致：{relative}")
    if assets["video"]["sha256"] != data["production"].get("video_hash"):
        raise ValueError("成片哈希与质检记录不一致")
    for name in PLATFORMS:
        item = data.get("platforms", {}).get(name)
        if not item or not item.get("title") or not item.get("description"):
            raise ValueError(f"缺少 {name} 文案")
        for cover_key in ("cover", "cover_landscape", "cover_portrait"):
            if cover_key in item and item[cover_key] not in assets:
                raise ValueError(f"{name} 封面映射无效")
    return data


def find_today(now: datetime | None = None) -> tuple[Path | None, dict | None, list[str]]:
    root = output_root()
    today = (now or datetime.now(BEIJING)).astimezone(BEIJING).date().isoformat()
    folder = root / today
    errors = []
    for path in sorted(folder.glob("r[0-9][0-9][0-9]/package.json"), reverse=True):
        try:
            return path, load_package(path, root), errors
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append(f"{path.parent.name}: {exc}")
    return None, None, errors


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS attempts (
        id TEXT PRIMARY KEY, edition_id TEXT NOT NULL, platform TEXT NOT NULL,
        account_id TEXT NOT NULL, package_path TEXT NOT NULL, revision TEXT NOT NULL,
        source TEXT NOT NULL, state TEXT NOT NULL, payload TEXT NOT NULL,
        remote_id TEXT, url TEXT, evidence TEXT, error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS attempts_lookup ON attempts(edition_id,platform,account_id,created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS drafts (
        package_path TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY(package_path,platform,account_id))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS legacy (
        edition_day TEXT NOT NULL, platform TEXT NOT NULL, account_id TEXT NOT NULL,
        state TEXT NOT NULL, remote_id TEXT, evidence TEXT NOT NULL,
        PRIMARY KEY(edition_day,platform,account_id))""")
    return conn


@contextmanager
def _connection():
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def import_legacy(path: Path):
    if not path.is_file():
        return 0
    entries = json.loads(path.read_text(encoding="utf-8"))
    priority = {"published": 6, "processing": 5, "uploading": 4, "unknown": 4, "prepared": 2, "failed": 1}
    merged = {}
    for key, item in entries.items():
        day = str(item.get("edition_id") or key)[:10]
        platform, account = item.get("platform"), item.get("account_id")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or platform not in PLATFORMS or not account:
            continue
        identity = (day, platform, str(account))
        current = merged.get(identity)
        if current and current.get("remote_id") and item.get("remote_id") and current["remote_id"] != item["remote_id"]:
            merged[identity] = {"state": "unknown", "remote_id": None,
                                "conflict_remote_ids": sorted({current["remote_id"], item["remote_id"]})}
            continue
        if current and current.get("conflict_remote_ids"):
            continue
        if current is None or priority.get(item.get("state"), 3) > priority.get(current.get("state"), 3):
            merged[identity] = item
    with _connection() as conn:
        for (day, platform, account), item in merged.items():
            state = item.get("state", "unknown")
            if state == "failed" and not item.get("remote_absent"):
                state = "unknown"
            conn.execute("""INSERT INTO legacy VALUES(?,?,?,?,?,?)
                ON CONFLICT(edition_day,platform,account_id) DO UPDATE SET
                state=excluded.state,remote_id=excluded.remote_id,evidence=excluded.evidence""",
                (day, platform, account, state, item.get("remote_id"), json.dumps(item, ensure_ascii=False)))
    return len(merged)


def _account(platform: str) -> dict:
    account = settings().get("accounts", {}).get(platform, {})
    if not account.get("account_id") or not (account.get("alias") or (platform == "wechat_channels" and account.get("web_account_id"))):
        raise ValueError(f"{platform} 尚未配置登录账号和已核实账号 ID")
    if account.get("alias") and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(account["alias"])):
        raise ValueError(f"{platform} 账号别名格式无效")
    return account


def _web_channels_cookie(account: dict) -> Path | None:
    web_id = account.get("web_account_id")
    if web_id is None:
        return None
    if not str(web_id).isdigit():
        raise ValueError("视频号工具账号记录 ID 无效")
    database = Path(BASE_DIR) / "db" / "database.db"
    if not database.is_file():
        raise ValueError("视频号工具账号库不存在")
    with closing(sqlite3.connect(database)) as conn:
        row = conn.execute("SELECT type,filePath,status FROM user_info WHERE id=?", (int(web_id),)).fetchone()
    if not row or row[0] != 2 or row[2] != 1:
        raise ValueError("视频号工具账号不存在或登录状态未通过")
    base = (Path(BASE_DIR) / "cookiesFile").resolve()
    cookie = (base / row[1]).resolve()
    if not cookie.is_relative_to(base) or not cookie.is_file():
        raise ValueError("视频号工具账号登录文件缺失或路径无效")
    return cookie


def _channels_access(account_id: str) -> str:
    source = output_root().parent / "publishing.json"
    if not source.is_file():
        return "publishing_config_missing"
    target = json.loads(source.read_text(encoding="utf-8")).get("platforms", {}).get("wechat_channels", {})
    if target.get("access_status") != "ready":
        return target.get("access_status") or "access_not_ready"
    if not target.get("enabled") or str(target.get("account_id")) != account_id or not target.get("verified_on"):
        return "account_not_verified"
    return "ready"


def _latest(conn, edition: str, platform: str, account_id: str):
    return conn.execute("SELECT * FROM attempts WHERE edition_id=? AND platform=? AND account_id=? ORDER BY created_at DESC LIMIT 1",
                        (edition, platform, account_id)).fetchone()


def recover_stale_uploads(now: datetime | None = None) -> int:
    """进程中断后保留不确定结果；超时任务绝不自动重发。"""
    cutoff = ((now or datetime.now(BEIJING)).astimezone(BEIJING) - timedelta(hours=6)).isoformat()
    with _connection() as conn:
        result = conn.execute("""UPDATE attempts SET state='unknown',
            error='上传进程已超过六小时未回报；先核对平台远端结果',updated_at=?
            WHERE state='uploading' AND updated_at<?""", ((now or datetime.now(BEIJING)).isoformat(), cutoff))
        return result.rowcount


def status_for(package: dict) -> dict:
    recover_stale_uploads()
    result = {}
    config = settings()
    with _connection() as conn:
        for platform in PLATFORMS:
            account = config.get("accounts", {}).get(platform, {})
            aid = str(account.get("account_id", ""))
            attempt = _latest(conn, package["edition_id"], platform, aid) if aid else None
            legacy = conn.execute("SELECT * FROM legacy WHERE edition_day=? AND platform=? AND account_id=?",
                                  (package["date"], platform, aid)).fetchone() if aid else None
            if not attempt:
                attempt = conn.execute("SELECT * FROM attempts WHERE edition_id=? AND platform=? ORDER BY created_at DESC LIMIT 1",
                                       (package["edition_id"], platform)).fetchone()
            if not legacy:
                legacy = conn.execute("SELECT * FROM legacy WHERE edition_day=? AND platform=? LIMIT 1",
                                      (package["date"], platform)).fetchone()
            row = attempt or legacy
            access = config.get("platforms", {}).get(platform, {}).get("access_status", "ready")
            if platform == "wechat_channels":
                remote_access = _channels_access(aid)
                if remote_access != "ready":
                    access = remote_access
            result[platform] = {"account": account, "state": row["state"] if row else "ready",
                                "remote_id": row["remote_id"] if row else None,
                                "task_id": attempt["id"] if attempt else None,
                                "account_mismatch": bool(row and row["account_id"] != aid),
                                "access_status": access}
    return result


def get_draft(path: Path, platform: str, account_id: str):
    with _connection() as conn:
        row = conn.execute("SELECT payload FROM drafts WHERE package_path=? AND platform=? AND account_id=?",
                           (str(path.resolve()), platform, account_id)).fetchone()
        return json.loads(row["payload"]) if row else None


def save_draft(path: Path, platform: str, account_id: str, payload: dict):
    load_package(path)
    if platform not in PLATFORMS or not isinstance(payload, dict):
        raise ValueError("草稿平台或格式无效")
    if account_id != str(_account(platform)["account_id"]):
        raise ValueError("草稿账号不匹配")
    allowed = {"title", "description", "tags", "short_title", "category", "ai_declaration"}
    clean = {key: value for key, value in payload.items() if key in allowed}
    if not clean.get("title") or not clean.get("description") or not isinstance(clean.get("tags", []), list):
        raise ValueError("草稿标题、简介或话题无效")
    with _connection() as conn:
        conn.execute("INSERT OR REPLACE INTO drafts VALUES(?,?,?,?)",
                     (str(path.resolve()), platform, account_id, json.dumps(clean, ensure_ascii=False)))
    return clean


def reserve(path: Path, platform: str, source: str, payload: dict | None = None) -> str:
    if source not in {"manual", "automation"} or platform not in PLATFORMS:
        raise ValueError("发布来源或平台无效")
    package = load_package(path)
    if package["date"] != datetime.now(BEIJING).date().isoformat():
        raise ValueError("只能提交北京时间当天的资源包")
    import_legacy(output_root().parent / "publications.json")
    config = settings()
    if platform == "wechat_channels" and source != "manual":
        raise ValueError("视频号仅允许工具内手动发布")
    if config.get("platforms", {}).get(platform, {}).get("access_status", "ready") != "ready":
        raise ValueError(f"{platform} 的访问状态尚未就绪")
    account = _account(platform)
    aid = str(account["account_id"])
    if platform == "wechat_channels" and _channels_access(aid) != "ready":
        raise ValueError("视频号工作区访问或账号核验尚未就绪")
    cookie_platform = "tencent" if platform == "wechat_channels" else platform
    cookie = _web_channels_cookie(account) if platform == "wechat_channels" else None
    if cookie is None:
        cookie = Path(BASE_DIR) / "cookies" / f"{cookie_platform}_{account['alias']}.json"
    if not cookie.is_file():
        raise ValueError(f"{platform} 账号登录文件缺失，请先登录并核实账号")
    material = dict(package["platforms"][platform])
    if source == "manual":
        material.update(payload or get_draft(path, platform, aid) or {})
    if not material.get("title") or not material.get("description"):
        raise ValueError("发布文案不完整")
    if package["platforms"][platform].get("ai_declaration") and not material.get("ai_declaration"):
        raise ValueError("该资源包要求填写平台 AI 内容声明")
    if platform == "douyin" and len(material["title"]) > 30:
        raise ValueError("抖音标题超过 30 字")
    if platform == "bilibili":
        category = material.get("category") or config.get("platforms", {}).get(platform, {}).get("category")
        if not str(category or "").isdigit() or int(category) <= 0:
            raise ValueError("B站分区 ID 尚未正确配置")
    if platform == "bilibili" and package["platforms"][platform].get("ai_declaration") and not config.get("platforms", {}).get(platform, {}).get("ai_declaration_fields"):
        raise ValueError("B站 AI 声明提交字段尚未按当前平台验收")
    job_id = str(uuid.uuid4())
    now = datetime.now(BEIJING).isoformat()
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        legacy = conn.execute("SELECT * FROM legacy WHERE edition_day=? AND platform=? AND account_id=?",
                              (package["date"], platform, aid)).fetchone()
        if legacy and (legacy["state"] != "failed" or legacy["remote_id"] or
                       not json.loads(legacy["evidence"]).get("remote_absent")):
            raise ValueError(f"历史账本已有 {platform} {legacy['state']}，先核对远端")
        old = _latest(conn, package["edition_id"], platform, aid)
        if old and (old["state"] != "failed" or old["remote_id"] or
                    not json.loads(old["evidence"] or "{}").get("remote_absent")):
            raise ValueError(f"该期 {platform} 已预约或提交：{old['state']}")
        other_legacy = conn.execute("SELECT * FROM legacy WHERE edition_day=? AND platform=? AND account_id<>? LIMIT 1",
                                    (package["date"], platform, aid)).fetchone()
        other_attempt = conn.execute("SELECT * FROM attempts WHERE edition_id=? AND platform=? AND account_id<>? LIMIT 1",
                                     (package["edition_id"], platform, aid)).fetchone()
        if other_legacy or other_attempt:
            raise ValueError(f"{platform} 已有其他账号的历史投稿或任务，先核对远端")
        conn.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (job_id, package["edition_id"], platform, aid, str(path.resolve()), package["revision"], source,
                      "uploading", json.dumps(material, ensure_ascii=False), None, None, None, None, now, now))
    return job_id


def task(job_id: str) -> dict:
    with _connection() as conn:
        row = conn.execute("SELECT * FROM attempts WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("发布任务不存在")
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["evidence"] = json.loads(result["evidence"]) if result["evidence"] else None
        return result


def _state(job_id: str, state: str, error: str | None = None, evidence: dict | None = None):
    now = datetime.now(BEIJING).isoformat()
    with _connection() as conn:
        conn.execute("UPDATE attempts SET state=?,error=?,evidence=?,remote_id=?,url=?,updated_at=? WHERE id=?",
                     (state, error, json.dumps(evidence, ensure_ascii=False) if evidence else None,
                      evidence.get("remote_id") if evidence else None, evidence.get("url") if evidence else None, now, job_id))


def reconcile(job_id: str, state: str, evidence: dict):
    current = task(job_id)
    if state not in {"processing", "published", "failed", "unknown", "needs_action"}:
        raise ValueError("不支持的核对状态")
    if current["state"] == "published" and state != "published":
        raise ValueError("已发布状态不能回退")
    if evidence.get("platform") != current["platform"] or str(evidence.get("account_id")) != current["account_id"] or not evidence.get("note"):
        raise ValueError("远端证据的平台、账号或说明不完整")
    try:
        if datetime.fromisoformat(evidence["checked_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("远端核对时间必须带时区") from None
    if state in {"processing", "published"} and not evidence.get("remote_id"):
        raise ValueError("已提交或已发布需要真实作品 ID")
    if state == "published" and not str(evidence.get("url", "")).startswith("https://"):
        raise ValueError("已发布需要实际作品链接")
    if state == "failed" and (evidence.get("remote_absent") is not True or evidence.get("remote_id")):
        raise ValueError("确认失败需核对远端确无作品，并记录 remote_absent=true")
    _state(job_id, state, evidence=evidence)
    return task(job_id)


def _upload(job: dict, package: dict):
    from sau_cli import (BilibiliVideoUploadRequest, DouyinVideoUploadRequest,
                         TencentVideoUploadRequest, upload_bilibili_video, upload_video, upload_tencent_video)
    root = Path(job["package_path"]).parent
    assets = package["assets"]
    material = job["payload"]
    account_config = _account(job["platform"])
    account = account_config.get("alias")
    video = root / assets["video"]["path"]
    tags = material.get("tags", [])
    if job["platform"] == "bilibili":
        bili_config = settings().get("platforms", {}).get("bilibili", {})
        category = material.get("category") or bili_config.get("category")
        request = BilibiliVideoUploadRequest(account, video, material["title"], material["description"], int(category),
                                             tags, 0, root / assets[material["cover"]]["path"],
                                             extra_fields=bili_config.get("ai_declaration_fields"))
        asyncio.run(upload_bilibili_video(request))
    elif job["platform"] == "douyin":
        request = DouyinVideoUploadRequest(account, video, material["title"], material["description"], tags, 0,
                                           thumbnail_landscape_file=root / assets[material["cover_landscape"]]["path"],
                                           thumbnail_portrait_file=root / assets[material["cover_portrait"]]["path"],
                                           declaration=material.get("ai_declaration"), headless=False)
        asyncio.run(upload_video(request))
    else:
        landscape = root / assets[material["cover_landscape"]]["path"]
        portrait = root / assets[material["cover_portrait"]]["path"]
        web_cookie = _web_channels_cookie(account_config)
        if web_cookie:
            from uploader.tencent_uploader.main import TencentVideo, cookie_auth
            async def upload_web_account():
                if not await cookie_auth(str(web_cookie)):
                    raise ValueError("视频号工具账号登录已失效")
                app = TencentVideo(title=material["title"], file_path=str(video), tags=tags,
                                   publish_date=0, account_file=str(web_cookie), desc=material["description"],
                                   thumbnail_landscape_path=str(landscape), thumbnail_portrait_path=str(portrait),
                                   short_title=material.get("short_title"), headless=False,
                                   require_content_label=True, require_thumbnail=True)
                await app.tencent_upload_video()
            asyncio.run(upload_web_account())
        else:
            request = TencentVideoUploadRequest(account, video, material["title"], material["description"], tags, 0,
                                                thumbnail_landscape_file=landscape, thumbnail_portrait_file=portrait,
                                                short_title=material.get("short_title"), headless=False,
                                                require_content_label=True, require_thumbnail=True)
            asyncio.run(upload_tencent_video(request))


def run(job_id: str):
    current = task(job_id)
    if current["state"] != "uploading":
        raise ValueError("任务已执行或需要先核对，不能重发")
    try:
        package = load_package(Path(current["package_path"]))
        _upload(current, package)
    except Exception as exc:
        # 浏览器在提交前后都可能抛错；统一保留未知状态，避免自动重试造成重复投稿。
        _state(job_id, "unknown", error=str(exc))
        return task(job_id)
    _state(job_id, "unknown", error="上传器已返回；需从平台内容管理回读作品 ID 与审核状态")
    return task(job_id)


def submit(path: Path, platforms: list[str], source: str, synchronous: bool = False) -> dict:
    jobs, errors = [], {}
    for platform in dict.fromkeys(platforms):
        try:
            jobs.append(reserve(path, platform, source))
        except ValueError as exc:
            errors[platform] = str(exc)
    if jobs:
        def worker():
            for job_id in jobs:
                run(job_id)
        if synchronous:
            worker()
        else:
            threading.Thread(target=worker, daemon=True, name="daily-publish").start()
    return {"jobs": jobs, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="AI 日报统一发布账本")
    parser.add_argument("command", choices=["today", "import-legacy", "submit", "task", "reconcile"])
    parser.add_argument("--package")
    parser.add_argument("--platforms", default="bilibili,douyin")
    parser.add_argument("--task-id")
    parser.add_argument("--state")
    parser.add_argument("--evidence-file")
    args = parser.parse_args()
    if args.command == "today":
        path, package, errors = find_today()
        result = {"package": str(path) if path else None, "data": package, "errors": errors,
                  "status": status_for(package) if package else {}}
    elif args.command == "import-legacy":
        result = {"imported": import_legacy(output_root().parent / "publications.json")}
    elif args.command == "submit":
        if not args.package:
            parser.error("--package is required")
        result = submit(Path(args.package), args.platforms.split(","), "automation", synchronous=True)
        result["results"] = [task(job_id) for job_id in result["jobs"]]
    elif args.command == "reconcile":
        if not args.task_id or not args.state or not args.evidence_file:
            parser.error("--task-id, --state and --evidence-file are required")
        result = reconcile(args.task_id, args.state, json.loads(Path(args.evidence_file).read_text(encoding="utf-8")))
    else:
        if not args.task_id:
            parser.error("--task-id is required")
        result = task(args.task_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "submit" and (result["errors"] or any(row["state"] == "unknown" for row in result["results"])):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
