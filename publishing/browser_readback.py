"""Read creator records; absence requires a complete original-account list."""
import asyncio
from datetime import datetime, timezone, timedelta
import json
import re
from urllib.parse import urlsplit, parse_qs
from playwright.async_api import async_playwright

MANAGERS = {'douyin': 'https://creator.douyin.com/creator-micro/content/manage',
            'kuaishou': 'https://cp.kuaishou.com/article/manage/video',
            'toutiao': 'https://mp.toutiao.com/profile_v4/manage/content/all'}


def kuaishou_records(payload, description, day, remote_id=None):
    if not isinstance(payload, dict) or payload.get('result') != 1:
        return []
    def plain(text):
        return re.sub(r'\s+', '', re.sub(r'#[^\s#]+', '', text or ''))
    expected = plain(description)
    records = []
    for row in (payload.get('data') or {}).get('list', []):
        receipt, work_id, stamp = str(row.get('publishId', '')), row.get('workId'), row.get('uploadTime')
        ident = work_id if isinstance(work_id, str) and re.fullmatch(r'[A-Za-z0-9]{8,}', work_id) else None
        if not (ident or (receipt.isdigit() and int(receipt) > 0)) or not isinstance(stamp, (int, float)) or stamp <= 0 or row.get('photoOwner') is not True:
            continue
        created = datetime.fromtimestamp(stamp / 1000, timezone(timedelta(hours=8))).date().isoformat()
        matches = ident == remote_id if remote_id else created == day and expected and expected in plain(row.get('title'))
        if matches:
            public = bool(ident and row.get('publishStatus') == 4 and row.get('judgementStatus') == 1 and row.get('photoStatus') == 0)
            records.append({'status': 'found', 'remote_id': ident, 'state': 'published' if public else 'processing', 'url': None,
                            'match_key': ident or f'publish:{receipt}', 'publish_id': receipt, 'created_at': created,
                            'platform_status': row.get('publishStatus'),
                            'note': ('快手原账号官方作品列表确认已发布且公开，workId 已保存。' if public else
                                     f'快手原账号官方作品列表确认收稿，publishId={receipt}；workId 尚未生成或公开状态未确认。')})
    return records


def douyin_complete_before(payload, day):
    if not isinstance(payload, dict) or payload.get('status_code') != 0 or payload.get('has_more') is not False:
        return False
    rows, total = payload.get('aweme_list'), payload.get('total')
    if not isinstance(rows, list) or type(total) is not int or len(rows) != total:
        return False
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not str(row.get('aweme_id', '')).isdigit(): return False
        ids.add(str(row['aweme_id']))
        stamp = row.get('create_time')
        if not isinstance(stamp, (int, float)) or stamp <= 0: return False
        if datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).date().isoformat() >= day: return False
    return len(ids) == total


def douyin_records(payload, title, description, day, remote_id=None):
    found = {}
    def walk(value):
        if isinstance(value, list):
            for item in value: walk(item)
        elif isinstance(value, dict):
            ident, text = str(value.get('aweme_id', '')), value.get('desc', '')
            created = value.get('create_time')
            if ident.isdigit() and isinstance(created, (int, float)) and created > 0:
                date = datetime.fromtimestamp(created, timezone(timedelta(hours=8))).date().isoformat()
                matching = ident == remote_id if remote_id else date == day and title in text and all(
                    line in text for line in description.splitlines() if line.strip() and not line.startswith('#'))
                if matching:
                    url = value.get('share_url') or value.get('share_info', {}).get('share_url')
                    if not isinstance(url, str) or urlsplit(url).hostname not in ('www.douyin.com', 'www.iesdouyin.com'):
                        url = None
                    elif urlsplit(url).scheme == 'https':
                        url = urlsplit(url)._replace(query='', fragment='').geturl()
                    else:
                        url = None
                    flags = value.get('status') if isinstance(value.get('status'), dict) else {}
                    # Verified against the creator page's 已发布 row. Missing flags
                    # are unknown, never equivalent to explicit public visibility.
                    public = (value.get('status_value') == 102 and flags.get('private_status') == 0
                              and all(flags.get(key) is False for key in (
                                  'in_reviewing', 'is_delete', 'is_private', 'is_prohibited', 'self_see')))
                    found[ident] = {'status': 'found', 'remote_id': ident, 'url': url,
                                    'created_at': date, 'state': 'published' if public else 'processing', 'source': 'official_content_list',
                                    'status_value': value.get('status_value'),
                                    'platform_status': {k:v for k,v in (value.get('status') if isinstance(value.get('status'), dict) else {}).items()
                                        if k in ('in_reviewing','is_delete','is_private','is_prohibited','private_status','reviewed','self_see')},
                                    'note': ('原账号内容管理确认作品已发布，且非审核中、删除、私密、禁止或仅自己可见。' if public else
                                             '原账号内容管理返回匹配作品，发布及公开状态尚未确认。')}
            for item in value.values():
                if isinstance(item, (list, dict)): walk(item)
    walk(payload)
    return list(found.values())


def toutiao_records(payload, title, day, remote_id=None):
    """Parse the creator page's own feed; preserve 64-bit IDs as strings."""
    found = {}
    def walk(value):
        if isinstance(value, list):
            for item in value: walk(item)
        elif isinstance(value, dict):
            ident = str(value.get('gidStr') or value.get('groupID') or '')
            stamp = value.get('createTime')
            if ident.isdigit() and isinstance(stamp, (int, float)) and stamp > 0 and 'itemStatus' in value:
                date = datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).date().isoformat()
                matching = ident == remote_id if remote_id else value.get('title') == title and date == day
                if matching:
                    url = value.get('articleURL')
                    parsed = urlsplit(url) if isinstance(url, str) else None
                    preview = (parsed and parsed.hostname == 'i.snssdk.com'
                               and parsed.path == '/feoffline/mp-article-preview/video'
                               and parse_qs(parsed.query).get('pgc_id') == [ident])
                    public_link = (parsed and parsed.hostname == 'm.toutiaoimg.com'
                                   and parsed.path.rstrip('/') == f'/i{ident}')
                    if not (parsed and parsed.scheme == 'https' and not parsed.username and not parsed.password
                            and (preview or public_link)):
                        url = None
                    elif public_link:
                        url = parsed._replace(query='', fragment='').geturl()
                    # Verified against the official 已发布 row; visibility=0 is
                    # public. A preview URL or a missing visibility is not proof.
                    public = value['itemStatus'] == 20 and value.get('visibilityLevel') == 0
                    found[ident] = {'status': 'found', 'remote_id': ident, 'url': url,
                        'created_at': date, 'state': 'published' if public else 'processing', 'source': 'official_content_list',
                        'platform_status': value['itemStatus'], 'visibility': value.get('visibilityLevel'),
                        'note': ('头条原账号内容管理确认作品已发布且公开可见。' if public else
                                 '头条原账号内容列表返回匹配作品，发布或公开可见状态尚未确认。')}
            for item in value.values():
                if isinstance(item, (list, dict)): walk(item)
    walk(payload)
    return list(found.values())


async def scan_context(context, platform, title, description, day, remote_id=None):
    page = await context.new_page()
    matches, jobs, completed_lists = {}, [], []
    async def observe(response):
        parsed = urlsplit(response.url)
        if not response.ok: return
        if platform == 'douyin' and parsed.hostname == 'creator.douyin.com':
            parser = lambda body: douyin_records(body, title, description, day, remote_id)
        elif platform == 'toutiao' and parsed.hostname == 'mp.toutiao.com' and parsed.path == '/api/feed/mp_provider/v1/':
            parser = lambda body: toutiao_records(body, title, day, remote_id)
        elif platform == 'kuaishou' and parsed.hostname == 'cp.kuaishou.com' and parsed.path == '/rest/cp/works/v2/video/pc/photo/list':
            parser = lambda body: kuaishou_records(body, description, day, remote_id)
        else: return
        if 'json' not in response.headers.get('content-type', ''): return
        try: body = await response.json()
        except Exception: return
        if platform == 'douyin' and parsed.path == '/janus/douyin/creator/pc/work_list' and douyin_complete_before(body, day):
            completed_lists.append(body['total'])
        for record in parser(body):
            matches[record.get('match_key') or record['remote_id']] = record
    page.on('response', lambda response: jobs.append(asyncio.create_task(observe(response))))
    try:
        await page.goto(MANAGERS[platform], wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(4)
        from myUtils.browser_login import inspect_page
        if await inspect_page({'douyin': 3, 'toutiao': 7, 'kuaishou': 4}[platform], page) != 'valid':
            return {'status': 'needs_login', 'message': '原账号登录未通过，请先重新登录后核对'}
        if jobs: await asyncio.gather(*jobs)
        if len(matches) == 1: return next(iter(matches.values()))
        if len(matches) > 1:
            return {'status': 'unknown', 'message': '后台有多个匹配作品，请核对作品 ID；不会重新投稿'}
        if platform == 'douyin' and not remote_id and completed_lists:
            from publishing.diagnostics import capture
            return {'status': 'absent', 'state': 'failed', 'remote_id': None, 'url': None, 'remote_absent': True,
                    'screenshot': await capture(page, platform, 'remote-absent'),
                    'note': f'原账号默认内容列表共 {completed_lists[-1]} 条，全部读完且均早于 {day}；没有本期作品。',
                    'message': '完整后台列表确认本期尚无作品，保留原任务失败记录。'}
        if platform == 'toutiao':
            # Accept only an actual work link in a dated row containing this title.
            links = await page.locator('a[href]').evaluate_all('''(xs, expected) => xs.filter(a => {
                let row=a;
                for(let n=0;n<5 && row;n++,row=row.parentElement){
                    const text=row.innerText||'';
                    if(text.includes(expected.title)&&text.includes(expected.day)&&text.length<2500)return true;
                } return false;
            }).map(a=>a.href)''', {'title': title, 'day': day})
            from publishing.creator_browser import receipt_from_links
            receipt = receipt_from_links(platform, links)
            if receipt['status'] == 'found' and (not remote_id or receipt['remote_id'] == remote_id):
                return {**receipt, 'source': 'official_content_list', 'state': 'processing',
                        'note': '原账号内容管理找到同日同标题作品链接，尚未确认公开状态。'}
        return {'status': 'unknown', 'message': '当前后台列表未返回唯一可核对作品，保留原记录；不会重新投稿'}
    finally:
        await page.close()


async def readback(platform, cookie, material, day, remote_id=None):
    raw = json.loads(cookie.read_text(encoding='utf-8'))
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        try:
            context = await browser.new_context(storage_state={k:v for k,v in raw.items() if k in ('cookies','origins')})
            return await scan_context(context, platform, material['title'], material['description'], day, remote_id)
        finally:
            await browser.close()
