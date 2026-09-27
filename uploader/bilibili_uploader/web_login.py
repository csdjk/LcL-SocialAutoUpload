"""Bilibili Web account adapter. Credentials never appear in logs or API replies.

The TV QR / web-cookie authorization protocol and LoginInfo format follow
biliup's crates/biliup/src/uploader/credential.rs (MIT). User confirmation is
required for QR login; platform rejection is never treated as successful auth.
"""
import asyncio
import base64
import hashlib
import io
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import segno
from playwright.async_api import async_playwright
from conf import BASE_DIR
from utils.douyin_credentials import CredentialFormatError, MAX_CREDENTIAL_BYTES

TYPE = 5
# Public client protocol constants used by the existing biliup runtime, not user secrets.
_APP_KEY = '4409e2ce8ffd12b8'
_APP_SECRET = '59b43e04ad6965f34319062b478f83dd'
_HEADERS = {'Referer': 'https://www.bilibili.com/', 'User-Agent': 'Mozilla/5.0'}
_PASSPORT = 'https://passport.bilibili.com/x/passport-tv-login'


def _signed(**values):
    values = {'appkey': _APP_KEY, 'local_id': '0', 'ts': str(int(time.time())), **values}
    encoded = urlencode(sorted(values.items()))
    return {**values, 'sign': hashlib.md5((encoded + _APP_SECRET).encode()).hexdigest()}


def normalize_bilibili_credentials(text):
    if not isinstance(text, str) or not text.strip():
        raise CredentialFormatError('请粘贴完整 B 站 Cookie，或选择登录态 JSON')
    if len(text.encode('utf-8')) > MAX_CREDENTIAL_BYTES:
        raise CredentialFormatError('登录态内容不能超过 512 KB')
    text = text.strip().lstrip('\ufeff')
    native = False
    try:
        if text.startswith(('{', '[')):
            value = json.loads(text)
            native = isinstance(value, dict) and isinstance(value.get('cookie_info'), dict)
            if native:
                items = value['cookie_info'].get('cookies')
            elif isinstance(value, list):
                items = value
            elif isinstance(value, dict):
                items = value.get('cookies')
            else:
                items = None
        else:
            value = {}
            text = re.sub(r'^cookie\s*:\s*', '', text, count=1, flags=re.I)
            if '\n' in text or '\r' in text:
                raise CredentialFormatError('只粘贴 Cookie 的完整值，不要粘贴整段请求或 cURL')
            items = []
            for part in text.split(';'):
                if not part.strip():
                    continue
                name, separator, val = part.strip().partition('=')
                if not separator:
                    raise CredentialFormatError('单个 Token 不能替代 B 站完整 Cookie')
                items.append({'name': name, 'value': val, 'domain': '.bilibili.com'})
        if not isinstance(items, list) or not items or len(items) > 1000:
            raise CredentialFormatError('需要 B 站 Cookie 数组、storage_state 或 biliup 登录态 JSON')
        cookies = {}
        for item in items:
            if not isinstance(item, dict):
                raise CredentialFormatError('Cookie 格式不正确')
            domain = item.get('domain', '.bilibili.com' if native else '')
            if not isinstance(domain, str) or domain.lower().lstrip('.') not in (
                'bilibili.com', 'www.bilibili.com', 'passport.bilibili.com',
                'member.bilibili.com', 'api.bilibili.com'):
                continue
            name, val = item.get('name'), item.get('value')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_!#$%&\'*+.^`|~-]+', name):
                raise CredentialFormatError('Cookie 名称格式不正确')
            if not isinstance(val, str) or any(ord(c) < 32 or ord(c) == 127 for c in val):
                raise CredentialFormatError('Cookie 值格式不正确')
            expires = item.get('expires', item.get('expirationDate', -1))
            if expires is not None and (isinstance(expires, bool) or not isinstance(expires, (float, int)) or not math.isfinite(expires)):
                raise CredentialFormatError('Cookie 有效期格式不正确')
            if expires not in (None, -1, 0) and expires <= time.time():
                continue
            if name in cookies and cookies[name] != val:
                raise CredentialFormatError('存在同名但值不同的 Cookie，请只复制当前登录页面的一组 Cookie')
            cookies[name] = val
        if not all(cookies.get(k) for k in ('SESSDATA', 'bili_jct')):
            raise CredentialFormatError('缺少 B 站 SESSDATA 或 bili_jct，请登录 B 站后复制完整 Cookie')
        info = {'cookie_info': {'cookies': [{'name': k, 'value': v} for k, v in cookies.items()]},
                'sso': [], 'platform': None,
                'token_info': {'access_token': '', 'refresh_token': '', 'expires_in': 0,
                               'mid': int(cookies.get('DedeUserID', '0'))}}
        if native and isinstance(value.get('token_info'), dict):
            token = value['token_info']
            if value.get('platform') in ('BiliTV', 'Android', None) and all(
                    isinstance(token.get(k), str) for k in ('access_token', 'refresh_token')):
                info['platform'] = value.get('platform')
                info['token_info'] = {'access_token': token['access_token'], 'refresh_token': token['refresh_token'],
                                     'mid': int(token.get('mid', 0)), 'expires_in': int(token.get('expires_in', 0))}
        return info
    except CredentialFormatError:
        raise
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise CredentialFormatError('B 站登录态格式不正确，请重新复制或导出') from None


def _storage(info):
    return {'cookies': [{'name': c['name'], 'value': c['value'], 'domain': '.bilibili.com',
                         'path': '/', 'secure': True, 'httpOnly': True, 'sameSite': 'Lax', 'expires': -1}
                        for c in info['cookie_info']['cookies']], 'origins': []}


async def _json_response(response):
    try:
        if not response.ok:
            raise RuntimeError(f'B 站请求失败（HTTP {response.status}），请稍后重试')
        result = await response.json()
        if not isinstance(result, dict):
            raise RuntimeError('B 站返回格式异常，请稍后重试')
        return result
    finally:
        await response.dispose()


async def _nav(api):
    payload = await _json_response(await api.get('https://api.bilibili.com/x/web-interface/nav', timeout=10000, max_redirects=0))
    if payload.get('code') == -101:
        return None
    if payload.get('code') != 0:
        raise RuntimeError('B 站暂时无法验证登录态，请稍后重试')
    data = payload.get('data') or {}
    if data.get('isLogin') is not True:
        return None
    if not isinstance(data.get('mid'), int) or data['mid'] <= 0:
        raise RuntimeError('B 站未返回有效账号信息')
    return data


async def request_qrcode(api):
    payload = await _json_response(await api.post(_PASSPORT + '/qrcode/auth_code', form=_signed(), timeout=10000, max_redirects=0))
    data = payload.get('data') or {}
    if payload.get('code') != 0 or not data.get('auth_code') or not data.get('url'):
        raise RuntimeError('B 站二维码获取失败，请稍后重试或改用 Cookie 导入')
    parsed = urlsplit(data['url'])
    if parsed.scheme not in ('https', 'http') or parsed.hostname != 'passport.bilibili.com' or parsed.username or parsed.password:
        raise RuntimeError('B 站二维码地址校验失败')
    return data


async def _poll(api, auth_code):
    return await _json_response(await api.post(_PASSPORT + '/qrcode/poll',
        form=_signed(auth_code=auth_code), timeout=10000, max_redirects=0))


def _native_result(payload):
    data = payload.get('data')
    if payload.get('code') != 0 or not isinstance(data, dict):
        raise RuntimeError('B 站尚未完成登录授权')
    info = normalize_bilibili_credentials(json.dumps({**data, 'platform': 'BiliTV'}))
    if not info['token_info']['access_token'] or not info['token_info']['refresh_token'] or info['token_info']['mid'] <= 0:
        raise RuntimeError('B 站登录授权不完整，请重试')
    return info


async def _prepare_native(api, info, expected_mid):
    """Cookie import explicitly authorizes this local uploader, as biliup does."""
    qr = await request_qrcode(api)
    csrf = next(c['value'] for c in info['cookie_info']['cookies'] if c['name'] == 'bili_jct')
    result = await _json_response(await api.post(_PASSPORT + '/h5/qrcode/confirm',
        form={'auth_code': qr['auth_code'], 'csrf': csrf, 'scanning_type': '3'},
        headers={'User-Agent': 'Mozilla/5.0 BiliApp'}, timeout=10000, max_redirects=0))
    if result.get('code') != 0:
        raise RuntimeError('B 站未允许此次登录态授权，请完成官方安全验证或使用扫码登录')
    for _ in range(5):
        payload = await _poll(api, qr['auth_code'])
        if payload.get('code') == 0:
            native = _native_result(payload)
            if native['token_info']['mid'] != expected_mid:
                raise RuntimeError('B 站授权账号与导入账号不一致，未保存')
            return native
        if payload.get('code') != 86039:
            raise RuntimeError('B 站授权未通过，请重新扫码')
        await asyncio.sleep(1)
    raise RuntimeError('B 站授权等待超时，请使用扫码登录')


async def validate_bilibili_credentials(account_file, *, prepare=False, require_name=False):
    from utils.account_identity import account_name, AccountNameError
    try:
        info = normalize_bilibili_credentials(Path(account_file).read_text(encoding='utf-8'))
        async with async_playwright() as p:
            api = await p.request.new_context(storage_state=_storage(info), extra_http_headers=_HEADERS)
            try:
                user = await _nav(api)
                if user is None:
                    return {'success': False, 'status': 'invalid', 'message': 'B 站登录态已失效，请重新扫码或导入'}
                if prepare:
                    # Re-authorize imported browser cookies to obtain a complete biliup LoginInfo.
                    info = await _prepare_native(api, info, user['mid'])
                    Path(account_file).write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
                return {'success': True, 'mid': user['mid'],
                        'account_name': account_name(user.get('uname')) if require_name else user.get('uname')}
            finally:
                await api.dispose()
    except (CredentialFormatError, AccountNameError) as exc:
        return {'success': False, 'status': 'invalid', 'message': str(exc)}
    except Exception:
        return {'success': False, 'status': 'unavailable', 'message': 'B 站登录态验证或授权未完成，请重试；平台要求验证时请先在官网完成'}


async def prepare_bilibili_credentials(account_file, *, require_name=False):
    try:
        return await asyncio.wait_for(validate_bilibili_credentials(account_file, prepare=True, require_name=require_name), 35)
    except asyncio.TimeoutError:
        return {'success': False, 'status': 'unavailable', 'message': 'B 站验证超时，账号未保存，请重试'}


async def cookie_auth(account_file):
    result = await validate_bilibili_credentials(account_file)
    if result.get('status') == 'unavailable':
        raise RuntimeError('B 站网络验证暂未完成')
    return result['success']


async def get_bilibili_cookie(name, status_queue, *, account_id=None):
    root = Path(BASE_DIR)
    target = root / 'cookiesFile' / f'{uuid.uuid4()}.json'
    committed = False
    previous = None
    def status(message):
        status_queue.put({'event': 'login-status', 'data': {'message': message}})
    try:
        with closing(sqlite3.connect(root/'db/database.db')) as conn:
            if account_id is not None:
                previous = conn.execute('SELECT filePath FROM user_info WHERE id=? AND type=5', (account_id,)).fetchone()
                if previous is None:
                    raise RuntimeError('需要重新登录的 B站账号不存在，请刷新列表')
            elif conn.execute('SELECT 1 FROM user_info WHERE type=5 AND userName=?', (name.strip(),)).fetchone():
                raise RuntimeError('已有同名 B 站账号，请使用“上传Cookie”更新登录态')
        async with async_playwright() as p:
            api = await p.request.new_context(extra_http_headers=_HEADERS)
            try:
                status('正在获取 B 站登录二维码…')
                qr = await request_qrcode(api)
                buf = io.BytesIO()
                segno.make(qr['url'], micro=False).save(buf, kind='png', scale=6, border=4)
                status_queue.put('data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode())
                status('请使用哔哩哔哩 App 扫码，并在手机上确认登录')
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    if status_queue.cancelled.is_set():
                        return
                    payload = await _poll(api, qr['auth_code'])
                    code = payload.get('code')
                    if code == 0:
                        info = _native_result(payload)
                        status('已确认登录，正在验证并保存 B 站账号…')
                        target.parent.mkdir(exist_ok=True)
                        target.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
                        auto_name = account_id is None and not name.strip()
                        valid = await validate_bilibili_credentials(target, require_name=True) if auto_name else await validate_bilibili_credentials(target)
                        if not valid['success']:
                            raise RuntimeError(valid['message'])
                        if auto_name:
                            name = valid['account_name']
                        if status_queue.cancelled.is_set():
                            return
                        with closing(sqlite3.connect(root/'db/database.db')) as conn:
                            conn.execute('BEGIN IMMEDIATE')
                            if account_id is not None:
                                updated = conn.execute('UPDATE user_info SET filePath=?,status=1 WHERE id=? AND type=5 AND filePath=?', (target.name, account_id, previous[0]))
                                if updated.rowcount != 1:
                                    raise RuntimeError('账号已被删除或更新，请刷新后重试')
                            else:
                                if conn.execute('SELECT 1 FROM user_info WHERE type=5 AND userName=?', (name.strip(),)).fetchone():
                                    raise RuntimeError('已有同名 B 站账号，请刷新列表')
                                conn.execute('INSERT INTO user_info (type,filePath,userName,status) VALUES (5,?,?,1)',
                                             (target.name, name.strip()))
                            conn.commit()
                        committed = True
                        status_queue.put('200')
                        return
                    if code not in (86039, 86101, 86090):
                        raise RuntimeError('B 站二维码已过期或登录被取消，请重新获取二维码')
                    if code == 86090:
                        status('B 站已收到扫码，请在手机上确认')
                    await asyncio.sleep(2)
                raise RuntimeError('B 站扫码等待超时，请重新获取二维码')
            finally:
                await api.dispose()
    except RuntimeError as exc:
        status_queue.put({'event': 'login-error', 'data': {'message': str(exc)}})
        status_queue.put('500')
    except Exception:
        status_queue.put({'event': 'login-error', 'data': {'message': 'B 站登录服务暂不可用，请重试或改用 Cookie 导入'}})
        status_queue.put('500')
    finally:
        if not committed:
            target.unlink(missing_ok=True)
