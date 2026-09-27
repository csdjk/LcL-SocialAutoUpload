import asyncio
import sqlite3
from contextlib import closing

from playwright.async_api import async_playwright

from myUtils.auth import check_cookie
from uploader.douyin_uploader.main import douyin_cookie_gen as douyin_login_core
from uploader.tencent_uploader.main import tencent_cookie_gen
from utils.base_social_media import set_init_script
import uuid
from pathlib import Path
from conf import BASE_DIR, LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH


async def _automatic_name(platform, name, account_file, status_queue):
    if name.strip():
        return name.strip()
    if platform == 3:
        from utils.douyin_credentials import validate_douyin_credentials
        result = await validate_douyin_credentials(account_file, require_name=True)
    else:
        from utils.platform_credentials import validate_platform_credentials
        result = await validate_platform_credentials(platform, account_file, require_name=True)
    if not result.get('success'):
        status_queue.put({'event': 'login-error', 'data': {'message': result.get('message') or '未读取到平台昵称，账号未保存'}})
        status_queue.put('500')
        return None
    return result['account_name']


def _insert_account(conn, platform, filename, name):
    conn.execute('BEGIN IMMEDIATE')
    if conn.execute('SELECT 1 FROM user_info WHERE type=? AND userName=?', (platform, name)).fetchone():
        raise RuntimeError('已有同名账号，请使用原账号的重新登录')
    conn.execute('INSERT INTO user_info(type,filePath,userName,status) VALUES(?,?,?,1)', (platform, filename, name))
    conn.commit()

# 统一获取浏览器启动配置（防风控+引入本地浏览器）
def get_browser_options():
    options = {
        'headless': LOCAL_CHROME_HEADLESS,
        'args': [
            '--disable-blink-features=AutomationControlled',  # 核心防爬屏蔽：去掉 window.navigator.webdriver 标签
            '--lang=zh-CN',
            '--disable-infobars',
            '--start-maximized'
        ]
    }
    # 如果用户在 conf.py 里配置了本地 Chrome，就用本地的，这样成功率极高
    if LOCAL_CHROME_PATH:
        options['executable_path'] = LOCAL_CHROME_PATH

    return options

# Web 和 CLI 共用抖音扫码登录流程；只有核心确认成功并保存凭据后才记录账号。
async def get_douyin_cookie(id, status_queue, *, interactive=False):
    cookies_dir = Path(BASE_DIR) / "cookiesFile"
    cookies_dir.mkdir(exist_ok=True)
    account_file = cookies_dir / f"{uuid.uuid4()}.json"
    committed = False

    def on_qrcode(payload):
        image = payload.get("image_data_url", "")
        if not image.startswith("data:image/"):
            raise ValueError("抖音登录返回了无效的二维码图片")
        status_queue.put(image)
        status_queue.put({"event": "login-status", "data": {
            "stage": "waiting_scan", "message": "请使用抖音扫码并在手机上确认，最长等待约 2 分钟",
        }})

    def on_status(payload):
        status_queue.put({"event": "login-status", "data": payload})

    try:
        status_queue.put({"event": "login-status", "data": {
            "stage": "loading", "message": "正在打开抖音官方登录窗口…" if interactive else "正在获取抖音登录二维码…",
        }})
        result = await douyin_login_core(
            account_file, qrcode_callback=None if interactive else on_qrcode, status_callback=on_status,
            headless=False if interactive else LOCAL_CHROME_HEADLESS,
            interactive=interactive, max_checks=300 if interactive else 120,
        )
        if not result or not result.get("success"):
            message = (result or {}).get("message") or "抖音登录失败，请重试"
            status = (result or {}).get("status", "failed")
            if status == "verification_required":
                message = "抖音已收到扫码，但还需要额外身份验证（错误码 2046）。请点击下方按钮，在抖音官方窗口按提示完成验证。"
            status_queue.put({"event": "login-error", "data": {"message": message, "status": status}})
            status_queue.put("500")
            return
        if not account_file.is_file():
            raise RuntimeError("登录凭据未保存，账号添加已取消")
        id = await _automatic_name(3, id, account_file, status_queue)
        if id is None:
            return
        cancelled = getattr(status_queue, "cancelled", None)
        if cancelled is not None and cancelled.is_set():
            return
        with closing(sqlite3.connect(Path(BASE_DIR) / "db" / "database.db")) as conn:
            _insert_account(conn, 3, account_file.name, id)
        committed = True
        status_queue.put("200")
    finally:
        if not committed:
            account_file.unlink(missing_ok=True)


# Web 和 CLI 共用视频号登录逻辑，避免旧页面选择器再次失配。
async def get_tencent_cookie(id, status_queue):
    cookies_dir = Path(BASE_DIR) / "cookiesFile"
    cookies_dir.mkdir(exist_ok=True)
    account_file = cookies_dir / f"{uuid.uuid4()}.json"
    committed = False

    def on_qrcode(payload):
        image = payload.get("image_data_url", "")
        if not image.startswith("data:image/"):
            raise ValueError("视频号登录返回了无效的二维码图片")
        status_queue.put(image)
        on_status({"stage": "waiting_scan", "message": "请使用微信扫一扫，扫码后在手机上确认"})

    def on_status(payload):
        status_queue.put({"event": "login-status", "data": payload})

    try:
        on_status({"stage": "loading", "message": "正在获取视频号登录二维码…"})
        result = await tencent_cookie_gen(
            account_file, qrcode_callback=on_qrcode, status_callback=on_status,
            headless=LOCAL_CHROME_HEADLESS,
        )
        if not result or not result.get("success"):
            message = (result or {}).get("message") or "视频号登录失败，请重试"
            status_queue.put({"event": "login-error", "data": {"message": message}})
            status_queue.put("500")
            return
        if not account_file.is_file():
            raise RuntimeError("登录凭据未保存，账号添加已取消")
        id = await _automatic_name(2, id, account_file, status_queue)
        if id is None:
            return
        cancelled = getattr(status_queue, "cancelled", None)
        if cancelled is not None and cancelled.is_set():
            return
        with closing(sqlite3.connect(Path(BASE_DIR) / "db" / "database.db")) as conn:
            _insert_account(conn, 2, account_file.name, id)
        committed = True
        status_queue.put("200")
    finally:
        # A failed/cancelled attempt must not leave credentials or a database row.
        if not committed:
            account_file.unlink(missing_ok=True)


# 快手登录
async def get_ks_cookie(id,status_queue):
    url_changed_event = asyncio.Event()
    async def on_url_change():
        # 检查是否是主框架的变化
        if page.url != original_url:
            url_changed_event.set()
    async with async_playwright() as playwright:
        options = {
            'args': [
                '--lang en-GB'
            ],
            'headless': LOCAL_CHROME_HEADLESS,  # Set headless option here
        }
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://cp.kuaishou.com")

        # 定位并点击“立即登录”按钮（类型为 link）
        await page.get_by_role("link", name="立即登录").click()
        await page.get_by_text("扫码登录").click()
        img_locator = page.get_by_role("img", name="qrcode")
        # 获取 src 属性值
        src = await img_locator.get_attribute("src")
        original_url = page.url
        print("✅ 图片地址:", src)
        status_queue.put(src)
        # 监听页面的 'framenavigated' 事件，只关注主框架的变化
        page.on('framenavigated',
                lambda frame: asyncio.create_task(on_url_change()) if frame == page.main_frame else None)

        try:
            # 等待 URL 变化或超时
            await asyncio.wait_for(url_changed_event.wait(), timeout=200)  # 最多等待 200 秒
            print("监听页面跳转成功")
        except asyncio.TimeoutError:
            status_queue.put("500")
            print("监听页面跳转超时")
            await page.close()
            await context.close()
            await browser.close()
            return None
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        # 确保cookiesFile目录存在
        cookies_dir = Path(BASE_DIR / "cookiesFile")
        cookies_dir.mkdir(exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(4, f"{uuid_v1}.json")
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        id = await _automatic_name(4, id, cookies_dir / f"{uuid_v1}.json", status_queue)
        if id is None or getattr(status_queue, 'cancelled', None) and status_queue.cancelled.is_set():
            (cookies_dir / f"{uuid_v1}.json").unlink(missing_ok=True)
            await context.close()
            await browser.close()
            return
        await page.close()
        await context.close()
        await browser.close()

        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            _insert_account(conn, 4, f"{uuid_v1}.json", id)
            print("✅ 用户状态已记录")
        status_queue.put("200")

# 小红书登录
async def xiaohongshu_cookie_gen(id,status_queue):
    url_changed_event = asyncio.Event()

    async def on_url_change():
        # 检查是否是主框架的变化
        if page.url != original_url:
            url_changed_event.set()

    async with async_playwright() as playwright:
        options = {
            'args': [
                '--lang en-GB'
            ],
            'headless': LOCAL_CHROME_HEADLESS,  # Set headless option here
        }
        # Make sure to run headed.
        browser = await playwright.chromium.launch(**options)
        # Setup context however you like.
        context = await browser.new_context()  # Pass any options
        context = await set_init_script(context)
        # Pause the page, and start recording manually.
        page = await context.new_page()
        await page.goto("https://creator.xiaohongshu.com/")
        await page.locator('img.css-wemwzq').click()

        img_locator = page.get_by_role("img").nth(2)
        # 获取 src 属性值
        src = await img_locator.get_attribute("src")
        original_url = page.url
        print("✅ 图片地址:", src)
        status_queue.put(src)
        # 监听页面的 'framenavigated' 事件，只关注主框架的变化
        page.on('framenavigated',
                lambda frame: asyncio.create_task(on_url_change()) if frame == page.main_frame else None)

        try:
            # 等待 URL 变化或超时
            await asyncio.wait_for(url_changed_event.wait(), timeout=200)  # 最多等待 200 秒
            print("监听页面跳转成功")
        except asyncio.TimeoutError:
            status_queue.put("500")
            print("监听页面跳转超时")
            await page.close()
            await context.close()
            await browser.close()
            return None
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        # 确保cookiesFile目录存在
        cookies_dir = Path(BASE_DIR / "cookiesFile")
        cookies_dir.mkdir(exist_ok=True)
        await context.storage_state(path=cookies_dir / f"{uuid_v1}.json")
        result = await check_cookie(1, f"{uuid_v1}.json")
        if not result:
            status_queue.put("500")
            await page.close()
            await context.close()
            await browser.close()
            return None
        id = await _automatic_name(1, id, cookies_dir / f"{uuid_v1}.json", status_queue)
        if id is None or getattr(status_queue, 'cancelled', None) and status_queue.cancelled.is_set():
            (cookies_dir / f"{uuid_v1}.json").unlink(missing_ok=True)
            await context.close()
            await browser.close()
            return
        await page.close()
        await context.close()
        await browser.close()

        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            _insert_account(conn, 1, f"{uuid_v1}.json", id)
            print("✅ 用户状态已记录")
        status_queue.put("200")

# a = asyncio.run(xiaohongshu_cookie_gen(4,None))
# print(a)
