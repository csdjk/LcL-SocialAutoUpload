import asyncio
import base64
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from myUtils import login
from myUtils.login_session import LoginStatusQueue
from uploader.tencent_uploader import main as tencent


def image_locator(src='/connect/qrcode/test'):
    return SimpleNamespace(
        get_attribute=AsyncMock(return_value=src),
        is_visible=AsyncMock(return_value=True),
        screenshot=AsyncMock(return_value=b'png-screenshot'),
    )


def fake_page(url, visible=(), frames=None):
    page = MagicMock()
    page.url = url
    page.frames = frames or []
    def by_text(text, **_):
        marker = MagicMock()
        marker.first = marker
        marker.count = AsyncMock(return_value=int(text in visible))
        marker.is_visible = AsyncMock(return_value=text in visible)
        return marker
    page.get_by_text.side_effect = by_text
    return page


class TencentQrTests(unittest.IsolatedAsyncioTestCase):
    async def test_relative_qr_uses_wechat_iframe_origin_and_becomes_data_url(self):
        response = SimpleNamespace(ok=True, headers={'content-type': 'image/jpeg; charset=utf-8'},
                                   body=AsyncMock(return_value=b'jpeg-body'), dispose=AsyncMock())
        request = SimpleNamespace(get=AsyncMock(return_value=response))
        page = SimpleNamespace(context=SimpleNamespace(request=request))
        frame = SimpleNamespace(url='https://open.weixin.qq.com/connect/qrconnect?state=test')
        result = await tencent._qrcode_image_data_url(page, frame, image_locator())
        self.assertEqual(result, 'data:image/jpeg;base64,' + base64.b64encode(b'jpeg-body').decode())
        self.assertEqual(request.get.await_args.args[0], 'https://open.weixin.qq.com/connect/qrcode/test')
        response.dispose.assert_awaited_once()

    async def test_data_url_is_preserved(self):
        src = 'data:image/png;base64,dGVzdA=='
        self.assertEqual(await tencent._qrcode_image_data_url(None, None, image_locator(src)), src)

    async def test_untrusted_url_is_not_fetched(self):
        request = SimpleNamespace(get=AsyncMock())
        page = SimpleNamespace(context=SimpleNamespace(request=request))
        frame = SimpleNamespace(url='https://open.weixin.qq.com/connect/qrconnect')
        result = await tencent._qrcode_image_data_url(page, frame, image_locator('http://127.0.0.1/private'))
        request.get.assert_not_awaited()
        self.assertTrue(result.startswith('data:image/png;base64,'))

    async def test_non_image_response_uses_rendered_image(self):
        response = SimpleNamespace(ok=True, headers={'content-type': 'text/html'}, dispose=AsyncMock())
        page = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=response))))
        image = image_locator()
        await tencent._qrcode_image_data_url(page, SimpleNamespace(url='https://open.weixin.qq.com/'), image)
        image.screenshot.assert_awaited_once()

    async def test_network_failure_falls_back_to_image(self):
        page = SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(get=AsyncMock(side_effect=OSError('network')))))
        image = image_locator()
        await tencent._qrcode_image_data_url(page, SimpleNamespace(url='https://open.weixin.qq.com/'), image)
        image.screenshot.assert_awaited_once()

    async def test_new_login_page_is_not_success_even_with_marketing_publish_text(self):
        page = fake_page('https://channels.weixin.qq.com/login.html', ('发表视频', '首页', '内容管理'))
        self.assertFalse(await tencent._is_tencent_login_completed(page))

    async def test_blank_protected_route_is_not_success(self):
        self.assertFalse(await tencent._is_tencent_login_completed(fake_page(tencent.TENCENT_UPLOAD_URL)))

    async def test_authenticated_home_is_success_without_upload_redirect(self):
        page = fake_page('https://channels.weixin.qq.com/platform', ('首页', '内容管理'))
        self.assertTrue(await tencent._is_tencent_login_completed(page))

    async def test_authenticated_upload_page_is_success(self):
        self.assertTrue(await tencent._is_tencent_login_completed(fake_page(tencent.TENCENT_UPLOAD_URL, ('发表视频',))))

    async def test_login_iframe_on_protected_route_is_not_success(self):
        page = fake_page(tencent.TENCENT_UPLOAD_URL, ('发表视频',),
                         [SimpleNamespace(url='https://open.weixin.qq.com/connect/qrconnect')])
        self.assertFalse(await tencent._is_tencent_login_completed(page))

    async def test_lookalike_host_is_not_success(self):
        self.assertFalse(await tencent._is_tencent_login_completed(
            fake_page('https://channels.weixin.qq.com.invalid/platform', ('发表视频',))))

    async def test_closed_browser_does_not_wait_for_qrcode(self):
        with self.assertRaisesRegex(RuntimeError, '关闭'):
            await tencent._extract_tencent_qrcode_src(SimpleNamespace(is_closed=lambda: True))


class TencentWebAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'db').mkdir()
        with closing(sqlite3.connect(self.root / 'db/database.db')) as db:
            db.execute('CREATE TABLE user_info(id INTEGER PRIMARY KEY, type INTEGER, filePath TEXT, userName TEXT, status INTEGER)')
        self.base_patch = patch.object(login, 'BASE_DIR', self.root)
        self.base_patch.start()

    def tearDown(self):
        self.base_patch.stop()
        self.temp.cleanup()

    def rows(self):
        with closing(sqlite3.connect(self.root / 'db/database.db')) as db:
            return db.execute('SELECT type,filePath,userName,status FROM user_info').fetchall()

    async def test_success_forwards_refreshed_qr_and_saves_verified_account(self):
        queue = LoginStatusQueue()
        async def core(path, qrcode_callback, status_callback, **_):
            for version in ('first', 'refreshed'):
                qrcode_callback({'image_data_url': 'data:image/png;base64,' + version})
            status_callback({'stage': 'verifying', 'message': '正在保存'})
            Path(path).write_text('{"cookies":[],"origins":[]}', encoding='utf-8')
            return {'success': True}
        with patch.object(login, 'tencent_cookie_gen', new=core):
            await login.get_tencent_cookie(' 一游解忧 ', queue)
        messages = list(queue.queue)
        self.assertIn('data:image/png;base64,refreshed', messages)
        self.assertEqual(messages[-1], '200')
        self.assertEqual(self.rows()[0][0::2], (2, '一游解忧'))
        self.assertTrue((self.root / 'cookiesFile' / self.rows()[0][1]).is_file())

    async def test_failed_login_reports_reason_and_removes_partial_credentials(self):
        queue = LoginStatusQueue()
        async def core(path, **_):
            Path(path).write_text('{}')
            return {'success': False, 'message': '等待视频号扫码登录超时'}
        with patch.object(login, 'tencent_cookie_gen', new=core):
            await login.get_tencent_cookie('test', queue)
        self.assertEqual(self.rows(), [])
        self.assertEqual(list((self.root/'cookiesFile').iterdir()), [])
        self.assertEqual(list(queue.queue)[-2]['data']['message'], '等待视频号扫码登录超时')
        self.assertEqual(list(queue.queue)[-1], '500')

    async def test_cancelled_attempt_never_creates_an_account(self):
        queue = LoginStatusQueue()
        async def core(path, **_):
            Path(path).write_text('{}')
            queue.cancelled.set()
            return {'success': True}
        with patch.object(login, 'tencent_cookie_gen', new=core):
            await login.get_tencent_cookie('test', queue)
        self.assertEqual(self.rows(), [])
        self.assertEqual(list((self.root/'cookiesFile').iterdir()), [])
        self.assertNotIn('200', list(queue.queue))

    async def test_database_failure_removes_new_credentials(self):
        queue = LoginStatusQueue()
        async def core(path, **_):
            Path(path).write_text('{}')
            return {'success': True}
        with closing(sqlite3.connect(self.root/'db/database.db')) as db:
            db.execute('DROP TABLE user_info')
        with patch.object(login, 'tencent_cookie_gen', new=core):
            with self.assertRaises(sqlite3.OperationalError):
                await login.get_tencent_cookie('test', queue)
        self.assertEqual(list((self.root/'cookiesFile').iterdir()), [])


def test_real_dom_distinguishes_qrcode_expiration_from_parent_load_placeholder():
    async def scenario():
        async with tencent.async_playwright() as p:
            browser = await p.chromium.launch(**tencent._build_launch_kwargs(headless=True))
            try:
                page = await browser.new_page()
                await page.set_content('''
                    <div>加载失败，点击重试</div>
                    <div id="expired" style="display:none">二维码已失效，请点击刷新</div>
                    <div id="scanned" style="display:none">已扫码，请在手机上进行确认</div>
                ''')
                assert not await tencent._is_tencent_qrcode_expired(page)
                assert not await tencent._is_tencent_qrcode_scanned(page)
                await page.locator('#expired').evaluate("e => e.style.display = 'block'")
                assert await tencent._is_tencent_qrcode_expired(page)
                await page.locator('#expired').evaluate("e => e.style.display = 'none'")
                await page.locator('#scanned').evaluate("e => e.style.display = 'block'")
                assert not await tencent._is_tencent_qrcode_expired(page)
                assert await tencent._is_tencent_qrcode_scanned(page)
            finally:
                await browser.close()
    asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
