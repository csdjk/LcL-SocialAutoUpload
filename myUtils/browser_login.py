"""User-driven Edge login with automatic, platform-scoped state persistence.

No daily browser profile is opened, copied or decrypted. The user completes the
real site's login/security checks in an isolated visible Edge window. Neither
URLs containing credentials nor cookie values are logged or returned to the UI.
"""
import asyncio
import hashlib
import json
import re
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import closing, ExitStack
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, TimeoutError as BrowserTimeout
from conf import BASE_DIR
from utils.douyin_credentials import inspect_douyin_authenticated_page, normalize_douyin_credentials
from uploader.bilibili_uploader.web_login import (
    _nav, normalize_bilibili_credentials, prepare_bilibili_credentials,
)
from uploader.tencent_uploader.main import _is_tencent_login_completed
from utils.account_identity import read_account_name, AccountNameError

PLATFORMS = {
    6: {'name': 'YouTube', 'url': 'https://studio.youtube.com/',
        'host': 'studio.youtube.com', 'root': 'youtube.com'},
    7: {'name': '今日头条', 'url': 'https://mp.toutiao.com/',
        'host': 'mp.toutiao.com', 'root': 'toutiao.com'},
    1: {'name': '小红书', 'url': 'https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video',
        'host': 'creator.xiaohongshu.com', 'root': 'xiaohongshu.com'},
    2: {'name': '视频号', 'url': 'https://channels.weixin.qq.com/',
        'host': 'channels.weixin.qq.com', 'root': 'weixin.qq.com'},
    3: {'name': '抖音', 'url': 'https://creator.douyin.com/',
        'host': 'creator.douyin.com', 'root': 'douyin.com'},
    4: {'name': '快手', 'url': 'https://cp.kuaishou.com/article/publish/video',
        'host': 'cp.kuaishou.com', 'root': 'kuaishou.com'},
    5: {'name': 'B站', 'url': 'https://member.bilibili.com/platform/home',
        'host': 'member.bilibili.com', 'root': 'bilibili.com'},
}
_active = set()
_active_guard = threading.Lock()
_logger = logging.getLogger(__name__)


class BrowserLoginError(RuntimeError):
    """Only fixed, user-safe messages may be passed to this exception."""


def _claim(key):
    with _active_guard:
        if key in _active:
            raise BrowserLoginError('该账号已有登录窗口，请先完成或取消上一次登录')
        _active.add(key)


def _release(key):
    with _active_guard:
        _active.discard(key)


def scoped_state(platform, state):
    """Retain only the selected platform's cookies and browser storage."""
    root = PLATFORMS[platform]['root']
    def belongs(host):
        return host == root or host.endswith('.' + root)
    cookies = [c for c in state.get('cookies', []) if belongs(c.get('domain', '').lstrip('.').lower())
               or (platform == 2 and c.get('domain', '').lstrip('.').lower() == 'qq.com')]
    origins = []
    for origin in state.get('origins', []):
        parsed = urlsplit(origin.get('origin', ''))
        if parsed.scheme == 'https' and belongs(parsed.hostname or ''):
            origins.append(origin)
    if not cookies:
        raise BrowserLoginError('尚未获取到有效登录态，请在打开的浏览器完成登录')
    result = {'cookies': cookies, 'origins': origins}
    if platform == 3:
        return normalize_douyin_credentials(json.dumps(result, ensure_ascii=False))
    if platform == 5:
        return normalize_bilibili_credentials(json.dumps(result, ensure_ascii=False))
    return result


async def visible(locator):
    try:
        return await locator.count() > 0 and await locator.first.is_visible()
    except Exception:
        return False


async def inspect_page(platform, page):
    """Do not infer authentication from a URL change or a missing login form."""
    if page.is_closed():
        return 'pending'
    parsed = urlsplit(page.url)
    expected = PLATFORMS[platform]
    if parsed.scheme != 'https' or not parsed.hostname:
        return 'pending'
    if platform == 5:
        if parsed.hostname not in ('www.bilibili.com', 'member.bilibili.com', 'passport.bilibili.com'):
            return 'pending'
    elif parsed.hostname != expected['host']:
        return 'pending'
    # Never dismiss, click through, solve or treat a visible challenge as success.
    for text in ('安全验证', '身份验证', '请完成验证', '验证身份'):
        if await visible(page.get_by_text(text, exact=True)):
            return 'verification_required'
    if platform == 3:
        status = await inspect_douyin_authenticated_page(page)
        if status != 'valid':
            return 'verification_required' if status == 'verification' else 'pending'
        cookies = await page.context.cookies('https://creator.douyin.com/')
        return 'valid' if any(c['name'] in ('sessionid', 'sessionid_ss') and c['value'] for c in cookies) else 'pending'
    if platform == 5:
        cookies = await page.context.cookies('https://www.bilibili.com/')
        names = {c['name'] for c in cookies if c.get('value')}
        if not {'SESSDATA', 'bili_jct'}.issubset(names):
            return 'pending'
        # Authenticated server response is stronger than generic B站 page text.
        return 'valid' if await _nav(page.context.request) else 'pending'
    if platform == 2:
        if await _is_tencent_login_completed(page) and await page.context.cookies(expected['url']):
            return 'valid'
        return 'pending'
    if any(word in parsed.path.lower() for word in ('login', 'passport')):
        return 'pending'
    for text in ('扫码登录', '手机号登录', '立即登录', 'APP扫一扫登录'):
        if await visible(page.get_by_text(text, exact=True)):
            return 'pending'
    if not await page.context.cookies(expected['url']):
        return 'pending'
    if platform == 1:
        # Authenticated creator navigation, not public-site marketing copy.
        if await visible(page.get_by_text('笔记管理', exact=True)) and (
            await visible(page.get_by_text('创作首页', exact=True))
            or await visible(page.get_by_text('发布笔记', exact=True))
            or await visible(page.get_by_text('首页', exact=True))
        ):
            return 'valid'
    if platform == 4:
        if parsed.path.startswith('/article/') and await visible(page.locator("button[class^='_upload-btn']")):
            return 'valid'
        if parsed.path not in ('', '/') and await visible(page.get_by_text('作品管理', exact=True)) and (
            await visible(page.get_by_text('发布视频', exact=True)) or await visible(page.get_by_text('首页', exact=True))
        ):
            return 'valid'
    if platform == 6:
        if re.match(r'^/channel/UC[A-Za-z0-9_-]+', parsed.path) and await visible(page.locator('ytcp-app')) and await visible(page.locator('ytcp-navigation-drawer')):
            return 'valid'
    if platform == 7:
        cookies = await page.context.cookies(expected['url'])
        session = any(c['name'] in ('sessionid', 'sessionid_ss') and c.get('value') for c in cookies)
        if session and await visible(page.get_by_text(re.compile(r'^(内容管理|作品管理)$'))) and await visible(page.get_by_text(re.compile(r'^(创作|发布视频)$'))):
            return 'valid'
    return 'pending'


def _previous(database, platform, name, account_id):
    with closing(sqlite3.connect(database)) as conn:
        if account_id is not None:
            row = conn.execute('SELECT filePath,userName FROM user_info WHERE id=? AND type=?', (account_id, platform)).fetchone()
            if not row:
                raise BrowserLoginError('原账号不存在或平台不符，请刷新账号列表')
            return row
        if conn.execute('SELECT 1 FROM user_info WHERE type=? AND userName=?', (platform, name)).fetchone():
            raise BrowserLoginError('已有同名账号，请使用该账号的“重新登录”')
    return None


def _profile_key(root, name, account_id, previous):
    # Uploaders refresh their credential files. Keep the durable profile binding
    # in the account database so such refreshes cannot discard the login history.
    if account_id is not None:
        with closing(sqlite3.connect(root / 'db/database.db')) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_profiles'").fetchone():
                row = conn.execute('SELECT profile_key FROM browser_profiles WHERE account_id=?', (account_id,)).fetchone()
                if row:
                    if not re.fullmatch(r'(account-[0-9]+|new-[a-f0-9]{64})', row[0]):
                        raise BrowserLoginError('账号浏览器配置标识无效，请检查本机账号数据')
                    return row[0]
    # Older/imported credentials have no profile reference. A broken old login
    # file must still be recoverable by re-login; never print credential data.
    if previous is not None:
        try:
            saved = json.loads((root / 'cookiesFile' / previous[0]).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _logger.warning('旧登录态无法读取，将使用该账号的独立浏览器配置')
        else:
            key = saved.get('publisher_browser_profile') if isinstance(saved, dict) else None
            if isinstance(key, str) and re.fullmatch(r'(account-[0-9]+|new-[a-f0-9]{64})', key):
                return key
            if key is not None:
                _logger.warning('旧登录态的浏览器配置标识无效，将使用该账号的独立配置')
    if account_id is not None:
        return f'account-{account_id}'
    return 'new-' + hashlib.sha256((name or uuid.uuid4().hex).encode('utf-8')).hexdigest()


def commit_account(database, platform, name, filename, account_id, previous, cancelled, *, profile_key=None):
    """CAS prevents a late login from recreating/deleting/overwriting newer data."""
    with closing(sqlite3.connect(database)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if cancelled.is_set():
            raise BrowserLoginError('登录已取消，账号未保存')
        if account_id is not None:
            cursor = conn.execute('UPDATE user_info SET filePath=?,status=1 WHERE id=? AND type=? AND filePath=?',
                                  (filename, account_id, platform, previous[0]))
            if cursor.rowcount != 1:
                raise BrowserLoginError('账号已被删除或更新，请刷新后重试')
        else:
            if conn.execute('SELECT 1 FROM user_info WHERE type=? AND userName=?', (platform, name)).fetchone():
                raise BrowserLoginError('已有同名账号，请刷新列表，未重复添加')
            cursor = conn.execute('INSERT INTO user_info(type,filePath,userName,status) VALUES(?,?,?,1)',
                                  (platform, filename, name))
            account_id = cursor.lastrowid
        if profile_key is not None:
            conn.execute('CREATE TABLE IF NOT EXISTS browser_profiles (account_id INTEGER PRIMARY KEY, profile_key TEXT NOT NULL)')
            conn.execute('INSERT INTO browser_profiles(account_id,profile_key) VALUES(?,?) '
                         'ON CONFLICT(account_id) DO UPDATE SET profile_key=excluded.profile_key', (account_id, profile_key))
        conn.commit()
    return account_id


async def _wait_for_login(platform, browser, context, queue, status, *, timeout=300, poll_interval=1):
    deadline = time.monotonic() + timeout
    candidate = None
    consecutive = 0
    last_stage = None
    while time.monotonic() < deadline:
        if queue.cancelled.is_set():
            return False
        if not browser.is_connected() or not context.pages:
            raise BrowserLoginError('登录窗口已关闭，账号未保存。请重新打开登录窗口')
        valid_page = None
        stage = 'waiting_browser'
        for page in list(context.pages):
            try:
                result = await inspect_page(platform, page)
            except Exception:
                # Navigation may destroy an execution context. Do not close the
                # user's security-verification window because one probe failed.
                result = 'pending'
            if result == 'verification_required':
                stage = result
            if result == 'valid':
                valid_page = page
                break
        if valid_page is not None:
            consecutive = consecutive + 1 if valid_page == candidate else 1
            candidate = valid_page
            if consecutive >= 2:
                return True
        else:
            candidate = None
            consecutive = 0
        if stage != last_stage:
            message = ('请在 Edge 或手机 App 中完成平台要求的安全验证，完成后会自动保存'
                       if stage == 'verification_required' else
                       '请在新打开的 Edge 窗口登录；完成后自动保存，无需复制 Cookie')
            status(stage, message)
            last_stage = stage
        await asyncio.sleep(poll_interval)
    raise BrowserLoginError('等待登录超时，账号未保存。请重新打开窗口并完成平台验证')


async def get_browser_cookie(platform, name, queue, *, account_id=None, timeout=300, poll_interval=1):
    """Open an account-owned persistent Edge; close and flush it before saving."""
    if platform not in PLATFORMS:
        raise BrowserLoginError('不支持的登录平台')
    name = name.strip()
    root = Path(BASE_DIR)
    key = (platform, 'id', account_id) if account_id is not None else (platform, 'name', name)
    claimed = False
    browser = None
    context = None
    resources = ExitStack()
    target = None
    committed = False
    def status(stage, message):
        queue.put({'event': 'login-status', 'data': {'stage': stage, 'message': message}})
    try:
        if len(name) > 80 or any(ord(c) < 32 for c in name):
            raise BrowserLoginError('请输入1～80字的有效账号名称')
        previous = _previous(root / 'db/database.db', platform, name, account_id)
        _claim(key)
        claimed = True
        profile_key = _profile_key(root, name, account_id, previous)
        profile = root / 'db/browser-profiles' / str(platform) / profile_key
        from publishing.queue import file_lock
        if not resources.enter_context(file_lock(profile.with_suffix('.lock'))):
            raise BrowserLoginError('该账号已有登录窗口，请先完成或取消上一次登录')
        status('opening_browser', '正在打开 Edge 登录窗口…')
        async with async_playwright() as p:
            try:
                from publishing.creator_browser import browser_options
                options = browser_options('youtube' if platform == 6 else 'toutiao')
                context = await p.chromium.launch_persistent_context(
                    user_data_dir=str(profile), **options, timeout=20000,
                    no_viewport=True, locale='zh-CN')
                browser = context.browser
            except Exception:
                raise BrowserLoginError('无法启动 Edge，请确认已安装且未被系统策略阻止；也可改用扫码或导入') from None
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                status('browser_open', 'Edge 已打开，正在加载平台官方登录页…')
                try:
                    await page.goto(PLATFORMS[platform]['url'], wait_until='domcontentloaded', timeout=30000)
                except BrowserTimeout:
                    status('waiting_browser', '登录页加载较慢，可在 Edge 窗口刷新；工具会继续等待登录')
                if not await _wait_for_login(platform, browser, context, queue, status, timeout=timeout, poll_interval=poll_interval):
                    return
                if queue.cancelled.is_set():
                    return
                status('verifying', '已检测到登录，正在自动获取并验证登录态…')
                if account_id is None and not name:
                    names = {await read_account_name(platform, tab) for tab in context.pages
                             if await inspect_page(platform, tab) == 'valid'}
                    if len(names) != 1:
                        raise BrowserLoginError('无法确认唯一的平台账号昵称，账号未保存，请确认当前账号后重试')
                    name = names.pop()
                state = scoped_state(platform, await context.storage_state(indexed_db=True))
                if platform == 6:
                    channels = {re.search(r'/channel/(UC[A-Za-z0-9_-]+)', tab.url)[1]
                                for tab in context.pages if await inspect_page(platform, tab) == 'valid'}
                    if len(channels) != 1:
                        raise BrowserLoginError('请选择一个 YouTube 频道后重新登录')
                    state['publisher_channel_id'] = channels.pop()
                directory = root / 'cookiesFile'
                directory.mkdir(exist_ok=True)
                target = directory / f'{uuid.uuid4()}.json'
                with target.open('x', encoding='utf-8') as output:
                    json.dump(state, output, ensure_ascii=False)
                if platform == 5:
                    # B站's uploader uses native LoginInfo, not Playwright JSON.
                    result = await prepare_bilibili_credentials(target)
                    if not result.get('success'):
                        raise BrowserLoginError('B站登录态授权未完成，账号未保存。请完成官方验证后重试，或使用扫码方式')
                # Keep the same profile after an account is first assigned an ID
                # or renamed. B站's conversion above may replace the JSON file.
                saved = json.loads(target.read_text(encoding='utf-8'))
                saved['publisher_browser_profile'] = profile_key
                target.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
                if queue.cancelled.is_set():
                    return
                if not browser.is_connected() or not context.pages:
                    raise BrowserLoginError('登录窗口已关闭，账号未保存。请重试')
                status('saving', '登录验证通过，正在保存账号…')
            finally:
                # Close the owned browser BEFORE committing. There is no await
                # between commit and the success event, avoiding success/EOF races.
                if context is not None:
                    await asyncio.wait_for(context.close(), timeout=8)
                    context = None
                    browser = None
        if target is not None and not queue.cancelled.is_set():
            commit_account(root / 'db/database.db', platform, name, target.name, account_id, previous, queue.cancelled,
                           profile_key=profile_key)
            committed = True
            queue.put('200')
    except (BrowserLoginError, AccountNameError) as exc:
        if not queue.cancelled.is_set():
            queue.put({'event': 'login-error', 'data': {'message': str(exc)}})
            queue.put('500')
    except Exception as exc:
        _logger.warning('浏览器登录未完成，平台=%s，异常类型=%s', platform, type(exc).__name__)
        if not queue.cancelled.is_set():
            queue.put({'event': 'login-error', 'data': {'message': '浏览器登录未完成，账号未保存。请检查网络后重试'}})
            queue.put('500')
    finally:
        resources.close()
        if target is not None and not committed:
            target.unlink(missing_ok=True)
        if claimed:
            _release(key)
