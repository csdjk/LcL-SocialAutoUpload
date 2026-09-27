"""Ordinary video import and future reservation; no real platform access."""
from datetime import datetime
from io import BytesIO
import subprocess
from unittest.mock import patch

import pytest

import daily_publish as daily
from publishing import library, queue
from tests.test_daily_publish import DailyPublishTests


@pytest.fixture
def case():
    fixture = DailyPublishTests()
    fixture.setUp()
    try:
        with patch.object(queue, 'ensure_worker'):
            yield fixture
    finally:
        fixture.doCleanups()


def video_bytes(directory):
    target = directory / 'sample.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:d=1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(target)], check=True, capture_output=True)
    return target.read_bytes()


def test_import_deduplicates_video_and_schedules_future_job(case):
    content = video_bytes(case.base)
    first = library.import_video(BytesIO(content), 'sample.mp4', '普通视频', '独立于AI日报的说明', ['测试'])
    assert first['existing'] is False
    package = first['package']
    assert package['kind'] == 'imported' and 'production' not in package
    assert set(package['assets']) == {'video', 'bilibili', 'landscape', 'portrait'}
    second = library.import_video(BytesIO(content), 'again.mp4', '新标题', '新简介', [])
    assert second['existing'] is True and second['package_path'] == first['package_path']
    due = datetime(2026, 9, 23, 13, tzinfo=daily.BEIJING).isoformat()
    result = library.submit(package['edition_id'], ['bilibili'], due)
    assert len(result['jobs']) == 1 and not result['errors']
    assert queue.process_next() is False
    with daily._connection() as conn:
        row = conn.execute('SELECT available_at FROM delivery_queue').fetchone()
    assert row['available_at'] == due
    repeated = library.submit(package['edition_id'], ['bilibili'], due)
    assert not repeated['jobs'] and 'bilibili' in repeated['errors']


def test_import_rejects_non_mp4_and_invalid_web_origin(case):
    with pytest.raises(ValueError, match='MP4'):
        library.import_video(BytesIO(b'data'), 'demo.mov', '标题', '简介', [])
    from sau_backend import app
    client = app.test_client()
    response = client.post('/daily/library/submit', json={'id': 'unknown', 'platforms': ['bilibili']},
                           headers={'Origin': 'https://bad.example', 'X-SAU-Local': '1'})
    assert response.status_code == 403


def test_local_import_endpoint_streams_video_into_library(case):
    from sau_backend import app
    client = app.test_client()
    response = client.post('/daily/library/import', data={
        'video': (BytesIO(video_bytes(case.base)), 'sample.mp4'),
        'title': '导入测试', 'description': '本机普通视频', 'tags': '教程,工具'
    }, headers={'Origin': 'http://localhost', 'X-SAU-Local': '1'},
        content_type='multipart/form-data')
    assert response.status_code == 200, response.get_json()
    ident = response.json['data']['id']
    assert library.list_videos()[0]['id'] == ident
    asset = client.get(f'/daily/library/{ident}/asset/landscape')
    assert asset.status_code == 200 and asset.mimetype == 'image/png'
