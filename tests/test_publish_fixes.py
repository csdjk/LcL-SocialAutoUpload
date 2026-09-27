from unittest.mock import patch
import json
import pytest
import daily_publish as daily
from publishing import service, bilibili_metadata
from tests.test_publisher_service import case, enqueue
from unittest.mock import MagicMock


def test_bili_no_declaration_gate(case):
    config = daily.settings()
    config['platforms']['bilibili'].pop('ai_declaration_fields', None)
    case.settings_path.write_text(json.dumps(config), encoding='utf-8')
    assert 'bilibili' in service.today(cached=False)['publishable_platforms']
    assert enqueue(case)


def test_bili_remembers_category_per_account(case):
    bilibili_metadata.remember('one', 122)
    assert bilibili_metadata.category_for('one', {}, {}) == 122
    assert bilibili_metadata.category_for('two', {}, {}) is None
    assert bilibili_metadata.category_for('one', {'category': 17}, {}) == 17


def test_exception_preserves_submission_boundary_and_receipt(case):
    ident = daily.reserve(case.path, 'bilibili', 'manual')
    def fail(job, package):
        daily._progress(ident, {'stage': 'submitting', 'submission_started': True, 'upload_started': True,
                               'remote_id': 'BV1234567890', 'url': 'https://www.bilibili.com/video/BV1234567890'})
        daily._progress(ident, {'stage': 'verifying'})
        raise RuntimeError('driver disconnected')
    with patch.object(daily, '_upload', side_effect=fail):
        result = daily.run(ident)
    assert result['state'] == 'unknown'
    assert result['evidence']['submission_started'] is True
    assert result['remote_id'] == 'BV1234567890'
    assert result['evidence']['error_type'] == 'RuntimeError'
    with pytest.raises(ValueError):
        daily.reserve(case.path, 'bilibili', 'manual')


def test_bili_ambiguous_title_never_becomes_receipt():
    from datetime import datetime
    stamp = datetime(2026, 9, 27, tzinfo=daily.BEIJING).timestamp()
    rows = [{'Archive': {'bvid': ident, 'title': 'AI日报', 'ctime': stamp, 'state': -1}}
            for ident in ('BV1a', 'BV1b')]
    with patch.object(bilibili_metadata, 'session', return_value=MagicMock()), patch.object(
        bilibili_metadata, 'get', return_value={'arc_audits': rows, 'page': {'count': 2}}):
        assert bilibili_metadata.readback(None, None, title='AI日报', day='2026-09-27')['status'] == 'unknown'
        assert bilibili_metadata.readback(None, 'BV1b')['remote_id'] == 'BV1b'


def test_bili_incomplete_list_does_not_prove_absence():
    with patch.object(bilibili_metadata, 'session', return_value=MagicMock()), patch.object(
        bilibili_metadata, 'get', return_value={'arc_audits': [], 'page': {'count': 10}}):
        assert bilibili_metadata.readback(None, None, title='AI日报', day='2026-09-27')['status'] == 'unknown'


def test_bili_proxy_sanitizing_is_subprocess_only(monkeypatch):
    import os
    from uploader.bilibili_uploader import runtime
    monkeypatch.setenv('ALL_PROXY', 'socks5://localhost:1234')
    monkeypatch.setenv('HTTPS_PROXY', 'http://localhost:5678')
    with patch.object(runtime, 'ensure_biliup_binary', return_value='biliup'), patch.object(runtime.subprocess, 'run') as run:
        runtime.run_biliup_command(['list'])
    env = run.call_args.kwargs['env']
    assert 'ALL_PROXY' not in env
    assert env['HTTPS_PROXY'] == 'http://localhost:5678'
    assert os.environ['ALL_PROXY'] == 'socks5://localhost:1234'


def test_toutiao_cover_preserves_full_image(tmp_path, monkeypatch):
    from PIL import Image
    from publishing.creator_browser import prepare_toutiao_cover
    import conf
    monkeypatch.setattr(conf, 'BASE_DIR', tmp_path)
    source = tmp_path/'approved.png'
    Image.new('RGB', (1200, 900), 'red').save(source)
    before = source.read_bytes()
    with Image.open(prepare_toutiao_cover(source)) as image:
        assert image.size == (1920, 1080)
        assert image.getpixel((960, 0))[0] > 240
        assert image.getpixel((960, 1079))[0] > 240
        assert image.getpixel((0, 540))[0] < 50
    assert source.read_bytes() == before


def test_toutiao_receipt_requires_exact_title_and_day():
    from publishing.browser_readback import toutiao_records
    from datetime import datetime
    stamp = datetime(2026, 9, 27, tzinfo=daily.BEIJING).timestamp()
    record = {'title': 'AI日报', 'gidStr': '7690083921740022323', 'groupID': 7690083921740022323,
              'itemStatus': 10, 'createTime': stamp, 'articleURL': 'https://evil.example/video'}
    found = toutiao_records({'data':[record]}, 'AI日报', '2026-09-27')
    assert found[0]['remote_id'] == '7690083921740022323'
    assert found[0]['state'] == 'processing' and found[0]['url'] is None
    assert not toutiao_records([record], 'AI日报', '2026-09-26')
    assert not toutiao_records([record], '其他视频', '2026-09-27')
    assert not toutiao_records([record], 'AI日报', '2026-09-27', 'wrong-id')


def test_success_keeps_cover_and_submit_evidence(case):
    ident = daily.reserve(case.path, 'bilibili', 'manual')
    daily._progress(ident, {'stage': 'submitting', 'submission_started': True,
                           'cover_screenshot': 'local-cover.png'})
    row = daily._record_remote_found(daily.task(ident), {'remote_id': 'BV123', 'platform_status': -1})
    assert row['evidence']['cover_screenshot'] == 'local-cover.png'
    assert row['evidence']['submission_started'] is True
    assert row['evidence']['stage'] == 'verifying'
    assert row['state'] == 'processing'


def test_stale_service_stops_before_reserving(case):
    from publishing import runtime_version
    with patch.object(runtime_version, 'source_revision', return_value='changed'):
        with pytest.raises(ValueError, match='代码已更新'):
            daily.reserve(case.path, 'bilibili', 'manual')
    assert service.list_tasks() == []


def test_douyin_absence_requires_full_list_and_no_today_posts():
    from publishing.browser_readback import douyin_complete_before
    from datetime import datetime
    stamp = datetime(2026, 9, 26, tzinfo=daily.BEIJING).timestamp()
    data = {'status_code': 0, 'has_more': False, 'total': 1,
            'aweme_list': [{'aweme_id': '123', 'create_time': stamp}]}
    assert douyin_complete_before(data, '2026-09-27')
    assert not douyin_complete_before({**data, 'has_more': True}, '2026-09-27')
    assert not douyin_complete_before({**data, 'total': 2}, '2026-09-27')
    assert not douyin_complete_before(data, '2026-09-26')


def test_douyin_receipt_drops_tracking_query_and_preserves_review_flags():
    from publishing.browser_readback import douyin_records
    record = {'aweme_id': '123', 'desc': 'AI日报 内容说明', 'create_time': 1790488200,
              'share_url': 'https://www.iesdouyin.com/share/video/123/?did=tracking&share_sign=signature',
              'status': {'self_see': True, 'is_private': False}}
    found = douyin_records([record], 'AI日报', '内容说明', '2026-09-27')[0]
    assert found['url'] == 'https://www.iesdouyin.com/share/video/123/'
    assert found['platform_status']['self_see'] is True
    assert found['state'] == 'processing'
