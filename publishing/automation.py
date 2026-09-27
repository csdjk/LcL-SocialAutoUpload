"""Local Beijing-time discovery and reservation; platform calls stay in the worker."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading

import daily_publish as daily

_settings_lock = threading.Lock()
_CLOCK = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
DEFAULT = {"enabled": False, "after": "06:00", "deadline": "23:00",
           "platforms": {name: {"enabled": False, "verified": False} for name in daily.PLATFORMS}}


def settings_path() -> Path:
    return daily.DB_PATH.parent / "automation.json"


def read_settings() -> dict:
    path = settings_path()
    if not path.is_file():
        return json.loads(json.dumps(DEFAULT))
    data = json.loads(path.read_text(encoding="utf-8"))
    # Existing three-platform schedules retain their switches; new targets start off.
    if isinstance(data, dict) and isinstance(data.get("platforms"), dict):
        for name in ("youtube", "toutiao", "kuaishou", "xiaohongshu"):
            data["platforms"].setdefault(name, {"enabled": False, "verified": False})
    return validate(data)


def validate(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != {"enabled", "after", "deadline", "platforms"}:
        raise ValueError("自动发布设置格式无效")
    if type(data["enabled"]) is not bool or not all(isinstance(data[key], str) and _CLOCK.fullmatch(data[key]) for key in ("after", "deadline")):
        raise ValueError("自动发布开关或时间格式无效")
    if data["after"] >= data["deadline"]:
        raise ValueError("最晚投稿时间必须晚于开始时间，且处于同一北京时间日期")
    platforms = data["platforms"]
    if not isinstance(platforms, dict) or set(platforms) != set(daily.PLATFORMS):
        raise ValueError("自动发布平台列表无效")
    for name, item in platforms.items():
        if not isinstance(item, dict) or set(item) != {"enabled", "verified"} or any(type(value) is not bool for value in item.values()):
            raise ValueError(f"{name} 自动发布设置无效")
        if item["enabled"] and not item["verified"]:
            raise ValueError(f"{daily.PLATFORM_NAMES[name]}尚未确认真实账号链路验收")
    return data


def save_settings(data: dict) -> dict:
    clean = validate(data)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _settings_lock:
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return clean


def _record(day: str, platform: str, state: str, message: str, task_id: str | None = None) -> None:
    with daily._connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS automation_events (
            day TEXT NOT NULL, platform TEXT NOT NULL, state TEXT NOT NULL,
            message TEXT NOT NULL, task_id TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY(day,platform))""")
        conn.execute("""INSERT INTO automation_events VALUES(?,?,?,?,?,?)
            ON CONFLICT(day,platform) DO UPDATE SET state=excluded.state,
            message=excluded.message, task_id=excluded.task_id, updated_at=excluded.updated_at""",
            (day, platform, state, message, task_id, datetime.now(daily.BEIJING).isoformat()))


def status(now: datetime | None = None) -> dict:
    day = (now or datetime.now(daily.BEIJING)).astimezone(daily.BEIJING).date().isoformat()
    with daily._connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS automation_events (
            day TEXT NOT NULL, platform TEXT NOT NULL, state TEXT NOT NULL,
            message TEXT NOT NULL, task_id TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY(day,platform))""")
        rows = conn.execute("SELECT * FROM automation_events WHERE day=?", (day,)).fetchall()
    return {"settings": read_settings(), "day": day,
            "events": {row["platform"]: dict(row) for row in rows}}


def tick(now: datetime | None = None) -> list[str]:
    """Reserve today's complete package once per configured platform.

    A failed preflight is visible and retried only while no task was reserved.
    The atomic attempt ledger still decides whether submission is permitted.
    """
    config = read_settings()
    if not config["enabled"]:
        return []
    current = (now or datetime.now(daily.BEIJING)).astimezone(daily.BEIJING)
    clock = current.strftime("%H:%M")
    if clock < config["after"] or clock > config["deadline"]:
        return []
    from publishing import service
    try:
        bundle = service.today(cached=True)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        for name, item in config["platforms"].items():
            if item["enabled"]:
                _record(current.date().isoformat(), name, "waiting", str(exc))
        return []
    if not bundle["package"]:
        return []
    jobs = []
    for name, item in config["platforms"].items():
        if not item["enabled"]:
            continue
        try:
            if name not in bundle["publishable_platforms"]:
                info = bundle["status"][name]
                reason = info.get("access_message") or f"当前状态 {info['state']} / {info['access_status']} 不允许预约"
                _record(current.date().isoformat(), name, "waiting", reason, info.get("task_id"))
                continue
            path = Path(bundle["package_path"])
            info = bundle["status"][name]
            material = service._material(path, bundle["package"], name, info["account"])
            material["one_click"] = True
            material["_automation_deadline"] = f"{current.date().isoformat()}T{config['deadline']}:00+08:00"
            ident = daily.reserve(path, name, "automation", material, queued=True,
                                  expected_transport=info["transport"]["fingerprint"])
            jobs.append(ident)
            _record(current.date().isoformat(), name, "queued", "已创建自动投稿任务；入队不代表平台已发布", ident)
        except (ValueError, OSError, daily.sqlite3.Error) as exc:
            _record(current.date().isoformat(), name, "waiting", str(exc))
    return jobs
