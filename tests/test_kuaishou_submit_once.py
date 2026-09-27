import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from patchright.async_api import async_playwright
from uploader.ks_uploader.main import KSVideo


@pytest.mark.parametrize('delay,query', [(0, ''), (1300, '?status=2&from=publish')])
def test_submit_handles_async_confirmation_and_variable_manager_query(delay, query):
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            page = await browser.new_page()
            await page.route('https://cp.kuaishou.com/**', lambda r: r.fulfill(body='<html><body></body></html>'))
            await page.goto('https://cp.kuaishou.com/article/publish/video')
            await page.set_content('''<button id="publish">发布</button><div id="host"></div>''')
            await page.evaluate('''({delay,query}) => {
              window.clicks=0; window.confirms=0;
              document.querySelector('#publish').onclick=()=>{
                window.clicks++;
                setTimeout(()=>{
                  document.querySelector('#host').innerHTML='<div class="ant-modal-confirm-centered"><button class="ant-btn-primary">确认发布</button></div>';
                  document.querySelector('.ant-btn-primary').onclick=()=>{
                    window.confirms++; history.pushState({},'', '/article/manage/video'+query);
                    document.querySelector('#host').innerHTML='视频管理';
                  };
                },delay);
              };
            }''', {'delay': delay, 'query': query})
            app = KSVideo('title', 'video', [], 0, 'cookie', single_submission=True)
            try:
                with patch('uploader.ks_uploader.main.KUAISHOU_SUBMIT_TIMEOUT_SECONDS', 4):
                    await app.submit_once(page)
                assert await page.evaluate('window.clicks') == 1
                assert await page.evaluate('window.confirms') == 1
            finally:
                await browser.close()
    asyncio.run(run())


def test_validation_error_preserves_message_and_diagnostics_without_retry():
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            page = await browser.new_page()
            await page.set_content('<button>发布</button><div id="error"></div>')
            await page.evaluate('''() => {window.clicks=0; document.querySelector('button').onclick=()=>{
                window.clicks++; document.querySelector('#error').innerHTML='<div class="ant-message-error">请选择视频分类</div>';
            }}''')
            app = KSVideo('title', 'video', [], 0, 'cookie', single_submission=True)
            try:
                with patch('uploader.ks_uploader.main._dump_page_debug', AsyncMock(return_value='evidence')) as capture:
                    with pytest.raises(RuntimeError, match='请选择视频分类.*evidence'):
                        await app.submit_once(page)
                    capture.assert_awaited_once()
                assert await page.evaluate('window.clicks') == 1
            finally:
                await browser.close()
    asyncio.run(run())
