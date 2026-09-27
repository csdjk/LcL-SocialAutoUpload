import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from flask import Flask
from myUtils.credential_import import make_credential_import_blueprint
from utils.douyin_credentials import normalize_douyin_credentials, inspect_douyin_authenticated_page

# Synthetic fixtures only; no real account credentials are needed by these tests.
COOKIE = {'name': 'sessionid', 'value': 'synthetic-fixture-only', 'domain': '.douyin.com', 'path': '/'}
STATE = json.dumps({'cookies': [COOKIE], 'origins': []})
HEADERS = {'X-SAU-Local': '1', 'Origin': 'http://127.0.0.1:5173'}


def test_cookie_header_normalizes_without_requiring_a_new_login():
    state = normalize_douyin_credentials('Cookie: sessionid=synthetic-fixture-only; extra=a=b')
    assert len(state['cookies']) == 2
    assert state['cookies'][1]['value'] == 'a=b'
    assert state['cookies'][0]['domain'] == '.douyin.com'


def test_export_array_and_storage_state_both_work():
    assert normalize_douyin_credentials(json.dumps([COOKIE]))['cookies'][0]['name'] == 'sessionid'
    data = json.loads(STATE)
    data['origins'] = [{'origin': 'https://creator.douyin.com', 'localStorage': [{'name': 'setting', 'value': '1'}]}]
    assert normalize_douyin_credentials(json.dumps(data))['origins'] == data['origins']


def test_only_douyin_credentials_are_retained():
    unrelated = dict(COOKIE, domain='.example.com')
    data = {'cookies': [COOKIE, unrelated], 'origins': [{'origin': 'https://example.com', 'localStorage': []}]}
    state = normalize_douyin_credentials(json.dumps(data))
    assert len(state['cookies']) == 1
    assert not state['origins']


def test_export_expiration_and_same_site_are_supported():
    state = normalize_douyin_credentials(json.dumps([dict(COOKIE, expirationDate=time.time()+100, sameSite='no_restriction')]))
    assert state['cookies'][0]['sameSite'] == 'None'
    assert state['cookies'][0]['secure'] is True


@pytest.mark.parametrize('text', ['', 'act.only-a-token', '{"access_token":"synthetic-fixture-only"}', '{}', '[1]', '[]',
    'sessionid=abc\nAuthorization: invalid', 'sessionid=abc; broken', '{bad json', 'x' * (512*1024+1)], ids=['empty','token','oauth','object','bad-list','empty-list','multiline','bad-pair','bad-json','too-large'])
def test_invalid_inputs_are_rejected_without_echoing_secret(text):
    with pytest.raises(ValueError) as error:
        normalize_douyin_credentials(text)
    assert 'synthetic-fixture-only' not in str(error.value)


@pytest.mark.parametrize('changes', [{'domain': '.douyin.com.evil.test'}, {'expires': 1}, {'expires': float('nan')},
    {'name': 'not a cookie'}, {'value': 'line\nbreak'}, {'sameSite': 'unexpected'}, {'path': 'relative'}, {'secure': 'false'}])
def test_bad_cookie_attributes_fail(changes):
    with pytest.raises(ValueError):
        normalize_douyin_credentials(json.dumps([dict(COOKIE, **changes)]))


@pytest.fixture
def local_app(tmp_path):
    (tmp_path / 'db').mkdir()
    db = tmp_path / 'db' / 'database.db'
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE user_info (id INTEGER PRIMARY KEY, type INTEGER, filePath TEXT, userName TEXT, status INTEGER)')
        conn.commit()
    app = Flask(__name__)
    app.register_blueprint(make_credential_import_blueprint(tmp_path))
    return app, tmp_path, db


def send(app, **overrides):
    body = {'name': '测试抖音', 'credentials': STATE}
    body.update(overrides)
    return app.test_client().post('/accounts/import-douyin', json=body, headers=HEADERS)


def test_success_creates_account_and_keeps_credentials_out_of_reply(local_app):
    app, root, db = local_app
    with patch('myUtils.credential_import.validate_douyin_credentials', new=AsyncMock(return_value={'success': True})) as validate:
        response = send(app)
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert 'synthetic-fixture-only' not in response.get_data(as_text=True)
    row = response.json['data']
    assert row[1] == 3 and row[4] == 1
    assert (root/'cookiesFile'/row[2]).is_file()
    validate.assert_awaited_once()


def test_failed_verification_creates_no_account_or_credentials(local_app):
    app, root, db = local_app
    with patch('myUtils.credential_import.validate_douyin_credentials', new=AsyncMock(return_value={'success': False, 'message': '需额外验证'})):
        response = send(app)
    assert response.status_code == 422
    assert list((root/'cookiesFile').iterdir()) == []
    with closing(sqlite3.connect(db)) as c:
        assert c.execute('SELECT count(*) FROM user_info').fetchone()[0] == 0


def test_plain_token_fails_before_starting_browser(local_app):
    app, _, _ = local_app
    with patch('myUtils.credential_import.validate_douyin_credentials', new=AsyncMock()) as validate:
        response = send(app, credentials='act.only-a-token')
    assert response.status_code == 400
    validate.assert_not_awaited()


def test_local_origin_and_explicit_header_are_required(local_app):
    app, _, _ = local_app
    for headers in ({}, {'X-SAU-Local':'1', 'Origin':'https://evil.test'}, {'X-SAU-Local':'1', 'Origin':'null'}):
        response = app.test_client().post('/accounts/import-douyin', json={'name':'test','credentials':STATE}, headers=headers)
        assert response.status_code == 403


def test_get_does_not_accept_credentials_in_url(local_app):
    assert local_app[0].test_client().get('/accounts/import-douyin').status_code == 405


def test_failed_replacement_preserves_original_file_and_row(local_app):
    app, root, db = local_app
    (root/'cookiesFile').mkdir()
    (root/'cookiesFile'/'original.json').write_text('original', encoding='utf-8')
    with closing(sqlite3.connect(db)) as c:
        c.execute("INSERT INTO user_info VALUES (1,3,'original.json','测试抖音',1)")
        c.commit()
    with patch('myUtils.credential_import.validate_douyin_credentials', new=AsyncMock(return_value={'success':False})):
        assert send(app, account_id=1).status_code == 422
    assert (root/'cookiesFile'/'original.json').read_text() == 'original'
    assert len(list((root/'cookiesFile').iterdir())) == 1
    with closing(sqlite3.connect(db)) as c:
        assert c.execute('SELECT filePath,status FROM user_info').fetchone() == ('original.json', 1)


def test_duplicate_import_is_rejected(local_app):
    app, _, _ = local_app
    with patch('myUtils.credential_import.validate_douyin_credentials', new=AsyncMock(return_value={'success':True})):
        assert send(app).status_code == 200
        assert send(app).status_code == 409


def test_deleted_account_is_not_recreated_during_validation(local_app):
    app, _, db = local_app
    with closing(sqlite3.connect(db)) as c:
        c.execute("INSERT INTO user_info VALUES (1,3,'original.json','测试抖音',1)")
        c.commit()
    async def validate(_):
        with closing(sqlite3.connect(db)) as c:
            c.execute('DELETE FROM user_info')
            c.commit()
        return {'success':True}
    with patch('myUtils.credential_import.validate_douyin_credentials', new=validate):
        assert send(app, account_id=1).status_code == 409


def test_official_browser_survives_a_discarded_response_without_claiming_success():
    from uploader.douyin_uploader import main as douyin
    response = MagicMock(url='https://creator.douyin.com/passport/web/check_qrconnect/', status=200)
    response.json = AsyncMock(side_effect=ValueError('discarded response'))
    state = {}
    asyncio.run(douyin._observe_douyin_login_response(response, state, interactive=True))
    assert 'failure' not in state
    assert 'success' not in state


def test_real_dom_requires_positive_login_markers_and_no_verification():
    from patchright.async_api import async_playwright
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='chrome', headless=True)
            try:
                page = await browser.new_page()
                await page.route('**/*', lambda route: route.fulfill(content_type='text/html', body='<html></html>'))
                await page.goto('https://creator.douyin.com/creator-micro/home')
                assert await inspect_douyin_authenticated_page(page) == 'pending'
                await page.set_content('<button>发布视频</button>')
                assert await inspect_douyin_authenticated_page(page) == 'valid'
                await page.set_content('<button>发布视频</button><div>身份验证</div>')
                assert await inspect_douyin_authenticated_page(page) == 'verification'
                await page.set_content('<button>发布视频</button><div>扫码登录</div>')
                assert await inspect_douyin_authenticated_page(page) == 'invalid'
                await page.goto('https://creator.douyin.com.evil.test/creator-micro/home')
                await page.set_content('<button>发布视频</button>')
                assert await inspect_douyin_authenticated_page(page) == 'pending'
            finally:
                await browser.close()
    asyncio.run(scenario())
