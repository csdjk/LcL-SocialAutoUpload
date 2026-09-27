"""Xiaohongshu daily submission: custom cover, one final click, creator readback."""
import asyncio
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from uploader.tencent_uploader.flow import PublicationProgress, SubmissionNotStarted
from publishing.diagnostics import capture

ENTRY = 'https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video'
MANAGER = 'https://creator.xiaohongshu.com/new/note-manager'
LIST_PATH = '/api/galaxy/v2/creator/note/user/posted'


def text_and_topics(material):
    description = material.get('description', '').strip()
    tags = material.get('tags', [])
    if not isinstance(tags, list) or any(not isinstance(t, str) or not t.strip() for t in tags):
        raise ValueError('小红书话题格式无效')
    tags = list(dict.fromkeys(t.strip().lstrip('#') for t in tags))
    for tag in tags:
        description = re.sub(r'(?<!\S)#' + re.escape(tag) + r'(?=\s|$)', '', description)
    if len(tags) > 10 or any(not t or re.search(r'[\s#]', t) for t in tags):
        raise ValueError('小红书最多 10 个话题，话题内不能有空格或井号')
    declaration = material.get('ai_declaration', '').strip()
    if declaration and declaration not in description:
        description = description.rstrip() + '\n' + declaration
    return description.strip(), tags


def validate_material(material):
    title = material.get('title')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 20:
        raise ValueError('小红书标题须为 1–20 个字，请编辑平台标题后投稿')
    if not isinstance(material.get('description'), str) or not material['description'].strip():
        raise ValueError('小红书简介不能为空')
    description, tags = text_and_topics(material)
    if len(description) + sum(len(t) + 2 for t in tags) > 1000:
        raise ValueError('小红书正文、话题及 AI 声明合计不能超过 1000 字')
    if material.get('cover_mode', 'custom') != 'custom':
        raise ValueError('小红书须使用资源包自定义封面')


def records(payload, title, day, remote_id=None):
    if not isinstance(payload, dict) or payload.get('success') is not True or payload.get('code') != 0:
        return []
    output = []
    for note in payload.get('data', {}).get('notes', []):
        ident = str(note.get('id', ''))
        if not re.fullmatch(r'[0-9a-f]{24}', ident):
            continue
        if remote_id:
            if ident != remote_id:
                continue
        elif note.get('display_title') != title or not str(note.get('time', '')).startswith(day):
            continue
        state = 'published' if note.get('tab_status') == 1 and note.get('permission_code') == 0 else 'processing'
        output.append({'status': 'found', 'remote_id': ident, 'url': f'https://www.xiaohongshu.com/explore/{ident}',
                       'state': state, 'created_at': note.get('time'), 'source': 'official_content_list',
                       'platform_status': {'tab_status': note.get('tab_status'), 'permission_code': note.get('permission_code'),
                                           'permission_msg': note.get('permission_msg')},
                       'note': '原账号官方笔记列表已找到作品，状态取自平台回执。'})
    return output


def list_outcome(payload, title, day, remote_id=None):
    found = records(payload, title, day, remote_id)
    if len(found) == 1:
        return found[0]
    data = payload.get('data', {}) if isinstance(payload, dict) else {}
    if not found and not remote_id and payload.get('success') is True and payload.get('code') == 0 and data.get('page') == -1 and isinstance(data.get('notes'), list) and any(t.get('name') == '所有笔记' and t.get('checked') is True and t.get('notes_count') == len(data['notes']) for t in data.get('tags', [])):
        # The server total must equal the full returned list; filtered/paginated lists never prove absence.
        return {'status': 'absent', 'remote_id': None, 'state': 'failed', 'url': None, 'remote_absent': True,
                'note': '原账号完整默认笔记列表没有同日同标题作品。'}
    return {'status': 'unknown', 'message': '未找到唯一作品或列表不完整，保留防重复投稿保护'}


async def scan_context(context, material, day, remote_id=None):
    page = await context.new_page()
    try:
        async with page.expect_response(lambda r: urlsplit(r.url).hostname == 'creator.xiaohongshu.com' and urlsplit(r.url).path == LIST_PATH, timeout=25000) as response:
            await page.goto(MANAGER, wait_until='domcontentloaded')
        from myUtils.browser_login import inspect_page
        if await inspect_page(1, page) != 'valid':
            return {'status': 'needs_login', 'message': '小红书登录已失效，请重新登录'}
        result = list_outcome(await (await response.value).json(), material['title'], day, remote_id)
        result['screenshot'] = await capture(page, 'xiaohongshu', 'readback')
        return result
    finally:
        await page.close()


async def readback(cookie, material, day, remote_id=None):
    raw = json.loads(Path(cookie).read_text(encoding='utf8'))
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        try:
            context = await browser.new_context(storage_state={k: v for k, v in raw.items() if k in ('cookies', 'origins')})
            return await scan_context(context, material, day, remote_id)
        finally:
            await browser.close()

async def fill_metadata(page, material):
    description, tags = text_and_topics(material)
    title = page.get_by_placeholder('填写标题会有更多赞哦')
    await title.fill(material['title'].strip())
    editor = page.locator('.tiptap[contenteditable=true]')
    await editor.fill(description)
    for index, tag in enumerate(tags):
        await editor.press('Control+End')
        await editor.press('Enter') if index == 0 else await editor.press('Space')
        await editor.press_sequentially('#' + tag, delay=80)
        choices = page.locator('.items .item').filter(has=page.locator('.name').filter(has_text=re.compile('^#' + re.escape(tag) + '$', re.I)))
        await choices.first.wait_for(timeout=10000)
        existing = choices.filter(has_not_text='新建话题')
        await (existing.first if await existing.count() else choices.first).click()
        await page.wait_for_function('(n)=>document.querySelectorAll(".tiptap a.tiptap-topic").length===n', arg=index + 1)
    if material.get('ai_declaration'):
        if await page.get_by_text('添加内容类型声明', exact=True).count():
            await page.get_by_text('添加内容类型声明', exact=True).click()
            await page.get_by_text('笔记含AI合成内容', exact=True).click()
        await page.locator('.d-select-description').filter(has_text='笔记含AI合成内容').wait_for()
    if await title.input_value() != material['title'].strip():
        raise ValueError('小红书标题回读不一致')
    actual = await editor.locator('a.tiptap-topic').evaluate_all('(xs)=>xs.map(x=>JSON.parse(x.dataset.topic).name.toLowerCase())')
    if actual != [t.lower() for t in tags]:
        raise ValueError('小红书已选话题与草稿不一致')


async def set_cover(page, cover, progress):
    preview = page.locator('.cover-plugin-preview .cover > .default')
    old = await preview.evaluate('(e)=>e.style.backgroundImage')
    await page.locator('.cover-plugin-preview[aria-busy="false"]').wait_for()
    # Upload completion can autofocus the title and scroll this preview away.
    # Retry only opening the editor, before any final submission boundary.
    for attempt in range(3):
        await preview.scroll_into_view_if_needed()
        await preview.hover(position={'x': 20, 'y': 20})
        try:
            await page.get_by_text('编辑封面', exact=True).click(timeout=2000)
            break
        except PlaywrightTimeoutError:
            if attempt == 2:
                raise
            await asyncio.sleep(.3)
    await page.locator('input[type=file][accept*="image"]').set_input_files(str(cover))
    uploaded = page.locator('.uploaded-thumbnail')
    await uploaded.wait_for(timeout=30000)
    await uploaded.click()
    await page.get_by_text('裁剪', exact=True).click()
    await page.get_by_text('3:4', exact=True).click()
    await page.get_by_text('完成', exact=True).click()
    await page.get_by_text('完成', exact=True).wait_for(state='hidden')
    await page.wait_for_function('''(old)=>{
        const e=document.querySelector('.cover-plugin-preview .cover > .default');
        const r=e?.getBoundingClientRect();
        return e&&e.style.backgroundImage!==old&&e.style.backgroundImage!=='none'&&r&&Math.abs(r.width/r.height-.75)<.02;
    }''', arg=old, timeout=30000)
    await page.mouse.move(0, 0)
    similarity = await compare_cover(page, cover)
    screenshot = await capture(page, 'xiaohongshu', 'custom-cover')
    await progress.emit('cover', '已保存 3:4 自定义封面', cover_sha256=hashlib.sha256(Path(cover).read_bytes()).hexdigest(), cover_screenshot=screenshot, cover_similarity=similarity)


def walk_nodes(node):
    yield node
    for key in ('children', 'shadowRoots'):
        for child in node.get(key, []):
            yield from walk_nodes(child)


async def submit_once(page, progress):
    # The site's publish button lives in a closed custom-element shadow root.
    # Read the real button's label and box through CDP, then send one normal click.
    host = page.locator('xhs-publish-btn')
    await host.scroll_into_view_if_needed()
    if await host.get_attribute('submit-disabled') != 'false':
        raise ValueError('小红书发布按钮尚未就绪')
    cdp = await page.context.new_cdp_session(page)
    try:
        tree = await cdp.send('DOM.getDocument', {'depth': -1, 'pierce': True})
        hosts = [n for n in walk_nodes(tree['root']) if n.get('nodeName') == 'XHS-PUBLISH-BTN']
        if len(hosts) != 1:
            raise ValueError('无法唯一定位小红书发布组件')
        buttons = [n for n in walk_nodes(hosts[0]) if n.get('nodeName') == 'BUTTON' and
                   ''.join(x.get('nodeValue', '') for x in walk_nodes(n)).strip() == '发布']
        if len(buttons) != 1:
            raise ValueError('无法唯一定位小红书发布按钮')
        button = buttons[0]
        attrs = dict(zip(button.get('attributes', [])[::2], button.get('attributes', [])[1::2]))
        if 'disabled' in attrs or attrs.get('aria-disabled') == 'true':
            raise ValueError('小红书发布按钮不可用')
        quad = (await cdp.send('DOM.getBoxModel', {'nodeId': button['nodeId']}))['model']['content']
        x, y = sum(quad[::2]) / 4, sum(quad[1::2]) / 4
        if not await host.evaluate('(e,p)=>e===document.elementFromPoint(p.x,p.y)', {'x': x, 'y': y}):
            raise ValueError('小红书发布按钮被弹层遮挡')
        progress.submission_started = True
        await progress.emit('submitting', '正在提交小红书笔记；结果不明时不会重复点击')
        await page.mouse.click(x, y)
    finally:
        await cdp.detach()


async def publish(video, cover, material, cookie, progress):
    validate_material(material)
    from PIL import Image
    with Image.open(cover) as image:
        if abs(image.width / image.height - .75) > .01:
            raise SubmissionNotStarted('小红书封面须为 3:4 图片', code='cover_invalid')
    raw = json.loads(Path(cookie).read_text(encoding='utf8'))
    flow = PublicationProgress(progress)
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel='msedge', headless=True)
        page = None
        try:
            context = await browser.new_context(storage_state={k:v for k,v in raw.items() if k in ('cookies','origins')}, viewport={'width':1440,'height':1000})
            await flow.emit('deduplicating')
            previous = await scan_context(context, material, day)
            if previous['status'] == 'found':
                return {**previous, 'existing': True}
            if previous['status'] != 'absent':
                raise flow.failure('无法确认小红书后台无重复作品，请先同步核对', 'readback_unavailable')
            page = await context.new_page()
            await flow.emit('opening')
            await page.goto(ENTRY, wait_until='domcontentloaded')
            await page.locator('input[type=file]').first.wait_for(state='attached')
            flow.upload_started = True
            await flow.emit('uploading')
            await page.locator('input[type=file]').first.set_input_files(str(video))
            await page.get_by_placeholder('填写标题会有更多赞哦').wait_for(timeout=180000)
            await flow.emit('metadata')
            await fill_metadata(page, material)
            await flow.emit('cover')
            await set_cover(page, cover, flow)
            shot = await capture(page, 'xiaohongshu', 'before-submit')
            await flow.emit('metadata', '文案、话题、AI 声明和封面已核对', screenshot=shot)
            await submit_once(page, flow)
            await flow.emit('verifying')
            await asyncio.sleep(5)
            result = await scan_context(context, material, day)
            # Content-list propagation can lag: never turn immediate absence into retryable failure.
            if result['status'] != 'found':
                return {'status': 'unknown', 'message': '已点击发布，作品列表暂未返回唯一作品，请稍后同步；不会重复投稿'}
            return result
        except Exception as exc:
            if page:
                shot = await capture(page, 'xiaohongshu', flow.stage + '-error')
                await flow.emit(message=str(exc), screenshot=shot)
            if not flow.submission_started:
                if isinstance(exc, SubmissionNotStarted):
                    raise
                raise flow.failure(str(exc)) from exc
            raise
        finally:
            await browser.close()
async def compare_cover(page, cover):
    import io
    from PIL import Image, ImageChops, ImageStat
    value = await page.locator('.cover-plugin-preview .cover > .default').evaluate('(e)=>e.style.backgroundImage')
    match = re.fullmatch(r'url\([\"\']?(.*?)[\"\']?\)', value)
    if not match or urlsplit(match[1]).scheme != 'https' or not urlsplit(match[1]).hostname.endswith('.xhscdn.com'):
        raise ValueError('小红书封面预览来源无法核验')
    response = await page.context.request.get(match[1])
    if not response.ok:
        raise ValueError('小红书封面预览读取失败')
    with Image.open(io.BytesIO(await response.body())) as actual, Image.open(cover) as expected:
        if abs(actual.width / actual.height - .75) > .02:
            raise ValueError('小红书保存的封面比例不是 3:4')
        size = (192, 256)
        error = sum(ImageStat.Stat(ImageChops.difference(actual.convert('RGB').resize(size), expected.convert('RGB').resize(size))).mean) / (3 * 255)
    if error > .06:
        raise ValueError(f'小红书封面与所选图片不一致（像素平均误差 {error:.3f}）')
    return round(1 - error, 4)
