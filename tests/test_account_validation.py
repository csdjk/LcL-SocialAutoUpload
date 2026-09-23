import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from myUtils.account_validation import validate_accounts


class AccountValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'db').mkdir()
        self.db = self.root / 'db' / 'database.db'
        with closing(sqlite3.connect(self.db)) as c:
            c.execute('CREATE TABLE user_info (id INTEGER PRIMARY KEY, type INTEGER, filePath TEXT, userName TEXT, status INTEGER)')
            c.executemany('INSERT INTO user_info VALUES (?,2,?,?,?)', [(1,'one.json','账号一',1),(2,'two.json','账号二',0)])
            c.commit()

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, sql, args=()):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute(sql, args)
            c.commit()

    def test_success_recovers_invalid_status_and_failure_marks_invalid(self):
        async def check(_type, file): return file == 'two.json'
        rows, errors = asyncio.run(validate_accounts(self.db, check))
        self.assertEqual([r[4] for r in rows], [0,1]); self.assertEqual(errors, [])

    def test_exception_keeps_previous_status_without_leaking_exception(self):
        rows, errors = asyncio.run(validate_accounts(self.db, AsyncMock(side_effect=RuntimeError('secret detail'))))
        self.assertEqual([r[4] for r in rows], [1,0]); self.assertEqual(len(errors), 2)
        self.assertNotIn('secret detail', str(errors))

    def test_timeout_cancels_checker_and_keeps_saved_status(self):
        cleaned = []
        async def check(_type, file):
            try: await asyncio.sleep(60)
            finally: cleaned.append(file)
        rows, errors = asyncio.run(validate_accounts(self.db, check, timeout=0.01))
        self.assertEqual([r[4] for r in rows], [1,0]); self.assertEqual(len(errors), 2)
        self.assertEqual(len(cleaned), 2)

    def test_batch_timeout_includes_queued_accounts(self):
        checker = AsyncMock(side_effect=lambda *_: None)
        async def slow(*_): await asyncio.sleep(60)
        rows, errors = asyncio.run(validate_accounts(self.db, slow, timeout=10, batch_timeout=0.02, concurrency=1))
        self.assertEqual(len(errors), 2); self.assertEqual([r[4] for r in rows], [1,0])

    def test_new_account_is_included_in_response(self):
        async def check(_type, file):
            if file == 'one.json': self.edit("INSERT INTO user_info VALUES (3,2,'new.json','新账号',1)")
            return True
        rows, _ = asyncio.run(validate_accounts(self.db, check))
        self.assertEqual([r[0] for r in rows], [1,2,3])

    def test_deleted_account_is_not_restored(self):
        async def check(_type, file):
            if file == 'one.json': self.edit('DELETE FROM user_info WHERE id=1')
            return True
        rows, _ = asyncio.run(validate_accounts(self.db, check))
        self.assertEqual([r[0] for r in rows], [2])

    def test_old_validation_cannot_overwrite_relogin(self):
        async def check(_type, file):
            if file == 'one.json': self.edit("UPDATE user_info SET filePath='relogin.json', status=1 WHERE id=1")
            return False
        rows, _ = asyncio.run(validate_accounts(self.db, check))
        self.assertEqual(rows[0][2], 'relogin.json'); self.assertEqual(rows[0][4], 1)

    def test_api_preserves_legacy_row_contract_and_provides_validation_errors(self):
        import sau_backend
        with patch.object(sau_backend, 'BASE_DIR', self.root), patch.object(sau_backend, 'check_cookie', AsyncMock(return_value=True)):
            result = sau_backend.app.test_client().get('/getValidAccounts')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['code'], 200)
        self.assertEqual(result.json['validation_errors'], [])
        self.assertEqual([r[4] for r in result.json['data']], [1,1])


if __name__ == '__main__':
    unittest.main()
