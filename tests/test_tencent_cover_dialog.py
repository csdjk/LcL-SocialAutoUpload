"""Offline regressions against the actual TencentVideo cover implementation.
Both drivers use only in-memory HTML. No credentials or real uploads are used.
"""
import base64
import importlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uploader.tencent_uploader import main as tencent

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a1ioAAAAASUVORK5CYII=')
HTML = r'''<!doctype html><html><meta charset="utf-8"><body>
<style>.weui-desktop-dialog {border:1px solid black;padding:10px;width:500px} img {width:100px;height:80px} button {padding:12px}</style>
<div id="hidden-stub" class="weui-desktop-dialog" style="display:none">编辑封面
 <div class="single-cover-uploader-wrap"><input type="file" onchange="document.body.dataset.hiddenTouched='true'"></div></div>
<div id="hidden-crop" class="weui-desktop-dialog" style="display:none">裁剪封面图<button>确定</button></div>
<div class="horizontal-cover-wrap"><img id="saved-landscape" style="display:none"><button onclick="openEditor('landscape')">编辑横版</button></div>
<div class="vertical-cover-wrap"><img id="saved-portrait" style="display:none"><button onclick="openEditor('portrait')">编辑竖版</button></div>
<div id="editor" class="weui-desktop-dialog" style="display:none"><h2>编辑封面</h2>
 <div class="single-cover-uploader-wrap"><input id="image-file" type="file" style="display:none" onchange="fileChanged(this)"><img id="preview"></div>
 <div class="weui-desktop-dialog__ft"><button id="confirm-main" class="weui-desktop-btn_primary" disabled onclick="saveCover()">确认</button></div>
</div>
<div id="crop" class="weui-desktop-dialog" style="display:none"><h2>裁剪封面图</h2>
 <div class="weui-desktop-dialog__ft"><button class="weui-desktop-btn_primary" onclick="applyCrop()">确定</button></div>
</div>
<button onclick="document.body.dataset.publishClicked=String(Number(document.body.dataset.publishClicked)+1)">发表</button>
<script>
// DOM state is readable from both drivers without depending on window globals.
document.body.dataset.cfg=JSON.stringify({openDelay:30,crop:false,ignoreClick:false,ignoreFile:false,keepOpen:false});
document.body.dataset.events='[]';document.body.dataset.saved='{}';
document.body.dataset.publishClicked='0';document.body.dataset.hiddenTouched='false';
function config(){return JSON.parse(document.body.dataset.cfg);}
function recordEvent(value){const events=JSON.parse(document.body.dataset.events);events.push(value);document.body.dataset.events=JSON.stringify(events);}
function openEditor(kind){recordEvent('open:'+kind);window.kind=kind;const cfg=config();
 if(cfg.ignoreClick)return;
 setTimeout(()=>{document.querySelector('#editor').style.display='block';document.querySelector('#confirm-main').disabled=true;},cfg.openDelay);
}
function fileChanged(input){if(!input.files.length)return;window.chosenFile=input.files[0];recordEvent('file:'+kind);
 if(config().ignoreFile){document.querySelector('#confirm-main').disabled=false;return;}
 if(config().crop){setTimeout(()=>document.querySelector('#crop').style.display='block',40);}else{setTimeout(updatePreview,60);}
}
function updatePreview(){const img=document.querySelector('#preview');img.onload=()=>document.querySelector('#confirm-main').disabled=false;img.src=URL.createObjectURL(chosenFile);}
function applyCrop(){recordEvent('crop:'+kind);document.querySelector('#crop').style.display='none';updatePreview();}
function saveCover(){recordEvent('save:'+kind);const saved=JSON.parse(document.body.dataset.saved);saved[kind]=chosenFile.name;document.body.dataset.saved=JSON.stringify(saved);if(!config().keepOpen){if(!config().keepCardUnchanged){const thumb=document.querySelector('#saved-'+kind);thumb.src=document.querySelector('#preview').src;thumb.style.display='block';}document.querySelector('#editor').style.display='none';}}
</script></body></html>'''


class FastClock:
    def __init__(self): self.now = 0
    def monotonic(self): self.now += 3; return self.now


class CoverScenarios:
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sau-offline-cover-')
        self.picture = Path(self.temp.name) / 'landscape.png'
        self.portrait = Path(self.temp.name) / 'portrait.png'
        self.picture.write_bytes(PNG)
        self.portrait.write_bytes(PNG)
        driver = importlib.import_module(self.driver + '.async_api')
        self.playwright = await driver.async_playwright().start()
        options = {'headless': True}
        if os.name == 'nt':
            options['channel'] = 'msedge'
        elif executable := os.environ.get('SAU_TEST_CHROMIUM') or shutil.which('chromium'):
            options['executable_path'] = executable
        self.browser = await self.playwright.chromium.launch(**options)
        self.context = await self.browser.new_context()
        self.network = []
        async def block(route):
            self.network.append(route.request.url)
            await route.abort()
        await self.context.route('http://**/*', block)
        await self.context.route('https://**/*', block)
        self.page = await self.context.new_page()
        await self.page.set_content(HTML)

    async def asyncTearDown(self):
        try:
            self.assertEqual(int(await self.page.locator('body').get_attribute('data-publish-clicked')), 0)
            self.assertEqual(await self.page.locator('body').get_attribute('data-hidden-touched'), 'false')
            self.assertEqual(self.network, [])
        finally:
            await self.browser.close()
            await self.playwright.stop()
            self.temp.cleanup()

    async def configure(self, **changes):
        await self.page.locator('body').evaluate('(el,changes) => {el.dataset.cfg=JSON.stringify({...JSON.parse(el.dataset.cfg),...changes});}', changes)

    async def fixture_data(self, name):
        return json.loads(await self.page.locator('body').get_attribute('data-' + name))

    def controller(self, clock=None):
        if clock is not None:
            timer_patch = patch.object(tencent, 'time', clock)
            timer_patch.start()
            self.addCleanup(timer_patch.stop)
        return tencent.TencentVideo('offline fixture', 'unused-video.mp4', [], 0,
                                   'unused-credentials.json', require_thumbnail=True)

    async def open_editor(self, app):
        return await app.open_thumbnail_dialog(self.page, ['div.horizontal-cover-wrap'], ['编辑封面'])

    async def test_hidden_first_stub_is_not_selected(self):
        old_match = self.page.locator('div.weui-desktop-dialog').filter(has_text='编辑封面').first
        self.assertEqual(await old_match.get_attribute('id'), 'hidden-stub')
        self.assertFalse(await old_match.is_visible())
        dialog = await self.open_editor(self.controller())
        self.assertEqual(await dialog.get_attribute('id'), 'editor')
        self.assertTrue(await dialog.is_visible())

    async def test_hover_recommendation_is_handled_before_card_click(self):
        await self.page.evaluate('''() => {
          const card=document.querySelector('.horizontal-cover-wrap');
          card.setAttribute('onmouseenter', `if(document.querySelector('.img-recommend-wrap'))return;
            const layer=document.createElement('div');layer.className='img-recommend-wrap';
            layer.style='position:fixed;inset:0;background:white;z-index:1000';
            layer.innerHTML='<button class="btn-directly-edit">直接编辑</button>';
            layer.querySelector('button').setAttribute('onclick', "document.querySelector('.horizontal-cover-wrap').removeAttribute('onmouseenter');this.parentElement.remove();openEditor('landscape')");
            document.body.appendChild(layer);
          `);
        }''')
        dialog = await self.open_editor(self.controller())
        self.assertTrue(await dialog.is_visible())
        self.assertEqual(await self.fixture_data('events'), ['open:landscape'])

    async def test_delayed_open_is_clicked_only_once(self):
        await self.configure(openDelay=800)
        self.assertTrue(await (await self.open_editor(self.controller())).is_visible())
        self.assertEqual(await self.fixture_data('events'), ['open:landscape'])

    async def test_both_orientations_and_hidden_crop_stub(self):
        app = self.controller()
        app.thumbnail_landscape_path = str(self.picture)
        app.thumbnail_portrait_path = str(self.portrait)
        await app.set_thumbnail(self.page)
        self.assertEqual(await self.fixture_data('events'),
                         ['open:landscape','file:landscape','save:landscape',
                          'open:portrait','file:portrait','save:portrait'])
        self.assertEqual(await self.fixture_data('saved'), {'landscape':'landscape.png','portrait':'portrait.png'})
        self.assertFalse(await self.page.locator('#editor').is_visible())

    async def test_crop_confirmed_before_main_editor_for_both_orientations(self):
        await self.configure(crop=True)
        app = self.controller()
        app.thumbnail_landscape_path = str(self.picture)
        app.thumbnail_portrait_path = str(self.portrait)
        await app.set_thumbnail(self.page)
        self.assertEqual(await self.fixture_data('events'),
                         ['open:landscape','file:landscape','crop:landscape','save:landscape',
                          'open:portrait','file:portrait','crop:portrait','save:portrait'])

    async def test_ambiguous_visible_dialogs_rejected(self):
        await self.page.evaluate('''() => {let x=document.querySelector('#editor');x.style.display='block';let y=x.cloneNode(true);y.id='editor2';document.body.appendChild(y);}''')
        with self.assertRaisesRegex(RuntimeError, '多个可见'):
            await self.controller()._visible_cover_dialog(self.page, ['编辑封面'])

    async def test_missing_open_does_not_use_hidden_editor_or_repeat_click(self):
        await self.configure(ignoreClick=True)
        with self.assertRaisesRegex(RuntimeError, '自定义封面编辑器未就绪'):
            await self.open_editor(self.controller(FastClock()))
        self.assertEqual(await self.fixture_data('events'), ['open:landscape'])

    async def test_unchanged_preview_is_not_claimed_saved(self):
        await self.configure(ignoreFile=True)
        dialog = await self.open_editor(self.controller())
        with self.assertRaisesRegex(RuntimeError, '封面预览或确认按钮未就绪'):
            await self.controller(FastClock()).upload_thumbnail_in_dialog(self.page, dialog, str(self.picture))
        self.assertEqual(await self.fixture_data('events'), ['open:landscape','file:landscape'])

    async def test_ambiguous_file_inputs_are_rejected(self):
        app = self.controller()
        dialog = await self.open_editor(app)
        await self.page.locator('#editor .single-cover-uploader-wrap').evaluate('x=>x.appendChild(x.querySelector("input").cloneNode(true))')
        with self.assertRaisesRegex(RuntimeError, '唯一的图片选择控件'):
            await app.upload_thumbnail_in_dialog(self.page, dialog, str(self.picture))

    async def test_required_cover_failure_is_not_ignored(self):
        app = self.controller(FastClock())
        app.thumbnail_landscape_path = str(self.picture)
        await self.configure(ignoreClick=True)
        with self.assertRaisesRegex(RuntimeError, '4:3 横版封面设置失败，尚未提交投稿'):
            await app.set_thumbnail(self.page)

    async def test_same_file_reused_in_same_component_triggers_change(self):
        app = self.controller()
        app.thumbnail_landscape_path = str(self.picture)
        await app.set_thumbnail(self.page)
        await app.set_thumbnail(self.page)
        self.assertEqual((await self.fixture_data('events')).count('file:landscape'), 2)
        self.assertEqual((await self.fixture_data('events')).count('save:landscape'), 2)

    async def test_no_cover_does_not_open_editor(self):
        await self.controller().set_thumbnail(self.page)
        self.assertEqual(await self.fixture_data('events'), [])

    async def test_editor_staying_open_is_not_a_success(self):
        await self.configure(keepOpen=True)
        app = self.controller()
        app.thumbnail_landscape_path = str(self.picture)
        with self.assertRaisesRegex(RuntimeError, '尚未提交投稿') as error:
            await app.set_thumbnail(self.page)
        self.assertNotIn('Call log', str(error.exception))
        self.assertEqual((await self.fixture_data('events')).count('save:landscape'), 1)

    async def test_hidden_cover_wrapper_does_not_block_visible_wrapper(self):
        await self.page.evaluate('''() => {const x=document.createElement('div');x.className='horizontal-cover-wrap';x.style.display='none';document.body.prepend(x);}''')
        app = self.controller()
        app.thumbnail_landscape_path = str(self.picture)
        await app.set_thumbnail(self.page)
        self.assertEqual((await self.fixture_data('saved'))['landscape'], 'landscape.png')

    async def test_driver_error_does_not_leak_raw_call_log(self):
        app = self.controller()
        app.thumbnail_landscape_path = str(self.picture)
        driver = importlib.import_module(self.driver + '.async_api')
        with patch.object(app, 'open_thumbnail_dialog', AsyncMock(side_effect=driver.TimeoutError('synthetic-secret Call log'))):
            with self.assertRaisesRegex(RuntimeError, '页面控件等待超时或操作失败') as error:
                await app.set_thumbnail(self.page)
        self.assertNotIn('synthetic-secret', str(error.exception))


    async def test_recommendation_direct_edit_before_local_custom_image(self):
        await self.page.evaluate("""() => {
            const box=document.createElement('div'); box.className='img-recommend-wrap'; box.style.display='none'; box.id='recommend';
            box.innerHTML='<p>使用此素材作为封面？</p><button class="btn-directly-edit">直接编辑</button><button>使用素材</button>';
            document.body.appendChild(box);
            document.querySelector('.horizontal-cover-wrap button').setAttribute('onclick',"document.querySelector('#recommend').style.display='block'");
            box.querySelector('.btn-directly-edit').setAttribute('onclick',"document.querySelector('#recommend').style.display='none';openEditor('landscape')");
        }""")
        app=self.controller(); app.thumbnail_landscape_path=str(self.picture)
        await app.set_thumbnail(self.page)
        self.assertEqual(await self.fixture_data('saved'),{'landscape':'landscape.png'})
        self.assertEqual((await self.fixture_data('events')).count('open:landscape'),1)

    async def test_nested_crop_is_not_mistaken_for_main_dialog(self):
        await self.page.evaluate("() => document.querySelector('#editor .single-cover-uploader-wrap').appendChild(document.querySelector('#crop'))")
        await self.configure(crop=True)
        app=self.controller(); app.thumbnail_landscape_path=str(self.picture); app.thumbnail_portrait_path=str(self.portrait)
        await app.set_thumbnail(self.page)
        events=await self.fixture_data('events')
        self.assertLess(events.index('crop:landscape'),events.index('save:landscape'))
        self.assertLess(events.index('crop:portrait'),events.index('save:portrait'))
        self.assertEqual(await self.fixture_data('saved'),{'landscape':'landscape.png','portrait':'portrait.png'})

    async def test_hidden_upload_tab_is_activated_before_selecting_file(self):
        await self.page.evaluate("""() => {
            const host=document.querySelector('#editor .single-cover-uploader-wrap'); host.style.display='none';
            const tab=document.createElement('button'); tab.setAttribute('role','tab'); tab.textContent='本地上传';
            tab.setAttribute('onclick',"document.querySelector('#editor .single-cover-uploader-wrap').style.display='block'");
            document.querySelector('#editor').prepend(tab);
        }""")
        app=self.controller(); app.thumbnail_landscape_path=str(self.picture)
        await app.set_thumbnail(self.page)
        self.assertEqual(await self.fixture_data('saved'),{'landscape':'landscape.png'})

    async def test_nested_crop_does_not_add_second_main_confirm_button(self):
        from uploader.tencent_uploader.cover_controls import CoverControls
        await self.page.evaluate("""() => {
            document.querySelector('#editor').style.display='block';
            const crop=document.querySelector('#crop'); crop.style.display='block';
            document.querySelector('#editor').appendChild(crop);
        }""")
        controls=CoverControls(self.page)
        main=await controls.find_dialog(['编辑封面'])
        crop=await controls.find_dialog(['裁剪封面图'],crop=True)
        self.assertEqual(await main.get_attribute('id'),'editor')
        self.assertEqual(await crop.get_attribute('id'),'crop')
        confirm=await controls.confirm_button(main)
        self.assertEqual(await confirm.get_attribute('id'),'confirm-main')
        self.assertEqual((await (await controls.confirm_button(crop,crop=True)).inner_text()).strip(),'确定')

    async def test_busy_upload_blocks_ready_button(self):
        from uploader.tencent_uploader.cover_controls import CoverControls
        dialog=await self.open_editor(self.controller())
        await self.page.locator('#editor').evaluate("""root => {
            const busy=document.createElement('div'); busy.className='uploading-wrap'; busy.textContent='上传中'; root.appendChild(busy);
        }""")
        self.assertTrue(await CoverControls(self.page).busy(dialog))
        await self.page.locator('.uploading-wrap').evaluate("e=>e.style.display='none'")
        self.assertFalse(await CoverControls(self.page).busy(dialog))

    async def test_closed_editor_with_unchanged_card_is_not_success(self):
        from uploader.tencent_uploader.cover_controls import CoverControls, CoverControlError, preview_state
        app=self.controller(); dialog=await self.open_editor(app)
        before=await preview_state(self.page.locator('.horizontal-cover-wrap'))
        await self.configure(keepCardUnchanged=True)
        saved=await app.upload_thumbnail_in_dialog(self.page,dialog,str(self.picture))
        with self.assertRaisesRegex(CoverControlError,'封面预览尚未更新'):
            await CoverControls(self.page,clock=FastClock()).verify_saved_preview(['.horizontal-cover-wrap'],before,saved)


    async def test_unrelated_video_preview_is_not_custom_cover_proof(self):
        from uploader.tencent_uploader.cover_controls import preview_state
        dialog=await self.open_editor(self.controller())
        await dialog.evaluate("""root => {
            const unrelated=document.createElement('img'); unrelated.src='data:image/png;base64,not-a-cover';
            unrelated.className='video-frame-preview'; root.appendChild(unrelated);
        }""")
        result=await preview_state(dialog)
        self.assertNotIn('data:image/png;base64,not-a-cover',result['sources'])


class PlaywrightCoverTests(CoverScenarios, unittest.IsolatedAsyncioTestCase):
    driver = 'playwright'


class PatchrightCoverTests(CoverScenarios, unittest.IsolatedAsyncioTestCase):
    driver = 'patchright'


class TencentUploadOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_required_cover_failure_stops_before_publish(self):
        app = tencent.TencentVideo('offline', 'unused.mp4', [], 0, 'unused.json', require_thumbnail=True)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cookie = Path(temp.name) / 'fixture.json'
        cookie.write_text('{}')
        app.account_file = str(cookie)
        app.validate_video_file = lambda value: Path(value)
        page = MagicMock()
        page.url = 'https://channels.weixin.qq.com/platform/post/create'
        context = MagicMock()
        context.new_page = AsyncMock(return_value=page)
        context.close = AsyncMock()
        context.storage_state = AsyncMock()
        browser = MagicMock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        driver = MagicMock()
        driver.chromium.launch = AsyncMock(return_value=browser)
        names = ('validate_upload_args','open_upload_page','upload_video_file',
                 'prepare_video_for_publish','wait_for_upload_complete','apply_collection',
                 'apply_original_statement','set_short_title','submit_publish')
        for name in names:
            setattr(app, name, AsyncMock())
        app.set_thumbnail = AsyncMock(side_effect=RuntimeError('fixture cover failed'))
        with self.assertRaisesRegex(RuntimeError, 'fixture cover failed'):
            await app.upload(driver)
        app.submit_publish.assert_not_awaited()
        app.set_short_title.assert_not_awaited()
        context.storage_state.assert_not_awaited()
        context.close.assert_awaited_once()
        browser.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
