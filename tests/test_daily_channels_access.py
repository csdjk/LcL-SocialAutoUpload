"""Local fixtures only: never upload media or write the user's publication ledger."""
import json
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, patch

import pytest
import daily_publish as daily
import tests.test_daily_publish as fixtures

KEY = 'wechat_channels'


@pytest.fixture
def case():
    value = fixtures.DailyPublishTests()
    value.setUp()
    try:
        (value.base / 'db').mkdir()
        (value.base / 'cookiesFile').mkdir()
        (value.base / 'cookiesFile' / 'web.json').write_text('{}', encoding='utf-8')
        with closing(sqlite3.connect(value.base / 'db/database.db')) as conn:
            conn.execute('CREATE TABLE user_info (id INTEGER PRIMARY KEY,type INTEGER,filePath TEXT,userName TEXT,status INTEGER)')
            conn.execute("INSERT INTO user_info VALUES (7,2,'web.json','测试视频号',1)")
            conn.commit()
        config = json.loads(value.settings_path.read_text(encoding='utf-8'))
        config['accounts'][KEY] = {'web_account_id': 7, 'display_name': '测试视频号'}
        config['platforms'][KEY] = {'access_status': 'blocked_browser_policy'}
        value.settings_path.write_text(json.dumps(config), encoding='utf-8')
        (value.base / 'publishing.json').write_text(json.dumps({'platforms': {KEY: {
            'access_status': 'blocked_browser_policy', 'enabled': False}}}), encoding='utf-8')
        yield value
    finally:
        value.doCleanups()


def configure(case, update):
    config = json.loads(case.settings_path.read_text(encoding='utf-8'))
    update(config)
    case.settings_path.write_text(json.dumps(config), encoding='utf-8')


def status(case):
    return daily.status_for(daily.load_package(case.path))[KEY]


def test_ready_without_external_identity_or_old_browser_flags(case):
    original = case.settings_path.read_bytes()
    item = status(case)
    assert item['access_status'] == 'ready'
    assert item['account']['account_id'] == 'web:wechat_channels:7'
    assert item['account']['identity_source'] == 'tool'
    assert not item['account_mismatch']
    assert case.settings_path.read_bytes() == original
    payload = daily.load_package(case.path)['platforms'][KEY]
    aid = item['account']['account_id']
    daily.save_draft(case.path, KEY, aid, payload)
    assert daily.get_draft(case.path, KEY, aid)['title'] == payload['title']
    job = daily.task(daily.reserve(case.path, KEY, 'manual'))
    assert job['account_id'] == aid and job['source'] == 'manual'
    with pytest.raises(ValueError, match='已预约或提交'):
        daily.reserve(case.path, KEY, 'manual')


def test_missing_external_workspace_file_is_not_a_block(case):
    (case.base / 'publishing.json').unlink()
    assert status(case)['access_status'] == 'ready'


def test_explicit_account_identity_is_preserved_for_old_drafts_and_ledger(case):
    configure(case, lambda cfg: cfg['accounts'][KEY].update(account_id='known-channel-id'))
    assert status(case)['account']['account_id'] == 'known-channel-id'
    assert daily.task(daily.reserve(case.path, KEY, 'manual'))['account_id'] == 'known-channel-id'


@pytest.mark.parametrize('broken', ['deleted', 'wrong_platform', 'expired', 'missing_file', 'escape'])
def test_invalid_web_account_cannot_be_enabled_by_removing_policy(case, broken):
    with closing(sqlite3.connect(case.base / 'db/database.db')) as conn:
        if broken == 'deleted': conn.execute('DELETE FROM user_info')
        if broken == 'wrong_platform': conn.execute('UPDATE user_info SET type=3')
        if broken == 'expired': conn.execute('UPDATE user_info SET status=0')
        if broken == 'escape':
            (case.base / 'outside.json').write_text('{}', encoding='utf-8')
            conn.execute("UPDATE user_info SET filePath='../outside.json'")
        conn.commit()
    if broken == 'missing_file': (case.base / 'cookiesFile/web.json').unlink()
    expected = 'binding_missing' if broken in ('deleted', 'wrong_platform') else 'needs_login'
    assert status(case)['access_status'] == expected
    with pytest.raises(ValueError, match='账号|登录'):
        daily.reserve(case.path, KEY, 'manual')


@pytest.mark.parametrize('bad_id', [True, 0, -1, '../7', 'bad'])
def test_invalid_binding_is_not_mapped_to_an_arbitrary_account(case, bad_id):
    configure(case, lambda cfg: cfg['accounts'][KEY].update(web_account_id=bad_id))
    assert status(case)['access_status'] != 'ready'
    with pytest.raises(ValueError): daily.reserve(case.path, KEY, 'manual')


def test_explicit_tool_disable_and_platform_restriction_are_still_honored(case):
    configure(case, lambda cfg: cfg['platforms'][KEY].update(enabled=False))
    assert status(case)['access_status'] == 'disabled'
    with pytest.raises(ValueError, match='关闭'): daily.reserve(case.path, KEY, 'manual')
    configure(case, lambda cfg: cfg['platforms'][KEY].update(enabled=True, access_status='verification_required'))
    assert status(case)['access_status'] == 'verification_required'
    with pytest.raises(ValueError, match='登录状态'): daily.reserve(case.path, KEY, 'manual')


def test_automation_and_yesterday_are_still_rejected(case):
    with pytest.raises(ValueError, match='自动投稿.*验收'): daily.reserve(case.path, KEY, 'automation')
    yesterday = case.make_package('2026-09-22', 'r001')
    with pytest.raises(ValueError, match='当天'): daily.reserve(yesterday, KEY, 'manual')


def test_cross_account_ledger_is_not_lost_when_binding_changes(case):
    daily.reserve(case.path, KEY, 'manual')
    with closing(sqlite3.connect(case.base / 'db/database.db')) as conn:
        conn.execute("INSERT INTO user_info VALUES (8,2,'web.json','其他视频号',1)")
        conn.commit()
    configure(case, lambda cfg: cfg['accounts'][KEY].update(web_account_id=8))
    assert status(case)['account_mismatch']
    with pytest.raises(ValueError, match='其他账号'): daily.reserve(case.path, KEY, 'manual')


def test_required_ai_declaration_is_still_enforced(case):
    payload = dict(daily.load_package(case.path)['platforms'][KEY], ai_declaration='')
    with pytest.raises(ValueError, match='AI 内容声明'): daily.reserve(case.path, KEY, 'manual', payload)


def test_uploader_still_validates_real_cookie_before_upload(case):
    from uploader.tencent_uploader import main as tencent
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(tencent, 'cookie_auth', AsyncMock(return_value=False)) as auth, patch.object(tencent, 'TencentVideo') as upload:
        result = daily.run(job_id)
        assert result['state'] == 'failed'
        assert result['evidence']['upload_started'] is False
        assert result['evidence']['failure_code'] == 'login_check_failed'
        auth.assert_awaited_once()
        upload.assert_not_called()
    assert status(case)['retry_allowed']
    assert status(case)['access_status'] == 'needs_login'
    assert '尚未上传' in status(case)['error']


def test_uploader_uses_bound_cookie_cover_and_ai_requirements(case):
    from uploader.tencent_uploader import main as tencent
    job_id = daily.reserve(case.path, KEY, 'manual')
    with patch.object(tencent, 'cookie_auth', AsyncMock(return_value=True)), patch.object(tencent, 'TencentVideo') as upload:
        upload.return_value.tencent_upload_video = AsyncMock()
        daily.run(job_id)
        args = upload.call_args.kwargs
        assert args['account_file'] == str((case.base / 'cookiesFile/web.json').resolve())
        assert args['require_content_label'] and args['require_thumbnail']
        assert args['thumbnail_landscape_path'] and args['thumbnail_portrait_path']
        assert args['desc'] == '简介'
        upload.return_value.tencent_upload_video.assert_awaited_once()


def test_http_today_draft_and_manual_reservation_share_resolved_identity(case):
    import sau_backend
    package = daily.load_package(case.path)
    with patch.object(daily, 'find_today', return_value=(case.path, package, [])):
        response = sau_backend.app.test_client().get('/daily/today')
    assert response.status_code == 200
    item = response.json['data']['status'][KEY]
    assert item['access_status'] == 'ready'
    aid = item['account']['account_id']
    client = sau_backend.app.test_client()
    result = client.put('/daily/draft', json={'package_path': str(case.path), 'platform': KEY,
        'account_id': aid, 'payload': package['platforms'][KEY]})
    assert result.status_code == 200
    # Reserve only in the isolated fixture DB. Never launch the publication worker.
    with patch('publishing.queue.ensure_worker') as worker:
        result = client.post('/daily/submit', json={'package_path': str(case.path), 'platforms': [KEY]})
        worker.assert_called_once()
    assert result.status_code == 200 and not result.json['data']['errors']
    assert len(result.json['data']['jobs']) == 1
