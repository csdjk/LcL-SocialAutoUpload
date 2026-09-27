"""Visible, scoped controls for the two independent Channels cover editors.
No final-publish action, profile access or hidden-element clicks belong here.
"""
import asyncio
import re
import time

DIALOGS = 'div.weui-desktop-dialog, [role="dialog"], div.common-dialog'


class CoverControlError(RuntimeError):
    """Fixed, user-safe stage error; never include a locator dump or image URL."""


class CoverControls:
    def __init__(self, page, *, clock=time):
        self.page = page
        self.clock = clock

    async def scopes(self):
        from .workflow import scopes
        result = []
        for scope in scopes(self.page):
            # Wujie renders an iframe's DOM into the main page's shadow root.
            # Its hidden iframe is a second view of the SAME editor, not another
            # visible dialog. Only operate on rendered surfaces.
            if hasattr(scope, 'frame_element'):
                frame = await scope.frame_element()
                if not await frame.is_visible():
                    continue
            result.append(scope)
        return result

    async def find_dialog(self, titles, *, crop=False):
        candidates = []
        for scope in await self.scopes():
            roots = scope.locator(DIALOGS).filter(visible=True)
            indices = await roots.evaluate_all('''(roots, args) => {
                const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
                return roots.map((root, index) => {
                    if (!visible(root)) return null;
                    // Crop dialogs can be nested inside the main editor. Parent
                    // textContent must NOT make it a second crop-dialog match.
                    const copy = root.cloneNode(true);
                    copy.querySelectorAll(args.selector).forEach(e => e.remove());
                    const text = copy.textContent || '';
                    return args.titles.some(t => text.includes(t)) ? index : null;
                }).filter(index => index !== null);
            }''', {'selector': DIALOGS, 'titles': titles})
            for index in indices:
                candidates.append(roots.nth(index))
        if len(candidates) > 1:
            raise CoverControlError('出现多个可见封面弹窗，已停止，避免操作错误窗口')
        return candidates[0] if candidates else None

    async def find_cover(self, selectors):
        for scope in await self.scopes():
            for selector in selectors:
                entries = scope.locator(selector + ':visible')
                if await entries.count() == 1:
                    return entries
        return None

    async def recommendation_action(self):
        # The official cover component may first ask “使用此素材作为封面？”.
        # Choose “直接编辑”, not the recommended (possibly opposite-ratio) image.
        for scope in await self.scopes():
            for selector in (
                '.img-recommend-wrap:visible .btn-directly-edit:visible',
                '.img-recommend-wrap:visible button:visible',
                '.ant-popover:visible button:visible',
                '.weui-desktop-popover:visible button:visible',
            ):
                choices = scope.locator(selector).filter(has_text=re.compile(
                    r'^\s*(直接编辑|上传封面|本地上传|自定义封面|从本地选择)\s*$'))
                if await choices.count() > 1:
                    raise CoverControlError('封面推荐层有多个编辑入口，未自动选择素材')
                if await choices.count() == 1:
                    return choices
        return None

    async def open_editor(self, selectors, titles, *, timeout=18):
        deadline = self.clock.monotonic() + timeout
        clicked = False
        recommendation_used = False
        while self.clock.monotonic() < deadline:
            if clicked:
                dialog = await self.find_dialog(titles)
                if dialog is not None:
                    return dialog
                if not recommendation_used:
                    choice = await self.recommendation_action()
                    if choice is not None:
                        try:
                            await choice.click(timeout=5000)
                        except Exception:
                            raise CoverControlError('封面推荐层的“直接编辑”未能打开，请检查页面遮挡') from None
                        recommendation_used = True
            else:
                entry = await self.find_cover(selectors)
                if entry is not None:
                    try:
                        await entry.scroll_into_view_if_needed(timeout=4000)
                        await entry.hover(timeout=4000)
                        # Hover can open the recommendation layer OVER the card.
                        # Handle its explicit edit action before clicking underneath.
                        choice = await self.recommendation_action()
                        if choice is not None:
                            await choice.click(timeout=5000)
                            recommendation_used = True
                            clicked = True
                            continue
                        controls = entry.locator('.edit-btn:visible, .cover-edit:visible').filter(
                            has_text=re.compile(r'编辑|修改|设置|更换'))
                        if await controls.count() == 0:
                            controls = entry.get_by_role('button', name=re.compile(r'编辑|修改|设置|更换'))
                        if await controls.count() > 1:
                            raise CoverControlError('封面区域内存在多个编辑按钮，未进行点击')
                        # Current Channels cards handle clicks on the image wrap.
                        # Their decorative edit label can lie under the image and
                        # fails hit-testing; use the actual visible card surface.
                        surface = entry.locator('.horizon-img-wrap:visible, .vertical-img-wrap:visible')
                        target = surface if await surface.count() == 1 else controls if await controls.count() == 1 else entry
                        clicked = True
                        await target.click(timeout=5000)
                    except CoverControlError:
                        raise
                    except Exception:
                        # The popover may be mounted asynchronously AFTER hover
                        # or while Playwright checks click actionability.
                        dialog = await self.find_dialog(titles)
                        if dialog is not None:
                            return dialog
                        choice = await self.recommendation_action()
                        if choice is None or recommendation_used:
                            raise CoverControlError('封面编辑入口被遮挡或不可操作，尚未提交投稿') from None
                        await choice.click(timeout=5000)
                        recommendation_used = True
                        clicked = True
            await asyncio.sleep(.15)
        raise CoverControlError('自定义封面编辑器未就绪，未改变封面模式，尚未提交投稿')

    async def image_input(self, dialog, *, timeout=12):
        deadline = self.clock.monotonic() + timeout
        switched = False
        while self.clock.monotonic() < deadline:
            inputs = dialog.locator('input[type="file"]')
            indices = await inputs.evaluate_all('''inputs => inputs.map((input,index) => {
                const accept = (input.getAttribute('accept') || '').toLowerCase();
                if (accept && !/image|png|jpe?g|webp|gif|bmp/.test(accept)) return null;
                // The input itself is intentionally display:none. Its upload
                // panel, unlike a cached inactive tab, must be visible.
                const panel = input.closest('.single-cover-uploader-wrap') || input.parentElement;
                if (!panel || !panel.getClientRects().length || getComputedStyle(panel).visibility === 'hidden') return null;
                return index;
            }).filter(index => index !== null)''')
            if len(indices) > 1:
                raise CoverControlError('封面弹窗内未找到唯一的图片选择控件')
            if len(indices) == 1:
                return inputs.nth(indices[0])
            if not switched:
                for selector in ('[role="tab"]:visible', '[class*="tab"]:visible', 'button:visible', 'a:visible'):
                    tabs = dialog.locator(selector).filter(has_text=re.compile(
                        r'^\s*(上传封面|本地上传|自定义封面|上传图片|本地图片|从本地选择)\s*$'))
                    if await tabs.count() == 1:
                        await tabs.click(timeout=5000)
                        switched = True
                        break
            await asyncio.sleep(.15)
        raise CoverControlError('封面弹窗内未找到唯一的图片选择控件，请检查本地上传选项')

    async def confirm_button(self, dialog, *, crop=False):
        # Exclude buttons in a nested crop editor from the main-editor footer.
        buttons = dialog.locator('button')
        valid = await dialog.evaluate('''(root, arg) => [...root.querySelectorAll('button')].map((button,index) => {
            if (!button.getClientRects().length || getComputedStyle(button).visibility === 'hidden') return null;
            const text = (button.textContent || '').trim();
            if (!(arg.crop ? /^(确定|确认|完成)$/ : /^(确定|确认|完成|保存)$/).test(text)) return null;
            if (button.closest(arg.selector) !== root) return null;
            return index;
        }).filter(index => index !== null)''', {'crop': crop, 'selector': DIALOGS})
        if len(valid) > 1:
            raise CoverControlError('封面编辑器出现多个确认按钮，已停止')
        return buttons.nth(valid[0]) if valid else None

    async def confirm_crop(self, *, timeout=12):
        dialog = await self.find_dialog(['裁剪封面图', '裁剪封面', '裁剪图片'], crop=True)
        if dialog is None:
            return False
        deadline = self.clock.monotonic() + timeout
        while self.clock.monotonic() < deadline:
            button = await self.confirm_button(dialog, crop=True)
            if button is not None and await button.is_enabled():
                klass = await button.get_attribute('class') or ''
                if 'disabled' not in klass:
                    try:
                        await button.click(timeout=8000)
                        await dialog.wait_for(state='hidden', timeout=12000)
                    except Exception:
                        raise CoverControlError('裁剪确认未完成，尚未保存该封面') from None
                    return True
            await asyncio.sleep(.15)
        raise CoverControlError('封面裁剪确认按钮未就绪，尚未保存该封面')

    async def busy(self, dialog):
        return await dialog.locator(
            '[aria-busy="true"]:visible, .weui-desktop-loading:visible, '
            '.uploading-wrap:visible, .uploading-status-wrap:visible, '
            '.primary-loading:visible, .loading-wrap:visible').count() > 0

    async def verify_saved_preview(self, selectors, before, saved, *, timeout=12):
        deadline = self.clock.monotonic() + timeout
        while self.clock.monotonic() < deadline:
            entry = await self.find_cover(selectors)
            if entry is not None:
                after = await preview_state(entry)
                matching = set(after['sources']) & set((saved or {}).get('sources', []))
                if after['sources'] and after['loaded'] and (
                    after['signature'] != before['signature'] or matching
                ):
                    return
            await asyncio.sleep(.2)
        raise CoverControlError('编辑弹窗已关闭，但对应横版或竖版封面预览尚未更新，未提交投稿')


async def preview_state(root):
    return await root.evaluate('''root => {
        const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
        const uploadPanels = [...root.querySelectorAll('.single-cover-uploader-wrap')].filter(visible);
        // Only the selected local image is proof of custom-cover saving. An
        // unrelated video-frame preview elsewhere in the editor is not proof.
        const imageRoot = uploadPanels.length === 1 ? uploadPanels[0] : root;
        const pictures = [...imageRoot.querySelectorAll('img')].filter(visible)
            .map(el => ({src:el.currentSrc || el.src || '',loaded:el.complete && el.naturalWidth>0}));
        const backgrounds = [...root.querySelectorAll('[class*="cover"], [class*="preview"]')]
            .filter(visible).map(el=>getComputedStyle(el).backgroundImage).filter(v=>v&&v!=='none');
        return {signature:JSON.stringify([pictures.map(x=>x.src),backgrounds]),
            loaded:pictures.every(x=>x.loaded),sources:pictures.map(x=>x.src).filter(Boolean)};
    }''')
