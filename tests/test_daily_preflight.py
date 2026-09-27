"""Only isolated fixtures. Tests never invoke a real upload or modify user data."""
from contextlib import closing
from unittest.mock import AsyncMock, patch
import sqlite3
import pytest
import daily_publish as daily
from tests.test_daily_channels_access import case, status, KEY
from uploader.tencent_uploader import main as tencent


def test_login_failure_is_failed_not_unknown_and_old_account_is_not_healthy(case):
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(tencent, 'cookie_auth', AsyncMock(return_value=False)), patch.object(tencent, 'TencentVideo') as upload:
        result = daily.run(job_id)
    upload.assert_not_called()
    assert result['state'] == 'failed'
    assert result['remote_id'] is None
    assert result['evidence']['stage'] == 'preflight'
    assert result['evidence']['remote_absent'] is True
    assert result['evidence']['upload_started'] is False
    item = status(case)
    assert item['next_action'] == 'relogin'
    assert item['failure_code'] == 'login_check_failed'
    assert item['access_status'] == 'needs_login'
    assert item['retry_allowed'] is True
    assert '尚未上传' in item['error']
    with pytest.raises(ValueError, match='登录'): daily.reserve(case.path, KEY, 'manual')


def test_auth_exception_is_preflight_failure_without_expiring_saved_account(case):
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(tencent, 'cookie_auth', AsyncMock(side_effect=OSError('network fixture'))), patch.object(tencent, 'TencentVideo') as upload:
        result = daily.run(job_id)
    upload.assert_not_called()
    assert result['state'] == 'failed'
    assert result['evidence']['failure_code'] == 'login_check_unavailable'
    assert status(case)['access_status'] == 'ready'
    assert status(case)['retry_allowed']


def test_same_error_after_uploader_entry_must_remain_unknown(case):
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(tencent, 'cookie_auth', AsyncMock(return_value=True)), patch.object(tencent, 'TencentVideo') as upload:
        upload.return_value.tencent_upload_video = AsyncMock(side_effect=ValueError('视频号工具账号登录已失效'))
        result = daily.run(job_id)
    assert result['state'] == 'unknown'
    assert result['evidence']['stage'] == 'checking'
    assert result['evidence'].get('submission_started') is not False
    assert not status(case)['retry_allowed']
    with pytest.raises(ValueError, match='已预约或提交'): daily.reserve(case.path, KEY, 'manual')


def test_bad_material_is_known_not_uploaded(case):
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(daily, 'load_package', side_effect=ValueError('hash mismatch')), patch.object(daily, '_upload') as upload:
        result = daily.run(job_id)
    upload.assert_not_called()
    assert result['state'] == 'failed'
    assert result['evidence']['failure_code'] == 'package_invalid'
    assert result['evidence']['upload_started'] is False


def test_relogin_allows_manual_retry_without_reconciliation_or_auto_upload(case):
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(tencent, 'cookie_auth', AsyncMock(return_value=False)):
        daily.run(job_id)
    (case.base/'cookiesFile/refreshed.json').write_text('{}')
    with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
        conn.execute("UPDATE user_info SET status=1,filePath='refreshed.json' WHERE id=7")
        conn.commit()
    assert status(case)['access_status'] == 'ready'
    assert status(case)['retry_allowed'] is True
    with patch.object(daily, '_upload') as upload:
        new_id = daily.reserve(case.path, KEY, 'manual')
        upload.assert_not_called()
    assert new_id != job_id
    with pytest.raises(ValueError, match='自动投稿.*验收'): daily.reserve(case.path, KEY, 'automation')


def test_concurrent_relogin_is_not_invalidated_by_old_cookie_check(case):
    job_id = daily.reserve(case.path, KEY, 'manual')
    async def old_check(_):
        (case.base/'cookiesFile/new.json').write_text('{}')
        with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
            conn.execute("UPDATE user_info SET filePath='new.json',status=1 WHERE id=7")
            conn.commit()
        return False
    with patch.object(tencent, 'cookie_auth', AsyncMock(side_effect=old_check)), patch.object(tencent, 'TencentVideo') as upload:
        assert daily.run(job_id)['state'] == 'failed'
    upload.assert_not_called()
    assert status(case)['access_status'] == 'ready'
    with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
        assert conn.execute('SELECT status,filePath FROM user_info WHERE id=7').fetchone() == (1,'new.json')


def test_live_status_api_returns_failure_stage_and_recovery_action(case):
    import sau_backend
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(tencent, 'cookie_auth', AsyncMock(return_value=False)):
        daily.run(job_id)
    package = daily.load_package(case.path)
    with patch.object(daily, 'find_today', return_value=(case.path, package, [])):
        response = sau_backend.app.test_client().get('/daily/today')
    assert response.status_code == 200
    item = response.json['data']['status'][KEY]
    assert item['state'] == 'failed'
    assert item['upload_started'] is False
    assert item['next_action'] == 'relogin'
    assert '尚未上传' in item['error']
