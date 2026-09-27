import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import daily_publish as daily
from publishing import automation, kuaishou_browser
from publishing.platforms import CORE_PLATFORMS, material_for, package_view
from tests.test_daily_publish import DailyPublishTests
from uploader.ks_uploader.main import KSVideo
from uploader.ks_uploader.main import normalize_topics


@pytest.fixture
def case():
    value = DailyPublishTests()
    value.setUp()
    (value.base / 'cookies/kuaishou_test.json').write_text('{}')
    try:
        yield value
    finally:
        value.doCleanups()


def test_old_package_kuaishou_and_duplicate_protection(case):
    raw = json.loads(case.path.read_text())
    raw['platforms'] = {key: raw['platforms'][key] for key in CORE_PLATFORMS}
    case.path.write_text(json.dumps(raw))
    before = case.path.read_bytes()
    package = daily.load_package(case.path)
    assert package_view(package)['platforms']['kuaishou']['cover'] == 'portrait'
    job = daily.reserve(case.path, 'kuaishou', 'manual', queued=True)
    assert daily.task(job)['payload']['_delivery']['id'] == 'browser'
    with pytest.raises(ValueError, match='已预约或提交'):
        daily.reserve(case.path, 'kuaishou', 'manual', queued=True)
    assert before == case.path.read_bytes()
    old = json.loads(json.dumps(automation.DEFAULT))
    del old['platforms']['kuaishou']
    automation.settings_path().write_text(json.dumps(old))
    assert automation.read_settings()['platforms']['kuaishou'] == {'enabled': False, 'verified': False}


def test_daily_dispatch_uses_bound_cookie_and_cover(case):
    package = daily.load_package(case.path)
    job = daily.reserve(case.path, 'kuaishou', 'manual')
    with patch.object(kuaishou_browser, 'publish', AsyncMock(return_value={'status': 'unknown'})) as publish:
        daily.run(job)
    args = publish.call_args.args
    assert args[1] == (case.path.parent / package['assets'][material_for(package, 'kuaishou')['cover']]['path']).resolve()
    assert args[3] == case.base / 'cookies/kuaishou_test.json'
    assert daily.task(job)['state'] == 'unknown'
    with pytest.raises(ValueError):
        daily.reserve(case.path, 'kuaishou', 'manual')


def test_bridge_single_submit_and_declaration(case):
    async def run():
        uploader = MagicMock(main=AsyncMock())
        with patch.object(kuaishou_browser, 'cookie_auth', AsyncMock(return_value=True)), patch.object(kuaishou_browser, 'KSVideo', return_value=uploader) as factory, patch('publishing.browser_readback.readback', AsyncMock(return_value={'status': 'unknown'})):
            result = await kuaishou_browser.publish('video', 'cover', {'title': '标题', 'description': '简介', 'ai_declaration': 'AI生成'}, 'cookie', lambda _: None)
        assert factory.call_args.kwargs['single_submission'] is True
        assert factory.call_args.kwargs['desc'] == '简介\nAI生成'
        assert result['status'] == 'unknown'
    asyncio.run(run())


def test_submit_timeout_does_not_click_publish_twice():
    async def run():
        app = KSVideo('title', 'video', [], 0, 'cookie', single_submission=True)
        button = MagicMock(click=AsyncMock())
        page = MagicMock(get_by_text=MagicMock(return_value=button))
        with patch('uploader.ks_uploader.main.KUAISHOU_SUBMIT_TIMEOUT_SECONDS', 0), patch('uploader.ks_uploader.main._dump_page_debug', AsyncMock(return_value='diagnostics')):
            with pytest.raises(RuntimeError, match='结果待核对'):
                await app.submit_once(page)
        button.click.assert_awaited_once()
    asyncio.run(run())


def test_old_description_topics_are_only_added_once():
    description, tags = normalize_topics('本期看点。\n#AI日报 #人工智能 #游戏AI\n内容由AI生成',
                                        ['AI日报', '人工智能', '游戏AI'])
    assert description == '本期看点。\n\n内容由AI生成'
    assert tags == ['AI日报', '人工智能', '游戏AI']
    app = KSVideo('title', 'video', tags, 0, 'cookie', desc=description)
    assert '#' not in app.desc
    assert app.tags == tags
    assert normalize_topics('C#编程与F#语言。 #AI', ['AI']) == ('C#编程与F#语言。', ['AI'])


def test_extra_inline_topics_stop_before_upload():
    async def run():
        with patch.object(kuaishou_browser, 'cookie_auth', AsyncMock()) as auth:
            with pytest.raises(daily.UploadNotStartedError, match='最多使用 3 个话题'):
                await kuaishou_browser.publish('video', 'cover', {'description': '#额外话题',
                    'tags': ['AI', '游戏', '新闻']}, 'cookie', lambda _: None)
            auth.assert_not_called()
    asyncio.run(run())


@pytest.mark.parametrize('failure', [False, OSError('network unavailable')])
def test_login_failure_stops_before_uploader(failure):
    async def run():
        auth = AsyncMock(side_effect=failure) if isinstance(failure, Exception) else AsyncMock(return_value=False)
        with patch.object(kuaishou_browser, 'cookie_auth', auth), patch.object(kuaishou_browser, 'KSVideo') as uploader:
            with pytest.raises(daily.UploadNotStartedError):
                await kuaishou_browser.publish('video', 'cover', {'description': ''}, 'cookie', lambda _: None)
        uploader.assert_not_called()
    asyncio.run(run())
