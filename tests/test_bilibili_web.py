import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from flask import Flask
import sau_backend
from myUtils.credential_import import make_credential_import_blueprint
from myUtils.login_session import LoginStatusQueue, sse_stream
from uploader.bilibili_uploader import web_login as login, web_publish as publish

SECRET = 'synthetic-bili-fixture'
HEADER = f'SESSDATA={SECRET}; bili_jct=fixture-csrf; DedeUserID=123'
NATIVE = {
    'cookie_info': {'cookies': [{'name':'SESSDATA','value':SECRET},{'name':'bili_jct','value':'fixture-csrf'},{'name':'DedeUserID','value':'123'}]},
    'token_info': {'access_token':'fixture-access','refresh_token':'fixture-refresh','mid':123,'expires_in':3600},
    'platform':'BiliTV', 'sso':[],
}
HEADERS = {'X-SAU-Local':'1','Origin':'http://127.0.0.1:5173'}

@pytest.fixture
def db_root(tmp_path):
    (tmp_path/'db').mkdir()
    (tmp_path/'cookiesFile').mkdir()
    (tmp_path/'videoFile').mkdir()
    with closing(sqlite3.connect(tmp_path/'db/database.db')) as c:
        c.execute('CREATE TABLE user_info (id INTEGER PRIMARY KEY, type INTEGER, filePath TEXT, userName TEXT, status INTEGER)')
        c.commit()
    return tmp_path

def rows(root):
    with closing(sqlite3.connect(root/'db/database.db')) as c:
        return c.execute('SELECT * FROM user_info ORDER BY id').fetchall()

def seed(root, *, type=5, status=1):
    (root/'cookiesFile/old.json').write_text(json.dumps(NATIVE),encoding='utf-8')
    with closing(sqlite3.connect(root/'db/database.db')) as c:
        c.execute('INSERT INTO user_info VALUES (1,?,?,?,?)',(type,'old.json','B站测试',status))
        c.commit()

def send(app, **kwargs):
    return app.test_client().post('/accounts/import-bilibili', headers=HEADERS,
        json={'name':'B站测试','credentials':HEADER, **kwargs})

@pytest.mark.parametrize('text', [HEADER, 'Cookie: '+HEADER, json.dumps(NATIVE),
    json.dumps([{'name':c['name'],'value':c['value'],'domain':'.bilibili.com'} for c in NATIVE['cookie_info']['cookies']]),
    json.dumps({'cookies':[{'name':c['name'],'value':c['value'],'domain':'.bilibili.com'} for c in NATIVE['cookie_info']['cookies']], 'origins':[]})], ids=['header','prefixed','biliup','cookie-array','storage-state'])
def test_supported_formats(text):
    result=login.normalize_bilibili_credentials(text)
    assert result['cookie_info']['cookies'][0]['value']==SECRET
    assert result['token_info']['mid']==123

@pytest.mark.parametrize('text', ['act.token', '{}', '[]', '{"cookies":[]}', 'SESSDATA=x', 'bili_jct=x',
    'Cookie: '+HEADER+'\nAuthorization: unsafe', '{invalid}', json.dumps([{'name':'SESSDATA','value':SECRET,'domain':'.evil.test'}])])
def test_invalid_formats_do_not_echo_credentials(text):
    with pytest.raises(ValueError) as error:
        login.normalize_bilibili_credentials(text)
    assert SECRET not in str(error.value)

def test_native_requires_complete_authorization():
    with pytest.raises(RuntimeError): login._native_result({'code':86039})
    with pytest.raises(RuntimeError): login._native_result({'code':0,'data':{'cookie_info':NATIVE['cookie_info']}})
    assert login._native_result({'code':0,'data':NATIVE})['token_info']['mid']==123

def test_import_registers_type5_and_keeps_secret_out_of_response(db_root):
    app=Flask(__name__); app.register_blueprint(make_credential_import_blueprint(db_root))
    with patch('myUtils.credential_import.prepare_bilibili_credentials',new=AsyncMock(return_value={'success':True})) as validate:
        response=send(app)
    assert response.status_code==200
    assert response.json['data'][1]==5 and response.json['data'][4]==1
    assert SECRET not in response.get_data(as_text=True)
    validate.assert_awaited_once()
    assert response.headers['Cache-Control']=='no-store'

@pytest.mark.parametrize('scenario',['invalid','duplicate','wrong-platform','missing','deleted-during-validation'])
def test_import_failures_preserve_existing_account(db_root,scenario):
    seed(db_root,type=3 if scenario=='wrong-platform' else 5)
    app=Flask(__name__); app.register_blueprint(make_credential_import_blueprint(db_root))
    kwargs={} if scenario=='duplicate' else {'account_id':999 if scenario=='missing' else 1}
    async def validate(_):
        if scenario=='deleted-during-validation':
            with closing(sqlite3.connect(db_root/'db/database.db')) as c:
                c.execute('DELETE FROM user_info'); c.commit()
        return {'success':scenario!='invalid'}
    with patch('myUtils.credential_import.prepare_bilibili_credentials',new=validate):
        response=send(app,**kwargs)
    assert response.status_code in (404,409,422)
    assert len(list((db_root/'cookiesFile').iterdir()))==1
    assert (db_root/'cookiesFile/old.json').read_text()==json.dumps(NATIVE)

@pytest.mark.parametrize('headers',[{}, {'X-SAU-Local':'1','Origin':'https://evil.test'}, {'X-SAU-Local':'1','Origin':'null'}])
def test_import_is_local_only(db_root,headers):
    app=Flask(__name__); app.register_blueprint(make_credential_import_blueprint(db_root))
    assert app.test_client().post('/accounts/import-bilibili',json={'name':'x','credentials':HEADER},headers=headers).status_code==403

def _driver():
    api=MagicMock(); api.dispose=AsyncMock()
    driver=MagicMock(); driver.request.new_context=AsyncMock(return_value=api)
    cm=MagicMock(); cm.__aenter__=AsyncMock(return_value=driver); cm.__aexit__=AsyncMock()
    return api,cm

@pytest.mark.parametrize('mode',['success','cancel','expired','invalid','replace','deleted'])
def test_scan_lifecycle_and_relogin_are_transactional(db_root,mode):
    if mode in ('replace','deleted'): seed(db_root,status=0)
    queue=LoginStatusQueue(); api,driver=_driver()
    async def poll(*_):
        if mode=='cancel':
            queue.cancelled.set()
        if mode=='deleted':
            with closing(sqlite3.connect(db_root/'db/database.db')) as c:
                c.execute('DELETE FROM user_info'); c.commit()
        return {'code':86038} if mode=='expired' else {'code':0,'data':NATIVE}
    with patch.object(login,'BASE_DIR',db_root), patch.object(login,'async_playwright',return_value=driver), \
        patch.object(login,'request_qrcode',new=AsyncMock(return_value={'url':'https://passport.bilibili.com/fixture','auth_code':'fixture'})), \
        patch.object(login,'_poll',new=poll), patch.object(login,'validate_bilibili_credentials',new=AsyncMock(return_value={'success':mode!='invalid','message':'校验失败'})):
        asyncio.run(login.get_bilibili_cookie('B站测试',queue,account_id=1 if mode in ('replace','deleted') else None))
    messages=list(queue.queue)
    assert any(isinstance(m,str) and m.startswith('data:image/png') for m in messages)
    if mode in ('success','replace'):
        assert messages[-1]=='200' and len(rows(db_root))==1 and rows(db_root)[0][4]==1
        if mode=='replace': assert rows(db_root)[0][0]==1 and rows(db_root)[0][2]!='old.json'
    else:
        assert '200' not in messages
        assert not rows(db_root)
        assert len(list((db_root/'cookiesFile').iterdir()))==(1 if mode=='deleted' else 0)
    api.dispose.assert_awaited_once()

def test_qr_dispatch_and_unsupported_mode():
    with patch.object(sau_backend,'get_bilibili_cookie',new=AsyncMock()) as core:
        q=LoginStatusQueue(); sau_backend.run_async_function('5','test',q)
        core.assert_awaited_once_with('test',q)
    with patch.object(sau_backend.threading,'Thread') as worker:
        assert sau_backend.app.test_client().get('/login?type=5&id=test&mode=external').status_code==400
        worker.assert_not_called()

def test_bilibili_account_check_is_registered():
    from myUtils.auth import check_cookie
    with patch.object(login,'cookie_auth',new=AsyncMock(return_value=True)) as checked:
        assert asyncio.run(check_cookie(5,'fixture.json'))
        checked.assert_awaited_once()

@pytest.fixture
def submission(db_root,monkeypatch):
    seed(db_root)
    (db_root/'videoFile/video.mp4').write_bytes(b'not-a-video-do-not-upload')
    monkeypatch.setattr(publish,'BASE_DIR',db_root)
    return {'type':5,'title':'测试标题','tid':17,'copyright':1,'tags':['游戏'],'fileList':['video.mp4'],
            'accountList':['old.json'],'description':'测试简介','isDraft':False}

@pytest.mark.parametrize('overrides',[
    {'tid':None}, {'copyright':None}, {'copyright':2}, {'tags':[]}, {'title':'x'*81},
    {'fileList':['../outside.mp4']},{'accountList':['unknown.json']},{'fileList':['video.mp4','video.mp4']},
    {'isDraft':True},{'accountList':['old.json','old.json']},
    {'enableTimer':1,'dailyTimes':['25:00']},
])
def test_submission_rejects_bad_input_before_process(submission,overrides):
    with patch.object(publish.subprocess,'run') as run:
        with pytest.raises(ValueError): publish.post_video_bilibili({**submission,**overrides})
        run.assert_not_called()

def test_submission_builds_explicit_arguments_and_handles_cli_failure(submission):
    with patch.object(publish,'cookie_auth',new=AsyncMock(return_value=True)), \
        patch.object(publish,'ensure_biliup_binary',return_value=Path('biliup.exe')), \
        patch.object(publish.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
        result=publish.post_video_bilibili({**submission,'copyright':2,'source':'https://example.org/original'})
        cmd=run.call_args.args[0]
        assert cmd[cmd.index('--copyright')+1]=='2'
        assert cmd[cmd.index('--source')+1]=='https://example.org/original'
        assert cmd[cmd.index('--tid')+1]=='17'
        assert '--submit' in cmd and 'web' in cmd
        assert 'shell' not in run.call_args.kwargs
        assert result['status']=='needs_platform_check'
        run.return_value=SimpleNamespace(returncode=1)
        with pytest.raises(RuntimeError): publish.post_video_bilibili(submission)
    assert not publish._busy_accounts

def test_schedule_uses_HH_MM_and_start_days(submission):
    _,commands=publish.prepare_submissions({**submission,'enableTimer':1,'dailyTimes':['10:35'],'startDays':2,'videosPerDay':1})
    value=commands[0][commands[0].index('--dtime')+1]
    from datetime import datetime
    date=datetime.fromtimestamp(int(value))
    assert (date.hour,date.minute)==(10,35)

def test_web_dispatches_type5_without_running_other_uploaders(submission):
    with patch.object(sau_backend,'post_video_bilibili',return_value={'message':'请核对','status':'needs_platform_check'}) as run:
        response=sau_backend.app.test_client().post('/postVideo',json=submission)
        assert response.status_code==200
        run.assert_called_once_with(submission)
    with patch.object(sau_backend,'post_video_bilibili',side_effect=RuntimeError(SECRET)):
        response=sau_backend.app.test_client().post('/postVideo',json=submission)
        assert response.status_code==500 and SECRET not in response.get_data(as_text=True)
