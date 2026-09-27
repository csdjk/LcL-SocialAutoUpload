"""Synthetic local fixtures only: no login, media transfer or real settings changes."""
import json
import sqlite3
from contextlib import closing
from unittest.mock import patch

import pytest
import daily_publish as daily
from tests.test_daily_channels_access import case, status, KEY, configure

HEADERS={'Origin':'http://127.0.0.1:5173','X-SAU-Local':'1'}


def add_new(case, ident=8, platform=2, saved_status=1):
    with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
        conn.execute('INSERT INTO user_info VALUES (?,?,?, ?,?)',(ident,platform,'web.json','新视频号',saved_status))
        conn.commit()


def preflight(case):
    ident=daily.reserve(case.path,KEY,'manual')
    daily._state(ident,'failed',error='旧登录校验失败',evidence={
        'stage':'preflight','upload_started':False,'remote_absent':True,
        'failure_code':'login_check_failed','next_action':'relogin'})
    return ident


def choose(ident):
    return daily.bind_channels_account(ident,daily.channels_binding_options()['revision'])


def test_deleted_binding_is_not_the_new_accounts_login_failure(case):
    old=preflight(case); add_new(case)
    with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
        conn.execute('DELETE FROM user_info WHERE id=7'); conn.commit()
    before=case.settings_path.read_bytes()
    item=status(case)
    assert item['access_status']=='binding_missing'
    assert item['account']['web_account_id']==7
    assert item['task_id']==old
    options=daily.channels_binding_options()
    assert [r['id'] for r in options['accounts']]==[8]
    assert options['accounts'][0]['selectable'] is True
    assert 'web.json' not in json.dumps(options)
    assert case.settings_path.read_bytes()==before  # Reading never silently switches accounts.


def test_explicit_binding_preserves_other_settings_tasks_and_drafts(case):
    old=preflight(case); add_new(case)
    config=json.loads(case.settings_path.read_text(encoding='utf-8'))
    payload=daily.load_package(case.path)['platforms'][KEY]
    saved_draft=daily.save_draft(case.path,KEY,'web:wechat_channels:7',payload)
    original=daily.task(old)
    chosen=choose(8)
    updated=json.loads(case.settings_path.read_text(encoding='utf-8'))
    assert chosen['account']['web_account_id']==8
    assert chosen['account']['display_name']=='新视频号'
    assert chosen['account']['account_id']=='web:wechat_channels:8'
    assert updated['platforms']==config['platforms']
    assert updated['accounts']['douyin']==config['accounts']['douyin']
    assert updated['accounts']['bilibili']==config['accounts']['bilibili']
    assert daily.task(old)==original
    assert daily.get_draft(case.path,KEY,'web:wechat_channels:7')==saved_draft
    assert daily.get_draft(case.path,KEY,'web:wechat_channels:8') is None


def test_only_proven_unuploaded_old_account_failure_allows_new_manual_attempt(case):
    old=preflight(case); add_new(case); choose(8)
    item=status(case)
    assert item['access_status']=='ready'
    assert not item['account_mismatch']
    assert item['retry_allowed'] is True
    assert item['task_account_id']=='web:wechat_channels:7'
    assert item['task_id']==old
    with patch.object(daily,'_upload') as uploader:
        new=daily.reserve(case.path,KEY,'manual')
        uploader.assert_not_called()
    assert new!=old and daily.task(new)['account_id']=='web:wechat_channels:8'
    assert daily.task(old)['account_id']=='web:wechat_channels:7'
    with pytest.raises(ValueError,match='已预约或提交'): daily.reserve(case.path,KEY,'manual')


@pytest.mark.parametrize('state,proof,remote',[
    ('unknown',None,None),('published',None,'real-remote-id'),('processing',None,'real-remote-id'),
    ('needs_action',None,None),('failed',{'remote_absent':True},None),
    ('failed',{'stage':'preflight','upload_started':True,'remote_absent':True},None),
    ('failed',{'stage':'preflight','upload_started':False,'remote_absent':True},'real-remote-id'),
])
def test_account_switch_does_not_bypass_uncertain_or_submitted_history(case,state,proof,remote):
    old=daily.reserve(case.path,KEY,'manual')
    data=dict(proof or {})
    if remote: data['remote_id']=remote
    daily._state(old,state,evidence=data)
    add_new(case); choose(8)
    assert status(case)['account_mismatch']
    with pytest.raises(ValueError,match='其他账号'): daily.reserve(case.path,KEY,'manual')


def test_all_other_records_checked_not_only_first_safe_failure(case):
    old=preflight(case)
    with daily._connection() as conn:
        values=list(conn.execute('SELECT * FROM attempts WHERE id=?',(old,)).fetchone())
        values[0]='second-unknown'; values[7]='unknown'; values[11]=None
        conn.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',values)
    add_new(case); choose(8)
    assert status(case)['account_mismatch']
    with pytest.raises(ValueError,match='其他账号'): daily.reserve(case.path,KEY,'manual')


def test_active_upload_prevents_account_switch(case):
    daily.reserve(case.path,KEY,'manual'); add_new(case)
    before=case.settings_path.read_bytes()
    with pytest.raises(ValueError,match='正在执行'): choose(8)
    assert case.settings_path.read_bytes()==before


def test_legacy_cross_account_history_still_blocks(case):
    old=preflight(case)
    with daily._connection() as conn:
        conn.execute('INSERT INTO legacy VALUES(?,?,?,?,?,?)',
            ('2026-09-23',KEY,'older-account','failed',None,json.dumps({'remote_absent':True})))
    add_new(case); choose(8)
    assert status(case)['account_mismatch']
    with pytest.raises(ValueError,match='其他账号'): daily.reserve(case.path,KEY,'manual')
    assert daily.task(old)['state']=='failed'


def test_stale_dialog_cannot_overwrite_newer_config(case):
    options=daily.channels_binding_options(); add_new(case)
    configure(case,lambda cfg: cfg['platforms']['douyin'].update(custom='preserve-new-setting'))
    updated=case.settings_path.read_bytes()
    with pytest.raises(ValueError,match='已变化'):
        daily.bind_channels_account(8,options['revision'])
    assert case.settings_path.read_bytes()==updated


@pytest.mark.parametrize('ident',[True,0,-1,'../8','abc',999])
def test_invalid_account_choice_never_writes(case,ident):
    before=case.settings_path.read_bytes()
    with pytest.raises(ValueError): choose(ident)
    assert case.settings_path.read_bytes()==before


@pytest.mark.parametrize('broken',['wrong_platform','expired','missing_file','escape'])
def test_bad_candidate_rejected(case,broken):
    add_new(case,platform=3 if broken=='wrong_platform' else 2,saved_status=0 if broken=='expired' else 1)
    if broken=='missing_file': (case.base/'cookiesFile/web.json').unlink()
    if broken=='escape':
        (case.base/'outside.json').write_text('{}')
        with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
            conn.execute("UPDATE user_info SET filePath='../outside.json' WHERE id=8"); conn.commit()
    before=case.settings_path.read_bytes()
    with pytest.raises(ValueError): choose(8)
    assert case.settings_path.read_bytes()==before


def test_new_record_does_not_inherit_previous_remote_identity(case):
    configure(case,lambda cfg: cfg['accounts'][KEY].update(account_id='remote-old',alias='old_alias'))
    add_new(case); result=choose(8)
    assert result['account']['account_id']=='web:wechat_channels:8'
    assert 'alias' not in result['account']


def test_explicit_disabled_platform_stays_disabled_after_binding(case):
    configure(case,lambda cfg: cfg['platforms'][KEY].update(enabled=False))
    add_new(case)
    assert choose(8)['access_status']=='disabled'
    with pytest.raises(ValueError,match='关闭'): daily.reserve(case.path,KEY,'manual')


def test_http_binding_local_confirmation_and_invalid_origins(case):
    import sau_backend
    add_new(case); client=sau_backend.app.test_client()
    options=client.get('/daily/channels-account').json['data']
    body={'web_account_id':8,'revision':options['revision']}
    before=case.settings_path.read_bytes()
    for headers in ({},{'X-SAU-Local':'1','Origin':'https://evil.example'},
                    {'X-SAU-Local':'1','Origin':'http://localhost:5173.evil/'},
                    {'X-SAU-Local':'1','Origin':'null'}):
        assert client.post('/daily/channels-account',json=body,headers=headers).status_code==403
    assert case.settings_path.read_bytes()==before
    with patch.object(daily,'_upload') as uploader:
        result=client.post('/daily/channels-account',json=body,headers=HEADERS)
        uploader.assert_not_called()
    assert result.status_code==200
    assert result.json['data']['account']['web_account_id']==8
    assert '未上传' in result.json['msg']
    assert client.post('/daily/channels-account',json=body,headers=HEADERS).status_code==409


def test_same_record_relogin_retains_task_and_allows_manual_retry(case):
    old=preflight(case)
    with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
        conn.execute('UPDATE user_info SET status=0 WHERE id=7'); conn.commit()
    assert status(case)['access_status']=='needs_login'
    with closing(sqlite3.connect(case.base/'db/database.db')) as conn:
        conn.execute('UPDATE user_info SET status=1 WHERE id=7'); conn.commit()
    current=status(case)
    assert current['access_status']=='ready' and current['retry_allowed']
    assert current['task_id']==old and current['error']=='旧登录校验失败'
