"""Read-only result matching through the official page's own list response.

Never construct a private API request, use another browser's profile, infer
publication from disappearing controls, or expose authentication/nonce values.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import re
import time
from urllib.parse import urlsplit

BEIJING = timezone(timedelta(hours=8))
HOME = 'https://channels.weixin.qq.com/platform'


def canonical_description(text):
    if not isinstance(text, str): return ''
    # Tags are appended by the editor and need not have identical whitespace.
    return re.sub(r'\s+', '', re.sub(r'#[^\s#]+', '', text)).strip()


def match_posts(payload, title, description, edition_day):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
        return {'status': 'unavailable', 'message': '后台列表返回格式异常'}
    if any(payload.get(key) not in (None,0,'0') for key in ('errcode','errCode','retcode','retCode')):
        return {'status':'unavailable','message':'后台列表请求未成功，不能判定投稿结果'}
    data = payload['data']; rows = data.get('list')
    if not isinstance(rows, list): return {'status': 'unavailable', 'message': '未读取到后台作品列表'}
    desired = canonical_description(description)
    # A short generic sentence is not adequate evidence for automatic matching.
    if len(desired) < 30:
        return {'status': 'unavailable', 'message': '简介过短，无法可靠区分后台同名作品，请人工核对'}
    acceptable = {desired, canonical_description(title + '\n' + description)}
    matches = {}; times = []
    for row in rows:
        if not isinstance(row, dict): continue
        try:
            timestamp = int(row.get('createTime', 0)); day = datetime.fromtimestamp(timestamp, BEIJING).date().isoformat()
            times.append(day)
        except (ValueError, TypeError, OverflowError, OSError): continue
        desc = row.get('desc') or {}
        if not isinstance(desc, dict) or day != edition_day: continue
        ident = row.get('objectId') or row.get('exportId')
        if not isinstance(ident, str) or not ident or len(ident) > 200: continue
        if canonical_description(desc.get('description')) in acceptable:
            # Official normal/public row: status=1 and visibleType=1; its
            # visibility action offers 设为仅自己可见. Unknown codes stay pending.
            public = row.get('status') == 1 and row.get('visibleType') == 1
            matches[ident] = {'remote_id': ident, 'created_at': datetime.fromtimestamp(timestamp, BEIJING).isoformat(),
                             'match': 'same_account_exact_description_and_day',
                             'state': 'published' if public else 'processing',
                             'note': ('原账号官方内容列表确认作品正常且公开可见。' if public else '已找到作品，平台发布或可见状态尚未确认。'),
                             'platform_status': row.get('status'), 'visible_type': row.get('visibleType')}
    if len(matches) > 1:
        return {'status': 'ambiguous', 'message': '后台存在多条同日同内容作品，已停止自动投稿，避免重复'}
    if matches:
        return {'status': 'found', **next(iter(matches.values())),
                'message': '后台已找到同日同内容作品，未重复投稿'}
    total = data.get('totalCount')
    complete = ((isinstance(total, int) and not isinstance(total, bool) and total <= len(rows))
                or data.get('continueFlag') in (0, False)
                or bool(times and min(times) < edition_day))
    if complete:
        return {'status': 'absent', 'message': '已检查后台当日列表，未找到同内容作品'}
    return {'status': 'unavailable', 'message': '后台当日列表尚未完整读取，未自动投稿，请刷新重试'}


async def scan_context(context, title, description, edition_day, *, timeout=20):
    """New read-only tab in the owned context; never changes the upload form."""
    page = await context.new_page(); responses = []; jobs = set(); last_error = None
    async def response_handler(response):
        nonlocal last_error
        parsed = urlsplit(response.url)
        if parsed.hostname != 'channels.weixin.qq.com' or not parsed.path.endswith('/post/post_list'): return
        try:
            if response.ok:
                body = await response.json()
                result = match_posts(body, title, description, edition_day)
                responses.append(result)
        except Exception: last_error = True
    def on_response(response):
        job = asyncio.create_task(response_handler(response)); jobs.add(job); job.add_done_callback(jobs.discard)
    page.on('response', on_response)
    try:
        await page.goto(HOME, wait_until='domcontentloaded', timeout=int(timeout*1000))
        deadline = time.monotonic() + timeout
        login_since = None
        while time.monotonic() < deadline:
            if responses:
                return next((x for x in responses if x['status'] in ('found','ambiguous')), responses[-1])
            if 'login' in urlsplit(page.url).path:
                if login_since is None: login_since=time.monotonic()
                if time.monotonic()-login_since>=3:
                    return {'status': 'needs_login', 'message': '后台登录已失效，请重新登录'}
            else: login_since=None
            await asyncio.sleep(.2)
        return {'status': 'unavailable', 'message': '后台结果读取超时，请稍后刷新结果；不会自动重发'}
    except Exception:
        return {'status': 'unavailable', 'message': '后台结果暂时无法读取，请检查网络或登录状态'}
    finally:
        page.remove_listener('response', on_response)
        for job in list(jobs): job.cancel()
        if jobs: await asyncio.gather(*jobs, return_exceptions=True)
        await page.close()


async def readback_file(account_file, title, description, edition_day):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True, timeout=15000)
        try:
            context = await browser.new_context(storage_state=str(account_file), locale='zh-CN')
            return await scan_context(context, title, description, edition_day)
        finally: await browser.close()
