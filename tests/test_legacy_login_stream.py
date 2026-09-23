import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

import sau_backend
from myUtils.login_session import LoginStatusQueue, run_login, sse_stream


class LegacyLoginStreamTests(unittest.TestCase):
    def test_login_exception_reports_failure_and_closes_stream(self):
        queue = LoginStatusQueue()
        with patch.object(sau_backend, 'get_tencent_cookie', new=AsyncMock(side_effect=RuntimeError('test'))):
            with self.assertLogs(sau_backend.app.logger, level='ERROR'):
                sau_backend.run_async_function('2', 'test-account', queue)
        messages = list(sse_stream(queue))
        self.assertTrue(any('event: login-error' in message for message in messages))
        self.assertEqual(messages[-1], 'data: 500\n\n')
        self.assertTrue(queue.finished.is_set())

    def test_login_success_delivers_qr_and_terminal_status(self):
        queue = LoginStatusQueue()
        async def local_login(_name, queue):
            queue.put('data:image/png;base64,local-test-qr')
            queue.put('200')
        with patch.object(sau_backend, 'get_tencent_cookie', new=local_login):
            sau_backend.run_async_function('2', 'test-account', queue)
        self.assertEqual(list(sse_stream(queue)), [
            ': connected\n\n', 'data: data:image/png;base64,local-test-qr\n\n', 'data: 200\n\n',
        ])

    def test_invalid_login_request_does_not_start_worker(self):
        with patch.object(sau_backend.threading, 'Thread') as worker:
            for url in ('/login?type=unknown&id=test', '/login?type=2&id=%20', '/login?type=2'):
                self.assertEqual(sau_backend.app.test_client().get(url).status_code, 400)
            worker.assert_not_called()

    def test_stream_flushes_headers_and_sends_heartbeat(self):
        queue = LoginStatusQueue()
        stream = sse_stream(queue, heartbeat=0.001)
        self.assertEqual(next(stream), ': connected\n\n')
        self.assertEqual(next(stream), ': keep-alive\n\n')
        stream.close()
        self.assertTrue(queue.cancelled.is_set())

    def test_disconnect_cancels_running_worker_and_executes_cleanup(self):
        queue = LoginStatusQueue()
        started, cleaned = threading.Event(), threading.Event()
        async def local_login(_name, _queue):
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                cleaned.set()
        worker = threading.Thread(target=run_login, args=(local_login, 'test', queue), daemon=True)
        worker.start()
        self.assertTrue(started.wait(2))
        stream = sse_stream(queue)
        next(stream)
        stream.close()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(cleaned.is_set())
        self.assertTrue(queue.empty())

    def test_total_timeout_reports_failure_and_cleans_worker(self):
        queue = LoginStatusQueue()
        cleaned = threading.Event()
        async def local_login(_name, _queue):
            try:
                await asyncio.sleep(60)
            finally:
                cleaned.set()
        run_login(local_login, 'test', queue, timeout=0.01)
        self.assertTrue(cleaned.is_set())
        self.assertEqual(list(sse_stream(queue))[-1], 'data: 500\n\n')

    def test_worker_return_without_terminal_event_is_failure(self):
        queue = LoginStatusQueue()
        run_login(AsyncMock(), 'test', queue)
        self.assertEqual(list(sse_stream(queue))[-1], 'data: 500\n\n')

    def test_status_messages_are_json_and_only_one_terminal_is_sent(self):
        queue = LoginStatusQueue()
        queue.put({'event': 'login-status', 'data': {'message': '已扫码\n请确认'}})
        queue.put('200')
        queue.put('500')
        messages = list(sse_stream(queue))
        self.assertIn('event: login-status\ndata:', messages[1])
        self.assertIn('\\n', messages[1])
        self.assertEqual(messages[-1], 'data: 200\n\n')
        self.assertEqual(len(messages), 3)


if __name__ == '__main__':
    unittest.main()
