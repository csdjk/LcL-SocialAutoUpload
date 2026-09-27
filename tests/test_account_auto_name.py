import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import async_playwright
from myUtils import browser_login as login
from myUtils.login_session import LoginStatusQueue
from utils.account_identity import AccountNameError, read_account_name
from tests.test_douyin_credential_import import local_app
from tests.credential_fixtures import fixture_cookies
from tests.test_browser_auto_login import root, driver, rows, state


@pytest.mark.parametrize('platform', [3, 5])
def test_unnamed_import_uses_validated_platform_name(local_app, platform):
    app, directory, db = local_app
    client = app.test_client()
    headers = {'X-SAU-Local': '1'}
    validator = ('validate_douyin_credentials' if platform == 3 else
                 'prepare_bilibili_credentials' if platform == 5 else 'validate_platform_credentials')
    with patch('myUtils.credential_import.' + validator,
               new=AsyncMock(return_value={'success': True, 'account_name': ' 平台昵称🌱 ' })) as validate:
        result = client.post('/accounts/import-' + ('douyin' if platform == 3 else 'bilibili'), headers=headers,
                             json={'credentials': fixture_cookies(platform)})
    assert result.status_code == 200, result.json
    assert result.json['data'][3] == '平台昵称🌱'
    assert validate.call_args.kwargs == {'require_name': True}


@pytest.mark.parametrize('nickname', [None, '', 'x' * 81, 'bad\nname', 42])
def test_missing_or_invalid_name_does_not_save_credentials(local_app, nickname):
    app, directory, db = local_app
    with patch('myUtils.credential_import.validate_douyin_credentials',
               new=AsyncMock(return_value={'success': True, 'account_name': nickname})):
        response = app.test_client().post('/accounts/import-douyin',
            headers={'X-SAU-Local': '1'}, json={'credentials': fixture_cookies(3)})
    assert response.status_code == 422, response.json
    with closing(sqlite3.connect(db)) as conn:
        assert not conn.execute('SELECT * FROM user_info').fetchall()
    assert not list((directory / 'cookiesFile').glob('*.json'))


def test_auto_name_duplicate_leaves_existing_account_untouched(local_app):
    app, directory, db = local_app
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("INSERT INTO user_info VALUES(1,3,'original.json','同名账号',1)")
        conn.commit()
    with patch('myUtils.credential_import.validate_douyin_credentials',
               new=AsyncMock(return_value={'success': True, 'account_name': '同名账号'})):
        result = app.test_client().post('/accounts/import-douyin', headers={'X-SAU-Local': '1'},
            json={'credentials': fixture_cookies(3)})
    assert result.status_code == 409
    assert not list((directory / 'cookiesFile').glob('*.json'))


@pytest.mark.parametrize('platform', range(1, 8))
def test_unnamed_dedicated_login_saves_detected_name(root, driver, platform):
    driver.context.storage_state.return_value = state(platform)
    driver.page.url = 'https://studio.youtube.com/channel/UCfixture'
    queue = LoginStatusQueue()
    with patch.object(login, 'async_playwright', return_value=driver.manager), \
         patch.object(login, '_wait_for_login', new=AsyncMock(return_value=True)), \
         patch.object(login, 'inspect_page', new=AsyncMock(return_value='valid')), \
         patch.object(login, 'read_account_name', new=AsyncMock(return_value='平台本人昵称')), \
         patch.object(login, 'prepare_bilibili_credentials', new=AsyncMock(return_value={'success': True})):
        asyncio.run(login.get_browser_cookie(platform, '', queue))
    assert list(queue.queue)[-1] == '200', list(queue.queue)
    assert rows(root)[0][3] == '平台本人昵称'


def test_name_lookup_failure_closes_login_without_saving(root, driver):
    queue = LoginStatusQueue()
    with patch.object(login, 'async_playwright', return_value=driver.manager), \
         patch.object(login, '_wait_for_login', new=AsyncMock(return_value=True)), \
         patch.object(login, 'inspect_page', new=AsyncMock(return_value='valid')), \
         patch.object(login, 'read_account_name', new=AsyncMock(side_effect=AccountNameError('未读取到平台昵称'))):
        asyncio.run(login.get_browser_cookie(3, '', queue))
    assert rows(root) == []
    assert list(queue.queue)[-1] == '500'
    assert not list((root/'cookiesFile').glob('*.json'))
    driver.context.close.assert_awaited_once()


def test_real_edge_reads_names_not_titles_and_rejects_ambiguous_or_wrong_host():
    async def scenario():
        cases = {
            1: ('creator.xiaohongshu.com', '<div class="user-name">账号小红书</div>'),
            2: ('channels.weixin.qq.com', '<div class="finder-nickname">账号视频号</div>'),
            3: ('creator.douyin.com', '<div class="user-name-test">账号抖音</div>'),
            4: ('cp.kuaishou.com', '<div class="userName-test">账号快手</div>'),
            6: ('studio.youtube.com', '<ytcp-navigation-drawer><div class="entity-name">账号YouTube</div></ytcp-navigation-drawer>'),
            7: ('mp.toutiao.com', '<div class="auth-avator-name">账号头条</div>'),
        }
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            page = await browser.new_page()
            for platform, (host, body) in cases.items():
                await page.route('**/*', lambda r: r.fulfill(body=body + '<h1>无关视频标题</h1>', content_type='text/html; charset=utf-8'))
                await page.goto('https://' + host + '/')
                assert (await read_account_name(platform, page)).startswith('账号')
                await page.set_content('<h1>无关视频标题</h1><div class="user-name" hidden>隐藏昵称</div>')
                with pytest.raises(AccountNameError):
                    await read_account_name(platform, page, timeout=0)
                await page.unroute('**/*')
            await page.set_content('<div class="user-name">甲</div><div class="user-name">乙</div>')
            with pytest.raises(AccountNameError, match='多个'):
                await read_account_name(7, page, timeout=0)
            with pytest.raises(AccountNameError, match='不属于'):
                await read_account_name(3, page, timeout=0)
            await browser.close()
    asyncio.run(scenario())


def test_kuaishou_current_header_name_is_read_without_using_video_names():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            try:
                page = await browser.new_page()
                await page.route('**/*', lambda route: route.fulfill(
                    body='<div class="user-info-name">L</div><div class="video-name">无关作品标题</div>'
                         '<div class="user-info-name" hidden>旧昵称</div>',
                    content_type='text/html; charset=utf-8'))
                await page.goto('https://cp.kuaishou.com/article/publish/video')
                assert await read_account_name(4, page, timeout=0) == 'L'
                await page.set_content('<div class="video-name">无关作品标题</div>')
                with pytest.raises(AccountNameError):
                    await read_account_name(4, page, timeout=0)
                await page.set_content('<div class="user-info-name">甲</div><div class="user-info-name">乙</div>')
                with pytest.raises(AccountNameError, match='多个'):
                    await read_account_name(4, page, timeout=0)
            finally:
                await browser.close()
    asyncio.run(scenario())


def test_xiaohongshu_header_name_excludes_notes_hidden_and_conflicting_names():
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            try:
                page = await browser.new_page()
                await page.route('**/*', lambda route: route.fulfill(
                    body='<div class="name">创作服务平台</div>'
                         '<div class="user-info"><span class="name-box">发现好游</span></div>'
                         '<div class="note"><span class="name-box">无关笔记名称</span></div>'
                         '<div class="user-info" hidden><span class="name-box">旧昵称</span></div>',
                    content_type='text/html; charset=utf-8'))
                await page.goto('https://creator.xiaohongshu.com/publish/publish')
                assert await read_account_name(1, page, timeout=0) == '发现好游'
                await page.set_content('<div class="name">创作服务平台</div>'
                                       '<span class="name-box">无关笔记名称</span>')
                with pytest.raises(AccountNameError):
                    await read_account_name(1, page, timeout=0)
                await page.set_content('<div class="user-info"><span class="name-box">甲</span>'
                                       '<span class="name-box">乙</span></div>')
                with pytest.raises(AccountNameError, match='多个'):
                    await read_account_name(1, page, timeout=0)
            finally:
                await browser.close()
    asyncio.run(scenario())
