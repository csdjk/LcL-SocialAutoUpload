"""Normalize user-supplied Douyin browser state and validate it without publishing.

Credentials must never be included in logs, exception messages, URLs or API replies.
"""
import asyncio
import json
import math
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from patchright.async_api import async_playwright
from conf import LOCAL_CHROME_PATH

MAX_CREDENTIAL_BYTES = 512 * 1024
SESSION_COOKIES = {'sessionid', 'sessionid_ss'}


class CredentialFormatError(ValueError):
    """Contains only a fixed, user-safe error message."""


def _douyin_host(host):
    return isinstance(host, str) and (host.lower().lstrip('.') == 'douyin.com'
        or host.lower().lstrip('.').endswith('.douyin.com'))


def normalize_douyin_credentials(text):
    """Accept a Cookie header, browser-export cookie array, or storage_state JSON."""
    return normalize_browser_credentials(text, _douyin_host, SESSION_COOKIES, "抖音")


def normalize_browser_credentials(text, allowed_host, session_names, platform_name):
    if not isinstance(text, str) or not text.strip():
        raise CredentialFormatError('请粘贴完整 Cookie，或选择登录态 JSON 文件')
    if len(text.encode('utf-8')) > MAX_CREDENTIAL_BYTES:
        raise CredentialFormatError(f'登录态内容超过 512 KiB，请仅导出{platform_name}网站的登录态')
    text = text.strip().lstrip('\ufeff')
    origins = []
    if text.startswith(('{', '[')):
        try:
            payload = json.loads(text)
        except (ValueError, RecursionError):
            raise CredentialFormatError('JSON 格式不正确，请选择浏览器导出的 Cookie 或登录态文件') from None
        if isinstance(payload, list):
            raw_cookies = payload
        elif isinstance(payload, dict) and isinstance(payload.get('cookies'), list):
            raw_cookies = payload['cookies']
            origins = payload.get('origins', [])
        else:
            raise CredentialFormatError('需要 Cookie 数组或包含 cookies 字段的登录态 JSON，不支持开放平台 access_token')
    else:
        text = re.sub(r'^cookie\s*:\s*', '', text, count=1, flags=re.I)
        if '\n' in text or '\r' in text:
            raise CredentialFormatError('请只复制请求标头中 Cookie 的完整值，不要复制整段请求或 cURL 命令')
        raw_cookies = []
        for pair in text.split(';'):
            if not pair.strip():
                continue
            name, separator, value = pair.strip().partition('=')
            if not separator:
                raise CredentialFormatError('单个 Token 不能替代网页登录态，请复制完整的 Cookie 请求标头')
            raw_cookies.append({'name': name.strip(), 'value': value.strip(),
                'domain': '.douyin.com', 'path': '/', 'secure': True, 'httpOnly': True})
    if not raw_cookies or len(raw_cookies) > 1000:
        raise CredentialFormatError('Cookie 列表为空或数量过多')
    cookies = {}
    for item in raw_cookies:
        if not isinstance(item, dict):
            raise CredentialFormatError('Cookie 列表格式不正确')
        domain = item.get('domain')
        if not domain and isinstance(item.get('url'), str):
            domain = urlsplit(item['url']).hostname
        if not allowed_host(domain):
            continue  # Do not retain credentials belonging to unrelated websites.
        domain = domain.lower()
        name, value = item.get('name'), item.get('value')
        if not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise CredentialFormatError('Cookie 名称格式不正确')
        if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise CredentialFormatError('Cookie 值包含无效字符')
        path = item.get('path', '/')
        if not isinstance(path, str) or not path.startswith('/'):
            raise CredentialFormatError('Cookie 路径格式不正确')
        expires = item.get('expires', item.get('expirationDate', -1))
        if item.get('session') is True or expires in (None, 0):
            expires = -1
        if isinstance(expires, bool) or not isinstance(expires, (float, int)) or not math.isfinite(expires):
            raise CredentialFormatError('Cookie 有效期格式不正确')
        if expires != -1 and expires <= time.time():
            continue
        same_site = {'strict': 'Strict', 'lax': 'Lax', 'none': 'None',
            'no_restriction': 'None', 'unspecified': 'Lax'}.get(str(item.get('sameSite', 'Lax')).lower())
        if not same_site:
            raise CredentialFormatError('Cookie SameSite 格式不正确')
        for field in ('secure', 'httpOnly'):
            if field in item and not isinstance(item[field], bool):
                raise CredentialFormatError('Cookie 安全属性格式不正确')
        cookie = {'name': name, 'value': value, 'domain': domain, 'path': path,
            'expires': expires, 'secure': item.get('secure', True),
            'httpOnly': item.get('httpOnly', False), 'sameSite': same_site}
        if same_site == 'None':
            cookie['secure'] = True
        cookies[(name, domain, path)] = cookie
    if not cookies or (session_names and not any(c['name'] in session_names and c['value'] for c in cookies.values())):
        raise CredentialFormatError(f'未找到有效的{platform_name}会话 Cookie。请先登录该平台创作者中心，再连接；不要使用开放平台 access_token')
    if not isinstance(origins, list) or len(origins) > 100:
        raise CredentialFormatError('登录态 origins 格式不正确')
    clean_origins = []
    for item in origins:
        if not isinstance(item, dict) or not isinstance(item.get('origin'), str):
            raise CredentialFormatError('登录态网站地址格式不正确')
        parsed = urlsplit(item['origin'])
        if parsed.scheme != 'https' or not allowed_host(parsed.hostname) or parsed.username or parsed.password:
            continue
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise CredentialFormatError('登录态 origin 必须为网站域名')
        storage = item.get('localStorage', [])
        if not isinstance(storage, list) or any(not isinstance(s, dict)
                or not isinstance(s.get('name'), str) or not isinstance(s.get('value'), str) for s in storage):
            raise CredentialFormatError('登录态 localStorage 格式不正确')
        clean = {'origin': parsed.scheme + '://' + parsed.netloc,
            'localStorage': [{'name': s['name'], 'value': s['value']} for s in storage]}
        # IndexedDB is a native storage_state field, not arbitrary executable code.
        if 'indexedDB' in item:
            if not isinstance(item['indexedDB'], list):
                raise CredentialFormatError('登录态 IndexedDB 格式不正确')
            clean['indexedDB'] = item['indexedDB']
        clean_origins.append(clean)
    return {'cookies': list(cookies.values()), 'origins': clean_origins}


async def _visible(page, locator):
    try:
        return await locator.count() > 0 and await locator.first.is_visible()
    except Exception:
        return False


async def inspect_douyin_authenticated_page(page):
    """Return valid/invalid/verification/pending, using positive rendered markers."""
    parsed = urlsplit(page.url)
    if parsed.hostname != 'creator.douyin.com':
        return 'pending'
    for text in ('安全验证', '身份验证', '请完成验证'):
        if await _visible(page, page.get_by_text(text, exact=True)):
            return 'verification'
    for text in ('扫码登录', '手机号登录', '登录/注册'):
        if await _visible(page, page.get_by_text(text, exact=True)):
            return 'invalid'
    if not parsed.path.startswith('/creator-micro/'):
        return 'pending'
    for text in ('发布视频', '发布作品', '退出登录'):
        if await _visible(page, page.get_by_text(text, exact=True)):
            return 'valid'
    for selector in ('a[href*="/creator-micro/content/manage"]', 'a[href*="/creator-micro/content/upload"]'):
        if await _visible(page, page.locator(selector)):
            return 'valid'
    if await _visible(page, page.get_by_text('首页', exact=True)) and (
        await _visible(page, page.get_by_text('作品管理', exact=True))
        or await _visible(page, page.get_by_text('内容管理', exact=True))
    ):
        return 'valid'
    return 'pending'


async def validate_douyin_credentials(account_file, *, timeout=30, require_name=False):
    """Read the official creator home page only; never upload/publish or solve checks."""
    from utils.account_identity import read_account_name, AccountNameError
    browser = None
    async def run():
        nonlocal browser
        async with async_playwright() as p:
            options = {'headless': True, 'timeout': 15000}
            if LOCAL_CHROME_PATH:
                options['executable_path'] = LOCAL_CHROME_PATH
            else:
                options['channel'] = 'chrome'
            browser = await p.chromium.launch(**options)
            try:
                context = await browser.new_context(storage_state=str(account_file))
                page = await context.new_page()
                await page.goto('https://creator.douyin.com/creator-micro/home',
                    wait_until='domcontentloaded', timeout=20000)
                for _ in range(30):
                    status = await inspect_douyin_authenticated_page(page)
                    if status == 'valid':
                        cookies = await context.cookies('https://creator.douyin.com/')
                        if any(c.get('name') in SESSION_COOKIES and c.get('value') for c in cookies):
                            nickname = await read_account_name(3, page) if require_name else None
                            # Preserve refreshed browser state after a successful read-only check.
                            await context.storage_state(path=str(account_file), indexed_db=True)
                            return {'success': True, 'status': 'valid', 'account_name': nickname}
                    if status == 'verification':
                        return {'success': False, 'status': 'verification_required',
                            'message': '抖音仍要求安全验证，请先在原浏览器或抖音 App 完成验证，再重新导入；Cookie 不能跳过平台验证'}
                    # Give an initial login shell time to hydrate with the imported session.
                    if status == 'invalid' and _ >= 3:
                        return {'success': False, 'status': 'cookie_invalid',
                            'message': '登录态已失效或不完整。请在原浏览器登录抖音创作者中心后重新复制完整 Cookie'}
                    await asyncio.sleep(0.4)
                return {'success': False, 'status': 'unconfirmed',
                    'message': '未能确认创作者中心已登录，账号未保存。请检查网络，或改用完整登录态 JSON'}
            finally:
                await browser.close()
    try:
        return await asyncio.wait_for(run(), timeout=timeout)
    except AccountNameError as exc:
        return {'success': False, 'status': 'name_unavailable', 'message': str(exc)}
    except asyncio.TimeoutError:
        return {'success': False, 'status': 'timeout', 'message': '验证超时，账号未保存。请检查网络后重试'}
    except Exception:
        # Playwright exception text can contain a URL or stored state; never expose it.
        return {'success': False, 'status': 'verification_error',
            'message': '无法完成验证，账号未保存。请确认本机 Chrome 可运行，并能打开抖音创作者中心'}
