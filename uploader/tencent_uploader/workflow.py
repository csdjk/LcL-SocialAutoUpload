"""A single-owned-session video workflow with an explicit final-submit boundary."""
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from urllib.parse import urlsplit
from .flow import PublicationProgress, SubmissionNotStarted
from .readback import scan_context

class EditorPage:
    """Locators belong to the editor frame; keyboard/browser events to its page."""
    def __init__(self, page, frame): self.page, self.frame = page, frame
    def __getattr__(self, name):
        if name in ('locator','get_by_role','get_by_text','get_by_placeholder','get_by_label','evaluate'):
            return getattr(self.frame,name)
        return getattr(self.page,name)


def scopes(page):
    root = page.page if isinstance(page, EditorPage) else page
    result = [root]
    for frame in getattr(root, 'frames', []):
        if frame == getattr(root, 'main_frame', None): continue
        parsed = urlsplit(frame.url)
        if parsed.hostname == 'channels.weixin.qq.com' or parsed.scheme in ('about', ''):
            result.append(frame)
    return result


async def visible_generated_cover(page, timeout=15):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        for scope in scopes(page):
            images = scope.locator('.vertical-cover-wrap img:visible, .horizontal-cover-wrap img:visible, .video-cover img:visible')
            for image in await images.all():
                if await image.evaluate('e => e.complete && e.naturalWidth >= 32 && e.naturalHeight >= 32 && !!(e.currentSrc || e.src)'):
                    return True
        await asyncio.sleep(.25)
    return False


async def final_submit_once(app, page, flow, *, timeout=25):
    """No force-click, DOM deletion, blind retries, or success by button absence."""
    deadline = asyncio.get_running_loop().time() + timeout
    button = None
    while asyncio.get_running_loop().time() < deadline:
        for scope in scopes(page):
            name = '保存草稿' if app.is_draft else '发表'
            candidates = scope.locator('button:visible').filter(has_text=re.compile(r'^\s*'+name+r'\s*$'))
            if await candidates.count() == 1 and await candidates.is_enabled():
                klass = await candidates.get_attribute('class') or ''
                if 'disabled' not in klass:
                    button = candidates;break
        if button is not None:break
        await asyncio.sleep(.25)
    if button is None:
        raise flow.failure('发表按钮尚不可用，未提交。请检查官方页面的必填项或验证提示。','submit_not_ready')
    await flow.emit('submitting')
    # Persist this conservative boundary before attempting the irreversible click.
    flow.submission_started = True
    await flow.emit('submitting')
    await button.click(timeout=15000)


async def run_video(app, playwright):
    flow = PublicationProgress(getattr(app,'progress_callback',None))
    app.publication_progress = flow
    flow.account_file = app.account_file
    browser = None; context = None; result = None
    try:
        await flow.emit('checking')
        if not Path(app.account_file).is_file():
            raise flow.failure('登录文件不存在，尚未上传，请重新登录。','login_check_failed')
        if not str(app.title or '').strip(): raise flow.failure('请输入投稿标题。','material_invalid')
        app.file_path = str(app.validate_video_file(app.file_path))
        mode = getattr(app,'cover_mode','custom')
        if mode not in ('custom','video_frame'): raise flow.failure('封面模式无效。','material_invalid')
        if mode == 'custom':
            for attr in ('thumbnail_landscape_path','thumbnail_portrait_path'):
                if getattr(app,attr,None):setattr(app,attr,str(app.validate_image_file(getattr(app,attr))))
        if app.publish_strategy not in ('immediate','scheduled'):
            raise flow.failure('发布时间设置无效。','material_invalid')
        if app.publish_strategy == 'scheduled':app.publish_date=app.validate_publish_date(app.publish_date)
        else:app.publish_date=0
        browser = await playwright.chromium.launch(channel='msedge',headless=app.headless,timeout=20000)
        context = await browser.new_context(storage_state=app.account_file,locale='zh-CN',viewport={'width':1440,'height':1000})
        day = getattr(app,'edition_day',None)
        if day:
            await flow.emit('deduplicating')
            check = await scan_context(context,app.title,app.desc,day)
            if check['status'] == 'found': return {**check,'existing':True,'warnings':flow.warnings}
            if check['status'] != 'absent':
                raise flow.failure(check['message'], 'login_check_failed' if check['status']=='needs_login' else 'remote_check_unavailable')
        page = await context.new_page()
        await flow.emit('opening')
        await app.open_upload_page(page)
        if 'login' in urlsplit(page.url).path:
            raise flow.failure('视频号登录已失效，尚未上传，请重新登录。','login_check_failed')
        await flow.emit('uploading')
        flow.upload_started = True
        await flow.emit('uploading')
        await app.upload_video_file(page,app.file_path)
        editor = getattr(app,'_editor_scope',page)
        await flow.emit('metadata')
        await app.prepare_video_for_publish(editor)
        await flow.emit('uploading','等待视频上传完成')
        await app.wait_for_upload_complete(editor)
        await app.apply_collection(editor)
        await app.apply_original_statement(editor)
        await flow.emit('cover')
        if mode == 'video_frame':
            if not await visible_generated_cover(page):
                raise flow.failure('视频画面封面仍在生成，尚未提交。可稍后重试。','cover_not_ready')
            await flow.emit('cover','使用已生成的视频画面封面')
        else:
            await app.set_thumbnail(page)
            from publishing.diagnostics import capture
            shot = await capture(page, 'wechat_channels', 'covers-saved')
            await flow.emit('cover', '横版、竖版封面均已保存并核对预览', cover_screenshot=shot,
                            verified_covers=['landscape', 'portrait'])
        if app.publish_strategy == 'scheduled' and app.publish_date != 0:
            await app.set_schedule_time_tencent(editor,app.publish_date)
        await app.set_short_title(editor,app.title,app.short_title)
        await final_submit_once(app,page,flow)
        await flow.emit('verifying')
        if app.is_draft:
            return {'status':'unknown','message':'已点击保存草稿，请到官方草稿箱确认；不会自动再提交。','warnings':flow.warnings}
        day = day or datetime.now(timezone(timedelta(hours=8))).date().isoformat()
        for attempt in range(2):
            await asyncio.sleep(2 if attempt==0 else 4)
            result = await scan_context(context,app.title,app.desc,day,timeout=15)
            if result['status']=='found':
                return {**result,'existing':False,'warnings':flow.warnings}
        return {'status':'unknown','message':'已尝试发表，后台暂未返回可确认的作品记录。请点“同步后台结果”，不要重复投稿。','warnings':flow.warnings}
    except SubmissionNotStarted:
        raise
    except Exception as exc:
        if 'page' in locals():
            from publishing.diagnostics import capture
            screenshot = await capture(page, 'wechat_channels', flow.stage)
            if app.progress_callback:
                app.progress_callback({'stage': flow.stage, 'screenshot': screenshot,
                                       'submission_started': flow.submission_started})
        if flow.submission_started:
            raise RuntimeError('已尝试提交，但未能确认后台结果。请同步后台结果；不会重复点击发表。') from None
        if type(exc) is RuntimeError:
            message=str(exc).split('Call log:')[0][:240]
        else:
            message='页面操作未完成，尚未提交投稿。请检查当前阶段后重试。'
        raise flow.failure(message,'cover_failed' if flow.stage=='cover' else 'prepare_failed') from None
    finally:
        # Do not overwrite a failure with a close/save exception.
        if context is not None:
            try: await asyncio.wait_for(context.close(),8)
            except Exception: pass
        if browser is not None:
            try: await asyncio.wait_for(browser.close(),8)
            except Exception: pass
