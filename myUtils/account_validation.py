"""Bounded account validation; transient errors do not destroy saved validity."""
import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3


async def validate_accounts(database: Path, checker, *, timeout=25, batch_timeout=30, concurrency=2):
    with closing(sqlite3.connect(database)) as conn:
        rows = conn.execute('SELECT id, type, filePath, userName, status FROM user_info').fetchall()
    results = {}
    errors = {}
    semaphore = asyncio.Semaphore(concurrency)

    async def validate(row):
        async with semaphore:
            try:
                result = await asyncio.wait_for(checker(row[1], row[2]), timeout=timeout)
                if result is True or result is False:
                    results[row[0]] = int(result)
                else:
                    errors[row[0]] = '校验未返回有效结果，已保留上次状态'
            except asyncio.TimeoutError:
                errors[row[0]] = '校验超时，已保留上次状态'
            except Exception:
                errors[row[0]] = '校验服务暂时不可用，已保留上次状态'

    tasks = {asyncio.create_task(validate(row)): row[0] for row in rows}
    try:
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=batch_timeout)
            for task in pending:
                errors[tasks[task]] = '校验超时，已保留上次状态'
                task.cancel()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    with closing(sqlite3.connect(database)) as conn:
        with conn:
            for row in rows:
                if row[0] in results and row[0] not in errors:
                    # A concurrent re-login may have replaced the cookie. Do not overwrite it.
                    conn.execute(
                        'UPDATE user_info SET status=? WHERE id=? AND type=? AND filePath=? AND status=?',
                        (results[row[0]], row[0], row[1], row[2], row[4]),
                    )
        # Re-read so newly added/deleted accounts are not lost in the response.
        current = conn.execute('SELECT id, type, filePath, userName, status FROM user_info').fetchall()
    return [list(row) for row in current], [{'id': key, 'message': message} for key, message in errors.items()]
