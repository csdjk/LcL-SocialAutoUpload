"""Explicit Web submission adapter around the project's existing biliup runtime.

All input is checked before any upload. This module never schedules a background
job, retries a submission, or treats a CLI exit as proof of public publication.
"""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import asyncio
import json
import re
import sqlite3
import subprocess
import tempfile
import threading

from conf import BASE_DIR
from uploader.bilibili_uploader.runtime import ensure_biliup_binary
from uploader.bilibili_uploader.web_login import normalize_bilibili_credentials, cookie_auth

# Prevent concurrent submissions through either Web route for the same account.
_busy_accounts = set()
_busy_lock = threading.Lock()


def _integer(value, label, low, high):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise ValueError(f'{label}必须为整数')
    result = int(value)
    if not low <= result <= high:
        raise ValueError(f'{label}超出允许范围')
    return result


def _text(value, label, maximum, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise ValueError(f'{label}格式不正确或超过{maximum}字')
    result = value.strip()
    if required and not result:
        raise ValueError(f'请填写{label}')
    return result


def _local_file(folder, name):
    if not isinstance(name, str) or not name or Path(name).is_absolute() or '\x00' in name:
        raise ValueError('本机文件路径无效')
    base = (Path(BASE_DIR) / folder).resolve()
    path = (base / name).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise ValueError('文件不存在或不属于本工具素材目录')
    return path


def prepare_submissions(data):
    title = _text(data.get('title'), 'B站标题', 80, required=True)
    desc = _text(data.get('description', ''), '视频简介', 2000)
    tid = _integer(data.get('tid'), 'B站分区 ID', 1, 65535)
    copyright = _integer(data.get('copyright'), '稿件类型', 1, 2)
    source = _text(data.get('source', ''), '转载来源', 200, required=copyright == 2)
    tags = data.get('tags')
    if not isinstance(tags, list) or not 1 <= len(tags) <= 10:
        raise ValueError('B站投稿需要1～10个标签')
    tags = [_text(tag, '标签', 20, required=True) for tag in tags]
    if any(',' in t for t in tags):
        raise ValueError('单个标签不能包含英文逗号')
    files, accounts = data.get('fileList'), data.get('accountList')
    if not isinstance(files, list) or not 1 <= len(files) <= 50:
        raise ValueError('请选择1～50个视频文件')
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= 10 or any(not isinstance(x, str) for x in accounts):
        raise ValueError('请选择有效的 B站账号')
    if len(set(accounts)) != len(accounts):
        raise ValueError('发布账号不能重复')
    videos = [_local_file('videoFile', name) for name in files]
    if len(set(videos)) != len(videos) or any(p.suffix.lower() not in {'.mp4','.mov','.mkv','.avi','.flv','.webm','.wmv'} for p in videos):
        raise ValueError('请选择不重复的视频文件')
    if data.get('isDraft'):
        raise ValueError('B站当前入口不支持仅保存草稿，请在官网操作')
    with closing(sqlite3.connect(Path(BASE_DIR)/'db/database.db')) as conn:
        rows = conn.execute('SELECT filePath FROM user_info WHERE type=5 AND status=1').fetchall()
    allowed = {r[0] for r in rows}
    if any(name not in allowed for name in accounts):
        raise ValueError('所选账号不是有效的 B站账号，请刷新或重新登录')
    credentials = [_local_file('cookiesFile', name) for name in accounts]
    for path in credentials:
        info = normalize_bilibili_credentials(path.read_text(encoding='utf-8'))
        if not info['token_info']['access_token']:
            raise ValueError('B站登录态不完整，请重新扫码或通过导入入口验证')
    times = [None] * len(videos)
    if data.get('enableTimer'):
        per_day = _integer(data.get('videosPerDay', 1), '每日视频数', 1, 24)
        start_days = _integer(data.get('startDays', 0), '延后天数', 0, 365)
        daily_times = data.get('dailyTimes')
        if not isinstance(daily_times, list) or len(daily_times) < per_day:
            raise ValueError('请为每天的视频填写定时时间')
        parsed = []
        for value in daily_times[:per_day]:
            if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
                raise ValueError('定时时间格式必须为 HH:MM')
            parsed.append(tuple(map(int, value.split(':'))))
        now = datetime.now().astimezone()
        for index in range(len(videos)):
            hour, minute = parsed[index % per_day]
            dt = (now + timedelta(days=start_days + 1 + index // per_day)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now + timedelta(hours=4):
                raise ValueError('B站定时投稿需至少提前4小时，请调整时间')
            times[index] = int(dt.timestamp())
    commands = []
    for index, video in enumerate(videos):
        for account in credentials:
            args = ['-u', str(account), 'upload', str(video), '--submit', 'web', '--title', title,
                    '--desc', desc, '--tid', str(tid), '--tag', ','.join(tags), '--copyright', str(copyright)]
            if copyright == 2:
                args.extend(['--source', source])
            if times[index] is not None:
                args.extend(['--dtime', str(times[index])])
            commands.append(args)
    return credentials, commands


def post_video_bilibili(data):
    credentials, commands = prepare_submissions(data)
    keys = {str(p) for p in credentials}
    with _busy_lock:
        if _busy_accounts.intersection(keys):
            raise ValueError('该 B站账号已有投稿正在处理，请等待结束，不要重复提交')
        _busy_accounts.update(keys)
    try:
        # Recheck every account before uploading any file. Network errors are not validity.
        for account in credentials:
            if not asyncio.run(cookie_auth(account)):
                raise ValueError('B站登录态已失效，请重新登录')
        binary = ensure_biliup_binary(force_check=False)
        completed = 0
        for args in commands:
            # Old runtimes may write logs in cwd; isolate and delete those ephemeral files.
            with tempfile.TemporaryDirectory(prefix='sau-bilibili-submit-') as cwd:
                result = subprocess.run([str(binary), *args], cwd=cwd, check=False, capture_output=True,
                    text=True, encoding='utf-8', errors='replace', timeout=3600,
                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, 'CREATE_NO_WINDOW') else {}))
            if result.returncode != 0:
                raise RuntimeError('B站投稿进程未正常完成，请先核对稿件，避免重复投稿')
            completed += 1
        return {'status': 'needs_platform_check', 'processed': completed,
                'message': f'B站投稿流程已返回（{completed}项）；请到创作中心核对稿件及审核状态，勿重复提交'}
    finally:
        with _busy_lock:
            _busy_accounts.difference_update(keys)
