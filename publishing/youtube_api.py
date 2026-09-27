"""YouTube desktop OAuth and resumable uploads through documented Google APIs.

Credentials stay local. Browser automation is never used for Google sign-in.
Ambiguous uploads are not replayed as new insert requests.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
import webbrowser
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

SCOPES = ['https://www.googleapis.com/auth/youtube.upload',
          'https://www.googleapis.com/auth/youtube.readonly']
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API = 'https://www.googleapis.com/youtube/v3/'
UPLOAD = 'https://www.googleapis.com/upload/youtube/v3/'
CHUNK = 8 * 1024 * 1024


class YouTubeError(ValueError):
    """Fixed, user-safe messages only; never include raw HTTP errors or bodies."""


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (OSError, ValueError, TypeError):
        raise YouTubeError('YouTube 授权文件不可用，请在账号管理重新配置或授权') from None


def client_config(data):
    client = data.get('installed') if isinstance(data, dict) else None
    if (not isinstance(client, dict)
            or not isinstance(client.get('client_id'), str)
            or not client['client_id'].endswith('.apps.googleusercontent.com')
            or not isinstance(client.get('client_secret'), str) or not client['client_secret'].strip()):
        raise YouTubeError('请选择 Google Cloud 下载的“桌面应用”OAuth 客户端 JSON，不能使用 API 密钥或 Web 客户端')
    return {'client_id': client['client_id'], 'client_secret': client['client_secret']}


def config_path(base):
    return Path(base) / 'db/credentials/youtube-client.json'


def read_credentials(path):
    data = read_json(path)
    if (data.get('kind') != 'youtube_oauth'
            or any(not isinstance(data.get(key), str) or not data[key] for key in
                   ('client_id', 'client_secret', 'access_token', 'refresh_token', 'channel_id'))
            or not re.fullmatch(r'UC[A-Za-z0-9_-]+', data['channel_id'])
            or not isinstance(data.get('scopes'), list) or not set(SCOPES).issubset(data['scopes'])
            or not isinstance(data.get('expires_at'), (float, int))):
        raise YouTubeError('YouTube 需要重新使用 Google 授权；旧版浏览器登录态不支持官方 API')
    return data


def account_path(base, account):
    base = Path(base)
    ident = account.get('web_account_id')
    if ident is not None:
        with closing(sqlite3.connect(base / 'db/database.db')) as conn:
            row = conn.execute('SELECT type,filePath,status FROM user_info WHERE id=?', (ident,)).fetchone()
        if not row or row[0] != 6 or row[2] != 1:
            raise YouTubeError('YouTube 账号尚未授权，请到账号管理完成 Google 授权')
        path = (base / 'cookiesFile' / row[1]).resolve()
        if not path.is_relative_to((base / 'cookiesFile').resolve()):
            raise YouTubeError('YouTube 账号文件位置无效')
    else:
        alias = account.get('alias', '')
        if not re.fullmatch(r'[\w-]+', alias):
            raise YouTubeError('YouTube 账号绑定无效')
        path = base / 'cookies' / f'youtube_{alias}.json'
    read_credentials(path)
    return path


def request(session, method, url, **kwargs):
    try:
        return session.request(method, url, timeout=(15, 120), allow_redirects=False, **kwargs)
    except requests.RequestException:
        raise YouTubeError('Google API 网络请求未完成，请检查网络；已开始的投稿不会自动重发') from None


def body(response):
    if response.status_code not in (200, 201):
        messages = {400: '请求被拒绝，请检查客户端配置、授权或投稿参数',
                    401: '授权已失效，请重新进行 Google 授权',
                    403: '权限不足、API 未启用或配额受限，请检查 Google Cloud 和频道权限'}
        raise YouTubeError('Google API：' + messages.get(response.status_code, f'请求失败（HTTP {response.status_code}）'))
    try:
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise YouTubeError('Google API 未返回有效 JSON 回执') from None


def token_data(session, payload):
    result = body(request(session, 'POST', TOKEN_URL, data=payload))
    if not isinstance(result.get('access_token'), str) or not result['access_token']:
        raise YouTubeError('Google 未返回有效授权，请重新授权')
    try:
        expiry = int(result['expires_in'])
        if expiry <= 0:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise YouTubeError('Google 未返回有效授权期限') from None
    result['expires_at'] = time.time() + expiry
    if 'scope' in result and (not isinstance(result['scope'], str) or not set(SCOPES).issubset(result['scope'].split())):
        raise YouTubeError('请同时允许 YouTube 上传和读取频道权限')
    return result


def headers(data):
    return {'Authorization': 'Bearer ' + data['access_token']}


def channel(session, data):
    result = body(request(session, 'GET', API + 'channels', headers=headers(data),
                          params={'part': 'id,snippet', 'mine': 'true'}))
    rows = result.get('items', [])
    if len(rows) != 1 or not re.fullmatch(r'UC[A-Za-z0-9_-]+', rows[0].get('id', '')):
        raise YouTubeError('未找到唯一 YouTube 频道，请先创建频道，并在授权时选择需要投稿的频道')
    from utils.account_identity import account_name
    try:
        return rows[0]['id'], account_name(rows[0]['snippet']['title'])
    except (ValueError, KeyError, TypeError):
        raise YouTubeError('无法读取 YouTube 频道名称，请检查频道设置') from None


def credentials(path, session):
    from publishing.queue import file_lock
    with file_lock(Path(path).with_suffix('.oauth.lock')) as acquired:
        if not acquired:
            raise YouTubeError('YouTube 授权正在更新，请稍后再试')
        data = read_credentials(path)
        if data['expires_at'] <= time.time() + 180:
            fresh = token_data(session, {key: data[key] for key in ('client_id', 'client_secret', 'refresh_token')}
                               | {'grant_type': 'refresh_token'})
            data.update({key: fresh[key] for key in ('access_token', 'expires_at')})
            if fresh.get('refresh_token'):
                data['refresh_token'] = fresh['refresh_token']
            atomic_json(path, data)
        return data


def check_credentials(path):
    try:
        with requests.Session() as session:
            data = credentials(path, session)
            return channel(session, data)[0] == data['channel_id']
    except YouTubeError:
        return False


def authorize(base, queue, account_id=None, *, timeout=300):
    """Runs in a worker thread. Cancellation is checked before committing."""
    client = client_config(read_json(config_path(base)))
    base = Path(base)
    previous = None
    expected_channel = None
    with closing(sqlite3.connect(base / 'db/database.db')) as conn:
        if account_id is not None:
            previous = conn.execute('SELECT filePath FROM user_info WHERE id=? AND type=6', (account_id,)).fetchone()
            if not previous:
                raise YouTubeError('原 YouTube 账号已不存在，请刷新列表')
            old_path = (base / 'cookiesFile' / previous[0]).resolve()
            if not old_path.is_relative_to((base / 'cookiesFile').resolve()):
                raise YouTubeError('原账号文件位置无效')
            if old_path.is_file():
                old = read_json(old_path)
                expected_channel = old.get('channel_id') or old.get('publisher_channel_id')
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    callback = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Authorization codes must never reach access logs.

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            valid = (parsed.path == '/oauth/callback' and len(query.get('state', [])) == 1
                     and secrets.compare_digest(query['state'][0], state))
            if valid and (len(query.get('code', [])) == 1 or 'error' in query):
                callback.update({'code': query.get('code', [None])[0], 'denied': 'error' in query})
            else:
                valid = False
            content = ('授权回应已收到，请返回视频发布工作台查看验证结果。' if valid else '无效的授权回应，请返回工作台重新授权。').encode('utf-8')
            self.send_response(200 if valid else 400)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    class CallbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(3)
            return connection, address

    with CallbackServer(('127.0.0.1', 0), Handler) as server:
        server.timeout = 0.25
        redirect = f'http://127.0.0.1:{server.server_port}/oauth/callback'
        url = 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': client['client_id'], 'redirect_uri': redirect, 'response_type': 'code',
            'scope': ' '.join(SCOPES), 'state': state, 'code_challenge': challenge,
            'code_challenge_method': 'S256', 'access_type': 'offline', 'prompt': 'consent select_account'})
        if queue.cancelled.is_set():
            return
        if not webbrowser.open(url, new=1):
            raise YouTubeError('无法打开系统默认浏览器，请检查默认浏览器设置后重试')
        queue.put({'event': 'login-status', 'data': {'stage': 'browser_open', 'message': '请在默认浏览器选择 Google 账号及 YouTube 频道，并允许上传和读取权限'}})
        deadline = time.monotonic() + timeout
        while not callback and not queue.cancelled.is_set() and time.monotonic() < deadline:
            server.handle_request()
    if queue.cancelled.is_set():
        return
    if not callback or callback.get('denied'):
        raise YouTubeError('Google 授权已取消或超时，请重新授权')
    queue.put({'event': 'login-status', 'data': {'stage': 'verifying', 'message': '正在验证 YouTube 频道并保存授权'}})
    with requests.Session() as session:
        data = token_data(session, client | {'code': callback['code'], 'code_verifier': verifier,
                           'redirect_uri': redirect, 'grant_type': 'authorization_code'})
        if not data.get('refresh_token'):
            raise YouTubeError('未取得离线授权，请重新授权并允许所需权限')
        if not set(SCOPES).issubset(data.get('scope', '').split()):
            raise YouTubeError('授权回执缺少所需权限，请重新授权')
        channel_id, name = channel(session, data)
    if expected_channel and expected_channel != channel_id:
        raise YouTubeError('此次授权频道与原账号不同，请重新选择原频道，或通过“添加账号”新增频道')
    saved = client | {key: data[key] for key in ('access_token', 'refresh_token', 'expires_at')}
    saved.update(kind='youtube_oauth', scopes=SCOPES, channel_id=channel_id, account_name=name)
    target = base / 'cookiesFile' / f'{uuid.uuid4()}.json'
    committed = False
    try:
        with closing(sqlite3.connect(base / 'db/database.db')) as conn:
            conn.execute('BEGIN IMMEDIATE')
            if queue.cancelled.is_set():
                return
            for ident, filename in conn.execute('SELECT id,filePath FROM user_info WHERE type=6'):
                if ident == account_id:
                    continue
                candidate = (base / 'cookiesFile' / filename).resolve()
                if not candidate.is_relative_to((base / 'cookiesFile').resolve()):
                    raise YouTubeError('已有 YouTube 账号文件位置无效')
                if candidate.is_file():
                    existing = read_json(candidate)
                    if (existing.get('channel_id') or existing.get('publisher_channel_id')) == channel_id:
                        raise YouTubeError('这个 YouTube 频道已添加，请使用原账号的“重新登录”')
            atomic_json(target, saved)
            if account_id is None:
                conn.execute('INSERT INTO user_info(type,filePath,userName,status) VALUES(6,?,?,1)', (target.name, name))
            else:
                changed = conn.execute('UPDATE user_info SET filePath=?,userName=?,status=1 WHERE id=? AND type=6 AND filePath=?',
                                       (target.name, name, account_id, previous[0]))
                if changed.rowcount != 1:
                    raise YouTubeError('原账号已变更，未覆盖新数据，请刷新重试')
            if queue.cancelled.is_set():
                return
            conn.commit()
            committed = True
    finally:
        if not committed:
            target.unlink(missing_ok=True)
    queue.put('200')


def upload_metadata(material):
    from publishing.creator_browser import validate_material
    validate_material('youtube', material)
    return {'snippet': {'title': material['title'], 'description': material['description'],
                        'tags': material.get('tags', []), 'categoryId': str(material.get('category') or '28')},
            'status': {'privacyStatus': material['visibility'], 'selfDeclaredMadeForKids': material['made_for_kids'],
                       'containsSyntheticMedia': bool(material.get('ai_declaration'))}}


def preflight(video, cover, material, path):
    """Validate/refresh authorization before enqueue; never create an upload."""
    metadata = upload_metadata(material)
    size = Path(video).stat().st_size
    if not size:
        raise YouTubeError('视频文件为空')
    # Official thumbnails.set limit, checked 2026-09-27. The video workflow
    # separately targets <=2 MiB for faster uploads; this is not the API limit.
    if Path(cover).stat().st_size > 50_000_000:
        raise YouTubeError('YouTube API 封面不能超过 47.68 MiB（50000000 字节）')
    with Path(cover).open('rb') as image:
        signature = image.read(12)
    content_type = 'image/png' if signature.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if signature.startswith(b'\xff\xd8\xff') else None
    if not content_type:
        raise YouTubeError('YouTube 自定义封面必须是 PNG 或 JPEG')
    with requests.Session() as session:
        data = credentials(path, session)
        if channel(session, data)[0] != data['channel_id']:
            raise YouTubeError('当前授权频道与绑定频道不同，请重新授权')
    return metadata, size, content_type, data


def publish(video, cover, material, path, progress):
    from uploader.tencent_uploader.flow import SubmissionNotStarted
    try:
        metadata, size, content_type, data = preflight(video, cover, material, path)
    except (ValueError, OSError):
        raise SubmissionNotStarted('YouTube 投稿前检查未通过，请检查 Google 授权、视频、封面与发布参数', stage='preflight', upload_started=False) from None
    with requests.Session() as session:
        response = request(session, 'POST', UPLOAD + 'videos', params={'uploadType': 'resumable', 'part': 'snippet,status'},
                           headers=headers(data) | {'X-Upload-Content-Length': str(size), 'X-Upload-Content-Type': 'video/mp4'}, json=metadata)
        if response.status_code not in (200, 201):
            body(response)
        location = response.headers.get('Location', '')
        parsed = urlsplit(location)
        if (parsed.scheme != 'https' or parsed.hostname not in ('www.googleapis.com', 'youtube.googleapis.com')
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise YouTubeError('YouTube 未返回可信上传会话，未重试投稿')
        offset, stalls, uploaded = 0, 0, None
        with Path(video).open('rb') as stream:
            while uploaded is None:
                data = credentials(path, session)
                stream.seek(offset)
                chunk = stream.read(CHUNK)
                progress({'stage': 'media_upload', 'message': f'YouTube 已接收 {offset * 100 // size}%', 'submission_started': True})
                try:
                    response = request(session, 'PUT', location, headers=headers(data) | {
                        'Content-Type': 'video/mp4', 'Content-Range': f'bytes {offset}-{offset + len(chunk) - 1}/{size}'}, data=chunk)
                except YouTubeError:
                    response = None
                if response is None or response.status_code >= 500:
                    # Probe the SAME session. Never create another insert after an ambiguous PUT.
                    response = request(session, 'PUT', location, headers=headers(data) | {'Content-Range': f'bytes */{size}'}, data=b'')
                if response.status_code in (200, 201):
                    uploaded = body(response)
                    break
                if response.status_code != 308:
                    body(response)
                match = re.fullmatch(r'bytes=0-(\d+)', response.headers.get('Range', ''))
                next_offset = int(match[1]) + 1 if match else 0
                if next_offset < offset or next_offset > min(offset + len(chunk), size) or next_offset == size:
                    raise YouTubeError('YouTube 上传进度回执不一致，请核对频道内容；不会自动重发')
                stalls = stalls + 1 if next_offset == offset else 0
                if stalls >= 3:
                    raise YouTubeError('YouTube 上传未继续推进，请核对频道内容；不会自动重发')
                offset = next_offset
        remote_id = uploaded.get('id', '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', remote_id):
            raise YouTubeError('YouTube 上传回执缺少有效视频 ID，请到 Studio 核对，不会自动重发')
        receipt = {'status': 'found', 'remote_id': remote_id, 'url': f'https://www.youtube.com/watch?v={remote_id}',
                   'source': 'official_api_receipt', 'note': 'YouTube API 已接收视频，等待回读处理状态及实际可见性。', 'warnings': []}
        # Persist the known ID BEFORE the optional cover request so a crash cannot lose it.
        progress({'stage': 'submitting', 'message': '视频已接收，正在设置封面', 'submission_started': True,
                  'remote_id': remote_id, 'url': receipt['url'], 'warnings': ['自定义封面尚未确认完成，请在 Studio 核对。']})
        try:
            data = credentials(path, session)
            with Path(cover).open('rb') as stream:
                body(request(session, 'POST', UPLOAD + 'thumbnails/set', params={'videoId': remote_id},
                             headers=headers(data) | {'Content-Type': content_type}, data=stream))
        except (YouTubeError, OSError):
            receipt['warnings'] = ['视频已上传，但自定义封面未确认成功；请在 YouTube Studio 核对并补设封面，不要重新上传视频。']
        return receipt


def readback(remote_id, path):
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', remote_id):
        raise YouTubeError('YouTube 视频 ID 格式无效')
    with requests.Session() as session:
        data = credentials(path, session)
        result = body(request(session, 'GET', API + 'videos', headers=headers(data),
                              params={'part': 'snippet,status,processingDetails', 'id': remote_id}))
    rows = result.get('items', [])
    if len(rows) != 1 or rows[0].get('id') != remote_id or rows[0].get('snippet', {}).get('channelId') != data['channel_id']:
        return {'status': 'unavailable', 'message': '未找到属于原频道的唯一视频，保留原任务，不自动重发'}
    status = rows[0].get('status', {})
    processed = rows[0].get('processingDetails', {}).get('processingStatus')
    privacy = status.get('privacyStatus')
    state, note = 'processing', 'YouTube 正在处理视频。'
    if status.get('uploadStatus') in ('failed', 'rejected', 'deleted') or processed in ('failed', 'terminated'):
        state, note = 'needs_action', 'YouTube 视频处理失败、被拒绝或已删除，请到 Studio 核对。'
    elif status.get('uploadStatus') == 'processed' or processed == 'succeeded':
        if privacy == 'public':
            state, note = 'published', 'YouTube API 确认处理完成，实际可见性为公开。'
        else:
            state, note = 'needs_action', '视频已处理，实际可见性为' + {'private': '私享', 'unlisted': '不公开'}.get(privacy, '未知') + '。未通过 API 审核的项目上传受私享限制，请在 Studio 核对。'
    return {'status': 'found', 'state': state, 'remote_id': remote_id, 'privacy': privacy,
            'processing': processed, 'note': note, 'checked_at': datetime.now(timezone.utc).isoformat()}
