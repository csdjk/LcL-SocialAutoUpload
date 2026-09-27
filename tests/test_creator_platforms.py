"""Isolated integration checks; never contact or publish to real platforms."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import daily_publish as daily
from publishing import automation, creator_browser as creator, service
from publishing.platforms import CORE_PLATFORMS, material_for, package_view
from tests.test_daily_publish import DailyPublishTests
from uploader.tencent_uploader.flow import SubmissionNotStarted


@pytest.fixture
def case():
    fixture = DailyPublishTests()
    fixture.setUp()
    for name in ('youtube', 'toutiao'):
        (fixture.base/'cookies'/f'{name}_test.json').write_text('{}')
    from tests.test_youtube_api import credential_fixture
    (fixture.base/'cookies/youtube_test.json').write_text(json.dumps(credential_fixture()))
    try:
        yield fixture
    finally:
        fixture.doCleanups()


def test_old_package_is_immutable_and_new_targets_reserve_once(case):
    raw = json.loads(case.path.read_text())
    raw['platforms'] = {key: raw['platforms'][key] for key in CORE_PLATFORMS}
    case.path.write_text(json.dumps(raw))
    before = case.path.read_bytes()
    package = daily.load_package(case.path)
    view = package_view(package)
    assert len(view['platforms']) == 7
    assert view['platforms']['youtube']['made_for_kids'] is False
    for name in ('youtube', 'toutiao'):
        ident = daily.reserve(case.path, name, 'manual', queued=True)
        task = daily.task(ident)
        assert task['payload']['_package_sha256'] == daily.package_digest(package)
        assert task['payload']['cover'] == 'landscape'
        with pytest.raises(ValueError):
            daily.reserve(case.path, name, 'manual', queued=True)
    assert case.path.read_bytes() == before
    assert len(package['platforms']) == 3


def test_imported_video_requires_audience_choice(case):
    package = daily.load_package(case.path)
    package['kind'] = 'imported'
    material = material_for(package, 'youtube')
    assert material['made_for_kids'] is None
    with pytest.raises(ValueError, match='面向儿童'):
        creator.validate_material('youtube', material)
    material['made_for_kids'] = False
    creator.validate_material('youtube', material)
    with pytest.raises(ValueError, match='1–30'):
        creator.validate_material('toutiao', {**material, 'title': '长'*31})


def test_old_schedule_keeps_new_platforms_disabled(case):
    config = copy.deepcopy(automation.DEFAULT)
    config['platforms'] = {key: {'enabled': True, 'verified': True} for key in CORE_PLATFORMS}
    automation.settings_path().parent.mkdir(exist_ok=True)
    automation.settings_path().write_text(json.dumps(config))
    migrated = automation.read_settings()
    assert migrated['platforms']['youtube'] == {'enabled': False, 'verified': False}
    assert migrated['platforms']['toutiao'] == {'enabled': False, 'verified': False}
    assert migrated['platforms']['bilibili']['enabled']


def test_youtube_auth_failure_stops_before_queue(case):
    from publishing import youtube_api
    with patch.object(youtube_api, 'preflight', side_effect=ValueError('授权已失效')):
        with pytest.raises(ValueError, match='授权已失效'):
            daily.reserve(case.path, 'youtube', 'manual', queued=True)
    assert not service.list_tasks()


def test_youtube_dispatch_and_official_result_reconciliation(case):
    from publishing import youtube_api
    ident = daily.reserve(case.path, 'youtube', 'manual')
    job = daily.task(ident)
    assert job['payload']['_delivery']['id'] == 'youtube_api'
    outcome = {'status': 'found', 'remote_id': 'abcdefghijk', 'url': 'https://www.youtube.com/watch?v=abcdefghijk',
               'source': 'official_api_receipt', 'warnings': []}
    with patch.object(youtube_api, 'publish', return_value=outcome) as api, patch.object(creator, 'publish') as browser:
        assert daily._upload(job, daily.load_package(case.path)) == outcome
    api.assert_called_once(); browser.assert_not_called()
    daily._record_remote_found(job, outcome)
    result = {'status':'found','state':'needs_action','remote_id':'abcdefghijk','privacy':'private','processing':'succeeded',
              'note':'视频为私享，尚未公开。','checked_at':'2026-09-23T13:00:00+08:00'}
    with patch.object(youtube_api, 'readback', return_value=result), patch.object(youtube_api, 'publish') as upload:
        row = service.sync_result(ident)['task']
    upload.assert_not_called()
    assert row['state'] == 'needs_action' and row['remote_id'] == 'abcdefghijk'
    assert row['evidence']['source'] == 'official_api_query'
    assert row['evidence']['privacy'] == 'private'


def test_youtube_accepted_id_survives_later_error(case):
    ident = daily.reserve(case.path, 'youtube', 'manual')
    def failing_upload(job, package):
        daily._progress(job['id'], {'remote_id':'abcdefghijk','url':'https://www.youtube.com/watch?v=abcdefghijk',
                                   'warnings':['封面需要核对']})
        raise RuntimeError('interrupted after receipt')
    with patch.object(daily, '_upload', side_effect=failing_upload):
        row = daily.run(ident)
    assert row['state'] == 'unknown' and row['remote_id'] == 'abcdefghijk'
    assert row['evidence']['warnings'] == ['封面需要核对']
    from publishing import youtube_api
    result = {'status':'found','state':'published','remote_id':'abcdefghijk','privacy':'public','processing':'succeeded',
              'note':'视频公开','checked_at':'2026-09-23T13:00:00+08:00'}
    with patch.object(youtube_api, 'readback', return_value=result):
        checked = service.sync_result(ident)['task']
    assert checked['state'] == 'needs_action'
    assert checked['evidence']['warnings'] == ['封面需要核对']


@pytest.mark.parametrize('platform,url,ident', [
    ('youtube','https://youtu.be/abcdefghijk','abcdefghijk'),
    ('toutiao','https://www.toutiao.com/video/123456789/','123456789')])
def test_receipt_is_scoped_and_preserves_url(case, platform, url, ident):
    result = creator.receipt_from_links(platform, [url])
    assert result['remote_id'] == ident
    assert creator.receipt_from_links(platform, ['https://evil.example/watch?v=abcdefghijk'])['status'] == 'unknown'
    task_id = daily.reserve(case.path, platform, 'manual')
    updated = daily._record_remote_found(daily.task(task_id), result)
    assert updated['state'] == 'processing'
    assert updated['url'] == url


@pytest.mark.parametrize('failure', ['cover', 'upload', 'receipt', None])
def test_submission_boundary_and_single_final_click(case, failure):
    async def run():
        page = MagicMock()
        page.url = 'https://studio.youtube.com/channel/UCfixture'
        page.goto = AsyncMock()
        browser = MagicMock()
        browser.close = AsyncMock()
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        browser.new_context = AsyncMock(return_value=context)
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=playwright)
        manager.__aexit__ = AsyncMock(return_value=False)
        control = MagicMock()
        control.set_input_files = AsyncMock()
        control.is_enabled = AsyncMock(return_value=True)
        control.get_attribute = AsyncMock(return_value=None)
        control.click = AsyncMock()
        material = material_for(daily.load_package(case.path), 'youtube')
        (case.base/'cookies/youtube_test.json').write_text(json.dumps({'publisher_channel_id': 'UCfixture'}))
        with patch.object(creator, 'async_playwright', return_value=manager), \
             patch.object(creator, 'authenticated', AsyncMock(return_value=True)), \
             patch.object(creator, 'unique', AsyncMock(return_value=control)), \
             patch.object(creator, 'click', AsyncMock()), \
             patch.object(creator, 'youtube_fields', AsyncMock(side_effect=ValueError('封面失败') if failure=='cover' else None)), \
             patch.object(creator, 'wait_upload', AsyncMock(side_effect=ValueError('上传未完成') if failure=='upload' else None)), \
             patch.object(creator, 'submission_receipt', AsyncMock(side_effect=ValueError('回执缺失') if failure=='receipt' else None, return_value={'status':'unknown'})):
            args = ('youtube', case.path.parent/'video.bin', case.path.parent/'landscape.bin', material, case.base/'cookies/youtube_test.json')
            if failure:
                with pytest.raises(SubmissionNotStarted if failure != 'receipt' else RuntimeError):
                    await creator.publish(*args)
            else:
                assert await creator.publish(*args) == {'status':'unknown'}
        assert control.click.await_count == (0 if failure in ('cover','upload') else 1)
        browser.close.assert_awaited_once()
    asyncio.run(run())
