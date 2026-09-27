import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image
from patchright.async_api import async_playwright
from uploader.ks_uploader.main import KSVideo


@pytest.mark.parametrize('wrong_preview', [False, True])
def test_cover_waits_for_decoding_and_verifies_selected_preview(tmp_path, wrong_preview):
    cover = tmp_path / 'cover.png'
    Image.new('RGB', (90, 120), (31, 131, 231)).save(cover)

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True)
            page = await browser.new_page()
            await page.set_content('''<div class="_default-cover_test"><img width="90" height="120"></div>
                <div role="document" class="ant-modal" style="display:none">
                <span>上传封面</span><input type="file"><canvas class="_cutter-raw_test" width="90" height="120"></canvas>
                <button>确认</button></div>''')
            await page.evaluate('''(wrong) => {
                const canvas=document.querySelector('canvas'), preview=document.querySelector('img');
                canvas.getContext('2d').fillRect(0,0,90,120);
                preview.src=canvas.toDataURL();
                preview.parentElement.onclick=()=>document.querySelector('.ant-modal').style.display='block';
                window.decoded=false; window.confirmedBeforeDecoded=false;
                document.querySelector('input').onchange=event=>{
                    const source=new Image(); source.src=URL.createObjectURL(event.target.files[0]);
                    source.onload=()=>setTimeout(()=>{canvas.getContext('2d').drawImage(source,0,0);window.decoded=true},1500);
                };
                document.querySelector('button').onclick=()=>{
                    window.confirmedBeforeDecoded=!window.decoded;
                    if(!wrong) preview.src=canvas.toDataURL();
                    document.querySelector('.ant-modal').style.display='none';
                };
            }''', wrong_preview)
            app = KSVideo('title', 'video', [], 0, 'cookie', thumbnail_path=str(cover))
            try:
                with patch('uploader.ks_uploader.cover_controls.COVER_TIMEOUT_SECONDS', 3), patch('uploader.ks_uploader.main._dump_page_debug', AsyncMock(return_value='evidence')) as capture:
                    if wrong_preview:
                        with pytest.raises(RuntimeError, match='保存后的选用封面.*evidence'):
                            await app.set_thumbnail(page)
                        capture.assert_awaited_once()
                    else:
                        await app.set_thumbnail(page)
                        capture.assert_not_awaited()
                    assert not await page.evaluate('window.confirmedBeforeDecoded')
            finally:
                await browser.close()
    asyncio.run(run())
