"""Bounded, cancellable login workers and backward-compatible SSE messages."""
import asyncio
import json
import logging
import threading
import time
from queue import Empty, Queue


class LoginStatusQueue(Queue):
    def __init__(self):
        super().__init__()
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.terminal = threading.Event()

    def put(self, item, block=True, timeout=None):
        if self.terminal.is_set():
            return
        if isinstance(item, str) and item in ('200', '500'):
            self.terminal.set()
        super().put(item, block=block, timeout=timeout)


def report_failure(queue, message):
    queue.put({'event': 'login-error', 'data': {'message': message}})
    queue.put('500')


async def _run_login(login_function, name, queue, timeout):
    task = asyncio.create_task(login_function(name, queue))
    deadline = time.monotonic() + timeout
    try:
        while not task.done():
            if queue.cancelled.is_set():
                return
            if time.monotonic() >= deadline:
                report_failure(queue, '登录等待超时，请重新获取二维码并在手机上确认')
                return
            await asyncio.wait({task}, timeout=0.2)
        await task
        if not queue.terminal.is_set() and not queue.cancelled.is_set():
            report_failure(queue, '登录流程已结束，但未保存账号，请重试')
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def run_login(login_function, name, queue, *, timeout=360, logger=None):
    try:
        asyncio.run(_run_login(login_function, name, queue, timeout))
    except Exception:
        (logger or logging.getLogger(__name__)).exception('账号登录流程失败')
        if not queue.cancelled.is_set():
            report_failure(queue, '登录服务运行失败，请查看后端日志后重试')
    finally:
        queue.finished.set()


def sse_stream(queue, heartbeat=2):
    try:
        # Flush response headers immediately instead of waiting for the QR image.
        yield ': connected\n\n'
        while True:
            try:
                message = queue.get(timeout=heartbeat)
            except Empty:
                if queue.finished.is_set():
                    return
                yield ': keep-alive\n\n'
                continue
            if isinstance(message, dict):
                event = message.get('event')
                if event in ('login-status', 'login-error'):
                    data = json.dumps(message.get('data', {}), ensure_ascii=False)
                    yield f'event: {event}\ndata: {data}\n\n'
            else:
                # Prefix every line so that a message cannot inject SSE fields.
                yield ''.join(f'data: {line}\n' for line in str(message).splitlines()) + '\n'
                if message in ('200', '500'):
                    return
    finally:
        # Closing the dialog/tab terminates the corresponding browser task.
        queue.cancelled.set()
