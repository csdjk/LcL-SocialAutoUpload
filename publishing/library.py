"""Import ordinary MP4 files without claiming AI日报 editorial review."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import uuid

from PIL import Image, ImageOps

import daily_publish as daily
from publishing import queue
from publishing.platforms import material_for, package_view

MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024


def root() -> Path:
    return Path(daily.BASE_DIR) / "db" / "imports"


def _copy_limited(stream, target: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with target.open("wb") as output:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_VIDEO_BYTES:
                raise ValueError("视频超过 2 GiB 导入上限")
            digest.update(chunk)
            output.write(chunk)
    if not size:
        raise ValueError("视频文件为空")
    return size, digest.hexdigest()


def _probe(video: Path) -> float:
    executable = shutil.which("ffprobe")
    if not executable:
        raise ValueError("缺少 ffprobe，无法检查普通视频")
    result = subprocess.run([executable, "-v", "error", "-show_entries", "stream=codec_type",
                             "-show_entries", "format=duration", "-of", "json", str(video)],
                            capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        raise ValueError("视频技术检查失败：请确认文件可以正常播放")
    data = json.loads(result.stdout)
    if not any(stream.get("codec_type") == "video" for stream in data.get("streams", [])):
        raise ValueError("文件中没有视频轨道")
    duration = float(data.get("format", {}).get("duration", 0))
    if duration <= 0:
        raise ValueError("无法读取视频时长")
    return duration


def _covers(video: Path, directory: Path, cover_stream=None) -> None:
    source = directory / "source-cover.png"
    if cover_stream is not None:
        with Image.open(cover_stream) as image:
            image.convert("RGB").save(source)
    else:
        executable = shutil.which("ffmpeg")
        if not executable:
            raise ValueError("缺少 ffmpeg，无法从视频提取封面")
        result = subprocess.run([executable, "-v", "error", "-y", "-i", str(video),
                                 "-frames:v", "1", str(source)], capture_output=True, timeout=90, check=False)
        if result.returncode or not source.is_file():
            raise ValueError("无法从视频提取封面画面")
    with Image.open(source) as image:
        rgb = image.convert("RGB")
        ImageOps.fit(rgb, (1280, 720), method=Image.Resampling.LANCZOS).save(directory / "landscape.png")
        ImageOps.fit(rgb, (720, 960), method=Image.Resampling.LANCZOS).save(directory / "portrait.png")
    source.unlink(missing_ok=True)


def _asset(path: Path) -> dict:
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": daily._sha(path)}


def import_video(stream, filename: str, title: str, description: str, tags: list[str],
                 cover_stream=None, ai_declaration: str = "") -> dict:
    if not isinstance(filename, str) or Path(filename).suffix.lower() != ".mp4":
        raise ValueError("普通视频当前仅支持 MP4 文件")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 100:
        raise ValueError("标题须为 1–100 个字")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 3000:
        raise ValueError("简介须为 1–3000 个字")
    if not isinstance(tags, list) or len(tags) > 10 or any(not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 30 for tag in tags):
        raise ValueError("话题最多 10 个，每个 1–30 字")
    if not isinstance(ai_declaration, str) or len(ai_declaration) > 200:
        raise ValueError("AI 声明格式无效")
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="import-", dir=base) as temporary:
        folder = Path(temporary)
        video = folder / "video.mp4"
        size, digest = _copy_limited(stream, video)
        ident = str(uuid.uuid5(uuid.NAMESPACE_URL, "local-video:" + digest))
        final = base / ident
        if final.is_dir():
            return {"package_path": str(final / "package.json"), "package": daily.load_package(final / "package.json"),
                    "existing": True}
        duration = _probe(video)
        _covers(video, folder, cover_stream)
        assets = {"video": _asset(video), "bilibili": _asset(folder / "landscape.png"),
                  "landscape": _asset(folder / "landscape.png"), "portrait": _asset(folder / "portrait.png")}
        material = {"title": title.strip(), "description": description.strip(),
                    "tags": [tag.strip() for tag in tags], "cover": "bilibili",
                    "cover_landscape": "landscape", "cover_portrait": "portrait",
                    "cover_mode": "custom", "ai_declaration": ai_declaration.strip()}
        now = datetime.now(daily.BEIJING)
        package = {"schema_version": 2, "kind": "imported", "timezone": "Asia/Shanghai",
                   "edition_id": ident, "date": now.date().isoformat(), "revision": "r001",
                   "video": {"bytes": size, "duration_seconds": duration, "sha256": digest},
                   "assets": assets, "platforms": {name: dict(material) for name in daily.PLATFORMS}}
        (folder / "package.json").write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            folder.rename(final)
        except OSError:
            if not final.is_dir():
                raise
            return {"package_path": str(final / "package.json"), "package": daily.load_package(final / "package.json"),
                    "existing": True}
    return {"package_path": str(final / "package.json"), "package": daily.load_package(final / "package.json"),
            "existing": False}


def list_videos(limit: int = 30) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("读取数量无效")
    items = []
    for path in sorted(root().glob("*/package.json"), key=lambda item: item.stat().st_mtime_ns, reverse=True)[:limit]:
        try:
            package = daily.load_package(path)
            status = daily.status_for(package)
            items.append({"id": package["edition_id"], "package_path": str(path),
                          "date": package["date"], "title": package["platforms"]["bilibili"]["title"],
                          "video": package["video"], "platforms": package_view(package)["platforms"],
                          "status": {key: {"state": value["state"], "access_status": value["access_status"],
                                           "task_id": value["task_id"]} for key, value in status.items()}})
        except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
            items.append({"id": path.parent.name, "error": str(exc)})
    return items


def submit(ident: str, platforms: list[str], scheduled_for: str | None = None,
           payloads: dict | None = None) -> dict:
    if not isinstance(ident, str) or not isinstance(platforms, list) or not platforms or len(platforms) != len(set(platforms)):
        raise ValueError("请选择导入内容和不重复的平台列表")
    path = root() / ident / "package.json"
    package = daily.load_package(path)
    if payloads is None:
        payloads = {}
    if not isinstance(payloads, dict) or any(key not in daily.PLATFORMS or not isinstance(value, dict) for key, value in payloads.items()):
        raise ValueError("平台文案格式无效")
    jobs, errors = [], {}
    for platform in platforms:
        if platform not in daily.PLATFORMS:
            raise ValueError("投稿平台无效")
        try:
            from publishing.service import EDITABLE
            change = payloads.get(platform, {})
            if any(key not in EDITABLE for key in change):
                raise ValueError("平台文案包含不支持的字段")
            material = {**material_for(package, platform), **change, "one_click": True}
            if not isinstance(material.get("title"), str) or not material["title"].strip() or not isinstance(material.get("description"), str) or not material["description"].strip():
                raise ValueError("请填写标题和简介")
            if not isinstance(material.get("tags", []), list) or any(not isinstance(tag, str) for tag in material["tags"]):
                raise ValueError("话题格式无效")
            job_id = daily.reserve(path, platform, "manual", material, queued=True, scheduled_for=scheduled_for)
            jobs.append({"task_id": job_id, "platform": platform, "state": "queued"})
        except (ValueError, OSError) as exc:
            errors[platform] = str(exc)
    warning = None
    if jobs:
        try:
            queue.ensure_worker()
        except OSError:
            warning = "任务已保存，后台执行器未能启动；请重启工具恢复"
    return {"jobs": jobs, "errors": errors, "warning": warning}
