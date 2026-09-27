import asyncio
import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sau_backend
from myUtils import browser_login as login
from myUtils.login_session import LoginStatusQueue

SECRET = 'synthetic-browser-fixture'

def state(platform):
    root = login.PLATFORMS[platform]['root']
    names = ('SESSDATA', 'bili_jct') if platform == 5 else ('sessionid',)
    return {'cookies': [{'name': name, 'value': SECRET, 'domain': '.' + root,
                         'path': '/', 'secure': True, 'httpOnly': True, 'expires': -1, 'sameSite': 'Lax'}
                        for name in names], 'origins': []}

@pytest.fixture
def root(tmp_path_factory, monkeypatch):
    # Chromium's IndexedDB backing files still hit Windows path-length limits.
    tmp_path = tmp_path_factory.mktemp('browser-login')
    (tmp_path/'db').mkdir()
    (tmp_path/'cookiesFile').mkdir()
    with closing(sqlite3.connect(tmp_path/'db/database.db')) as c:
        c.execute('CREATE TABLE user_info (id INTEGER PRIMARY KEY, type INTEGER, filePath TEXT, userName TEXT, status INTEGER)')
        c.commit()
    monkeypatch.setattr(login, 'BASE_DIR', tmp_path)
    return tmp_path

def rows(root):
    with closing(sqlite3.connect(root/'db/database.db')) as c:
        return c.execute('SELECT id,type,filePath,userName,status FROM user_info').fetchall()

def seed(root, platform=3):
    (root/'cookiesFile/original.json').write_text('original', encoding='utf-8')
    with closing(sqlite3.connect(root/'db/database.db')) as c:
        c.execute("INSERT INTO user_info VALUES (7,?,'original.json','原账号',0)", (platform,))
        c.commit()

@pytest.fixture
def driver():
    context=MagicMock()
    page=MagicMock()
    page.goto=AsyncMock()
    page.context=context
    page.is_closed.return_value=False
    context.pages=[page]
    context.new_page=AsyncMock(return_value=page)
    context.storage_state=AsyncMock(return_value=state(3))
    browser=MagicMock()
    browser.new_context=AsyncMock(return_value=context)
    context.close=AsyncMock()
    context.browser=browser
    browser.is_connected.return_value=True
    p=MagicMock()
    p.chromium.launch_persistent_context=AsyncMock(return_value=context)
    manager=MagicMock()
    manager.__aenter__=AsyncMock(return_value=p)
    manager.__aexit__=AsyncMock(return_value=False)
    return SimpleNamespace(context=context,page=page,browser=browser,p=p,manager=manager)

@pytest.mark.parametrize('platform', [1,2,3,4,5])
def test_success_auto_saves_platform_scoped_state_and_closes_before_success(root,driver,platform):
    driver.context.storage_state.return_value=state(platform)
    queue=LoginStatusQueue()
    with patch.object(login,'async_playwright',return_value=driver.manager), \
         patch.object(login,'_wait_for_login',new=AsyncMock(return_value=True)), \
         patch.object(login,'prepare_bilibili_credentials',new=AsyncMock(return_value={'success':True})) as prepare:
        asyncio.run(login.get_browser_cookie(platform,'测试账号',queue))
    assert list(queue.queue)[-1]=='200'
    assert SECRET not in str(list(queue.queue))
    row=rows(root)[0]
    assert row[1]==platform and row[4]==1
    assert (root/'cookiesFile'/row[2]).exists()
    saved=json.loads((root/'cookiesFile'/row[2]).read_text(encoding='utf-8'))
    assert ('cookie_info' in saved) == (platform==5)
    assert prepare.await_count==(1 if platform==5 else 0)
    kwargs = driver.p.chromium.launch_persistent_context.call_args.kwargs
    assert kwargs == dict(user_data_dir=str(root/'db/browser-profiles'/str(platform)/saved['publisher_browser_profile']),
                          channel='msedge', headless=False, timeout=20000, no_viewport=True, locale='zh-CN')
    driver.browser.new_context.assert_not_awaited()
    driver.context.close.assert_awaited_once()
    assert not login._active

@pytest.mark.parametrize('platform', [1,2,3,4,5])
def test_relogin_updates_existing_id_instead_of_creating_duplicate(root,driver,platform):
    seed(root,platform)
    queue=LoginStatusQueue()
    driver.context.storage_state.return_value=state(platform)
    with patch.object(login,'async_playwright',return_value=driver.manager), \
         patch.object(login,'_wait_for_login',new=AsyncMock(return_value=True)), \
         patch.object(login,'prepare_bilibili_credentials',new=AsyncMock(return_value={'success':True})):
        asyncio.run(login.get_browser_cookie(platform,'原账号',queue,account_id=7))
    assert list(queue.queue)[-1]=='200'
    assert len(rows(root))==1 and rows(root)[0][:2]==(7,platform)
    assert rows(root)[0][2]!='original.json'
    assert (root/'cookiesFile/original.json').read_text()=='original'

@pytest.mark.parametrize('mode',['wait_failure','cancel','closed','close_error','bili_rejected','no_cookies'])
def test_failure_or_cancel_never_saves_account(root,driver,mode):
    queue=LoginStatusQueue()
    platform=5 if mode=='bili_rejected' else 3
    driver.context.storage_state.return_value=state(platform)
    async def wait(*args,**kwargs):
        if mode=='wait_failure': raise login.BrowserLoginError('等待验证超时')
        if mode=='cancel': queue.cancelled.set(); return False
        return True
    if mode=='closed': driver.browser.is_connected.return_value=False
    if mode=='close_error': driver.context.close.side_effect=OSError('private-debug-data')
    if mode=='no_cookies': driver.context.storage_state.return_value={'cookies':[],'origins':[]}
    with patch.object(login,'async_playwright',return_value=driver.manager), patch.object(login,'_wait_for_login',new=wait), \
         patch.object(login,'prepare_bilibili_credentials',new=AsyncMock(return_value={'success':False})):
        asyncio.run(login.get_browser_cookie(platform,'测试',queue))
    assert rows(root)==[] and list((root/'cookiesFile').iterdir())==[]
    assert '200' not in list(queue.queue) and 'private-debug-data' not in str(list(queue.queue))
    assert not login._active
    driver.context.close.assert_awaited_once()

@pytest.mark.parametrize('mutation',['deleted','newer_cookie','cancel_on_close'])
def test_transaction_guards_late_completion(root,driver,mutation):
    seed(root)
    queue=LoginStatusQueue()
    async def close():
        if mutation=='cancel_on_close': queue.cancelled.set(); return
        with closing(sqlite3.connect(root/'db/database.db')) as c:
            if mutation=='deleted': c.execute('DELETE FROM user_info')
            else: c.execute("UPDATE user_info SET filePath='newer.json'")
            c.commit()
    driver.context.close.side_effect=close
    with patch.object(login,'async_playwright',return_value=driver.manager),patch.object(login,'_wait_for_login',new=AsyncMock(return_value=True)):
        asyncio.run(login.get_browser_cookie(3,'原账号',queue,account_id=7))
    assert '200' not in list(queue.queue)
    assert len(list((root/'cookiesFile').iterdir()))==1
    assert mutation!='deleted' or not rows(root)

@pytest.mark.parametrize('kind',['duplicate_name','missing_id','wrong_platform','duplicate_window'])
def test_rejects_before_launching_browser(root,driver,kind):
    queue=LoginStatusQueue(); seed(root)
    name='原账号' if kind=='duplicate_name' else '测试'
    aid=100 if kind=='missing_id' else 7 if kind=='wrong_platform' else None
    platform=5 if kind=='wrong_platform' else 3
    key=(3,'name','测试')
    if kind=='duplicate_window': login._claim(key)
    try:
        with patch.object(login,'async_playwright',return_value=driver.manager):
            asyncio.run(login.get_browser_cookie(platform,name,queue,account_id=aid))
        driver.p.chromium.launch_persistent_context.assert_not_awaited()
        assert list(queue.queue)[-1]=='500'
    finally:
        login._release(key)

@pytest.mark.parametrize('platform',[1,2,3,4,5,6,7])
def test_scoped_storage_drops_other_sites(platform):
    s=state(platform)
    s['cookies'].append(dict(s['cookies'][0],domain='.unrelated.example'))
    s['origins']=[{'origin':'https://unrelated.example','localStorage':[{'name':'private','value':SECRET}]}]
    result=login.scoped_state(platform,s)
    assert 'unrelated.example' not in json.dumps(result)

def test_qr_fallback_and_browser_route_dispatch(root):
    for platform in (1,2,3,4,5,7):
        queue=LoginStatusQueue(); queue.account_id=7
        async def browser_login(*args,**kwargs): args[2].put('200')
        with patch.object(sau_backend,'get_browser_cookie',new=AsyncMock(side_effect=browser_login)) as core:
            sau_backend.run_async_function(str(platform),'test',queue,'browser')
            core.assert_awaited_once_with(platform,'test',queue,account_id=7)

@pytest.mark.parametrize('platform',[1,2,3,4,5,6,7])
def test_local_browser_route_supported_for_all_platforms(platform):
    with patch.object(sau_backend.threading,'Thread') as worker:
        response=sau_backend.app.test_client().get(f'/login?type={platform}&id=test&mode=browser&account_id=7',buffered=False,
                                                 headers={'Origin':'http://127.0.0.1:5173'})
        assert response.status_code==200
        response.close()
        worker.assert_called_once()

@pytest.mark.parametrize('origin',['https://evil.example','null','http://localhost:9999','http://127.0.0.1:5173/path'])
def test_external_page_cannot_open_browser(origin):
    with patch.object(sau_backend.threading,'Thread') as worker:
        response=sau_backend.app.test_client().get('/login?type=3&id=test&mode=browser',headers={'Origin':origin})
        assert response.status_code==403
        worker.assert_not_called()

@pytest.mark.parametrize('port', [5410, 5425])
def test_desktop_login_uses_its_own_service_origin(port):
    origin = f'http://127.0.0.1:{port}'
    with patch.object(sau_backend.threading, 'Thread') as worker:
        client = sau_backend.app.test_client()
        response = client.get('/login?type=3&id=test&mode=browser&account_id=7',
                              base_url=origin, headers={'Origin': origin}, buffered=False)
        assert response.status_code == 200
        response.close()
        worker.assert_called_once()
        worker.reset_mock()
        # A desktop page must not silently open a login on a different service.
        response = client.get('/login?type=3&id=test&mode=browser&account_id=7',
                              base_url='http://localhost:5409', headers={'Origin': origin})
        assert response.status_code == 403
        worker.assert_not_called()


def test_waits_through_challenges_and_transient_navigation_errors(driver):
    queue=LoginStatusQueue(); statuses=[]
    probe=AsyncMock(side_effect=['verification_required',RuntimeError('transient'),'valid','valid'])
    with patch.object(login,'inspect_page',new=probe):
        assert asyncio.run(login._wait_for_login(3,driver.browser,driver.context,queue,
                            lambda s,m:statuses.append(s),timeout=2,poll_interval=0))
    assert 'verification_required' in statuses and probe.await_count==4

@pytest.mark.parametrize('case',['closed','timeout','cancel'])
def test_wait_is_bounded_and_requires_completed_login(driver,case):
    queue=LoginStatusQueue()
    if case=='closed': driver.context.pages=[]
    if case=='cancel': queue.cancelled.set()
    with patch.object(login,'inspect_page',new=AsyncMock(return_value='pending')):
        run=login._wait_for_login(3,driver.browser,driver.context,queue,lambda *args:None,
                                 timeout=0 if case=='timeout' else 1,poll_interval=0)
        if case=='cancel': assert asyncio.run(run) is False
        else:
            with pytest.raises(login.BrowserLoginError): asyncio.run(run)


def test_real_edge_dom_does_not_accept_blank_routes_or_login_shells():
    async def scenario():
        async with login.async_playwright() as p:
            browser=await p.chromium.launch(channel='msedge',headless=True)
            try:
                for platform in (1,2,3,4,6,7):
                    context=await browser.new_context()
                    await context.add_cookies(state(platform)['cookies'])
                    page=await context.new_page()
                    body={'value':'<p>Loading</p>'}
                    await page.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8',body=body['value']))
                    url={1:'https://creator.xiaohongshu.com/new/home',2:'https://channels.weixin.qq.com/platform',
                         3:'https://creator.douyin.com/creator-micro/home',4:'https://cp.kuaishou.com/article/publish/video',
                         6:'https://studio.youtube.com/channel/UCfixture',7:'https://mp.toutiao.com/profile_v4/'}[platform]
                    await page.goto(url)
                    assert await login.inspect_page(platform,page)=='pending'
                    body['value']={1:'<nav>创作首页</nav><nav>笔记管理</nav>',2:'<nav>首页</nav><nav>内容管理</nav>',
                                   3:'<button>发布视频</button>',4:'<button class="_upload-btn-fixture">上传</button>',
                                   6:'<ytcp-app><ytcp-navigation-drawer>内容</ytcp-navigation-drawer></ytcp-app>',
                                   7:'<nav>内容管理</nav><button>发布视频</button>'}[platform]
                    await page.reload()
                    assert await login.inspect_page(platform,page)=='valid', (platform, await page.locator('body').inner_text(), len(await context.cookies(login.PLATFORMS[platform]['url'])))
                    body['value']='<div>安全验证</div>'+body['value']
                    await page.reload()
                    assert await login.inspect_page(platform,page)=='verification_required'
                    await context.close()
            finally:
                await browser.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('platform', [6, 7])
def test_new_creator_login_saves_binding_and_channel(root, driver, platform):
    driver.context.storage_state.return_value = state(platform)
    driver.page.url = 'https://studio.youtube.com/channel/UCfixture'
    queue = LoginStatusQueue()
    with patch.object(login, 'async_playwright', return_value=driver.manager), \
         patch.object(login, '_wait_for_login', AsyncMock(return_value=True)), \
         patch.object(login, 'inspect_page', AsyncMock(return_value='valid')):
        asyncio.run(login.get_browser_cookie(platform, '新平台账号', queue))
    assert list(queue.queue)[-1] == '200'
    row = rows(root)[0]
    assert row[1] == platform
    saved = json.loads((root/'cookiesFile'/row[2]).read_text(encoding='utf-8'))
    assert saved.get('publisher_channel_id') == ('UCfixture' if platform == 6 else None)
    assert SECRET not in str(list(queue.queue))
    driver.context.close.assert_awaited_once()


def test_new_platform_qr_rejected_and_desktop_same_origin_allowed():
    client = sau_backend.app.test_client()
    for platform in (6, 7):
        assert client.get(f'/login?type={platform}&id=test&mode=qr').status_code == 400
        with patch.object(sau_backend.threading, 'Thread') as worker:
            response = client.get(f'/login?type={platform}&id=test&mode=browser',
                                  base_url='http://127.0.0.1:5410', headers={'Origin': 'http://127.0.0.1:5410'}, buffered=False)
            assert response.status_code == 200
            response.close()
            worker.assert_called_once()


def test_profile_survives_first_account_save_and_rename(root, driver):
    with patch.object(login, 'async_playwright', return_value=driver.manager), \
         patch.object(login, '_wait_for_login', AsyncMock(return_value=True)), \
         patch.object(login, 'inspect_page', AsyncMock(return_value='valid')), \
         patch.object(login, 'read_account_name', AsyncMock(return_value='原名称')):
        first = LoginStatusQueue()
        asyncio.run(login.get_browser_cookie(3, '', first))
        assert list(first.queue)[-1] == '200'
        profile = driver.p.chromium.launch_persistent_context.call_args.kwargs['user_data_dir']
        aid = rows(root)[0][0]
        with closing(sqlite3.connect(root/'db/database.db')) as conn:
            conn.execute('UPDATE user_info SET userName=? WHERE id=?', ('新名称', aid))
            conn.commit()
        second = LoginStatusQueue()
        asyncio.run(login.get_browser_cookie(3, '新名称', second, account_id=aid))
        assert list(second.queue)[-1] == '200'
        assert driver.p.chromium.launch_persistent_context.call_args.kwargs['user_data_dir'] == profile
        assert len(rows(root)) == 1


def test_delete_account_removes_profile_binding_without_deleting_other_profiles(root, monkeypatch):
    seed(root)
    monkeypatch.setattr(sau_backend, 'BASE_DIR', root)
    with closing(sqlite3.connect(root/'db/database.db')) as conn:
        conn.execute('CREATE TABLE browser_profiles(account_id INTEGER PRIMARY KEY, profile_key TEXT NOT NULL)')
        conn.executemany('INSERT INTO browser_profiles VALUES(?,?)', [(7,'account-7'),(8,'account-8')])
        conn.commit()
    response = sau_backend.app.test_client().get('/deleteAccount?id=7')
    assert response.status_code == 200
    with closing(sqlite3.connect(root/'db/database.db')) as conn:
        assert conn.execute('SELECT * FROM browser_profiles').fetchall() == [(8,'account-8')]


def test_profile_lock_prevents_other_service_opening_same_profile(root, driver):
    from publishing.queue import file_lock
    seed(root)
    with file_lock(root/'db/browser-profiles/3/account-7.lock') as owned:
        assert owned
        queue = LoginStatusQueue()
        with patch.object(login, 'async_playwright', return_value=driver.manager):
            asyncio.run(login.get_browser_cookie(3, '原账号', queue, account_id=7))
        assert list(queue.queue)[-1] == '500'
        driver.p.chromium.launch_persistent_context.assert_not_awaited()


@pytest.mark.parametrize('bad_key', ['../../outside', '/absolute', 'account-7/../8', None])
def test_old_or_invalid_profile_reference_stays_inside_account_directory(root, bad_key):
    seed(root)
    (root/'cookiesFile/original.json').write_text(json.dumps({'publisher_browser_profile': bad_key}))
    assert login._profile_key(root, '原账号', 7, ('original.json', '原账号')) == 'account-7'


def test_real_edge_login_profile_retains_storage_and_isolates_accounts(root):
    """Real Edge disks + production login flow; platform responses are fixtures."""
    async def scenario():
        async with login.async_playwright() as p:
            launch = p.chromium.launch_persistent_context
            profiles = []
            async def persistent(**options):
                options['headless'] = True
                context = await launch(**options)
                profiles.append(options['user_data_dir'])
                await context.route('**/*', lambda route: route.fulfill(body='<html><body>登录回归夹具</body></html>', content_type='text/html'))
                return context
            manager = MagicMock()
            manager.__aenter__ = AsyncMock(return_value=p)
            manager.__aexit__ = AsyncMock(return_value=False)
            calls = 0
            async def wait(platform, browser, context, queue, status, **kwargs):
                nonlocal calls
                page = context.pages[0]
                if calls == 1:
                    assert await page.evaluate("localStorage.getItem('profile-test')") == SECRET
                    assert any(c['name'] == 'sessionid' and c['value'] == SECRET for c in await context.cookies())
                else:
                    assert await page.evaluate("localStorage.getItem('profile-test')") is None
                await page.evaluate('(value) => localStorage.setItem("profile-test", value)', SECRET)
                cookie = state(3)['cookies'][0]
                cookie['expires'] = time.time() + 3600
                await context.add_cookies([cookie])
                calls += 1
                return True
            with patch.object(p.chromium, 'launch_persistent_context', side_effect=persistent), \
                 patch.object(login, 'async_playwright', return_value=manager), \
                 patch.object(login, '_wait_for_login', side_effect=wait):
                for name, aid in [('账号甲', None), ('账号甲', 1), ('账号乙', None)]:
                    queue = LoginStatusQueue()
                    await login.get_browser_cookie(3, name, queue, account_id=aid)
                    assert list(queue.queue)[-1] == '200', list(queue.queue)
                    if calls == 1:
                        # A subsequent upload may replace the storage-state file.
                        # The database binding must still reopen the original profile.
                        credential_file = root/'cookiesFile'/rows(root)[0][2]
                        refreshed = json.loads(credential_file.read_text(encoding='utf-8'))
                        refreshed.pop('publisher_browser_profile', None)
                        credential_file.write_text(json.dumps(refreshed), encoding='utf-8')
            assert profiles[0] == profiles[1] and profiles[0] != profiles[2]
            assert calls == 3 and len(rows(root)) == 2
    asyncio.run(scenario())
