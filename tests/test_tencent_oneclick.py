"""Fixtures only. No real credential, upload, publish or user's task is changed."""
import asyncio
import copy
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from uploader.tencent_uploader import readback, workflow
from uploader.tencent_uploader.flow import SubmissionNotStarted, PublicationProgress
from tests.test_daily_channels_access import case, KEY, status
import daily_publish as daily
from myUtils import daily_oneclick as one
from publishing import queue

DESC='这是用于隔离测试的足够长且明确的每日新闻简介，包含唯一的测试内容，并非真实新闻，也不会上传到任何平台。'
DAY='2026-09-23'
STAMP=int(datetime(2026,9,23,16,tzinfo=daily.BEIJING).timestamp())


def response(rows=None,more=0,total=None):
    rows=rows if rows is not None else [{'objectId':'test-id','createTime':STAMP,'desc':{'description':DESC+' #测试'},'status':1,'visibleType':1}]
    return {'data':{'list':rows,'continueFlag':more,'totalCount':len(rows) if total is None else total}}


@pytest.mark.parametrize('driver',['playwright','patchright'])
def test_real_browser_generated_cover_and_embedded_editor(driver):
    import html
    import importlib
    from urllib.parse import quote
    from uploader.tencent_uploader.main import TencentVideo
    async def scenario():
        factory=importlib.import_module(driver+'.async_api').async_playwright
        async with factory() as p:
            browser=await p.chromium.launch(channel='msedge',headless=True)
            try:
                page=await browser.new_page()
                svg='data:image/svg+xml,'+quote('<svg xmlns="http://www.w3.org/2000/svg" width="128" height="96"><rect width="128" height="96" fill="gray"/></svg>')
                await page.set_content('<div class="vertical-cover-wrap"><img src="'+svg+'"></div>')
                assert await workflow.visible_generated_cover(page,timeout=2)
                await page.set_content('<div class="vertical-cover-wrap">封面生成中</div>')
                assert not await workflow.visible_generated_cover(page,timeout=.1)
                frame_html='''<div class="horizontal-cover-wrap"><div class="edit-btn" onclick="document.querySelector('.weui-desktop-dialog').style.display='block'">编辑</div></div><div class="weui-desktop-dialog" style="display:none"><h2>编辑封面</h2><button>本地上传</button></div>'''
                await page.set_content('<div class="weui-desktop-dialog" style="display:none">编辑封面<input type="file"></div><iframe width="800" height="500" srcdoc="'+html.escape(frame_html,quote=True)+'"></iframe>')
                app=TencentVideo('fixture','unused.mp4',[],0,'unused.json')
                editor=await app.open_thumbnail_dialog(page,['.horizontal-cover-wrap'],['编辑封面'])
                assert await editor.is_visible()
                assert await editor.locator('input[type=file]').count()==0  # lazy-created only after local-upload tab
            finally:await browser.close()
    asyncio.run(scenario())


def test_worker_start_failure_retains_durable_queue(case):
    payload=dict(daily.load_package(case.path)['platforms'][KEY],cover_mode='video_frame')
    with patch.object(queue,'ensure_worker',side_effect=OSError('fixture startup failure')):
        result = one.submit_one(case.path,KEY,'web:wechat_channels:7',payload)
        assert '未启动' in result['warning']
    item=status(case)
    assert item['state']=='queued' and not item['retry_allowed']


def test_readback_matches_account_list_exact_description_day_and_real_id():
    match=readback.match_posts(response(),'测试',DESC,DAY)
    assert match['status']=='found' and match['remote_id']=='test-id'

@pytest.mark.parametrize('change',[{'objectId':''},{'createTime':STAMP-86400},{'desc':{'description':DESC+'不一样'}}])
def test_readback_never_uses_title_substrings_or_wrong_dates(change):
    data=response();data['data']['list'][0].update(change)
    assert readback.match_posts(data,'测试',DESC,DAY)['status']=='absent'

def test_duplicate_candidates_are_ambiguous_and_never_auto_publish():
    data=response();data['data']['list'].append(dict(data['data']['list'][0],objectId='second'))
    assert readback.match_posts(data,'测试',DESC,DAY)['status']=='ambiguous'

def test_partial_current_day_list_does_not_prove_absence():
    data=response();data['data'].update(totalCount=80,continueFlag=1)
    assert readback.match_posts(data,'测试',DESC+'完全不同',DAY)['status']=='unavailable'

def test_short_generic_description_is_not_auto_matched():
    assert readback.match_posts(response(),'title','今日新闻',DAY)['status']=='unavailable'

@pytest.mark.parametrize('data',[None,[],{}, {'data':None},{'data':{'list':{}}}])
def test_bad_response_is_not_success(data):
    assert readback.match_posts(data,'title',DESC,DAY)['status']=='unavailable'


def fake_app(tmp_path):
    cookie=tmp_path/'cookie.json';cookie.write_text('{}')
    video=tmp_path/'video.mp4';video.write_bytes(b'fixture-not-real-media')
    attrs=dict(account_file=str(cookie),title='测试',desc=DESC,file_path=str(video),tags=[],cover_mode='video_frame',
               headless=True,edition_day=DAY,is_draft=False,publish_strategy='immediate',publish_date=0,short_title=None,
               validate_video_file=lambda x:Path(x),validate_image_file=lambda x:Path(x),progress_callback=None,
               thumbnail_landscape_path=None,thumbnail_portrait_path=None)
    for name in ('open_upload_page','upload_video_file','prepare_video_for_publish','wait_for_upload_complete','apply_collection','apply_original_statement','set_thumbnail','set_short_title','set_schedule_time_tencent'):
        attrs[name]=AsyncMock()
    app=SimpleNamespace(**attrs);page=SimpleNamespace(url='https://channels.weixin.qq.com/platform/post/create')
    context=SimpleNamespace(new_page=AsyncMock(return_value=page),close=AsyncMock())
    browser=SimpleNamespace(new_context=AsyncMock(return_value=context),close=AsyncMock())
    p=SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    return app,p,context,browser

@pytest.mark.parametrize('mode',['existing','video_frame','custom_failure','post_click_error','readback_unavailable'])
def test_workflow_respects_submission_boundary(tmp_path,mode):
    app,p,ctx,browser=fake_app(tmp_path)
    async def clicked(_app,_page,flow):
        flow.submission_started=True
        if mode=='post_click_error':raise RuntimeError('reply lost after click')
    outcomes=[{'status':'absent','message':'not found'},{'status':'found','remote_id':'new-id'}]
    if mode=='existing':outcomes=[{'status':'found','remote_id':'existing-id'}]
    if mode=='readback_unavailable':outcomes=[{'status':'unavailable','message':'list not complete'}]
    if mode=='custom_failure':
        app.cover_mode='custom';app.set_thumbnail.side_effect=RuntimeError('editor unavailable')
    with patch.object(workflow,'scan_context',AsyncMock(side_effect=outcomes)), \
         patch.object(workflow,'visible_generated_cover',AsyncMock(return_value=True)), \
         patch.object(workflow,'final_submit_once',AsyncMock(side_effect=clicked)) as submit, \
         patch.object(workflow.asyncio,'sleep',AsyncMock()):
        if mode in ('custom_failure','readback_unavailable'):
            with pytest.raises(SubmissionNotStarted) as caught:asyncio.run(workflow.run_video(app,p))
            assert not app.publication_progress.submission_started
            assert caught.value.upload_started is (mode=='custom_failure')
            submit.assert_not_awaited()
        elif mode=='post_click_error':
            with pytest.raises(RuntimeError) as caught:asyncio.run(workflow.run_video(app,p))
            assert not isinstance(caught.value,SubmissionNotStarted)
            submit.assert_awaited_once()
        else:
            result=asyncio.run(workflow.run_video(app,p))
            assert result['status']=='found'
            if mode=='existing':
                app.upload_video_file.assert_not_awaited();submit.assert_not_awaited()
            else:
                submit.assert_awaited_once();app.set_thumbnail.assert_not_awaited()
                app.apply_original_statement.assert_awaited_once()
    ctx.close.assert_awaited_once();browser.close.assert_awaited_once()


def test_before_submit_after_media_failure_allows_retry_without_fake_remote_absence(case):
    ident=daily.reserve(case.path,KEY,'manual')
    with patch.object(daily,'_upload',side_effect=SubmissionNotStarted('封面设置未完成',stage='cover',upload_started=True)):
        result=daily.run(ident)
    assert result['state']=='failed'
    assert result['evidence']['submission_started'] is False
    assert result['evidence']['upload_started'] is True
    assert 'remote_absent' not in result['evidence']
    assert status(case)['retry_allowed']
    with patch.object(daily,'_upload') as uploader:
        next_id=daily.reserve(case.path,KEY,'manual');uploader.assert_not_called()
    assert next_id!=ident


def test_remote_record_result_stops_duplicate_reservation(case):
    ident=daily.reserve(case.path,KEY,'manual')
    with patch.object(daily,'_upload',return_value={'status':'found','remote_id':'actual-fixture-id','existing':True}):
        result=daily.run(ident)
    assert result['state']=='processing' and result['remote_id']=='actual-fixture-id'
    assert status(case)['remote_verified']
    with pytest.raises(ValueError):daily.reserve(case.path,KEY,'manual')


def test_progress_is_persisted_without_claiming_success(case):
    ident=daily.reserve(case.path,KEY,'manual')
    daily._progress(ident,{'stage':'cover','message':'设置封面','upload_started':True,'submission_started':False})
    assert daily.task(ident)['state']=='uploading'
    assert status(case)['progress_stage']=='cover'


def test_one_click_sends_current_payload_and_never_needs_separate_draft_save(case):
    payload=dict(daily.load_package(case.path)['platforms'][KEY],title='新标题',description=DESC,cover_mode='video_frame')
    with patch.object(queue,'ensure_worker') as worker:
        outcome=one.submit_one(case.path,KEY,'web:wechat_channels:7',payload)
        worker.assert_called_once()
    task=daily.task(outcome['task_id'])
    assert task['payload']['title']=='新标题'
    assert task['payload']['cover_mode']=='video_frame'
    assert daily.get_draft(case.path,KEY,'web:wechat_channels:7')['title']=='新标题'
    with pytest.raises(ValueError):one.submit_one(case.path,KEY,'web:wechat_channels:7',payload)


def test_click_cannot_switch_to_an_unseen_account(case):
    with pytest.raises(ValueError,match='账号已变化'):
        one.submit_one(case.path,KEY,'web:wechat_channels:999',{})


def test_sync_uses_original_account_and_preserves_uncertain_absence(case):
    ident=daily.reserve(case.path,KEY,'manual');daily._state(ident,'unknown',error='old')
    before=daily.task(ident)
    with patch.object(one,'readback_file',AsyncMock(return_value={'status':'absent','message':'absent'})):
        result=one.sync_result(ident)
    assert result['task']==before
    with patch.object(one,'readback_file',AsyncMock(return_value={'status':'found','remote_id':'real-fixture','match':'exact'})):
        result=one.sync_result(ident)
    assert result['task']['state']=='processing'
    assert result['task']['evidence']['source']=='official_content_list'


def test_http_publish_only_accepts_explicit_local_request(case):
    import sau_backend
    payload={'package_path':str(case.path),'platform':KEY,'account_id':'web:wechat_channels:7','payload':{'title':'测试','description':DESC,'cover_mode':'video_frame','tags':[],'ai_declaration':'AI'}}
    client=sau_backend.app.test_client()
    assert client.post('/daily/publish-one',json=payload).status_code==400
    with patch.object(queue,'ensure_worker') as worker:
        response=client.post('/daily/publish-one',json=payload,headers={'X-SAU-Local':'1','Origin':'http://127.0.0.1:5173'})
        assert response.status_code==200
        worker.assert_called_once()


def test_same_job_is_not_executed_twice(case):
    ident=daily.reserve(case.path,KEY,'manual')
    with patch.object(daily,'_upload') as upload:
        daily.run(ident)
        # Even if a stale external caller restores state, durable ownership remains.
        with daily._connection() as conn:conn.execute("UPDATE attempts SET state='uploading' WHERE id=?",(ident,))
        with pytest.raises(ValueError,match='接管'):daily.run(ident)
        upload.assert_called_once()

@pytest.mark.parametrize('driver',['playwright','patchright'])
def test_actual_browser_clicks_final_publish_only_once_and_ignores_hidden_duplicates(driver):
    import importlib
    async def scenario():
        factory=importlib.import_module(driver+'.async_api').async_playwright
        async with factory() as p:
            browser=await p.chromium.launch(channel='msedge',headless=True)
            try:
                page=await browser.new_page()
                await page.set_content('<body data-count="0"><button style="display:none">发表</button><button onclick="document.body.dataset.count=Number(document.body.dataset.count)+1">发表</button></body>')
                values=[];flow=PublicationProgress(values.append)
                await workflow.final_submit_once(SimpleNamespace(is_draft=False),page,flow,timeout=.5)
                assert await page.locator('body').get_attribute('data-count')=='1'
                assert flow.submission_started
                assert any(x['submission_started'] for x in values)
                await page.set_content('<button disabled>发表</button>')
                flow=PublicationProgress()
                with pytest.raises(SubmissionNotStarted):
                    await workflow.final_submit_once(SimpleNamespace(is_draft=False),page,flow,timeout=.1)
                assert not flow.submission_started
            finally:await browser.close()
    asyncio.run(scenario())
