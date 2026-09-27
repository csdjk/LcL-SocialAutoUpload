"""State reconciliation only; no live upload or credential access."""
from unittest.mock import AsyncMock, patch

import pytest
import daily_publish as daily
from myUtils import daily_oneclick as one
from publishing import queue, service
from publishing.browser_readback import douyin_records, toutiao_records
from uploader.tencent_uploader.readback import match_posts
from tests.test_daily_channels_access import case, KEY
from tests.test_tencent_oneclick import DESC, DAY, response


def douyin_row():
    return {'aweme_id': '123', 'desc': 'title description', 'create_time': 1790488200,
            'status_value': 102, 'status': {'private_status': 0, 'reviewed': False,
            **{key: False for key in ('in_reviewing', 'is_delete', 'is_private', 'is_prohibited', 'self_see')}}}


def state(row):
    return douyin_records([row], 'title', 'description', '2026-09-27', '123')[0]['state']


def test_douyin_published_despite_legacy_reviewed_false():
    assert state(douyin_row()) == 'published'


@pytest.mark.parametrize('key', ['in_reviewing', 'is_delete', 'is_private', 'is_prohibited', 'self_see', 'private_status'])
def test_douyin_restricted_or_incomplete_flags_never_imply_publication(key):
    row = douyin_row()
    row['status'][key] = 1 if key == 'private_status' else True
    assert state(row) == 'processing'
    row['status'].pop(key)
    assert state(row) == 'processing'


def test_douyin_unknown_status_stays_pending():
    row = douyin_row(); row.pop('status_value')
    assert state(row) == 'processing'


@pytest.mark.parametrize('status,visible,expected', [(1,1,'published'), (None,1,'processing'),
    (1,None,'processing'), (2,1,'processing'), (1,2,'processing'), (999,999,'processing')])
def test_channels_public_status_requires_both_known_codes(status, visible, expected):
    data = response()
    data['data']['list'][0].update(status=status, visibleType=visible)
    result = match_posts(data, 'title', DESC, DAY)
    assert result['state'] == expected


def test_channels_sync_persists_public_state_and_original_evidence(case):
    ident = daily.reserve(case.path, KEY, 'manual')
    daily._state(ident, 'processing', evidence={'remote_id':'same', 'cover_screenshot':'cover.png'})
    result = {'status':'found', 'state':'published', 'remote_id':'same', 'platform_status':1, 'visible_type':1}
    with patch.object(one, 'readback_file', AsyncMock(return_value=result)), patch.object(daily, '_upload') as upload:
        saved = one.sync_result(ident)['task']
    assert saved['state'] == 'published'
    assert saved['evidence']['cover_screenshot'] == 'cover.png'
    assert saved['evidence']['visible_type'] == 1
    upload.assert_not_called()
    with pytest.raises(ValueError): daily.reserve(case.path, KEY, 'manual')


def test_channels_wrong_work_still_requires_manual_investigation(case):
    ident = daily.reserve(case.path, KEY, 'manual')
    daily._state(ident, 'processing', evidence={'remote_id':'original'})
    before = daily.task(ident)
    with patch.object(one, 'readback_file', AsyncMock(return_value={'status':'found', 'state':'published', 'remote_id':'other'})):
        with pytest.raises(ValueError, match='不一致'): one.sync_result(ident)
    assert daily.task(ident) == before


def test_initial_readback_honors_confirmed_publication(case):
    ident = daily.reserve(case.path, KEY, 'manual')
    assert daily._record_remote_found(daily.task(ident), {'remote_id':'same', 'state':'published'})['state'] == 'published'


@pytest.mark.parametrize('platform,account,transport', [('wechat_channels','web:wechat_channels:7','browser'),
                                                     ('douyin','web:douyin:2','browser'),
                                                     ('toutiao','web:toutiao:5','browser')])
def test_worker_reconciles_existing_browser_posts_without_enabling_autopost(case, platform, account, transport):
    ident = daily.reserve(case.path, KEY, 'manual')
    with daily._connection() as conn:
        conn.execute('UPDATE attempts SET platform=?,account_id=?,state=?,remote_id=?,payload=? WHERE id=?',
                     (platform, account, 'processing', 'same', daily.json.dumps({'_delivery':{'id':transport}}), ident))
    with patch.object(service, 'sync_result') as sync, patch.object(daily, '_upload') as upload:
        assert queue.sync_pending() == 1
    sync.assert_called_once_with(ident)
    upload.assert_not_called()


@pytest.mark.parametrize('status,visibility,expected', [(20,0,'published'),(10,0,'processing'),
    (20,None,'processing'),(20,1,'processing'),(999,0,'processing'),('20',0,'processing')])
def test_toutiao_requires_published_status_and_public_visibility(status, visibility, expected):
    row={'gidStr':'7690083921740022323','title':'title','createTime':1790487204,
         'itemStatus':status,'visibilityLevel':visibility,
         'articleURL':'https://m.toutiaoimg.com/i7690083921740022323/?enter_from=creator'}
    result=toutiao_records([row], 'title', '2026-09-27')[0]
    assert result['state']==expected
    assert result['remote_id']=='7690083921740022323'
    assert result['url']=='https://m.toutiaoimg.com/i7690083921740022323/'
    assert not toutiao_records([row], 'title', '2026-09-27', '999')


@pytest.mark.parametrize('url', ['https://m.toutiaoimg.com/i999/',
    'https://m.toutiaoimg.com.evil.test/i123/', 'http://m.toutiaoimg.com/i123/',
    'https://user:password@m.toutiaoimg.com/i123/'])
def test_toutiao_rejects_unrelated_or_unsafe_work_links(url):
    row={'gidStr':'123','title':'title','createTime':1790487204,'itemStatus':20,'visibilityLevel':0,'articleURL':url}
    assert toutiao_records([row], 'title', '2026-09-27')[0]['url'] is None
