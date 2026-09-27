import asyncio,json
from unittest.mock import AsyncMock,MagicMock,patch
import pytest
import daily_publish as daily
from publishing import automation,xiaohongshu_browser as xhs
from publishing.platforms import CORE_PLATFORMS,material_for,package_view
from tests.test_daily_publish import DailyPublishTests

@pytest.fixture
def case():
 c=DailyPublishTests();c.setUp()
 (c.base/'cookies/xiaohongshu_test.json').write_text('{}')
 try:yield c
 finally:c.doCleanups()

def test_old_package_binding_material_duplicate_and_automation(case):
 raw=json.loads(case.path.read_text());raw['platforms']={k:raw['platforms'][k] for k in CORE_PLATFORMS}
 case.path.write_text(json.dumps(raw));before=case.path.read_bytes()
 assert package_view(daily.load_package(case.path))['platforms']['xiaohongshu']['cover']=='portrait'
 job=daily.reserve(case.path,'xiaohongshu','manual')
 with patch.object(xhs,'publish',AsyncMock(return_value={'status':'unknown'})) as publish:
  daily.run(job)
 assert publish.call_args.args[1].name=='portrait.bin'
 assert publish.call_args.args[3]==case.base/'cookies/xiaohongshu_test.json'
 assert daily.task(job)['state']=='unknown'
 with pytest.raises(ValueError):daily.reserve(case.path,'xiaohongshu','manual')
 assert before==case.path.read_bytes()
 old=json.loads(json.dumps(automation.DEFAULT));del old['platforms']['xiaohongshu']
 automation.settings_path().write_text(json.dumps(old))
 assert automation.read_settings()['platforms']['xiaohongshu']=={'enabled':False,'verified':False}

def test_preflight_no_silent_truncation_or_frame_cover(case):
 for change in [{'title':'很长的标题'*8},{'cover_mode':'video_frame'},{'tags':['标签'+str(i) for i in range(11)]},{'description':'字'*1001}]:
  with pytest.raises(ValueError):daily.reserve(case.path,'xiaohongshu','manual',change)
 with daily._connection() as db:assert db.execute('select count(*) from attempts').fetchone()[0]==0

def payload(notes,total=None):
 return {'code':0,'success':True,'data':{'notes':notes,'page':-1,'tags':[{'name':'所有笔记','checked':True,'notes_count':len(notes) if total is None else total}]}}

def test_remote_states_and_complete_absence():
 note={'id':'6ab4a48700000000180128d6','display_title':'标题','time':'2026-09-27 18:00','tab_status':1,'permission_code':0,'xsec_token':'must-not-be-stored'}
 result=xhs.list_outcome(payload([note]),'标题','2026-09-27')
 assert result['state']=='published' and 'must-not-be-stored' not in json.dumps(result)
 note['tab_status']=2
 assert xhs.list_outcome(payload([note]),'标题','2026-09-27')['state']=='processing'
 assert xhs.list_outcome(payload([note,note]),'标题','2026-09-27')['status']=='unknown'
 assert xhs.list_outcome(payload([]),'标题','2026-09-27')['status']=='absent'
 assert xhs.list_outcome(payload([],total=30),'标题','2026-09-27')['status']=='unknown'
 assert xhs.list_outcome(payload([]),'标题','2026-09-27',note['id'])['status']=='unknown'

def test_submit_timeout_is_never_clicked_twice():
 async def run():
  tree={'root':{'nodeName':'XHS-PUBLISH-BTN','children':[{'nodeId':3,'nodeName':'BUTTON','attributes':['aria-disabled','false'],'children':[{'nodeValue':'发布'}]}]}}
  cdp=MagicMock(send=AsyncMock(side_effect=[tree,{'model':{'content':[0,0,10,0,10,10,0,10]}}]),detach=AsyncMock())
  host=MagicMock(scroll_into_view_if_needed=AsyncMock(),get_attribute=AsyncMock(return_value='false'),evaluate=AsyncMock(return_value=True))
  page=MagicMock(locator=MagicMock(return_value=host),context=MagicMock(new_cdp_session=AsyncMock(return_value=cdp)),mouse=MagicMock(click=AsyncMock(side_effect=RuntimeError('disconnected'))))
  progress=xhs.PublicationProgress()
  with pytest.raises(RuntimeError,match='disconnected'):await xhs.submit_once(page,progress)
  assert progress.submission_started
  page.mouse.click.assert_awaited_once()
 asyncio.run(run())

def test_cover_mismatch_and_wrong_ratio_stop(tmp_path):
 import io
 from PIL import Image
 expected=tmp_path/'cover.png';Image.new('RGB',(108,144),'blue').save(expected)
 async def run():
  for dimensions,color in [((108,144),'red'),((144,108),'blue')]:
   data=io.BytesIO();Image.new('RGB',dimensions,color).save(data,format='PNG')
   response=MagicMock(ok=True,body=AsyncMock(return_value=data.getvalue()))
   page=MagicMock(locator=MagicMock(return_value=MagicMock(evaluate=AsyncMock(return_value='url("https://ros-preview.xhscdn.com/cover")'))),context=MagicMock(request=MagicMock(get=AsyncMock(return_value=response))))
   with pytest.raises(ValueError,match='封面'):await xhs.compare_cover(page,expected)
 asyncio.run(run())
