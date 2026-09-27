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
from uploader.tencent_uploader.flow import SubmissionNotStarted

from publishing.platforms import CORE_PLATFORMS, PLATFORMS, PLATFORM_TYPES, PLATFORM_NAMES, material_for
from publishing.runtime_version import require_current
BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")
ACTIVE = {"queued", "uploading", "needs_action", "processing", "published", "unknown"}
DB_PATH = Path(BASE_DIR) / "db" / "daily-jobs.db"
SETTINGS_PATH = Path(BASE_DIR) / "db" / "daily-settings.json"


def package_digest(package):
    return hashlib.sha256(json.dumps(package, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class AccountBindingMissingError(ValueError):
    """A deleted/wrong-platform binding is not a failed login of a new account."""


class UploadNotStartedError(ValueError):
    """Raised only at explicit checkpoints BEFORE invoking an uploader.

    Never classify an arbitrary uploader exception by matching its message:
    after upload starts, an exception still leaves the remote result unknown.
    """
    def __init__(self, message: str, code: str = "preflight_failed", action: str = "retry"):
        super().__init__(message)
        self.code = code
        self.action = action


def _mark_channels_needs_login(account: dict, cookie: Path) -> None:
    # A concurrent re-login changes filePath. Never invalidate that newer login.
    web_id = account.get("web_account_id")
    if web_id is None:
        return
    with closing(sqlite3.connect(Path(BASE_DIR) / "db/database.db")) as conn:
        conn.execute("UPDATE user_info SET status=0 WHERE id=? AND type=2 AND filePath=?",
                     (int(web_id), cookie.name))
        conn.commit()


def _task_failure_details(attempt) -> dict:
    if attempt is None:
        return {"error": None, "failure_stage": None, "failure_code": None,
                "upload_started": None, "next_action": None}
    try:
        evidence = json.loads(attempt["evidence"] or "{}")
        if not isinstance(evidence, dict):
            evidence = {}
    except (ValueError, TypeError):
        evidence = {}
    return {"error": attempt["error"], "failure_stage": evidence.get("stage"),
            "failure_code": evidence.get("failure_code"),
            "upload_started": evidence.get("upload_started"),
            "next_action": evidence.get("next_action"),
            "submission_started": evidence.get("submission_started"),
            "progress_stage": evidence.get("stage"), "progress_message": evidence.get("message"),
            "media_percent": evidence.get("media_percent"),
            "warnings": evidence.get("warnings", []), "remote_verified": evidence.get("source") == "official_content_list"}


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
    path = package_path.resolve()
    imports_root = (Path(BASE_DIR) / "db" / "imports").resolve()
    imported = path.is_relative_to(imports_root) and path.parent.parent == imports_root and bool(re.fullmatch(r"[0-9a-f-]{36}", path.parent.name))
    if not imported:
        root = (root or output_root()).resolve()
        if not path.is_relative_to(root) or not re.fullmatch(r"r\d{3}", path.parent.name):
            raise ValueError("资源包不在配置的正式版本目录")
    if path.name != "package.json" or not path.is_file():
        raise ValueError("缺少 package.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != (2 if imported else 1) or data.get("timezone") != "Asia/Shanghai":
        raise ValueError("资源包版本或时区不受支持")
    if imported:
        if data.get("kind") != "imported" or data.get("edition_id") != path.parent.name or data.get("revision") != "r001":
            raise ValueError("导入视频的内容身份不匹配")
    else:
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
    required = ("video", "bilibili", "landscape", "portrait") if imported else ("video", "subtitles", "chapters", "sources", "bilibili", "landscape", "portrait")
    if not isinstance(assets, dict) or not all(key in assets for key in required):
        raise ValueError("资源包文件清单不完整")
    for item in assets.values():
        relative = item["path"]
        asset = (path.parent / relative).resolve()
        if Path(relative).is_absolute() or not asset.is_relative_to(path.parent) or not asset.is_file():
            raise ValueError(f"资源路径无效：{relative}")
        if asset.stat().st_size != item["bytes"] or _sha(asset) != item["sha256"]:
            raise ValueError(f"资源哈希或大小不一致：{relative}")
    if not imported and assets["video"]["sha256"] != data["production"].get("video_hash"):
        raise ValueError("成片哈希与质检记录不一致")
    for name in dict.fromkeys((*CORE_PLATFORMS, *data.get("platforms", {}))):
        item = data.get("platforms", {}).get(name)
        if not item or not item.get("title") or not item.get("description"):
            raise ValueError(f"缺少 {name} 文案")
        for cover_key in ("cover", "cover_landscape", "cover_portrait"):
            if cover_key in item and item[cover_key] not in assets:
                raise ValueError(f"{name} 封面映射无效")
    from publishing.cover_assets import validate_cover_contract
    validate_cover_contract(data, path.parent)
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


_today_cache = None
_today_cache_lock = threading.Lock()


def find_today_cached():
    """Only the read-only dashboard caches verification. reserve() always rehashes."""
    import copy
    import time
    global _today_cache
    root = output_root()
    day = datetime.now(BEIJING).date().isoformat()
    def signature(path):
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    manifests = tuple(signature(p) for p in sorted((root/day).glob('r[0-9][0-9][0-9]/package.json')))
    with _today_cache_lock:
        if _today_cache:
            cached_at, old_manifests, result, assets = _today_cache
            try:
                fresh = time.monotonic() - cached_at < 30 and manifests == old_manifests and all(signature(Path(item[0])) == item for item in assets)
            except OSError:
                fresh = False
            if fresh:
                return copy.deepcopy(result)
        result = find_today()
        path, package, _ = result
        assets = [signature(path.parent/item['path']) for item in package['assets'].values()] if path and package else []
        _today_cache = (time.monotonic(), manifests, copy.deepcopy(result), assets)
        return result


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
    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_queue (
        job_id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at TEXT NOT NULL,
        available_at TEXT)""")
    if "available_at" not in {row[1] for row in conn.execute("PRAGMA table_info(delivery_queue)")}:
        conn.execute("ALTER TABLE delivery_queue ADD COLUMN available_at TEXT")
    conn.execute("CREATE TABLE IF NOT EXISTS run_claims(job_id TEXT PRIMARY KEY, claimed_at TEXT NOT NULL)")
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


def _configured_account(platform: str, config: dict) -> dict:
    """Resolve a local binding without inventing a remote platform identity.

    An explicit account_id keeps its existing ledger/draft identity. A Web-only
    account uses a stable, namespaced tool record key, including across re-login.
    """
    account = dict(config.get("accounts", {}).get(platform, {}))
    if platform in PLATFORMS and account.get("web_account_id") is not None:
        web_id = account["web_account_id"]
        if not isinstance(web_id, bool) and str(web_id).isdigit() and int(web_id) > 0:
            account["web_account_id"] = int(web_id)
            if not account.get("account_id"):
                account["account_id"] = f"web:{platform}:{int(web_id)}"
                account["identity_source"] = "tool"
            if not account.get("display_name"):
                account["display_name"] = f"工具账号 #{int(web_id)}"
    return account


def _account(platform: str) -> dict:
    account = _configured_account(platform, settings())
    if not account.get("account_id") or not (account.get("alias") or (platform == "douyin" and account.get("open_id")) or account.get("web_account_id")):
        if platform == "wechat_channels":
            raise ValueError("请先绑定视频号工具账号，或配置 CLI 账号及账号 ID")
        raise ValueError(f"{platform} 尚未配置登录账号和已核实账号 ID")
    if account.get("alias") and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(account["alias"])):
        raise ValueError(f"{platform} 账号别名格式无效")
    return account


def _web_channels_cookie(account: dict) -> Path | None:
    return _web_account_cookie("wechat_channels", account)


def _web_account_cookie(platform: str, account: dict) -> Path | None:
    label = PLATFORM_NAMES[platform]
    web_id = account.get("web_account_id")
    if web_id is None:
        return None
    if isinstance(web_id, bool) or not str(web_id).isdigit() or int(web_id) < 1:
        raise ValueError(f"{label}工具账号记录 ID 无效")
    database = Path(BASE_DIR) / "db" / "database.db"
    if not database.is_file():
        raise ValueError(f"{label}工具账号库不存在")
    with closing(sqlite3.connect(database)) as conn:
        row = conn.execute("SELECT type,filePath,status FROM user_info WHERE id=?", (int(web_id),)).fetchone()
    if not row or row[0] != PLATFORM_TYPES[platform]:
        raise AccountBindingMissingError(f"原{label}记录不存在或平台不符，请选择当前发布账号")
    if row[2] != 1:
        raise ValueError(f"{label}登录状态未通过，请重新登录")
    base = (Path(BASE_DIR) / "cookiesFile").resolve()
    cookie = (base / row[1]).resolve()
    if not cookie.is_relative_to(base) or not cookie.is_file():
        raise ValueError(f"{label}工具账号登录文件缺失或路径无效")
    return cookie


def _channels_access(account: dict, config: dict) -> str:
    """Readiness for a manual attempt, not a claim of remote publish permission.

    publishing.json describes the production workspace's old access conditions;
    it is not an authorization authority for the local uploader. In particular,
    its blocked_browser_policy marker must not disable a valid Web account.
    The uploader still checks the real cookie and official prompts on submission.
    """
    options = config.get("platforms", {}).get("wechat_channels", {})
    if options.get("enabled") is False:
        return "disabled"
    configured = options.get("access_status")
    if configured not in (None, "ready", "auto", "blocked_browser_policy"):
        return configured
    if account.get("web_account_id") is not None:
        try:
            _web_channels_cookie(account)
        except AccountBindingMissingError:
            return "binding_missing"
        except (ValueError, OSError, sqlite3.Error):
            return "needs_login"
        return "ready"
    if not account.get("alias") or not account.get("account_id"):
        return "needs_account"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(account["alias"])):
        return "needs_account"
    cookie = Path(BASE_DIR) / "cookies" / f"tencent_{account['alias']}.json"
    return "ready" if cookie.is_file() else "cookie_missing"


def _retry_allowed(row) -> bool:
    if row is None or row["state"] != "failed" or row["remote_id"]:
        return False
    try:
        proof = json.loads(row["evidence"] or "{}")
        return (proof.get("remote_absent") is True or
                (proof.get("source") == "local_submission_boundary" and proof.get("submission_started") is False))
    except (ValueError, TypeError, AttributeError):
        return False


def _proven_unuploaded(row) -> bool:
    """Only structured preflight proof allows changing account after a failed try.

    A plain error string, remote_absent alone, or any remote id is insufficient.
    Original task records and their account identities are never rewritten.
    """
    if row is None or row["state"] != "failed" or row["remote_id"]:
        return False
    if "url" in row.keys() and row["url"]:
        return False
    try:
        proof = json.loads(row["evidence"] or "{}")
        return (isinstance(proof, dict) and proof.get("stage") == "preflight"
                and proof.get("upload_started") is False and proof.get("remote_absent") is True)
    except (ValueError, TypeError):
        return False


def _other_account_history_blocks(conn, edition: str, day: str, platform: str, aid: str) -> bool:
    # Imported history has no trustworthy local preflight checkpoint. Preserve it.
    legacy = conn.execute("SELECT 1 FROM legacy WHERE edition_day=? AND platform=? AND account_id<>? LIMIT 1",
                          (day, platform, aid)).fetchone()
    attempts = conn.execute("SELECT * FROM attempts WHERE edition_id=? AND platform=? AND account_id<>?",
                            (edition, platform, aid)).fetchall()
    return bool(legacy or any(platform != "wechat_channels" or not _proven_unuploaded(row) for row in attempts))


def platform_access(platform, account, config):
    if platform == "wechat_channels":
        return _channels_access(account, config)
    options = config.get("platforms", {}).get(platform, {})
    if options.get("enabled") is False:
        return "disabled"
    if platform in ("youtube", "toutiao", "kuaishou", "xiaohongshu") and not account.get("account_id"):
        return "needs_account"
    configured = options.get("access_status", "ready")
    if configured not in {"ready", "auto"}:
        return configured
    if account.get("web_account_id"):
        try:
            _web_account_cookie(platform, account)
        except AccountBindingMissingError:
            return "binding_missing"
        except (ValueError, OSError, sqlite3.Error):
            return "needs_login"
    return "ready"


def account_binding_options(platform: str) -> dict:
    """Metadata only. Listing cached status is not a live platform login check."""
    raw = SETTINGS_PATH.read_bytes() if SETTINGS_PATH.is_file() else b"{}"
    if platform not in PLATFORMS:
        raise ValueError("平台无效")
    config = json.loads(raw)
    account = _configured_account(platform, config)
    rows = []
    database = Path(BASE_DIR) / "db/database.db"
    if database.is_file():
        with closing(sqlite3.connect(database)) as conn:
            values = conn.execute("SELECT id,userName,status,filePath FROM user_info WHERE type=? ORDER BY id", (PLATFORM_TYPES[platform],)).fetchall()
        base = (Path(BASE_DIR) / "cookiesFile").resolve()
        for ident, name, status, filename in values:
            path = (base / filename).resolve()
            exists = path.is_relative_to(base) and path.is_file()
            rows.append({"id": ident, "name": name, "status": status,
                         "selectable": status == 1 and exists})
    return {"account": account, "accounts": rows,
            "access_status": platform_access(platform, account, config),
            "revision": hashlib.sha256(raw).hexdigest()}


def bind_account(platform: str, web_account_id, expected_revision: str) -> dict:
    """Explicit local UI selection; no authorization, upload or automatic retry.

    The jobs transaction serializes this binding change with task reservations.
    Atomic settings replacement and a revision check protect existing settings.
    """
    if platform not in PLATFORMS:
        raise ValueError("平台无效")
    if isinstance(web_account_id, bool) or not str(web_account_id).isdigit() or int(web_account_id) < 1:
        raise ValueError(f"请选择有效的{PLATFORM_NAMES[platform]}账号")
    web_account_id = int(web_account_id)
    temporary = None
    with _connection() as jobs:
        jobs.execute("BEGIN IMMEDIATE")
        if jobs.execute("SELECT 1 FROM attempts WHERE platform=? AND state IN ('queued','uploading') LIMIT 1", (platform,)).fetchone():
            raise ValueError(f"{PLATFORM_NAMES[platform]}任务正在执行，暂不能切换账号")
        raw = SETTINGS_PATH.read_bytes() if SETTINGS_PATH.is_file() else b"{}"
        if not isinstance(expected_revision, str) or hashlib.sha256(raw).hexdigest() != expected_revision:
            raise ValueError("账号配置已变化，请重新打开选择账号窗口")
        config = json.loads(raw)
        database = Path(BASE_DIR) / "db/database.db"
        with closing(sqlite3.connect(database)) as conn:
            row = conn.execute("SELECT userName,status,filePath FROM user_info WHERE id=? AND type=?", (web_account_id, PLATFORM_TYPES[platform])).fetchone()
        if not row:
            raise ValueError(f"所选{PLATFORM_NAMES[platform]}账号已不存在，请刷新列表")
        base = (Path(BASE_DIR) / "cookiesFile").resolve()
        path = (base / row[2]).resolve()
        if row[1] != 1 or not path.is_relative_to(base) or not path.is_file():
            raise ValueError("所选账号没有可用的登录记录，请先在账号管理登录")
        current = config.get("accounts", {}).get(platform, {})
        # Different Web records must not inherit a previous account's remote ID.
        replacement = dict(current) if current.get("web_account_id") == web_account_id else {}
        replacement.update(web_account_id=web_account_id, display_name=row[0])
        config.setdefault("accounts", {})[platform] = replacement
        if platform != "wechat_channels":
            config.setdefault("platforms", {}).setdefault(platform, {})["access_status"] = "auto"
        updated = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode('utf-8')
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_PATH.with_name('.' + SETTINGS_PATH.name + '.' + str(uuid.uuid4()) + '.tmp')
        try:
            temporary.write_bytes(updated)
            latest = SETTINGS_PATH.read_bytes() if SETTINGS_PATH.is_file() else b"{}"
            if latest != raw:
                raise ValueError("配置已被其他操作修改，请刷新后重试")
            temporary.replace(SETTINGS_PATH)
        finally:
            temporary.unlink(missing_ok=True)
    return {"account": _configured_account(platform, config),
            "access_status": platform_access(platform, _configured_account(platform, config), config)}


def channels_binding_options():
    return account_binding_options("wechat_channels")


def bind_channels_account(web_account_id, expected_revision):
    return bind_account("wechat_channels", web_account_id, expected_revision)


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
            account = _configured_account(platform, config)
            aid = str(account.get("account_id", ""))
            attempt = _latest(conn, package["edition_id"], platform, aid) if aid else None
            legacy = conn.execute("SELECT * FROM legacy WHERE edition_day=? AND platform=? AND account_id=?",
                                  (package["date"], platform, aid)).fetchone() if aid and package.get("kind") != "imported" else None
            if not attempt:
                attempt = conn.execute("SELECT * FROM attempts WHERE edition_id=? AND platform=? ORDER BY created_at DESC LIMIT 1",
                                       (package["edition_id"], platform)).fetchone()
            if not legacy and package.get("kind") != "imported":
                legacy = conn.execute("SELECT * FROM legacy WHERE edition_day=? AND platform=? LIMIT 1",
                                      (package["date"], platform)).fetchone()
            row = attempt or legacy
            access = platform_access(platform, account, config)
            result[platform] = {"account": account, "state": row["state"] if row else "ready",
                                "remote_id": row["remote_id"] if row else None,
                                "task_id": attempt["id"] if attempt else None,
                                "account_mismatch": (
                                    bool(row and row["account_id"] != aid and not (platform == "wechat_channels" and _proven_unuploaded(row)))
                                     or _other_account_history_blocks(conn, package["edition_id"], "" if package.get("kind") == "imported" else package["date"], platform, aid)),
                                "task_account_id": attempt["account_id"] if attempt else None,
                                "task_created_at": attempt["created_at"] if attempt else None,
                                "task_updated_at": attempt["updated_at"] if attempt else None,
                                "access_status": access, "retry_allowed": _retry_allowed(row),
                                **_task_failure_details(attempt)}
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
    allowed = {"title", "description", "tags", "short_title", "category", "ai_declaration", "cover_mode", "visibility", "made_for_kids"}
    clean = {key: value for key, value in payload.items() if key in allowed}
    if not clean.get("title") or not clean.get("description") or not isinstance(clean.get("tags", []), list):
        raise ValueError("草稿标题、简介或话题无效")
    if platform == 'bilibili' and clean.get('category'):
        if isinstance(clean['category'], bool) or not str(clean['category']).isdigit() or int(clean['category']) <= 0:
            raise ValueError('请选择有效的B站分区')
    with _connection() as conn:
        conn.execute("INSERT OR REPLACE INTO drafts VALUES(?,?,?,?)",
                     (str(path.resolve()), platform, account_id, json.dumps(clean, ensure_ascii=False)))
    if platform == 'bilibili' and clean.get('category'):
        from publishing.bilibili_metadata import remember
        remember(account_id, clean['category'])
    return clean


def reserve(path: Path, platform: str, source: str, payload: dict | None = None, *, queued=False,
             expected_transport=None, scheduled_for: str | None = None) -> str:
    require_current()
    if source not in {"manual", "automation", "mcp"} or platform not in PLATFORMS:
        raise ValueError("发布来源或平台无效")
    package = load_package(path)
    imported = package.get("kind") == "imported"
    if not imported and package["date"] != datetime.now(BEIJING).date().isoformat():
        raise ValueError("只能提交北京时间当天的资源包")
    if not imported:
        import_legacy(output_root().parent / "publications.json")
    if scheduled_for is not None:
        due = datetime.fromisoformat(scheduled_for)
        if due.tzinfo is None or due.astimezone(BEIJING) <= datetime.now(BEIJING):
            raise ValueError("预约时间必须是带时区的未来时间")
        scheduled_for = due.astimezone(BEIJING).isoformat()
    config = settings()
    if platform == "wechat_channels" and source == "automation":
        from publishing.automation import read_settings
        channel = read_settings()["platforms"]["wechat_channels"]
        if not channel["enabled"] or not channel["verified"]:
            raise ValueError("视频号自动投稿尚未通过桌面版验收设置")
    account = _account(platform)
    if platform == "wechat_channels":
        access = _channels_access(account, config)
        if access != "ready":
            messages = {"disabled": "视频号手动投稿已在工具配置中关闭",
                        "needs_account": "请先绑定视频号发布账号",
                        "binding_missing": "原视频号账号记录已失效，请在今日待发布中选择当前发布账号",
                        "needs_login": "视频号工具账号不存在、登录已失效或登录文件缺失，请重新登录",
                        "cookie_missing": "视频号账号登录文件缺失，请重新登录"}
            raise ValueError(messages.get(access, "视频号当前登录状态尚未就绪，请检查账号"))
    elif platform_access(platform, account, config) != "ready":
        raise ValueError(f"{platform} 的访问状态尚未就绪")
    aid = str(account["account_id"])
    material = material_for(package, platform)
    if source in {"manual", "mcp", "automation"}:
        material.update(payload or get_draft(path, platform, aid) or {})
    from publishing.transports import resolve
    if platform in ("youtube", "toutiao"):
        from publishing.creator_browser import validate_material
        validate_material(platform, material)
    if platform == "xiaohongshu":
        from publishing.xiaohongshu_browser import validate_material
        validate_material(material)
    transport = resolve(platform, material, config, BASE_DIR)
    if expected_transport and transport["fingerprint"] != expected_transport:
        raise ValueError("发布通道或账号配置已变化，请重新读取今日资源")
    if transport["id"] != "douyin_api":
        cookie_platform = "tencent" if platform == "wechat_channels" else platform
        cookie = _web_channels_cookie(account) if platform == "wechat_channels" else _web_account_cookie(platform, account)
        if cookie is None:
            if not account.get("alias"):
                raise ValueError("浏览器通道需要已登录的账号别名")
            cookie = Path(BASE_DIR) / "cookies" / f"{cookie_platform}_{account['alias']}.json"
        if not cookie.is_file():
            raise ValueError(f"{platform} 账号登录文件缺失，请先登录并核实账号")
    if not material.get("title") or not material.get("description"):
        raise ValueError("发布文案不完整")
    if platform != 'bilibili' and material_for(package, platform).get("ai_declaration") and not material.get("ai_declaration"):
        raise ValueError("该资源包要求填写平台 AI 内容声明")
    if platform == "douyin" and len(material["title"]) > 30:
        raise ValueError("抖音标题超过 30 字")
    if platform == "bilibili":
        from publishing.bilibili_metadata import category_for
        category = category_for(aid, material, config)
        if not str(category or "").isdigit() or int(category) <= 0:
            raise ValueError("B站分区 ID 尚未正确配置")
        material['category'] = int(category)
    if transport['id'] == 'youtube_api':
        from publishing.youtube_api import preflight, account_path
        preflight(path.parent / package['assets']['video']['path'],
                  path.parent / package['assets'][material['cover']]['path'], material,
                  account_path(BASE_DIR, account))
    elif transport['id'] == 'douyin_api':
        from publishing.douyin_api import MAX_SINGLE_UPLOAD_BYTES
        if (path.parent / package['assets']['video']['path']).stat().st_size > MAX_SINGLE_UPLOAD_BYTES:
            raise ValueError('抖音 API 当前单文件上限为 286.10 MiB（300000000 字节）')
        if len('\n'.join([material['title'], material['description'],
                          ' '.join('#' + tag.lstrip('#') for tag in material.get('tags', []))]).strip()) > 1000:
            raise ValueError('抖音 API 发布文案超过 1000 字')
    material["_delivery"] = transport
    material["_package_sha256"] = package_digest(package)
    job_id = str(uuid.uuid4())
    now = datetime.now(BEIJING).isoformat()
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        legacy = None if imported else conn.execute("SELECT * FROM legacy WHERE edition_day=? AND platform=? AND account_id=?",
                                                    (package["date"], platform, aid)).fetchone()
        if legacy and (legacy["state"] != "failed" or legacy["remote_id"] or
                       not json.loads(legacy["evidence"]).get("remote_absent")):
            raise ValueError(f"历史账本已有 {platform} {legacy['state']}，先核对远端")
        old = _latest(conn, package["edition_id"], platform, aid)
        if old and not _retry_allowed(old):
            raise ValueError(f"该期 {platform} 已预约或提交：{old['state']}")
        if _other_account_history_blocks(conn, package["edition_id"], "" if imported else package["date"], platform, aid):
            raise ValueError(f"{platform} 已有其他账号的历史投稿或任务，先核对远端")
        # A binding change may have completed while this reservation waited for SQLite.
        if str(_account(platform)["account_id"]) != aid:
            raise ValueError("发布账号已变化，请刷新确认后再提交")
        if resolve(platform, material, settings(), BASE_DIR)["fingerprint"] != transport["fingerprint"]:
            raise ValueError("发布配置已变化，请刷新确认后再提交")
        conn.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (job_id, package["edition_id"], platform, aid, str(path.resolve()), package["revision"], source,
                      "queued" if queued else "uploading", json.dumps(material, ensure_ascii=False), None, None, None, None, now, now))
        if queued:
            conn.execute("INSERT INTO delivery_queue(job_id,state,updated_at,available_at) VALUES(?,?,?,?)",
                         (job_id, "queued", now, scheduled_for or now))
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


class ReconcileConflictError(ValueError):
    """The record changed after the user opened the reconciliation form."""


def confirm_task_status(job_id: str, state: str, *, expected_updated_at):
    """Record an explicit user status choice using the original task's metadata."""
    if not isinstance(expected_updated_at, str) or not expected_updated_at:
        raise ValueError("任务版本缺失，请重新打开状态确认窗口")
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("任务编号缺失，请重新打开状态确认窗口")
    labels = {"processing": "审核中", "published": "已公开发布", "needs_action": "需本人处理",
              "unknown": "仍不确定", "failed": "确认没有作品"}
    if not isinstance(state, str) or state not in labels:
        raise ValueError("不支持的核对状态")
    current = task(job_id)
    if state == "failed" and (current["remote_id"] or current["url"]):
        raise ValueError("原任务已有作品记录，请先从平台回读核对，不能标记为没有作品")
    return reconcile(job_id, state, {
        "platform": current["platform"], "account_id": current["account_id"],
        "remote_id": current["remote_id"], "url": current["url"],
        "note": f"用户手动确认状态：{labels[state]}。",
        "checked_at": datetime.now(BEIJING).isoformat(), "remote_absent": state == "failed",
    }, expected_updated_at=expected_updated_at, manual_confirmation=True)


def reconcile(job_id: str, state: str, evidence: dict, *, expected_updated_at=None, manual_confirmation=False):
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("任务编号缺失，请重新打开核对窗口")
    if not isinstance(state, str) or state not in {"processing", "published", "failed", "unknown", "needs_action"}:
        raise ValueError("不支持的核对状态")
    if not isinstance(evidence, dict):
        raise ValueError("核对依据格式无效，请重新填写")
    current = task(job_id)
    with _connection() as conn:
        queued_task = conn.execute("SELECT 1 FROM delivery_queue WHERE job_id=? AND state IN ('queued','running')", (job_id,)).fetchone()
    if current["state"] == "queued" or (current["state"] == "uploading" and queued_task):
        raise ReconcileConflictError("任务正在排队或执行，请等待执行结束后核对")
    if expected_updated_at is not None and expected_updated_at != current["updated_at"]:
        raise ReconcileConflictError("任务状态已更新，请关闭并重新打开核对窗口，确认最新结果后再保存")
    if current["state"] == "published" and state != "published":
        raise ValueError("已发布状态不能回退")
    if evidence.get("platform") != current["platform"]:
        raise ValueError("核对平台与原任务不一致，请重新打开核对窗口")
    if str(evidence.get("account_id", "")) != current["account_id"]:
        raise ValueError("核对账号与原任务不一致，请按原任务账号记录结果")
    note = evidence.get("note")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("请填写核对依据；确认没有作品时，可勾选确认项生成说明")
    note = note.strip()
    if len(note) > 2000:
        raise ValueError("核对依据不能超过 2000 字")
    checked_at = evidence.get("checked_at")
    try:
        if not isinstance(checked_at, str) or datetime.fromisoformat(checked_at.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError("远端核对时间必须带时区") from None
    remote_id, url = evidence.get("remote_id") or "", evidence.get("url") or ""
    if not isinstance(remote_id, str) or not isinstance(url, str):
        raise ValueError("作品 ID 和链接必须为文本")
    remote_id, url = remote_id.strip(), url.strip()
    if len(remote_id) > 200 or len(url) > 2048:
        raise ValueError("作品 ID 或链接过长")
    if not manual_confirmation and state in {"processing", "published"} and not remote_id:
        raise ValueError("已提交或已发布需要真实作品 ID")
    if not manual_confirmation and state == "published" and not url:
        raise ValueError("已发布需要实际作品链接")
    if url:
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(url)
            valid_url = parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
        except ValueError:
            valid_url = False
        if not valid_url:
            raise ValueError("作品链接必须是有效的 https:// 地址")
    if state == "failed" and (evidence.get("remote_absent") is not True or remote_id or url):
        raise ValueError("确认失败需核对远端确无作品（remote_absent=true），作品 ID 和链接须留空")
    if state != "failed" and evidence.get("remote_absent") is True:
        raise ValueError("已存在作品的状态不能同时确认没有作品，请检查核对状态")
    # Only user-facing evidence is accepted. A manual form cannot spoof the
    # internal preflight / upload_started=False proof used by retry protection.
    clean = {"platform": current["platform"], "account_id": current["account_id"],
             "note": note, "checked_at": checked_at, "remote_id": remote_id or None,
             "url": url or None, "remote_absent": state == "failed",
             "source": "manual_confirmation" if manual_confirmation else "manual_reconcile"}
    with _connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute("UPDATE attempts SET state=?,error=NULL,evidence=?,remote_id=?,url=?,updated_at=? "
                              "WHERE id=? AND updated_at=? AND state=?",
                              (state, json.dumps(clean, ensure_ascii=False), remote_id or None, url or None,
                               datetime.now(BEIJING).isoformat(), job_id, current["updated_at"], current["state"]))
        if cursor.rowcount != 1:
            raise ReconcileConflictError("任务状态已更新，请重新打开核对窗口后再保存")
    return task(job_id)


def _upload(job: dict, package: dict):
    from sau_cli import (BilibiliVideoUploadRequest, DouyinVideoUploadRequest,
                         TencentVideoUploadRequest, upload_bilibili_video, upload_video, upload_tencent_video)
    root = Path(job["package_path"]).parent
    assets = package["assets"]
    material = job["payload"]
    try:
        account_config = _account(job["platform"])
    except (ValueError, OSError) as exc:
        raise UploadNotStartedError("发布账号配置不可用，尚未上传。请检查账号绑定后再提交。", "account_config", "check_account") from exc
    if job.get("account_id") and str(account_config.get("account_id")) != job["account_id"]:
        raise UploadNotStartedError("发布账号绑定已变化，尚未上传。请核对任务与当前账号。", "account_changed", "check_account")
    if material.get("_delivery"):
        from publishing.transports import resolve
        try:
            channel = resolve(job["platform"], material, settings(), BASE_DIR)
        except ValueError as exc:
            raise UploadNotStartedError(str(exc), "transport_unavailable", "check_account") from exc
        if channel["fingerprint"] != material["_delivery"]["fingerprint"]:
            raise UploadNotStartedError("账号或发布通道配置已变化，尚未上传，请重新提交", "transport_changed", "check_account")
    account = account_config.get("alias")
    video = root / assets["video"]["path"]
    tags = material.get("tags", [])
    if material.get("_delivery", {}).get("id") == "douyin_api":
        from publishing.douyin_api import publish, read_credentials
        from publishing.transports import credentials_path
        try:
            api = settings()["platforms"]["douyin"]["api"]
            credentials = read_credentials(credentials_path(api, BASE_DIR), account_config)
        except ValueError as exc:
            raise UploadNotStartedError(str(exc), "api_auth_invalid", "relogin") from exc
        return publish(video, material, credentials, lambda value: _progress(job["id"], value))
    if job["platform"] == "bilibili":
        bili_config = settings().get("platforms", {}).get("bilibili", {})
        category = material.get("category") or bili_config.get("category")
        request = BilibiliVideoUploadRequest(account, video, material["title"], material["description"], int(category),
                                             tags, 0, root / assets[material["cover"]]["path"],
                                             extra_fields=bili_config.get("ai_declaration_fields"))
        if job.get("id"):
            _progress(job["id"], {"stage": "uploading", "message": "B站接口正在处理上传；暂不提供可核实的字节百分比"})
        return asyncio.run(upload_bilibili_video(request, with_receipt=True,
                                               account_file_override=_web_account_cookie("bilibili", account_config)))
    elif job["platform"] == "douyin":
        request = DouyinVideoUploadRequest(account, video, material["title"], material["description"], tags, 0,
                                           thumbnail_landscape_file=root / assets[material["cover_landscape"]]["path"],
                                           thumbnail_portrait_file=root / assets[material["cover_portrait"]]["path"],
                                           declaration=material.get("ai_declaration"), headless=False)
        if job.get("id"):
            _progress(job["id"], {"stage": "opening", "message": "正在打开抖音投稿页面"})
        return asyncio.run(upload_video(request, with_receipt=True, account_file_override=_web_account_cookie("douyin", account_config),
                                 progress_callback=(lambda value: _progress(job["id"], value)) if job.get("id") else None))
    elif job["platform"] == "xiaohongshu":
        from publishing.xiaohongshu_browser import publish
        cookie = _web_account_cookie("xiaohongshu", account_config)
        if cookie is None:
            cookie = Path(BASE_DIR) / "cookies" / f"xiaohongshu_{account}.json"
        return asyncio.run(publish(video, root / assets[material.get("cover", "portrait")]["path"],
                                   material, cookie, lambda value: _progress(job["id"], value)))
    elif job["platform"] == "kuaishou":
        from publishing.kuaishou_browser import publish
        cookie = _web_account_cookie("kuaishou", account_config)
        if cookie is None:
            cookie = Path(BASE_DIR) / "cookies" / f"kuaishou_{account}.json"
        return asyncio.run(publish(video, root / assets[material.get("cover", "portrait")]["path"],
                                   material, cookie, lambda value: _progress(job["id"], value)))
    elif job["platform"] == "youtube":
        from publishing.youtube_api import publish, account_path
        return publish(video, root / assets[material.get("cover", "landscape")]["path"], material,
                       account_path(BASE_DIR, account_config), lambda value: _progress(job["id"], value))
    elif job["platform"] == "toutiao":
        from publishing.creator_browser import publish
        cookie = _web_account_cookie(job["platform"], account_config)
        if cookie is None:
            cookie = Path(BASE_DIR) / "cookies" / f"{job['platform']}_{account}.json"
        return asyncio.run(publish(job["platform"], video, root / assets[material.get("cover", "landscape")]["path"],
                                   material, cookie, lambda value: _progress(job["id"], value)))
    elif job["platform"] == "wechat_channels":
        landscape = root / assets[material["cover_landscape"]]["path"]
        portrait = root / assets[material["cover_portrait"]]["path"]
        try:
            web_cookie = _web_channels_cookie(account_config)
        except (ValueError, OSError) as exc:
            raise UploadNotStartedError("视频号工具账号不可用，尚未上传。请到账号管理重新登录。", "login_check_failed", "relogin") from exc
        if web_cookie:
            from uploader.tencent_uploader.main import TencentVideo, cookie_auth
            async def upload_web_account():
                if not material.get('one_click'):
                    try:
                        authenticated = await cookie_auth(str(web_cookie))
                    except Exception as exc:
                        # No uploader has been created here, even if the check timed out.
                        raise UploadNotStartedError("视频号登录检查未完成，尚未上传。请检查网络后重试。", "login_check_unavailable") from exc
                    if not authenticated:
                        _mark_channels_needs_login(account_config, web_cookie)
                        raise UploadNotStartedError("视频号登录校验未通过，尚未上传。请到账号管理重新登录后再提交。", "login_check_failed", "relogin")
                app = TencentVideo(title=material["title"], file_path=str(video), tags=tags,
                                   publish_date=0, account_file=str(web_cookie), desc=material["description"],
                                   thumbnail_landscape_path=str(landscape), thumbnail_portrait_path=str(portrait),
                                   short_title=material.get("short_title"), headless=False,
                                   require_content_label=True, require_thumbnail=True,
                                   cover_mode=material.get("cover_mode", "custom"), edition_day=package["date"],
                                   progress_callback=(lambda value: _progress(job["id"], value)) if job.get("id") else None)
                return await app.tencent_upload_video()
            return asyncio.run(upload_web_account())
        else:
            request = TencentVideoUploadRequest(account, video, material["title"], material["description"], tags, 0,
                                                thumbnail_landscape_file=landscape, thumbnail_portrait_file=portrait,
                                                short_title=material.get("short_title"), headless=False,
                                                require_content_label=True, require_thumbnail=True)
            asyncio.run(upload_tencent_video(request))


def _progress(job_id: str, value: dict):
    # Stages contain no credentials, browser URLs, or user-entered descriptions.
    proof = {**value, "source": "local_progress"}
    with _connection() as conn:
        previous = conn.execute('SELECT evidence FROM attempts WHERE id=?', (job_id,)).fetchone()
        if previous and previous['evidence']:
            proof = {**json.loads(previous['evidence']), **proof}
        if value.get('remote_id'):
            # Preserve an accepted API video's ID even if the process exits while setting its cover.
            conn.execute("UPDATE attempts SET remote_id=?,url=? WHERE id=? AND state='uploading'",
                         (value['remote_id'], value.get('url'), job_id))
        conn.execute("UPDATE attempts SET evidence=?,updated_at=? WHERE id=? AND state='uploading'",
                     (json.dumps(proof, ensure_ascii=False), datetime.now(BEIJING).isoformat(), job_id))


def _record_remote_found(current, outcome):
    state = 'published' if outcome.get('state') == 'published' else 'processing'
    evidence = {**(task(current['id']).get('evidence') or {}),
                "platform": current["platform"], "account_id": current["account_id"],
                'stage': 'verifying', 'message': '官方后台已确认公开发布' if state == 'published' else '官方后台已确认收稿，等待审核或公开状态',
                "source": outcome.get("source", "official_content_list"), "remote_id": outcome["remote_id"], "url": outcome.get("url"),
                "checked_at": datetime.now(BEIJING).isoformat(), "remote_created_at": outcome.get("created_at"),
                "existing_before_attempt": outcome.get("existing", False), "match": outcome.get("match"),
                "warnings": outcome.get("warnings", []),
                'platform_status': outcome.get('platform_status'),
                'visible_type': outcome.get('visible_type'), 'status_value': outcome.get('status_value'),
                "note": outcome.get("note", "在当前账号官方后台找到同日同简介的唯一作品；这不是仅根据按钮或日志推断的成功。")}
    # Finding a record is not proof that it is publicly visible or has passed review.
    _state(current["id"], state, evidence=evidence)
    return task(current["id"])


def run(job_id: str):
    current = task(job_id)
    if current["state"] != "uploading":
        raise ValueError("任务已执行或需要先核对，不能重发")
    with _connection() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS run_claims(job_id TEXT PRIMARY KEY, claimed_at TEXT NOT NULL)")
        claimed = conn.execute("INSERT OR IGNORE INTO run_claims VALUES(?,?)", (job_id, datetime.now(BEIJING).isoformat()))
        if claimed.rowcount != 1:
            raise ValueError("该任务已由另一个执行器接管，不会重复投稿")
    try:
        deadline = current["payload"].get("_automation_deadline")
        if deadline and datetime.now(BEIJING) > datetime.fromisoformat(deadline):
            raise UploadNotStartedError("自动投稿已超过预设截止时间；尚未上传", "deadline_passed", "review_schedule")
        _progress(job_id, {"stage": "checking", "message": "正在校验资源包与账号"})
        try:
            package = load_package(Path(current["package_path"]))
            if package.get("kind") != "imported" and package["date"] != datetime.now(BEIJING).date().isoformat():
                raise ValueError("资源包已过期，不自动补发昨日视频")
            expected_hash = current["payload"].get("_package_sha256")
            if expected_hash and package_digest(package) != expected_hash:
                raise ValueError("资源包在入队后发生变化")
        except (ValueError, OSError, KeyError) as exc:
            raise UploadNotStartedError("资源包校验失败，尚未上传。请刷新并检查当日资源包。", "package_invalid", "check_material") from exc
        from publishing.queue import account_lock
        with account_lock(current["platform"], current["account_id"]) as acquired:
            if not acquired:
                raise UploadNotStartedError("同一账号还有任务正在执行，请稍后重试", "account_busy")
            outcome = _upload(current, package)
        if isinstance(outcome, dict) and outcome.get("status") == "found":
            return _record_remote_found(current, outcome)
    except SubmissionNotStarted as exc:
        if exc.code == "login_check_failed" and getattr(exc, "account_file", None):
            try:
                account = _account(current["platform"])
                if current["platform"] == "wechat_channels" and str(account["account_id"]) == current["account_id"]:
                    _mark_channels_needs_login(account, Path(exc.account_file))
            except (ValueError, OSError, sqlite3.Error):
                pass
        evidence = {**(task(job_id).get('evidence') or {}), "platform": current["platform"], "account_id": current["account_id"],
                    "checked_at": datetime.now(BEIJING).isoformat(), "source": "local_submission_boundary",
                    "stage": exc.stage, "upload_started": exc.upload_started, "submission_started": False,
                    "failure_code": exc.code, "next_action": "relogin" if exc.code == "login_check_failed" else "retry",
                    "note": "本次执行未尝试点击最终发表；可能已上传媒体或生成草稿，但没有提交本次投稿。"}
        _state(job_id, "failed", error=str(exc), evidence=evidence)
        return task(job_id)
    except UploadNotStartedError as exc:
        # This evidence establishes that THIS attempt never called the uploader;
        # it is not a fabricated remote query or a claim of successful publication.
        evidence = {"platform": current["platform"], "account_id": current["account_id"],
                    "checked_at": datetime.now(BEIJING).isoformat(),
                    "source": "local_preflight", "stage": "preflight", "upload_started": False,
                    "remote_absent": True, "failure_code": exc.code, "next_action": exc.action,
                    "note": "本次任务在上传器调用之前终止；未上传视频、未提交投稿。"}
        _state(job_id, "failed", error=str(exc), evidence=evidence)
        return task(job_id)
    except Exception as exc:
        # Once inside an uploader, an exception can happen after submission.
        # Preserve unknown and never auto-retry that potentially submitted work.
        latest = task(job_id)
        known = {**(latest.get('evidence') or {}),
                 'remote_id': latest.get('remote_id'), 'url': latest.get('url'),
                 'error_type': type(exc).__name__}
        _state(job_id, "unknown", error=str(exc), evidence=known)
        return task(job_id)
    latest = task(job_id)
    _state(job_id, "unknown", error="上传器已返回；需从平台内容管理回读作品 ID 与审核状态",
           evidence={**(latest.get('evidence') or {}), 'remote_id': latest.get('remote_id'), 'url': latest.get('url')})
    return task(job_id)


def submit(path: Path, platforms: list[str], source: str, synchronous: bool = False) -> dict:
    jobs, errors = [], {}
    for platform in dict.fromkeys(platforms):
        try:
            jobs.append(reserve(path, platform, source, queued=not synchronous))
        except ValueError as exc:
            errors[platform] = str(exc)
    if jobs:
        def worker():
            for job_id in jobs:
                run(job_id)
        if synchronous:
            worker()
        else:
            from publishing.queue import ensure_worker
            try:
                ensure_worker()
            except OSError:
                errors["worker"] = "后台执行器未启动，任务已保存；重启本机发布工具可继续排队任务"
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
