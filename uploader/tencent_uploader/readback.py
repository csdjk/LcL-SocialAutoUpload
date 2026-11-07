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
from .session import ChannelsSession, login_required

BEIJING = timezone(timedelta(hours=8))
HOME = 'https://channels.weixin.qq.com/platform'


def canonical_description(text):
    if not isinstance(text, str): return ''
    # Tags are appended by the editor and need not have identical whitespace.
    return re.sub(r'\s+', '', re.sub(r'#[^\s#]+', '', text)).strip()


def match_posts(payload, title, description, edition_day, *, all_dates=False, remote_id=None):
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
        return {'status': 'unavailable', 'message': '后台列表返回格式异常'}
    if any(payload.get(key) not in (None,0,'0') for key in ('errcode','errCode','retcode','retCode')):
        return {'status':'unavailable','message':'后台列表请求未成功，不能判定投稿结果'}
    data = payload['data']; rows = data.get('list')
    if not isinstance(rows, list): return {'status': 'unavailable', 'message': '未读取到后台作品列表'}
    desired = canonical_description(description)
    # A short generic sentence is not adequate evidence for automatic matching.
    if len(desired) < 30 and not remote_id:
        return {'status': 'unavailable', 'message': '简介过短，无法可靠区分后台同名作品，请人工核对'}
    acceptable = {desired, canonical_description(title + '\n' + description)}
    if all_dates:
        acceptable.add(canonical_description(title + '正文：' + description))
    matches = {}; times = []; potential = []
    for row in rows:
        if not isinstance(row, dict): continue
        try:
            timestamp = int(row.get('createTime', 0)); day = datetime.fromtimestamp(timestamp, BEIJING).date().isoformat()
            times.append(day)
        except (ValueError, TypeError, OverflowError, OSError): continue
        desc = row.get('desc') or {}
        if not isinstance(desc, dict) or (not all_dates and day != edition_day): continue
        ident = row.get('objectId') or row.get('exportId')
        if not isinstance(ident, str) or not ident or len(ident) > 200: continue
        raw_description = desc.get('description', '')
        if (all_dates and isinstance(raw_description, str)
                and ((day == edition_day and 'AI日报' in raw_description)
                     or (len(canonical_description(title)) >= 7
                         and canonical_description(raw_description).startswith(canonical_description(title))))):
            potential.append(ident)
        if (ident == remote_id if remote_id else canonical_description(desc.get('description')) in acceptable):
            # Official normal/public row: status=1 and visibleType=1; its
            # visibility action offers 设为仅自己可见. Unknown codes stay pending.
            public = row.get('status') == 1 and row.get('visibleType') == 1
            matches[ident] = {'remote_id': ident, 'created_at': datetime.fromtimestamp(timestamp, BEIJING).isoformat(),
                             'match': ('same_account_remote_id' if remote_id else
                                       'same_account_exact_description_all_dates' if all_dates else
                                       'same_account_exact_description_and_day'),
                             'state': 'published' if public else 'processing',
                             'note': ('原账号官方内容列表确认作品正常且公开可见。' if public else '已找到作品，平台发布或可见状态尚未确认。'),
                             'platform_status': row.get('status'), 'visible_type': row.get('visibleType')}
    if len(matches) > 1:
        return {'status': 'ambiguous', 'message': '后台存在多条同日同内容作品，已停止自动投稿，避免重复'}
    if matches:
        return {'status': 'found', **next(iter(matches.values())),
                'message': '后台已找到同日同内容作品，未重复投稿'}
    if potential:
        return {'status': 'ambiguous', 'message': '后台存在原日期日报或同标题作品，但文案或作品ID不同；请核对原作品，禁止补投',
                'potential_remote_ids': list(dict.fromkeys(potential))}
    total = data.get('totalCount')
    complete = ((isinstance(total, int) and not isinstance(total, bool) and total <= len(rows))
                or data.get('continueFlag') in (0, False)
                or bool(not all_dates and times and min(times) < edition_day))
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
            if login_required(page):
                if login_since is None: login_since=time.monotonic()
                if time.monotonic()-login_since>=3:
                    from .auth_diagnostics import record_login_failure
                    diagnostic = await record_login_failure(page, 'content_list')
                    return {'status': 'needs_login', 'message': '后台登录已失效，请重新登录',
                            'auth_diagnostic': diagnostic}
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


async def readback_file(account_file, title, description, edition_day, *, all_dates=False, remote_id=None):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        session = ChannelsSession(account_file)
        try:
            context = await session.open(p, headless=True, viewport={'width': 1440, 'height': 1000})
            if all_dates:
                result = await scan_history_context(context, title, description, edition_day, remote_id=remote_id)
            else:
                result = await scan_context(context, title, description, edition_day)
            session.authenticated = result['status'] in ('found', 'absent', 'ambiguous')
            return result
        finally: await session.close()


async def scan_history_context(context, title, description, edition_day, *, remote_id=None):
    """Read the full official content list, rather than the homepage's five recent works."""
    page = await context.new_page()
    try:
        await page.goto(HOME, wait_until='domcontentloaded', timeout=20000)
        menu = page.locator('a.finder-ui-desktop-menu__sub__link').filter(has_text=re.compile('^内容管理$'))
        await menu.hover(timeout=20000)
        link = page.locator('a.finder-ui-desktop-menu__link:visible').filter(has_text=re.compile('^视频$'))
        await link.wait_for(state='visible', timeout=5000)
        def is_list(response):
            parsed = urlsplit(response.url)
            return parsed.hostname == 'channels.weixin.qq.com' and parsed.path.endswith('/post/post_list')
        async with page.expect_response(is_list, timeout=20000) as pending:
            await link.click()
        response = await pending.value
        if not response.ok:
            return {'status': 'unavailable', 'message': '官方作品列表读取失败，未上传'}
        payload = await response.json()
        result = match_posts(payload, title, description, edition_day, all_dates=True, remote_id=remote_id)
        if result['status'] == 'absent':
            result['message'] = '官方完整作品列表确认所有日期均无相同内容'
        result['checked_at'] = datetime.now(BEIJING).isoformat()
        result['source'] = 'official_content_list'
        result['listed_count'] = len(payload.get('data', {}).get('list', []))
        result['total_count'] = payload.get('data', {}).get('totalCount')
        return result
    except Exception:
        return {'status': 'unavailable', 'message': '历史后台查重未完成；未上传，请检查网络或登录状态'}
    finally:
        await page.close()
