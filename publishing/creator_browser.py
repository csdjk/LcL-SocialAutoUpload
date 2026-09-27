"""Owned browser sessions for YouTube Studio and 头条号.

Only visible, uniquely identified controls are used. A final click is never
retried; missing fields, covers or receipts remain observable failures.
"""
import asyncio
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from playwright.async_api import async_playwright
from uploader.tencent_uploader.flow import PublicationProgress, SubmissionNotStarted

ENTRY = {"youtube": "https://studio.youtube.com/", "toutiao": "https://mp.toutiao.com/"}
KINDS = {"youtube": 6, "toutiao": 7}


def browser_options(platform, *, headless=False):
    options = {"channel": "msedge", "headless": headless}
    if platform == "youtube":
        import conf
        proxy = getattr(conf, "YT_PROXY", None)
        if proxy:
            options["proxy"] = {"server": proxy}
    return options


def validate_material(platform, material):
    maximum = 100 if platform == "youtube" else 30
    title = material.get("title")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= maximum:
        raise ValueError(f"投稿标题须为 1–{maximum} 个字，请编辑平台文案")
    if any(c in title for c in '<>'):
        raise ValueError("投稿标题不能包含尖括号")
    description = material.get("description")
    if not isinstance(description, str) or not description.strip() or len(description.encode('utf-8')) > 5000:
        raise ValueError("简介不能为空，且不能超过 5000 UTF-8 字节")
    tags = material.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags) or len(','.join(tags)) > 500:
        raise ValueError("标签格式无效或总长度超过 500 字")
    if material.get("cover_mode", "custom") != "custom":
        raise ValueError("此通道须使用资源包封面")
    if platform == "youtube":
        if material.get("visibility") not in ("public", "unlisted", "private"):
            raise ValueError("请选择 YouTube 可见性")
        if type(material.get("made_for_kids")) is not bool:
            raise ValueError("请选择 YouTube 视频是否面向儿童")


def storage(cookie):
    raw = json.loads(Path(cookie).read_text(encoding='utf-8'))
    channel = raw.get('publisher_channel_id')
    if channel is not None and (not isinstance(channel, str) or not re.fullmatch(r'UC[A-Za-z0-9_-]+', channel)):
        raise ValueError('YouTube 频道标识无效，请重新登录')
    return {key: raw[key] for key in ('cookies', 'origins') if key in raw}, channel


def matches_channel(page, channel):
    return bool(channel) and urlsplit(page.url).path.split('/')[1:3] == ['channel', channel]


async def unique(locator, label, *, attached=False, timeout=30):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        candidates = locator if attached else locator.filter(visible=True)
        count = await candidates.count()
        if count == 1:
            return candidates
        if count > 1:
            raise ValueError(f"{label}匹配到多个控件，请核对平台页面")
        await asyncio.sleep(.25)
    raise ValueError(f"未找到{label}，请核对平台页面或完成验证")


async def click(locator, label):
    control = await unique(locator, label)
    if not await control.is_enabled() or await control.get_attribute('aria-disabled') == 'true':
        raise ValueError(f"{label}尚未就绪")
    await control.click()


async def authenticated(platform, page):
    from myUtils.browser_login import inspect_page
    return await inspect_page(KINDS[platform], page) == 'valid'


async def check_cookie(platform, cookie):
    state, channel = storage(cookie)
    async with async_playwright() as p:
        browser = await p.chromium.launch(**browser_options(platform, headless=True))
        try:
            context = await browser.new_context(storage_state=state, locale='zh-CN')
            page = await context.new_page()
            url = ENTRY[platform]
            if platform == 'youtube' and channel:
                url += f'channel/{channel}'
            await page.goto(url, wait_until='domcontentloaded')
            for _ in range(12):
                if await authenticated(platform, page):
                    return platform != 'youtube' or matches_channel(page, channel)
                await asyncio.sleep(.5)
            return False
        finally:
            await browser.close()


async def upload_cover(container, cover):
    before = await container.locator('img').evaluate_all('(imgs) => imgs.map(x => x.currentSrc || x.src)')
    field = await unique(container.locator('input[type="file"]'), '封面图片上传控件', attached=True)
    await field.set_input_files(str(cover))
    for _ in range(120):
        saved = await container.locator('img').evaluate_all(
            '(imgs, before) => imgs.some(x => x.complete && x.naturalWidth > 32 && !before.includes(x.currentSrc || x.src))', before)
        if saved: return
        await asyncio.sleep(.5)
    raise ValueError('资源包封面预览未更新，停止投稿')


async def youtube_fields(page, material, cover, flow):
    await (await unique(page.locator('#title-textarea #textbox'), 'YouTube 标题')).fill(material['title'])
    await (await unique(page.locator('#description-textarea #textbox'), 'YouTube 简介')).fill(material['description'])
    await flow.emit('cover')
    await upload_cover(await unique(page.locator('ytcp-thumbnail-uploader'), 'YouTube 封面区域'), cover)
    kids = 'VIDEO_MADE_FOR_KIDS_MFK' if material['made_for_kids'] else 'VIDEO_MADE_FOR_KIDS_NOT_MFK'
    await click(page.locator(f'tp-yt-paper-radio-button[name="{kids}"]'), 'YouTube 受众选项')
    if material.get('tags') or material.get('ai_declaration'):
        if not await page.locator('#tags-container input').is_visible():
            await click(page.locator('#toggle-button'), '显示更多')
    if material.get('tags'):
        field = await unique(page.locator('#tags-container input'), 'YouTube 标签')
        await field.fill(','.join(material['tags']))
        await field.press('Enter')
    if material.get('ai_declaration'):
        heading = await unique(page.get_by_text(re.compile(r'^(Altered content|变造的内容|经过修改的内容|修改的内容)$')), 'AI 内容声明')
        section = heading.locator('xpath=ancestor::*[.//tp-yt-paper-radio-button or .//*[@role="radio"]][1]')
        await click(section.get_by_role('radio', name=re.compile(r'^(Yes|是)$')), 'AI 内容声明：是')
    # Studio has Details / Elements / Checks / Visibility. Never click the final
    # button while advancing these intermediate steps.
    for _ in range(4):
        public = page.locator('tp-yt-paper-radio-button[name="PUBLIC"]')
        if await public.count() == 1 and await public.is_visible(): break
        await click(page.locator('#next-button'), '下一步')
        await asyncio.sleep(.5)
    await click(page.locator(f'tp-yt-paper-radio-button[name="{material["visibility"].upper()}"]'), 'YouTube 可见性')


async def toutiao_fields(page, material, cover, flow):
    notice = page.get_by_text('我知道了', exact=True).filter(visible=True)
    if await notice.count() == 1:
        await notice.click()
    await (await unique(page.locator('input.xigua-input[placeholder="请输入 1～30 个字符"], input[placeholder*="标题"], textarea[placeholder*="标题"]'), '头条视频标题')).fill(material['title'])
    description = material['description']
    missing_tags = [tag for tag in material.get('tags', []) if '#'+tag.lstrip('#') not in description]
    if missing_tags:
        description += '\n' + ' '.join('#'+tag.lstrip('#')+'#' for tag in missing_tags)
    await (await unique(page.locator('textarea[placeholder*="简介"], [contenteditable="true"][data-placeholder*="简介"]'), '头条视频简介')).fill(description)
    await flow.emit('cover')
    button = page.get_by_text(re.compile(r'^(上传封面|设置封面|修改封面)$'))
    if await button.filter(visible=True).count(): await click(button, '设置封面')
    local = page.get_by_text('本地上传', exact=True).filter(visible=True)
    if await local.count() == 1:
        await local.click()
    image = await unique(page.locator('input[type="file"][accept*="image"]'), '头条封面上传控件', attached=True)
    container = page.locator('.m-xigua-dialog.m-poster-upgrade:visible')
    await upload_cover(await unique(container, '头条封面区域'), cover)
    dialog = await unique(container, '封面编辑窗口')
    for _ in range(5):
        finish = page.locator('.m-xigua-dialog.m-dialog-edit:visible').filter(has_text='完成后无法继续编辑')
        if await finish.count() == 1:
            await click(finish.get_by_text('确定', exact=True), '确认保存封面')
            await finish.wait_for(state='hidden', timeout=15000)
            await dialog.wait_for(state='hidden', timeout=20000)
            break
        if not await dialog.is_visible(): break
        crop = dialog.get_by_text('完成裁剪', exact=True).filter(visible=True)
        action = crop if await crop.count() else dialog.get_by_text(re.compile(r'^(下一步|确定|确认|完成|保存封面)$')).filter(visible=True)
        await click(action, '保存封面步骤')
        await asyncio.sleep(1)
    await dialog.wait_for(state='hidden', timeout=15000)
    saved = page.locator('.cover:not(.empty)')
    await saved.wait_for(state='visible', timeout=15000)
    if not await saved.evaluate('e => e.tagName === "IMG" ? e.complete && e.naturalWidth > 0 : !!e.querySelector("img") || getComputedStyle(e).backgroundImage !== "none"'):
        raise ValueError('头条封面保存后未出现图片预览，停止投稿')
    from publishing.diagnostics import capture
    await flow.emit('cover', '头条自定义封面已保存并核对预览',
                    cover_screenshot=await capture(page, 'toutiao', 'cover-saved'))
    if material.get('ai_declaration'):
        await flow.emit('metadata', '填写头条 AI 内容声明')
        declaration = await unique(page.get_by_text(re.compile(r'^(AI生成|内容由AI生成|内容由 AI 生成|含AI生成内容|使用AI工具生成)$')), '头条 AI 内容声明')
        # The fixed publish bar can cover the auto-scrolled checkbox in a short viewport.
        await declaration.evaluate('e => e.scrollIntoView({block:"center"})')
        await click(declaration, '头条 AI 内容声明')


async def wait_upload(page, platform, flow, timeout=1800):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if platform == 'toutiao' and await page.get_by_text('上传成功', exact=True).filter(visible=True).count() == 1:
            return
        area = page.locator('ytcp-video-upload-progress') if platform == 'youtube' else page.locator('[class*="upload-progress"]')
        texts = await area.all_text_contents()
        text = ' '.join(texts)
        percent = re.findall(r'(?<!\d)(\d{1,3})\s*%', text)
        if len(percent) == 1 and int(percent[0]) <= 100:
            await flow.emit('uploading', media_percent=int(percent[0]))
        if re.search(r'上传完成|已上传|Upload complete|Upload finished', text, re.I): return
        await asyncio.sleep(2)
    raise ValueError('等待平台确认视频上传完成超时，停止提交')


def receipt_from_links(platform, links):
    matches = {}
    for href in links:
        parsed = urlsplit(href or '')
        if parsed.scheme != 'https': continue
        ident = None
        if platform == 'youtube':
            if parsed.hostname == 'youtu.be': ident = parsed.path.strip('/')
            elif parsed.hostname in ('www.youtube.com', 'youtube.com') and parsed.path == '/watch':
                ident = parse_qs(parsed.query).get('v', [None])[0]
            if not ident or not re.fullmatch(r'[A-Za-z0-9_-]{11}', ident): continue
        elif parsed.hostname in ('www.toutiao.com', 'toutiao.com'):
            match = re.fullmatch(r'/(?:video|item)/(\d+)/?', parsed.path)
            if match: ident = match[1]
        if ident: matches[ident] = href
    if len(matches) != 1: return {'status': 'unknown'}
    ident, url = next(iter(matches.items()))
    return {'status': 'found', 'remote_id': ident, 'url': url, 'source': 'official_publish_receipt',
            'note': '平台提交后的结果窗口返回作品链接；尚未验证公开播放或审核状态。'}


async def submission_receipt(page, platform):
    if platform == 'youtube':
        area = await unique(page.locator('ytcp-video-share-dialog'), '发布结果窗口', timeout=30)
    else:
        # Require a submit-success message before accepting a unique work link.
        await unique(page.get_by_text(re.compile(r'^(发布成功|提交成功|已提交审核)$')), '头条提交结果', timeout=30)
        # A content-list link might belong to an older work. Accept links only
        # from an explicit success dialog; otherwise keep the result unknown.
        area = page.get_by_role('dialog').filter(visible=True)
        if await area.count() != 1:
            return {'status': 'unknown'}
    return receipt_from_links(platform, await area.locator('a[href]').evaluate_all('(xs) => xs.map(x => x.href)'))


async def publish(platform, video, cover, material, cookie, callback=None):
    flow = PublicationProgress(callback)
    browser = None
    try:
        await flow.emit('checking')
        validate_material(platform, material)
        if not all(Path(x).is_file() for x in (video, cover, cookie)):
            raise flow.failure('视频、封面或登录文件缺失', 'material_invalid')
        if platform == 'toutiao':
            cover = prepare_toutiao_cover(cover)
        state, channel = storage(cookie)
        async with async_playwright() as p:
            browser = await p.chromium.launch(**browser_options(platform))
            try:
                context = await browser.new_context(storage_state=state, locale='zh-CN', viewport={'width':1440,'height':1000})
                page = await context.new_page()
                await flow.emit('opening')
                url = ENTRY[platform] + (f'channel/{channel}' if platform == 'youtube' and channel else '')
                await page.goto(url, wait_until='domcontentloaded')
                for _ in range(20):
                    if await authenticated(platform, page): break
                    await asyncio.sleep(.5)
                else: raise flow.failure('登录失效或平台需要本人验证，请在账号管理重新登录', 'login_check_failed')
                if platform == 'youtube':
                    if not matches_channel(page, channel):
                        raise ValueError('当前 YouTube 频道与保存的账号不一致，请重新登录目标频道')
                    # Open creation inside the authenticated Studio channel so a
                    # brand channel is not silently replaced by the default one.
                    await click(page.locator('#create-icon'), 'YouTube 创建')
                    await click(page.get_by_text(re.compile(r'^(上传视频|Upload videos)$')), '上传视频')
                else:
                    entry = page.locator('a[href="/profile_v4/xigua/upload-video"]')
                    await click(entry, '发布视频入口')
                    dismiss = page.get_by_role('button', name='暂不开通', exact=True).filter(visible=True)
                    if await dismiss.count() == 1:
                        await dismiss.click()
                video_input = await unique(page.locator('input[type="file"][accept*="video"]'), '视频上传控件', attached=True)
                flow.upload_started = True
                await flow.emit('uploading')
                await video_input.set_input_files(str(video))
                if platform == 'toutiao':
                    await wait_upload(page, platform, flow)
                await flow.emit('metadata')
                if platform == 'youtube': await youtube_fields(page, material, cover, flow)
                else: await toutiao_fields(page, material, cover, flow)
                await wait_upload(page, platform, flow)
                button = page.locator('#done-button') if platform == 'youtube' else page.get_by_role('button', name='发布', exact=True)
                button = await unique(button, '最终发布按钮')
                if not await button.is_enabled() or await button.get_attribute('aria-disabled') == 'true':
                    raise ValueError('最终发布按钮尚不可用，请检查必填项或平台验证')
                flow.submission_started = True
                await flow.emit('submitting')
                await button.click()  # Exactly one final publish attempt.
                await flow.emit('verifying')
                try:
                    receipt = await submission_receipt(page, platform)
                except ValueError:
                    if platform != 'toutiao':
                        raise
                    receipt = {'status': 'unknown'}
                if receipt.get('status') == 'found' or platform != 'toutiao':
                    return receipt
                from publishing.browser_readback import scan_context
                from datetime import datetime, timezone, timedelta
                return await scan_context(context, platform, material['title'], material['description'],
                                          datetime.now(timezone(timedelta(hours=8))).date().isoformat())
            except Exception as exc:
                from publishing.diagnostics import capture
                screenshot = await capture(page, platform, flow.stage)
                if callback:
                    callback({'stage': flow.stage, 'screenshot': screenshot,
                              'error_type': type(exc).__name__,
                              'error_detail': re.sub(r'https?://[^\s<>"\x27]+', '[url]', str(exc))[:1200],
                              'submission_started': flow.submission_started})
                raise
            finally:
                try:
                    await browser.close()
                except Exception:
                    import logging
                    logging.getLogger(__name__).warning('投稿浏览器已断开，保留原始结果')
    except SubmissionNotStarted:
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else '平台页面未完成当前步骤，请检查页面或重新登录'
        raise flow.failure(message) from None


def prepare_toutiao_cover(cover):
    """Fit the full approved cover into 16:9; never crop away the title."""
    import hashlib
    from PIL import Image, ImageOps
    from conf import BASE_DIR
    cover = Path(cover)
    with Image.open(cover) as image:
        if image.width * 9 == image.height * 16:
            return cover
        digest = hashlib.sha256(cover.read_bytes()).hexdigest()[:24]
        target = Path(BASE_DIR) / 'Temp' / 'platform-covers' / f'toutiao-{digest}.jpg'
        target.parent.mkdir(parents=True, exist_ok=True)
        canvas = Image.new('RGB', (1920, 1080), '#101c2c')
        fitted = ImageOps.contain(ImageOps.exif_transpose(image).convert('RGB'), canvas.size, Image.Resampling.LANCZOS)
        canvas.paste(fitted, ((canvas.width-fitted.width)//2, (canvas.height-fitted.height)//2))
        canvas.save(target, quality=95)
    return target
