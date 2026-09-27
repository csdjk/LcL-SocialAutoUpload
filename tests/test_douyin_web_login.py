import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

import sau_backend
from myUtils import auth, login
from myUtils.login_session import LoginStatusQueue


class DouyinWebLoginTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_qr_refresh_and_confirmed_login_save_account(self):
        queue = LoginStatusQueue()

        async def core(path, qrcode_callback, status_callback, **_):
            qrcode_callback({'image_data_url': 'data:image/png;base64,first'})
            qrcode_callback({'image_data_url': 'data:image/png;base64,refreshed'})
            status_callback({'stage': 'saving', 'message': '正在保存抖音账号'})
            Path(path).write_text('{"cookies":[],"origins":[]}', encoding='utf-8')
            return {'success': True}

        with patch.object(login, 'douyin_login_core', new=core):
            await login.get_douyin_cookie(' 长生但酒狂 ', queue)

        messages = list(queue.queue)
        self.assertIn('data:image/png;base64,refreshed', messages)
        self.assertIn({'event': 'login-status', 'data': {'stage': 'saving', 'message': '正在保存抖音账号'}}, messages)
        self.assertEqual(messages[-1], '200')
        self.assertEqual(self.rows()[0][0::2], (3, '长生但酒狂'))
        self.assertTrue((self.root / 'cookiesFile' / self.rows()[0][1]).is_file())

    async def test_failure_reports_reason_and_removes_partial_credentials(self):
        queue = LoginStatusQueue()

        async def core(path, **_):
            Path(path).write_text('{}', encoding='utf-8')
            return {'success': False, 'message': '扫码后仍需安全验证'}

        with patch.object(login, 'douyin_login_core', new=core):
            await login.get_douyin_cookie('test', queue)

        self.assertEqual(self.rows(), [])
        self.assertEqual(list((self.root / 'cookiesFile').iterdir()), [])
        self.assertEqual(list(queue.queue)[-2]['data']['message'], '扫码后仍需安全验证')
        self.assertEqual(list(queue.queue)[-1], '500')

    async def test_cancelled_login_does_not_add_account(self):
        queue = LoginStatusQueue()

        async def core(path, **_):
            Path(path).write_text('{}', encoding='utf-8')
            queue.cancelled.set()
            return {'success': True}

        with patch.object(login, 'douyin_login_core', new=core):
            await login.get_douyin_cookie('test', queue)

        self.assertEqual(self.rows(), [])
        self.assertEqual(list((self.root / 'cookiesFile').iterdir()), [])
        self.assertNotIn('200', list(queue.queue))

    async def test_missing_storage_is_not_a_success(self):
        queue = LoginStatusQueue()
        with patch.object(login, 'douyin_login_core', new=AsyncMock(return_value={'success': True})):
            with self.assertRaisesRegex(RuntimeError, '凭据未保存'):
                await login.get_douyin_cookie('test', queue)
        self.assertEqual(self.rows(), [])

    async def test_database_error_removes_new_credentials(self):
        queue = LoginStatusQueue()
        async def core(path, **_):
            Path(path).write_text('{}', encoding='utf-8')
            return {'success': True}
        with closing(sqlite3.connect(self.root / 'db/database.db')) as db:
            db.execute('DROP TABLE user_info')
        with patch.object(login, 'douyin_login_core', new=core):
            with self.assertRaises(sqlite3.OperationalError):
                await login.get_douyin_cookie('test', queue)
        self.assertEqual(list((self.root / 'cookiesFile').iterdir()), [])

    async def test_account_refresh_uses_shared_cookie_checker(self):
        cookie_file = self.root / 'cookiesFile' / 'account.json'
        shared_checker = AsyncMock(return_value=True)
        with patch('uploader.douyin_uploader.main.cookie_auth', new=shared_checker):
            self.assertTrue(await auth.cookie_auth_douyin(cookie_file))
        shared_checker.assert_awaited_once_with(cookie_file)

    async def test_official_browser_mode_is_explicit_and_does_not_require_qr_forwarding(self):
        queue = LoginStatusQueue()
        core = AsyncMock(return_value={'success': False, 'status': 'cancelled', 'message': '窗口已关闭'})
        with patch.object(login, 'douyin_login_core', new=core):
            await login.get_douyin_cookie('test', queue, interactive=True)
        kwargs = core.call_args.kwargs
        self.assertTrue(kwargs['interactive'])
        self.assertFalse(kwargs['headless'])
        self.assertIsNone(kwargs['qrcode_callback'])
        self.assertEqual(self.rows(), [])

    async def test_security_failure_exposes_manual_verification_action(self):
        queue = LoginStatusQueue()
        core = AsyncMock(return_value={'success': False, 'status': 'verification_required', 'message': '需抖音APP验证'})
        with patch.object(login, 'douyin_login_core', new=core):
            await login.get_douyin_cookie('test', queue)
        error = list(queue.queue)[-2]['data']
        self.assertEqual(error['status'], 'verification_required')
        self.assertIn('官方窗口', error['message'])

    def test_unrecognized_browser_mode_is_rejected(self):
        with patch.object(sau_backend.threading, 'Thread') as worker:
            response = sau_backend.app.test_client().get('/login?type=2&id=test&mode=external')
            self.assertEqual(response.status_code, 400)
            worker.assert_not_called()

    def test_browser_mode_dispatches_unified_edge_login(self):
        queue = LoginStatusQueue()
        async def core(platform, _name, queue, *, account_id=None):
            self.assertEqual(platform, 3)
            self.assertIsNone(account_id)
            queue.put('200')
        with patch.object(sau_backend, 'get_browser_cookie', new=core):
            sau_backend.run_async_function('3', 'test', queue, mode='browser')
        self.assertEqual(list(queue.queue), ['200'])

    def test_web_route_dispatches_to_shared_douyin_login(self):
        queue = LoginStatusQueue()
        async def core(_name, queue):
            queue.put('200')
        with patch.object(sau_backend, 'get_douyin_cookie', new=core), \
             patch.object(sau_backend, 'douyin_cookie_gen', new=AsyncMock(side_effect=AssertionError('legacy login used')), create=True):
            sau_backend.run_async_function('3', 'test', queue)
        self.assertEqual(list(queue.queue), ['200'])


if __name__ == '__main__':
    unittest.main()
