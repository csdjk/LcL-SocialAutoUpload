import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from uploader.douyin_uploader import main as douyin


class DouyinLoginStateTests(unittest.IsolatedAsyncioTestCase):
    def response(self, payload, url='https://creator.douyin.com/passport/web/check_qrconnect/?token=private'):
        response = MagicMock()
        response.url = url
        response.status = 200
        response.json = AsyncMock(return_value=payload)
        return response

    async def test_phone_scan_is_reported_without_navigation(self):
        state, statuses = {}, []
        response = self.response({'message': 'success', 'data': {'error_code': 0, 'status': 'scanned'}})
        await douyin._observe_douyin_login_response(response, state, statuses.append)
        self.assertEqual(state['stage'], 'scanned')
        self.assertEqual(statuses[-1]['stage'], 'scanned')
        self.assertIn('手机', statuses[-1]['message'])
        await douyin._observe_douyin_login_response(response, state, statuses.append)
        self.assertEqual(len(statuses), 1)

    async def test_mobile_confirmation_security_rejection_ends_wait_immediately(self):
        state, statuses = {}, []
        response = self.response({'message': 'error', 'data': {
            'error_code': 2046,
            'description': '为保障你的账号安全，请前往抖音APP完成验证后再进行登录',
            'encrypt_uid': 'must-not-leak', 'schema': 'must-not-leak',
        }})
        await douyin._observe_douyin_login_response(response, state, statuses.append)
        page = MagicMock()
        page.url = 'https://creator.douyin.com/'
        with patch.object(douyin, '_is_douyin_login_completed', new=AsyncMock()) as completed, \
             patch.object(douyin.asyncio, 'sleep', new=AsyncMock()) as sleep:
            result = await douyin._wait_for_douyin_login(
                page, 'unused.json', {}, login_state=state, poll_interval=0,
            )
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'verification_required')
        self.assertIn('2046', result['message'])
        self.assertIn('抖音APP完成验证', result['message'])
        self.assertNotIn('must-not-leak', str(state) + str(statuses) + str(result))
        completed.assert_not_awaited()
        sleep.assert_not_awaited()

    async def test_unrelated_response_cannot_change_login_state(self):
        state = {}
        response = self.response({'message': 'error', 'data': {'error_code': 2046}},
                                 url='https://example.com/passport/web/check_qrconnect/')
        await douyin._observe_douyin_login_response(response, state)
        self.assertEqual(state, {})
        response.json.assert_not_awaited()

    async def test_confirmed_response_alone_is_not_login_success(self):
        state = {}
        response = self.response({'message': 'success', 'data': {'error_code': 0, 'status': 'confirmed'}})
        await douyin._observe_douyin_login_response(response, state)
        self.assertEqual(state['stage'], 'verifying')
        self.assertNotIn('success', state)

    async def test_interactive_verification_keeps_official_browser_session_pending(self):
        state, statuses = {}, []
        response = self.response({'message': 'error', 'data': {'error_code': 2046, 'description': '需额外验证'}})
        await douyin._observe_douyin_login_response(response, state, statuses.append, interactive=True)
        self.assertNotIn('failure', state)
        self.assertTrue(state['verification_required'])
        self.assertEqual(statuses[-1]['stage'], 'verification_required')
        page = MagicMock()
        page.url = 'https://creator.douyin.com/creator-micro/home'
        page.is_closed.return_value = False
        with patch.object(douyin, '_is_douyin_login_completed', new=AsyncMock(return_value=True)):
            result = await douyin._wait_for_douyin_login(page, 'unused.json', {}, login_state=state, interactive=True, max_checks=1)
        self.assertTrue(result['success'])

    async def test_closing_official_window_is_not_login_success(self):
        page = MagicMock()
        page.url = 'https://creator.douyin.com/'
        page.is_closed.return_value = True
        result = await douyin._wait_for_douyin_login(page, 'unused.json', {}, interactive=True, max_checks=1)
        self.assertFalse(result['success'])
        self.assertEqual(result['status'], 'cancelled')

    async def test_interactive_timeout_does_not_claim_verification_success(self):
        page = MagicMock()
        page.url = 'https://creator.douyin.com/'
        result = await douyin._wait_for_douyin_login(page, 'unused.json', {}, login_state={'verification_required': True}, interactive=True, max_checks=0)
        self.assertFalse(result['success'])
        self.assertIn('官方窗口', result['message'])

    async def test_other_rejection_reports_platform_reason(self):
        state = {}
        response = self.response({'message': 'error', 'data': {'error_code': 1001, 'description': '登录请求已失效'}})
        await douyin._observe_douyin_login_response(response, state)
        self.assertEqual(state['failure']['status'], 'rejected')
        self.assertIn('1001', state['failure']['message'])
        self.assertIn('登录请求已失效', state['failure']['message'])

    async def test_browser_response_rejection_closes_session_without_saving_account(self):
        response = self.response({'message': 'error', 'data': {'error_code': 2046, 'description': '请前往抖音APP完成验证'}})
        handlers = {}
        page = MagicMock()
        page.url = 'https://creator.douyin.com/'
        page.on.side_effect = lambda name, handler: handlers.update({name: handler})

        async def goto(*args, **kwargs):
            await handlers['response'](response)

        page.goto = AsyncMock(side_effect=goto)
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.storage_state = AsyncMock()
        context.close = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        driver = MagicMock()
        driver.chromium.launch = AsyncMock(return_value=browser)
        playwright = MagicMock()
        playwright.__aenter__ = AsyncMock(return_value=driver)
        playwright.__aexit__ = AsyncMock(return_value=None)
        with tempfile.TemporaryDirectory() as temp:
            account = Path(temp)/'account.json'
            qr = Path(temp)/'qr.png'
            qr.write_bytes(b'qr fixture')
            with patch.object(douyin, 'async_playwright', return_value=playwright), \
                 patch.object(douyin, 'set_init_script', new=AsyncMock(return_value=context)), \
                 patch.object(douyin, '_save_douyin_qrcode', new=AsyncMock(return_value={'image_path': str(qr), 'image_data_url': 'data:image/png;base64,fixture'})):
                result = await douyin.douyin_cookie_gen(account, qrcode_callback=lambda _: None)
            self.assertEqual(result['status'], 'verification_required')
            self.assertFalse(account.exists())
            self.assertFalse(qr.exists())
        context.storage_state.assert_not_awaited()
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()

    async def test_interactive_core_waits_for_official_verification_then_saves_session(self):
        response = self.response({'message': 'error', 'data': {'error_code': 2046, 'description': '需额外验证'}})
        handlers, statuses = {}, []
        page = MagicMock()
        page.url = 'https://creator.douyin.com/'
        page.is_closed.return_value = False
        page.on.side_effect = lambda name, handler: handlers.update({name: handler})
        async def goto(*args, **kwargs):
            await handlers['response'](response)
        page.goto = AsyncMock(side_effect=goto)
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.cookies = AsyncMock(return_value=[{'name': 'sessionid', 'value': 'test-session'}])
        context.storage_state = AsyncMock()
        context.close = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        driver = MagicMock()
        driver.chromium.launch = AsyncMock(return_value=browser)
        playwright = MagicMock()
        playwright.__aenter__ = AsyncMock(return_value=driver)
        playwright.__aexit__ = AsyncMock(return_value=None)
        completed = AsyncMock(side_effect=[False, True])
        with patch.object(douyin, 'async_playwright', return_value=playwright), \
             patch.object(douyin, 'set_init_script', new=AsyncMock(return_value=context)), \
             patch.object(douyin, '_save_douyin_qrcode', new=AsyncMock(side_effect=AssertionError('manual login cannot wait for QR extraction'))) as qr, \
             patch.object(douyin, '_is_douyin_login_completed', new=completed):
            result = await douyin.douyin_cookie_gen('unused.json', interactive=True, status_callback=statuses.append, max_checks=2, poll_interval=0)
        self.assertTrue(result['success'])
        self.assertEqual(completed.await_count, 2)
        self.assertFalse(driver.chromium.launch.call_args.kwargs['headless'])
        self.assertIn('browser_open', [s['stage'] for s in statuses])
        self.assertIn('verification_required', [s['stage'] for s in statuses])
        qr.assert_not_awaited()
        context.storage_state.assert_awaited_once_with(path='unused.json')
        browser.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
