"""Read account display names from authenticated creator pages, never from input."""
import asyncio
from urllib.parse import urlsplit


class AccountNameError(ValueError):
    pass


def account_name(value):
    if not isinstance(value, str):
        raise AccountNameError('未读取到平台账号昵称，账号未保存。请在平台创作者后台确认账号信息后重试')
    value = value.strip()
    if not value or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise AccountNameError('平台账号昵称为空或格式异常，账号未保存，请在平台后台确认后重试')
    return value


# Restrict reads to account-name elements, not arbitrary headings or video titles.
NAME_SELECTORS = {
    1: ('creator.xiaohongshu.com', '.user-info .name-box, .user-name, .user-info .name, .account-name'),
    2: ('channels.weixin.qq.com', '.finder-nickname, .finder-name, .account-name, .user-info .nickname'),
    3: ('creator.douyin.com', '[class*="creator-name"], [class*="account-name"], [class*="user-name"], [class*="userName"], .user-info .nickname'),
    4: ('cp.kuaishou.com', '.user-info-name, .user-name, .user-info .name, [class*="userName"], [class*="user-name"]'),
    6: ('studio.youtube.com', 'ytcp-navigation-drawer #channel-title, ytcp-navigation-drawer .channel-name, ytcp-navigation-drawer .entity-name'),
    7: ('mp.toutiao.com', '.auth-avator-name, .auth-avatar-name, .user-name, .user-info .name, [class*="userName"]'),
}


async def read_account_name(platform, page, *, timeout=5):
    """Caller must verify authentication first; ambiguous/missing names fail closed."""
    if platform == 5:
        from uploader.bilibili_uploader.web_login import _nav
        user = await _nav(page.context.request)
        return account_name((user or {}).get('uname'))
    host, selector = NAME_SELECTORS[platform]
    parsed = urlsplit(page.url)
    if parsed.scheme != 'https' or parsed.hostname != host:
        raise AccountNameError('账号信息页面不属于所选平台，账号未保存')
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        names = set()
        for element in await page.locator(selector).all():
            if await element.is_visible():
                text = (await element.inner_text()).strip()
                if text:
                    names.add(account_name(text))
        if len(names) == 1:
            return names.pop()
        if len(names) > 1:
            raise AccountNameError('读取到多个不同的账号昵称，账号未保存，请确认当前平台账号后重试')
        if asyncio.get_running_loop().time() >= deadline:
            return account_name(None)
        await asyncio.sleep(.25)
