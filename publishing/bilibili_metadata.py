"""Account-scoped categories and read-only receipts using the existing Bili session."""
import json
import requests


def session(cookie):
    from uploader.bilibili_uploader.web_login import normalize_bilibili_credentials, _storage
    state = _storage(normalize_bilibili_credentials(cookie.read_text(encoding='utf-8')))
    client = requests.Session()
    client.trust_env = False
    client.headers.update({'Referer': 'https://member.bilibili.com/', 'User-Agent': 'Mozilla/5.0'})
    for item in state['cookies']:
        client.cookies.set(item['name'], item['value'], domain=item['domain'], path=item.get('path', '/'))
    return client


def get(client, path, params=None):
    try:
        response = client.get('https://member.bilibili.com' + path, params=params, timeout=20)
        response.raise_for_status()
        body = response.json()
        if body.get('code') != 0 or not isinstance(body.get('data'), dict):
            raise ValueError('B站未返回可用数据，请检查登录状态')
        return body['data']
    except requests.RequestException:
        raise ValueError('B站数据读取失败，请检查网络后重试') from None


def preferences(account_id):
    import daily_publish as daily
    with daily._connection() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS bili_preferences(account_id TEXT PRIMARY KEY, category INTEGER, options TEXT NOT NULL)')
        row = conn.execute('SELECT category,options FROM bili_preferences WHERE account_id=?', (account_id,)).fetchone()
    return {'category': row['category'], 'options': json.loads(row['options'])} if row else {'category': None, 'options': []}


def remember(account_id, category, options=None):
    import daily_publish as daily
    previous = preferences(account_id)
    choices = previous['options'] if options is None else options
    if category is not None and (isinstance(category, bool) or not str(category).isdigit() or int(category) <= 0):
        raise ValueError('请选择有效的B站分区')
    with daily._connection() as conn:
        conn.execute('INSERT OR REPLACE INTO bili_preferences VALUES(?,?,?)',
                     (account_id, int(category) if category else None, json.dumps(choices, ensure_ascii=False)))


def category_for(account_id, material, config):
    return material.get('category') or preferences(account_id)['category'] or config.get('platforms', {}).get('bilibili', {}).get('category')


def load_categories():
    import daily_publish as daily
    account = daily._account('bilibili')
    aid = str(account['account_id'])
    cookie = daily._web_account_cookie('bilibili', account)
    if cookie is None:
        cookie = daily.Path(daily.BASE_DIR) / 'cookies' / f"bilibili_{account['alias']}.json"
    with session(cookie) as client:
        data = get(client, '/x/vupre/web/archive/pre')
        options = [{'value': child['id'], 'label': parent['name'] + ' / ' + child['name']}
                   for parent in data.get('typelist', []) for child in parent.get('children', [])
                   if child.get('show', True)]
        if not options:
            raise ValueError('B站分区列表为空，请稍后重试')
        selected = preferences(aid)['category']
        source = 'saved' if selected else None
        if not selected:
            rows = get(client, '/x/web/archives', {'status': 'pubed', 'pn': 1, 'ps': 50}).get('arc_audits', [])
            recent = [r['Archive'] for r in rows if r.get('Archive', {}).get('state') == 0
                      and 'AI日报' in r['Archive'].get('title', '')]
            recent.sort(key=lambda a: a.get('ptime', 0), reverse=True)
            if recent:
                selected, source = recent[0].get('tid'), 'recent_ai_daily'
        if selected not in {o['value'] for o in options}:
            selected, source = None, None
        remember(aid, selected, options)
        return {'account_id': aid, 'category': selected, 'options': options, 'source': source}


def readback(cookie, bvid, *, title=None, day=None):
    from datetime import datetime, timezone, timedelta
    checked = 0
    matches_by_id = {}
    complete = False
    with session(cookie) as client:
        for page in range(1, 11):
            data = get(client, '/x/web/archives', {'status': 'is_pubing,pubed,not_pubed', 'pn': page, 'ps': 50})
            rows = data.get('arc_audits', [])
            checked += len(rows)
            for row in rows:
                arc = row.get('Archive', {})
                if bvid:
                    matches = arc.get('bvid') == bvid
                else:
                    stamp = arc.get('ctime')
                    matches = (title and day and arc.get('title') == title and isinstance(stamp, (int, float))
                               and datetime.fromtimestamp(stamp, timezone(timedelta(hours=8))).date().isoformat() == day)
                if not matches:
                    continue
                if arc.get('bvid'):
                    matches_by_id[arc['bvid']] = arc
            if bvid and matches_by_id:
                return _receipt(next(iter(matches_by_id.values())))
            if len(rows) < 50:
                total = data.get('page', {}).get('count')
                complete = isinstance(total, int) and checked >= total
                break
    if len(matches_by_id) > 1:
        return {'status': 'unknown', 'message': '原账号有多个同日同标题稿件，请核对作品 ID；不会重新上传'}
    if complete and len(matches_by_id) == 1:
        return _receipt(next(iter(matches_by_id.values())))
    if complete and not bvid and title and day and not matches_by_id:
        return {'status': 'absent', 'state': 'failed', 'remote_id': None, 'url': None,
                'remote_absent': True, 'note': f'已核对原账号全部 {checked} 篇稿件，没有同日同标题作品。',
                'message': '原账号后台确认没有本期稿件；保留原失败记录。'}
    return {'status': 'unknown', 'message': 'B站列表未找到原稿件；保留回执，未重新上传'}


def _receipt(arc):
    ident, state = arc['bvid'], 'processing'
    public_error = None
    if arc.get('state') == 0:
        # Public availability must be checked without authenticated cookies.
        try:
            with requests.Session() as public_client:
                public_client.trust_env = False
                response = public_client.get('https://api.bilibili.com/x/web-interface/view',
                    headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'},
                    params={'bvid': ident}, timeout=15)
            response.raise_for_status()
            public = response.json()
            if public.get('code') == 0 and public.get('data', {}).get('bvid') == ident:
                state = 'published'
            else:
                public_error = '公开视频接口尚未返回该作品'
        except (requests.RequestException, ValueError):
            public_error = '公开视频接口核对失败，保留原账号收稿状态'
    elif arc.get('state', 0) < 0 and arc.get('state') not in (-1, -2, -4, -30):
        state = 'needs_action'
    return {'status': 'found', 'state': state, 'remote_id': ident,
            'url': f'https://www.bilibili.com/video/{ident}', 'platform_status': arc.get('state'),
            'public_check_error': public_error,
            'note': 'B站原账号稿件列表已核对；公开状态另经公开视频接口确认。' if state == 'published' else 'B站原账号稿件列表已找到作品，尚未确认公开。'}
