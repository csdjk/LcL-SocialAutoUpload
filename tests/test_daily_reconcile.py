"""Isolated reconciliation records only. Never query a platform or publish media."""
import json
from contextlib import contextmanager
from unittest.mock import patch

import pytest
import daily_publish as daily
import sau_backend

JOB = 'reconcile-fixture'
WHEN = '2026-09-24T12:00:00+08:00'

@pytest.fixture
def record(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, 'DB_PATH', tmp_path / 'daily.db')
    with daily._connection() as conn:
        conn.execute('INSERT INTO attempts (id,edition_id,platform,account_id,package_path,revision,source,state,payload,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                     (JOB,'2026-09-24','wechat_channels','web:wechat_channels:4','fixture/package.json','r001','manual','unknown','{}',WHEN,WHEN))
    return sau_backend.app.test_client()

def body(**changes):
    value = {'platform':'wechat_channels','account_id':'web:wechat_channels:4','note':'本人已检查内容管理，确认没有本次作品。',
             'checked_at':WHEN,'remote_absent':True,'remote_id':'','url':''}
    value.update(changes)
    return {'task_id':JOB,'state':'failed','evidence':value,'expected_updated_at':WHEN}

def send(client, value): return client.post('/daily/reconcile', json=value)

def test_screenshot_empty_note_has_specific_400_and_no_write(record):
    before=daily.task(JOB)
    result=send(record,body(note=''))
    assert result.status_code==400 and '请填写核对依据' in result.json['msg']
    assert daily.task(JOB)==before

@pytest.mark.parametrize('note',['   ','\n\t',None,{},123])
def test_whitespace_or_nontext_note_is_not_evidence(record,note):
    assert send(record,body(note=note)).status_code==400
    assert daily.task(JOB)['state']=='unknown'

def test_explicit_no_work_confirmation_saves_without_id_or_link(record):
    with patch.object(daily,'_upload') as upload, patch.object(daily,'submit') as submit:
        response=send(record,body())
    assert response.status_code==200
    item=daily.task(JOB)
    assert item['state']=='failed' and item['remote_id'] is None and item['url'] is None
    assert item['evidence']['remote_absent'] is True
    assert item['evidence']['source']=='manual_reconcile'
    with daily._connection() as conn:
        persisted = conn.execute('SELECT * FROM attempts WHERE id=?', (JOB,)).fetchone()
        assert daily._retry_allowed(persisted)
    upload.assert_not_called(); submit.assert_not_called()

@pytest.mark.parametrize('change',[{'remote_absent':False},{'remote_absent':'true'},{'remote_id':'real-id'},{'url':'https://example.test/video'}])
def test_failure_requires_explicit_confirmation_and_no_contradictory_work(record,change):
    response=send(record,body(**change))
    assert response.status_code==400
    assert daily.task(JOB)['state']=='unknown'

def test_trim_user_evidence_but_do_not_accept_internal_preflight_proof(record):
    result=send(record,body(note='  已核对，无作品  ',stage='preflight',upload_started=False,failure_code='login_check_failed'))
    assert result.status_code==200
    e=daily.task(JOB)['evidence']
    assert e['note']=='已核对，无作品'
    assert 'stage' not in e and 'upload_started' not in e and 'failure_code' not in e

@pytest.mark.parametrize('state',['processing','published'])
def test_real_work_id_required_for_submitted_states(record,state):
    value=body(remote_absent=False,note='内容管理中的实际状态'); value['state']=state
    assert '作品 ID' in send(record,value).json['msg']

def test_published_needs_real_id_and_https_link(record):
    value=body(remote_absent=False,remote_id='work-1',note='内容管理显示已发布'); value['state']='published'
    assert send(record,value).status_code==400
    value['evidence']['url']='https://'
    assert send(record,value).status_code==400
    value['evidence']['url']='https://example.test/video/1'
    assert send(record,value).status_code==200
    assert daily.task(JOB)['state']=='published'
    assert send(record,body()).status_code==409  # old version cannot clobber new state
    value=body(); value.pop('expected_updated_at')
    assert '不能回退' in send(record,value).json['msg']

@pytest.mark.parametrize('change',[{'platform':'douyin'},{'account_id':'web:wechat_channels:99'},{'checked_at':'2026-09-24T12:00:00'},{'checked_at':123}])
def test_identity_and_time_are_required(record,change):
    assert send(record,body(**change)).status_code==400
    assert daily.task(JOB)['state']=='unknown'

@pytest.mark.parametrize('value',[None,[],{},'bad',{'task_id':JOB,'state':'failed','evidence':None}, {'task_id':JOB,'state':[],'evidence':{}}])
def test_bad_payload_is_json_400_not_internal_500(record,value):
    response=record.post('/daily/reconcile',data=json.dumps(value),content_type='application/json')
    assert response.status_code==400 and response.is_json
    assert daily.task(JOB)['state']=='unknown'

def test_malformed_json_is_clear_400(record):
    response=record.post('/daily/reconcile',data='{bad json',content_type='application/json')
    assert response.status_code==400 and response.is_json

def test_stale_dialog_cannot_overwrite_new_status(record):
    daily._state(JOB,'processing',evidence={'remote_id':'work-9'})
    before=daily.task(JOB)
    result=send(record,body())
    assert result.status_code==409 and '已更新' in result.json['msg']
    assert daily.task(JOB)==before

def test_cas_preserves_record_changed_between_read_and_write(record):
    original=daily._connection
    @contextmanager
    def racing():
        with original() as conn:
            conn.execute("UPDATE attempts SET state='published',updated_at='new-version',remote_id='work-8' WHERE id=?",(JOB,))
        with original() as conn: yield conn
    # task() reads through original; intercept only the write connection after its snapshot.
    snapshot=daily.task(JOB)
    with patch.object(daily,'task',return_value=snapshot), patch.object(daily,'_connection',racing):
        with pytest.raises(daily.ReconcileConflictError): daily.reconcile(JOB,'failed',body()['evidence'])
    assert daily.task(JOB)['state']=='published'

def test_existing_cli_call_without_version_still_supported(record):
    result=daily.reconcile(JOB,'failed',body()['evidence'])
    assert result['state']=='failed'


@pytest.mark.parametrize('state',['processing','published','unknown','needs_action','failed'])
def test_status_confirmation_needs_no_form_metadata(record,state):
    with patch.object(daily,'_upload') as upload, patch.object(daily,'submit') as submit:
        result=send(record,{'task_id':JOB,'state':state,'expected_updated_at':WHEN,'confirmed':True})
    assert result.status_code==200
    item=daily.task(JOB)
    assert item['state']==state
    assert item['remote_id'] is None and item['url'] is None
    assert item['evidence']['source']=='manual_confirmation'
    assert item['evidence']['account_id']=='web:wechat_channels:4'
    assert item['evidence']['remote_absent'] is (state=='failed')
    upload.assert_not_called();submit.assert_not_called()


def test_status_confirmation_preserves_original_work_information(record):
    daily._state(JOB,'processing',evidence={'remote_id':'real-original','url':'https://example.test/original'})
    before=daily.task(JOB)
    value={'task_id':JOB,'state':'published','expected_updated_at':before['updated_at'],'confirmed':True,
           'evidence':{'remote_id':'fake-replacement','url':'https://example.test/replaced'}}
    assert send(record,value).status_code==200
    item=daily.task(JOB)
    assert item['remote_id']==before['remote_id'] and item['url']==before['url']


def test_confirmation_cannot_erase_existing_work_or_override_stale_version(record):
    daily._state(JOB,'processing',evidence={'remote_id':'original-id'})
    before=daily.task(JOB)
    value={'task_id':JOB,'state':'failed','expected_updated_at':before['updated_at'],'confirmed':True}
    assert send(record,value).status_code==400
    value.update(state='published',expected_updated_at=WHEN)
    assert send(record,value).status_code==409
    assert daily.task(JOB)==before


@pytest.mark.parametrize('change',[{'confirmed':'true'},{'confirmed':False},{'expected_updated_at':None},{'state':[]}])
def test_invalid_confirmation_never_updates_record(record,change):
    value={'task_id':JOB,'state':'published','expected_updated_at':WHEN,'confirmed':True}
    value.update(change)
    assert send(record,value).status_code==400
    assert daily.task(JOB)['state']=='unknown'
