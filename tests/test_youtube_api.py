"""Fake Google endpoints; real loopback OAuth callback; never publish remotely."""
import base64
import hashlib
import json
import sqlite3
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from unittest.mock import MagicMock, patch

import pytest
import requests
from flask import Flask

from myUtils.login_session import LoginStatusQueue
from myUtils.youtube_oauth import make_youtube_blueprint
from publishing import youtube_api as yt

CLIENT = {'installed': {'client_id': 'test.apps.googleusercontent.com', 'client_secret': 'fake-secret'}}
CHANNEL = {'items': [{'id': 'UCfixture', 'snippet': {'title': '测试频道'}}]}
MATERIAL = {'title': '测试视频', 'description': '内容说明', 'tags': ['AI'], 'visibility': 'public',
            'made_for_kids': False, 'cover_mode': 'custom', 'ai_declaration': 'AI生成'}


def credential_fixture():
    return CLIENT['installed'] | {'kind': 'youtube_oauth', 'access_token': 'fake-access',
        'refresh_token': 'fake-refresh', 'expires_at': time.time() + 3600,
        'channel_id': 'UCfixture', 'scopes': yt.SCOPES, 'account_name': '测试频道'}


def test_preflight_rejects_bad_cover_before_google_request(tmp_path):
    video, cover = tmp_path/'video.mp4', tmp_path/'cover.jpg'
    video.write_bytes(b'video'); cover.write_bytes(b'not an image')
    with patch.object(yt, 'credentials') as credentials:
        with pytest.raises(yt.YouTubeError, match='PNG 或 JPEG'):
            yt.preflight(video, cover, MATERIAL, tmp_path/'account.json')
    credentials.assert_not_called()


def test_preflight_reads_channel_without_creating_video(tmp_path):
    video, cover = tmp_path/'video.mp4', tmp_path/'cover.png'
    video.write_bytes(b'video'); cover.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    with patch.object(yt, 'credentials', return_value=credential_fixture()), patch.object(yt, 'request', return_value=response(data=CHANNEL)) as request:
        result = yt.preflight(video, cover, MATERIAL, tmp_path/'account.json')
    assert result[2] == 'image/png'
    assert len(request.call_args_list) == 1
    assert request.call_args.args[1] == 'GET'


def response(code=200, data=None, headers=None):
    result = MagicMock(status_code=code, headers=headers or {})
    result.json.return_value = data or {}
    return result


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'db').mkdir()
    with sqlite3.connect(tmp_path / 'db/database.db') as conn:
        conn.execute('CREATE TABLE user_info(id INTEGER PRIMARY KEY,type INTEGER,filePath TEXT,userName TEXT,status INTEGER)')
    yt.atomic_json(yt.config_path(tmp_path), CLIENT)
    return tmp_path


def oauth_roundtrip(root, *, bad_state=False, deny=False, queue=None, account_id=None, channel=CHANNEL):
    queue = queue or LoginStatusQueue()
    opened, calls, callback_status = [], [], []
    threads = []
    def open_browser(url, **kwargs):
        query = parse_qs(urlsplit(url).query)
        opened.append(query)
        def call():
            args = {'state': 'wrong' if bad_state else query['state'][0]}
            args.update({'error': 'access_denied'} if deny else {'code': 'fake-code'})
            try:
                with build_opener(ProxyHandler({})).open(query['redirect_uri'][0] + '?' + urlencode(args), timeout=3) as reply:
                    callback_status.append(reply.status)
            except HTTPError as exc:
                callback_status.append(exc.code)
                queue.cancelled.set()
        worker = threading.Thread(target=call)
        threads.append(worker); worker.start()
        return True
    def request(session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url == yt.TOKEN_URL:
            payload = kwargs['data']
            challenge = base64.urlsafe_b64encode(hashlib.sha256(payload['code_verifier'].encode()).digest()).rstrip(b'=').decode()
            assert challenge == opened[0]['code_challenge'][0]
            assert payload['redirect_uri'] == opened[0]['redirect_uri'][0]
            return response(data={'access_token': 'fake-access', 'refresh_token': 'fake-refresh',
                                  'expires_in': 3600, 'scope': ' '.join(yt.SCOPES)})
        return response(data=channel)
    try:
        with patch.object(yt.webbrowser, 'open', side_effect=open_browser), patch.object(yt, 'request', side_effect=request):
            yt.authorize(root, queue, account_id, timeout=2)
    finally:
        for thread in threads: thread.join(4)
    return queue, opened, calls, callback_status


def test_desktop_oauth_pkce_channel_name_and_no_secrets_in_events(root):
    queue, opened, calls, status = oauth_roundtrip(root)
    assert status == [200]
    assert opened[0]['code_challenge_method'] == ['S256']
    assert opened[0]['access_type'] == ['offline']
    assert calls[0][1] == yt.TOKEN_URL
    with sqlite3.connect(root / 'db/database.db') as conn:
        row = conn.execute('SELECT userName,filePath FROM user_info').fetchone()
    assert row[0] == '测试频道'
    assert yt.read_credentials(root / 'cookiesFile' / row[1])['channel_id'] == 'UCfixture'
    events = list(queue.queue)
    assert events[-1] == '200'
    assert 'fake-' not in json.dumps(events)


def test_invalid_state_and_cancel_do_not_exchange_or_save(root):
    queue, _, calls, status = oauth_roundtrip(root, bad_state=True)
    assert status == [400] and calls == []
    assert not list((root / 'cookiesFile').glob('*.json'))


def test_denial_and_existing_channel_protection(root):
    with pytest.raises(yt.YouTubeError, match='取消'):
        oauth_roundtrip(root, deny=True)
    oauth_roundtrip(root)
    with pytest.raises(yt.YouTubeError, match='已添加'):
        oauth_roundtrip(root)
    with pytest.raises(yt.YouTubeError, match='原账号不同'):
        oauth_roundtrip(root, account_id=1, channel={'items': [{'id': 'UCother', 'snippet': {'title': '另一频道'}}]})
    with sqlite3.connect(root / 'db/database.db') as conn:
        assert conn.execute('SELECT count(*) FROM user_info').fetchone()[0] == 1
    assert len(list((root / 'cookiesFile').glob('*.json'))) == 1


def test_configuration_local_only_and_response_hides_secrets(root):
    app = Flask(__name__); app.register_blueprint(make_youtube_blueprint(root)); client = app.test_client()
    url = '/accounts/youtube-oauth/config'
    assert client.get(url).status_code == 403
    headers = {'X-SAU-Local': '1'}
    assert client.get(url, headers=headers | {'Origin': 'https://evil.example'}).status_code == 403
    assert client.post(url, headers=headers, json={'web': CLIENT['installed']}).status_code == 400
    reply = client.post(url, headers=headers, json=CLIENT)
    assert reply.json['data'] == {'configured': True}
    assert 'fake-secret' not in reply.text
    assert client.post(url, headers=headers, data='x' * 65537).status_code == 413


def test_youtube_login_dispatch_never_launches_automated_browser():
    import sau_backend
    from unittest.mock import AsyncMock
    queue = LoginStatusQueue(); queue.account_id = 8
    async def oauth(name, queue, **kwargs): queue.put('200')
    with patch.object(sau_backend, 'get_youtube_oauth', AsyncMock(side_effect=oauth)) as google, \
         patch.object(sau_backend, 'get_browser_cookie', AsyncMock()) as browser:
        sau_backend.run_async_function('6', '', queue, 'browser')
    google.assert_awaited_once_with('', queue, account_id=8)
    browser.assert_not_awaited()


def test_ambiguous_upload_does_not_start_second_insert(media):
    video, cover, path = media
    replies = [response(data=CHANNEL), response(headers={'Location': yt.UPLOAD + 'videos?upload_id=fake'}),
               requests.ConnectionError('secret'), requests.ConnectionError('secret')]
    with patch.object(requests.Session, 'request', side_effect=replies) as req:
        with pytest.raises(yt.YouTubeError, match='不会自动重发') as error:
            yt.publish(video, cover, MATERIAL, path, lambda _: None)
    assert 'secret' not in str(error.value)
    assert sum(c.args[:2] == ('POST', yt.UPLOAD + 'videos') for c in req.call_args_list) == 1


def test_refresh_is_saved_and_scopes_rechecked(root):
    path = root / 'cookiesFile/test.json'
    old = credential_fixture(); old['expires_at'] = 0; yt.atomic_json(path, old)
    with patch.object(yt, 'request', return_value=response(data={'access_token': 'renewed', 'expires_in': 3600})) as req:
        assert yt.credentials(path, MagicMock())['access_token'] == 'renewed'
        assert req.call_args.kwargs['data']['grant_type'] == 'refresh_token'
    assert yt.read_credentials(path)['access_token'] == 'renewed'
    yt.atomic_json(path, old)
    with patch.object(yt, 'request', return_value=response(data={'access_token': 'bad', 'expires_in': 3600, 'scope': yt.SCOPES[0]})):
        with pytest.raises(yt.YouTubeError, match='同时允许'):
            yt.credentials(path, MagicMock())
    assert yt.read_credentials(path)['access_token'] == old['access_token']


@pytest.fixture
def media(root):
    video = root / 'video.mp4'; video.write_bytes(b'x' * 12)
    cover = root / 'cover.png'; cover.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    path = root / 'cookiesFile/test.json'; yt.atomic_json(path, credential_fixture())
    return video, cover, path


@pytest.mark.parametrize('cover_failure', [False, True])
def test_upload_recovers_same_session_and_keeps_id_on_cover_failure(media, cover_failure):
    video, cover, path = media
    session = 'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=secret'
    replies = [response(data=CHANNEL), response(headers={'Location': session}),
               response(308, headers={'Range': 'bytes=0-7'}), requests.ConnectionError('contains secret URL'),
               response(data={'id': 'abcdefghijk'}), response(403) if cover_failure else response(data={'items': [{}]})]
    events = []
    with patch.object(yt, 'CHUNK', 8), patch.object(requests.Session, 'request', side_effect=replies) as req:
        receipt = yt.publish(video, cover, MATERIAL, path, events.append)
    assert receipt['remote_id'] == 'abcdefghijk'
    assert bool(receipt['warnings']) is cover_failure
    calls = req.call_args_list
    assert sum(c.args[:2] == ('POST', yt.UPLOAD + 'videos') for c in calls) == 1
    assert calls[4].kwargs['headers']['Content-Range'] == 'bytes */12'
    assert calls[1].kwargs['json']['status']['containsSyntheticMedia'] is True
    assert calls[-1].kwargs['params']['videoId'] == receipt['remote_id']
    assert events[-1]['remote_id'] == receipt['remote_id']
    assert 'secret' not in json.dumps(events)


def test_upload_rejects_foreign_session_before_sending_token(media):
    video, cover, path = media
    with patch.object(yt, 'request', side_effect=[response(data=CHANNEL), response(headers={'Location': 'https://evil.example/upload'})]) as req:
        with pytest.raises(yt.YouTubeError, match='可信上传会话'):
            yt.publish(video, cover, MATERIAL, path, lambda _: None)
    assert req.call_count == 2


@pytest.mark.parametrize('privacy,processing,expected', [('public','succeeded','published'), ('private','succeeded','needs_action'),
    ('unlisted','succeeded','needs_action'), ('public','processing','processing'), ('public','failed','needs_action')])
def test_readback_distinguishes_private_processing_and_public(media, privacy, processing, expected):
    data = {'items': [{'id': 'abcdefghijk', 'snippet': {'channelId': 'UCfixture'},
                      'status': {'privacyStatus': privacy}, 'processingDetails': {'processingStatus': processing}}]}
    with patch.object(yt, 'request', return_value=response(data=data)):
        assert yt.readback('abcdefghijk', media[2])['state'] == expected
    data['items'][0]['snippet']['channelId'] = 'UCother'
    with patch.object(yt, 'request', return_value=response(data=data)):
        assert yt.readback('abcdefghijk', media[2])['status'] == 'unavailable'
