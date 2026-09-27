"""Synthetic platform sessions shared by login tests; never real credentials."""
import json

PLATFORM_COOKIES = {
    1: ('xiaohongshu.com', ['web_session']),
    2: ('weixin.qq.com', ['session']),
    3: ('douyin.com', ['sessionid']),
    4: ('kuaishou.com', ['session']),
    5: ('bilibili.com', ['SESSDATA', 'bili_jct']),
    6: ('youtube.com', ['SID']),
    7: ('toutiao.com', ['sessionid']),
}

def fixture_cookies(platform):
    domain, names = PLATFORM_COOKIES[platform]
    return json.dumps([dict(name=name, value='synthetic-fixture-only', domain='.'+domain,
                           path='/', sameSite='no_restriction', expirationDate=4102444800)
                       for name in names])
