"""Verify saved platform sessions and read account names for browser/QR login."""
import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import async_playwright
from myUtils.browser_login import PLATFORMS, inspect_page, scoped_state
from utils.account_identity import read_account_name, AccountNameError


async def validate_platform_credentials(platform, account_file, *, timeout=40, require_name=False):
    """Read the creator page; never publish or answer platform security checks."""
    name = PLATFORMS[platform]['name']
    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel='msedge', headless=True, timeout=15000)
            try:
                context = await browser.new_context(storage_state=str(account_file), locale='zh-CN')
                page = await context.new_page()
                await page.goto(PLATFORMS[platform]['url'], wait_until='domcontentloaded', timeout=20000)
                consecutive = 0
                for _ in range(30):
                    status = await inspect_page(platform, page)
                    if status == 'verification_required':
                        return {'success': False, 'message': f'{name}要求安全验证，请使用浏览器登录并完成平台提示的验证'}
                    consecutive = consecutive + 1 if status == 'valid' else 0
                    if consecutive >= 2:
                        nickname = await read_account_name(platform, page) if require_name else None
                        state = scoped_state(platform, await context.storage_state(indexed_db=True))
                        if platform == 6:
                            # Preserve the uploader's explicit channel binding contract.
                            state['publisher_channel_id'] = re.search(r'/channel/(UC[A-Za-z0-9_-]+)', page.url)[1]
                        Path(account_file).write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
                        return {'success': True, 'account_name': nickname}
                    await asyncio.sleep(.4)
                return {'success': False, 'message': f'未能确认{name}创作者后台已登录，账号未保存，请使用浏览器登录完成验证'}
            finally:
                await browser.close()
    try:
        return await asyncio.wait_for(run(), timeout)
    except AccountNameError as exc:
        return {'success': False, 'message': str(exc)}
    except asyncio.TimeoutError:
        return {'success': False, 'message': f'{name}验证超时，账号未保存，请检查网络后重试'}
    except Exception:
        return {'success': False, 'message': f'无法验证{name}登录态，账号未保存，请确认 Edge 和平台后台可正常访问'}
