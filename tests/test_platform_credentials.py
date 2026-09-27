import asyncio,json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from playwright.async_api import async_playwright
from utils import platform_credentials as credentials
from tests.credential_fixtures import fixture_cookies

@pytest.mark.parametrize('platform',[1,2,4,6,7])
@pytest.mark.parametrize('challenge',[False,True])
def test_real_edge_read_only_validator_requires_creator_ui_and_retains_channel(tmp_path,platform,challenge):
 async def scenario():
  target=tmp_path/'state.json'
  cookies=json.loads(fixture_cookies(platform))
  for cookie in cookies:
   cookie['sameSite']='None';cookie['secure']=True
   cookie['expires']=cookie.pop('expirationDate')
  state={'cookies':cookies,'origins':[]}
  target.write_text(json.dumps(state));original=target.read_bytes()
  urls={1:'https://creator.xiaohongshu.com/new/home',2:'https://channels.weixin.qq.com/platform',4:'https://cp.kuaishou.com/article/publish/video',6:'https://studio.youtube.com/channel/UCfixture',7:'https://mp.toutiao.com/profile_v4/'}
  bodies={1:'<nav>创作首页</nav><nav>笔记管理</nav>',2:'<nav>首页</nav><nav>内容管理</nav>',4:'<button class="_upload-btn-fixture">上传</button>',6:'<ytcp-app><ytcp-navigation-drawer>内容</ytcp-navigation-drawer></ytcp-app>',7:'<nav>内容管理</nav><button>发布视频</button>'}
  visited=[];browsers=[]
  @asynccontextmanager
  async def intercepted():
   async with async_playwright() as p:
    async def launch(**options):
     browser=await p.chromium.launch(**options);browsers.append(browser)
     real_context=browser.new_context
     async def new_context(**kwargs):
      context=await real_context(**kwargs)
      await context.add_cookies([{'name':'private','value':'synthetic','domain':'.example.com','path':'/'}])
      async def route(r):
       visited.append((r.request.method,r.request.url))
       await r.fulfill(content_type='text/html; charset=utf-8',body=('<p>安全验证</p>' if challenge else '')+bodies[platform])
      await context.route('**/*',route)
      real_page=context.new_page
      async def new_page():
       page=await real_page();real_goto=page.goto
       async def goto(url,**kwargs):return await real_goto(urls[platform],**kwargs)
       page.goto=goto
       return page
      context.new_page=new_page
      return context
     browser.new_context=new_context
     return browser
    yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))
  with patch.object(credentials,'async_playwright',new=intercepted):
   result=await credentials.validate_platform_credentials(platform,target)
  assert result['success'] is not challenge, result
  assert all(not b.is_connected() for b in browsers)
  assert all(method=='GET' for method,_ in visited)
  if challenge:assert target.read_bytes()==original
  else:
   saved=json.loads(target.read_text());assert 'example.com' not in json.dumps(saved)
   assert saved.get('publisher_channel_id')==('UCfixture' if platform==6 else None)
 asyncio.run(scenario())
