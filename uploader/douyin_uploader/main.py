# -*- coding: utf-8 -*-
from datetime import datetime

import asyncio
import inspect
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import Playwright
from playwright.async_api import async_playwright

from conf import BASE_DIR, DEBUG_MODE, LOCAL_CHROME_HEADLESS, LOCAL_CHROME_PATH
from uploader.base_video import BaseVideoUploader
from utils.base_social_media import set_init_script
from utils.login_qrcode import build_login_qrcode_path
from utils.login_qrcode import decode_qrcode_from_path
from utils.login_qrcode import print_terminal_qrcode
from utils.login_qrcode import remove_qrcode_file
from utils.login_qrcode import save_data_url_image
from utils.log import douyin_logger
from utils.douyin_credentials import inspect_douyin_authenticated_page, validate_douyin_credentials

DOUYIN_PUBLISH_STRATEGY_IMMEDIATE = "immediate"
DOUYIN_PUBLISH_STRATEGY_SCHEDULED = "scheduled"


def _msg(emoji: str, text: str) -> str:
    return f"{emoji} {text}"


async def _read_verify_code(code_file: str) -> str:
    if os.path.exists(code_file):
        with open(code_file, encoding="utf-8") as file_obj:
            return file_obj.read().strip()

    if not sys.stdin or not sys.stdin.isatty():
        return ""

    try:
        return (await asyncio.to_thread(input, "请输入抖音短信验证码（直接回车可稍后重试）: ")).strip()
    except (EOFError, OSError):
        return ""


def _msg(emoji: str, text: str) -> str:
    return f"{emoji} {text}"


async def _native_click(page, locator) -> bool:
    """对元素做"真人级"点击：真实鼠标点中心 + 派发完整 pointer/mouse 事件序列。
    抖音身份验证组件（uc_verification_component）只认这整套事件，不认单纯 click。
    返回是否点击成功。"""
    try:
        await locator.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass
    try:
        box = await locator.bounding_box()
    except Exception:
        box = None
    if not box:
        try:
            await locator.click(timeout=8000)
            return True
        except Exception:
            return False
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    try:
        await page.mouse.move(x, y)
        await asyncio.sleep(0.15)
        await page.mouse.click(x, y)
        await asyncio.sleep(0.2)
        await page.evaluate(
            """({x, y}) => {
                const el = document.elementFromPoint(x, y);
                if (!el) return;
                const opts = {bubbles:true,cancelable:true,composed:true,clientX:x,clientY:y,view:window,pointerId:1,pointerType:'mouse',isPrimary:true,button:0,buttons:1};
                for (const t of ['pointerover','pointerenter','pointerdown','mousedown','pointerup','mouseup','click']) {
                    const C = t.startsWith('pointer') ? PointerEvent : MouseEvent;
                    try { el.dispatchEvent(new C(t, opts)); } catch(e){ try{ el.dispatchEvent(new MouseEvent(t,opts)); }catch(_){} }
                }
            }""",
            {"x": x, "y": y},
        )
        return True
    except Exception:
        return False


async def _emit_login_callback(callback, payload: dict):
    if not callback:
        return

    callback_result = callback(payload)
    if inspect.isawaitable(callback_result):
        await callback_result


def _build_login_result(success: bool, status: str, message: str, account_file: str, qrcode: dict | None = None, current_url: str = "") -> dict:
    return {
        "success": success,
        "status": status,
        "message": message,
        "account_file": str(account_file),
        "qrcode": qrcode,
        "current_url": current_url,
    }


async def cookie_auth(account_file):
    if not os.path.exists(account_file):
        return False
    result = await validate_douyin_credentials(account_file)
    return result["success"]


async def douyin_setup(account_file, handle=False, return_detail=False, qrcode_callback=None, headless: bool = LOCAL_CHROME_HEADLESS, cdp_url: str | None = None):
    if not os.path.exists(account_file) or not await cookie_auth(account_file):
        if not handle:
            result = _build_login_result(False, "cookie_invalid", "cookie文件不存在或已失效", account_file)
            return result if return_detail else False
        douyin_logger.info(_msg("🥹", "cookie 失效了，准备打开浏览器重新登录"))
        result = await douyin_cookie_gen(account_file, qrcode_callback=qrcode_callback, headless=headless, cdp_url=cdp_url)
        return result if return_detail else result["success"]

    result = _build_login_result(True, "cookie_valid", "cookie有效", account_file)
    return result if return_detail else True


async def _extract_douyin_qrcode_src(page: Page) -> str:
    # 二维码一出现就回传；无需等待持续请求的 SPA 进入 networkidle。
    selectors = [
        'div#animate_qrcode_container img[src^="data:image"]',
        'div[class*="animate_qrcode_container"] img[src^="data:image"]',
        'div[class*="scan_qrcode_login_content"] img[src^="data:image"]',
        'img[aria-label="二维码"][src^="data:image"]',
    ]
    image = page.locator(", ".join(selectors)).first
    await image.wait_for(state="attached", timeout=30000)
    src = await image.get_attribute("src")
    if not src or not src.startswith("data:image/"):
        raise RuntimeError("抖音登录二维码图片尚未就绪")
    return src


async def _save_douyin_qrcode(page: Page, account_file: str, previous_qrcode_path: Path | None = None, qrcode_callback=None) -> dict:
    # 提取二维码 src 仅为了保存/终端显示；定位不到时不致命——有头浏览器里二维码可见，直接扫码即可
    try:
        qrcode_src = await _extract_douyin_qrcode_src(page)
    except Exception as exc:
        douyin_logger.warning(_msg("😵", f"没定位到二维码元素（{str(exc)[:50]}）——请直接在弹出的浏览器里扫码，小人继续等登录跳转"))
        return {"image_path": "", "image_data_url": ""}
    qrcode_path = save_data_url_image(qrcode_src, build_login_qrcode_path(account_file))
    if previous_qrcode_path and previous_qrcode_path != qrcode_path:
        if remove_qrcode_file(previous_qrcode_path):
            douyin_logger.info(_msg("🧹", f"临时二维码文件已清理: {previous_qrcode_path}"))
    douyin_logger.info(_msg("🖼️", f"二维码已经准备好啦，已保存到: {qrcode_path}"))
    if not qrcode_callback:
        qrcode_content = decode_qrcode_from_path(qrcode_path)
        if qrcode_content:
            print_terminal_qrcode(qrcode_content, qrcode_path, "抖音APP")
        else:
            douyin_logger.warning(_msg("😵", f"终端没法完整显示二维码，请打开 {qrcode_path} 扫码"))
    qrcode_info = {
        "image_path": str(qrcode_path),
        "image_data_url": qrcode_src,
    }
    await _emit_login_callback(qrcode_callback, qrcode_info)
    return qrcode_info


async def _is_douyin_login_completed(page: Page) -> bool:
    return await inspect_douyin_authenticated_page(page) == "valid"


async def _observe_douyin_login_response(response, state: dict, status_callback=None, *, interactive=False):
    """只观察官方页面自己的扫码回执，不另行请求登录接口或保存响应凭据。"""
    url = urlsplit(response.url)
    if url.hostname != "creator.douyin.com" or url.path.rstrip("/") != "/passport/web/check_qrconnect":
        return
    if state.get("failure"):
        return
    if response.status != 200:
        state["failure"] = {"status": "network_error", "message": f"抖音登录确认请求失败（HTTP {response.status}），请检查网络后重试"}
        return
    try:
        payload = await response.json()
    except (PlaywrightError, ValueError) as exc:
        douyin_logger.warning(f"无法读取抖音扫码回执：{type(exc).__name__}")
        if interactive:
            # Navigation may discard an earlier QR response while the official page
            # finishes a challenge. Keep the user's window open and require positive
            # authenticated-page evidence; never infer success from this exception.
            await _emit_login_callback(status_callback, {
                "stage": "browser_wait", "message": "请继续在官方窗口完成登录，工具会检查最终登录状态",
            })
        else:
            state["failure"] = {"status": "response_error", "message": "无法读取抖音登录确认结果，请重新扫码"}
        return
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        state["failure"] = {"status": "response_error", "message": "抖音登录确认结果格式异常，请重试"}
        return

    code = data.get("error_code", 0)
    if code not in (0, "0", None) or payload.get("message") == "error":
        security_required = str(code) == "2046"
        reason = data.get("description") or "平台未允许此次电脑端登录"
        status = "verification_required" if security_required else "rejected"
        message = f"抖音{'要求安全验证' if security_required else '拒绝登录'}（错误码 {code}）：{reason}"
        if security_required and interactive:
            # 2046 会触发官方 SDK 加载二次验证界面，不能在界面出现前关闭浏览器。
            state["verification_required"] = True
            douyin_logger.warning(message)
            await _emit_login_callback(status_callback, {
                "stage": status,
                "message": "抖音已收到扫码，请在打开的官方窗口完成额外身份验证；完成后账号会自动保存。",
            })
            return
        state["failure"] = {"status": status, "message": message}
        douyin_logger.warning(message)
        await _emit_login_callback(status_callback, {"stage": status, "message": message})
        return

    stages = {
        "scanned": ("scanned", "抖音已收到扫码，请在手机上确认登录"),
        "confirmed": ("verifying", "手机已确认，正在等待抖音建立电脑端登录"),
    }
    stage, message = stages.get(data.get("status"), (None, None))
    if stage and state.get("stage") != stage:
        state["stage"] = stage
        douyin_logger.info(message)
        await _emit_login_callback(status_callback, {"stage": stage, "message": message})


async def _wait_for_douyin_login(page: Page, account_file: str, qrcode_info: dict, qrcode_callback=None, status_callback=None, poll_interval: int = 3, max_checks: int = 100, login_state=None, interactive=False) -> dict:
    if login_state is None:
        login_state = {}
    qrcode_path = Path(qrcode_info["image_path"]) if qrcode_info.get("image_path") else None
    original_url = page.url
    saw_2fa = False
    saw_navigation = False
    for _ in range(max_checks):
        # 手机确认后仍可能被平台拒绝，而且主页 URL 不会变化；应立即返回真实原因。
        if failure := login_state.get("failure"):
            return _build_login_result(False, failure["status"], failure["message"], account_file, qrcode_info, page.url)
        if interactive and page.is_closed():
            return _build_login_result(False, "cancelled", "抖音官方窗口已关闭，账号尚未保存", account_file, qrcode_info)
        completed = await _is_douyin_login_completed(page)
        if completed:
            douyin_logger.info(_msg("🥳", f"扫码成功，已经跳转到登录后页面: {page.url}"))
            return _build_login_result(True, "success", "抖音扫码登录成功", account_file, qrcode_info, page.url)

        # 页面已跳转但登录界面仍可见时，继续等待安全验证。
        if page.url != original_url:
            if not saw_navigation:
                saw_navigation = True
                await _emit_login_callback(status_callback, {"stage": "verifying", "message": "页面已跳转，正在确认抖音登录状态"})
            sms_input = page.locator('input[placeholder*="验证码"], input[type="tel"], input[placeholder*="短信"], input[placeholder*="手机号"]').first
            if not saw_2fa and await sms_input.count() and await sms_input.is_visible():
                douyin_logger.warning(_msg("⚠️", "检测到抖音短信或安全二次验证"))
                saw_2fa = True
                await _emit_login_callback(status_callback, {"stage": "verification_required", "message": "抖音要求额外安全验证，等待验证完成"})
            await asyncio.sleep(poll_interval)
            continue

        expired_box = page.get_by_text("二维码失效", exact=True).locator("..").first
        if not interactive and await expired_box.count() and await expired_box.is_visible():
            douyin_logger.warning(_msg("😵", "二维码失效了，小人马上去刷新"))
            await expired_box.click()
            await asyncio.sleep(1)
            qrcode_info = await _save_douyin_qrcode(page, account_file, qrcode_path, qrcode_callback=qrcode_callback)
            qrcode_path = Path(qrcode_info["image_path"]) if qrcode_info.get("image_path") else None

        await asyncio.sleep(poll_interval)

    message = ("抖音官方窗口中的身份验证尚未完成，账号未保存；请重试并完成官方页面的验证" if interactive else
               "抖音安全验证尚未完成" if saw_2fa else
               "扫码后页面未完成登录跳转" if saw_navigation else
               "抖音已收到扫码，但尚未确认电脑端登录成功" if login_state.get("stage") else
               "等待扫码或手机确认超时")
    douyin_logger.warning(_msg("⌛", f"扫码登录等待结束：页面已跳转={saw_navigation}，出现安全验证={saw_2fa}"))
    return _build_login_result(False, "timeout", message, account_file, qrcode_info, page.url)


async def _wait_for_session_cookie(context, timeout: float = 3) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        cookies = await context.cookies("https://creator.douyin.com/")
        if any(cookie.get("name") == "sessionid" and cookie.get("value") for cookie in cookies):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.2)

async def douyin_cookie_gen(
    account_file,
    qrcode_callback=None,
    poll_interval: int = 1,
    max_checks: int = 120,
    headless: bool = LOCAL_CHROME_HEADLESS,
    cdp_url: str | None = None,
    status_callback=None,
    interactive: bool = False,
):
    started_at = time.monotonic()
    async with async_playwright() as playwright:
        if cdp_url:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            should_close_context = False
        else:
            options = {"headless": False if interactive else headless, "channel": "chrome" if interactive else "chromium"}
            if interactive and LOCAL_CHROME_PATH:
                options.pop("channel")
                options["executable_path"] = LOCAL_CHROME_PATH
            browser = await playwright.chromium.launch(**options)
            context = await browser.new_context()
            should_close_context = True
        if not interactive:
            context = await set_init_script(context)
        qrcode_path = None
        result = _build_login_result(False, "failed", "抖音登录失败", account_file)
        try:
            page = await context.new_page()
            login_state = {}

            async def on_login_response(response):
                await _observe_douyin_login_response(response, login_state, status_callback, interactive=interactive)

            page.on("response", on_login_response)
            await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=30000)
            if interactive:
                qrcode_info = {}
                await _emit_login_callback(status_callback, {
                    "stage": "browser_open",
                    "message": "已打开抖音官方窗口，请在该窗口登录并完成验证；最长等待约 5 分钟，完成后账号会自动保存。",
                })
            else:
                qrcode_info = await _save_douyin_qrcode(page, account_file, qrcode_callback=qrcode_callback)
                if qrcode_callback and not qrcode_info.get("image_data_url"):
                    raise RuntimeError("未获取到抖音登录二维码，请检查创作者中心页面")
            douyin_logger.info(_msg("⏱️", f"{'官方登录窗口' if interactive else '二维码'}就绪耗时 {time.monotonic() - started_at:.2f} 秒"))
            qrcode_path = Path(qrcode_info["image_path"]) if qrcode_info.get("image_path") else None
            douyin_logger.info(_msg("🧍", "请扫码，小人正在耐心等待登录完成"))
            wait_started_at = time.monotonic()
            result = await _wait_for_douyin_login(
                page,
                account_file,
                qrcode_info,
                qrcode_callback=qrcode_callback,
                status_callback=status_callback,
                poll_interval=poll_interval,
                max_checks=max_checks,
                login_state=login_state,
                interactive=interactive,
            )
            douyin_logger.info(_msg("⏱️", f"扫码确认耗时 {time.monotonic() - wait_started_at:.2f} 秒，状态 {result['status']}"))
            if result["success"]:
                await _emit_login_callback(status_callback, {"stage": "saving", "message": "登录已确认，正在保存抖音账号"})
                if await _wait_for_session_cookie(context):
                    await context.storage_state(path=account_file)
                else:
                    result = _build_login_result(
                        False, "cookie_invalid", "抖音页面已跳转，但登录凭据尚未生效",
                        account_file, qrcode_info, page.url,
                    )
        except Exception as exc:
            result = _build_login_result(False, "failed", str(exc), account_file, current_url=page.url if "page" in locals() else "")
        finally:
            if remove_qrcode_file(qrcode_path):
                douyin_logger.info(_msg("🧹", f"临时二维码文件已清理: {qrcode_path}"))
            if not result["success"]:
                douyin_logger.error(_msg("😢", f"登录失败: {result['message']}"))
            if should_close_context:
                await context.close()
            await browser.close()
        return result


class DouYinBaseUploader(BaseVideoUploader):
    def __init__(
        self,
        publish_date: datetime | int,
        account_file,
        publish_strategy: str = DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
        debug: bool = DEBUG_MODE,
        headless: bool = LOCAL_CHROME_HEADLESS,
    ):
        self.publish_date = publish_date
        self.account_file = account_file
        self.publish_strategy = publish_strategy
        self.debug = debug
        self.date_format = "%Y年%m月%d日 %H:%M"
        self.local_executable_path = LOCAL_CHROME_PATH
        self.headless = headless

    async def validate_base_args(self):
        if not os.path.exists(self.account_file):
            raise RuntimeError(f"cookie文件不存在，请先完成抖音登录: {self.account_file}")
        if not await cookie_auth(self.account_file):
            raise RuntimeError(f"cookie文件已失效，请先完成抖音登录: {self.account_file}")
        if self.publish_strategy not in {DOUYIN_PUBLISH_STRATEGY_IMMEDIATE, DOUYIN_PUBLISH_STRATEGY_SCHEDULED}:
            raise ValueError(f"不支持的发布策略: {self.publish_strategy}")

        if self.publish_strategy == DOUYIN_PUBLISH_STRATEGY_SCHEDULED:
            self.publish_date = self.validate_publish_date(self.publish_date)
        else:
            self.publish_date = 0

    async def set_schedule_time_douyin(self, page, publish_date):
        label_element = page.locator("[class^='radio']:has-text('定时发布')")
        await label_element.click()
        await asyncio.sleep(1)
        publish_date_hour = publish_date.strftime("%Y-%m-%d %H:%M")

        await asyncio.sleep(1)
        await page.locator('.semi-input[placeholder="日期和时间"]').click()
        await page.keyboard.press("Control+KeyA")
        await page.keyboard.type(str(publish_date_hour))
        await page.keyboard.press("Enter")
        await asyncio.sleep(1)

    async def fill_title_and_description(self, page: Page, title: str, description: str, tags: list[str] | None = None):
        # 2026-06 抖音发布页 DOM：标题=input[placeholder*=填写作品标题]，描述=div.zone-container[contenteditable]
        # version_2(post/video) 发布页要等视频上传完才渲染表单（实测约 40s），故等待超时给到 120s
        title_input = page.locator('input[placeholder*="填写作品标题"]').first
        await title_input.wait_for(state="visible", timeout=120000)
        await title_input.fill(title[:30])

        description_editor = page.locator('div.zone-container[contenteditable="true"]').first
        await description_editor.wait_for(state="visible", timeout=120000)
        await description_editor.click()
        await page.keyboard.press("Control+KeyA")
        await page.keyboard.press("Delete")

        # 先填正文描述，再填 #话题（此前 description 参数未被写入，导致抖音只有标签没有正文）
        if description and description.strip():
            await page.keyboard.type(description.strip())

        for tag in tags or []:
            await page.keyboard.type(" #" + tag)
            await page.keyboard.press("Space")
        await page.keyboard.press("Escape")  # 收起话题下拉，避免浮层拦截后续点击

    async def set_location(self, page: Page, location: str = ""):
        if not location:
            return
        await page.locator('div.semi-select span:has-text("输入地理位置")').click()
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(2000)
        await page.keyboard.type(location)
        await page.wait_for_selector('div[role="listbox"] [role="option"]', timeout=5000)
        await page.locator('div[role="listbox"] [role="option"]').first.click()

    async def handle_product_dialog(self, page: Page, product_title: str):
        await page.wait_for_timeout(2000)
        await page.wait_for_selector('input[placeholder="请输入商品短标题"]', timeout=10000)
        short_title_input = page.locator('input[placeholder="请输入商品短标题"]')
        if not await short_title_input.count():
            douyin_logger.error(_msg("😵", "没找到商品短标题输入框"))
            return False

        product_title = product_title[:10]
        await short_title_input.fill(product_title)
        await page.wait_for_timeout(1000)

        finish_button = page.locator('button:has-text("完成编辑")')
        if "disabled" not in await finish_button.get_attribute("class"):
            await finish_button.click()
            douyin_logger.debug(_msg("🥳", "已点击“完成编辑”按钮"))
            await page.wait_for_selector(".semi-modal-content", state="hidden", timeout=5000)
            return True

        douyin_logger.error(_msg("😵", "“完成编辑”按钮是灰的，小人先把弹窗关掉"))
        cancel_button = page.locator('button:has-text("取消")')
        if await cancel_button.count():
            await cancel_button.click()
        else:
            close_button = page.locator(".semi-modal-close")
            await close_button.click()
        await page.wait_for_selector(".semi-modal-content", state="hidden", timeout=5000)
        return False

    async def set_product_link(self, page: Page, product_link: str, product_title: str):
        await page.wait_for_timeout(2000)
        try:
            await page.wait_for_selector("text=添加标签", timeout=10000)
            dropdown = page.get_by_text("添加标签").locator("..").locator("..").locator("..").locator(".semi-select").first
            if not await dropdown.count():
                douyin_logger.error(_msg("😵", "没找到标签下拉框"))
                return False
            douyin_logger.debug(_msg("🧍", "找到标签下拉框，小人准备选择“购物车”"))
            await dropdown.click()
            await page.wait_for_selector('[role="listbox"]', timeout=5000)
            await page.locator('[role="option"]:has-text("购物车")').click()
            douyin_logger.debug(_msg("🥳", "已经选中“购物车”"))

            await page.wait_for_selector('input[placeholder="粘贴商品链接"]', timeout=5000)
            input_field = page.locator('input[placeholder="粘贴商品链接"]')
            await input_field.fill(product_link)
            douyin_logger.debug(_msg("🔗", f"商品链接已经填好了: {product_link}"))

            add_button = page.locator('span:has-text("添加链接")')
            button_class = await add_button.get_attribute("class")
            if "disable" in button_class:
                douyin_logger.error(_msg("😵", "“添加链接”按钮现在点不了"))
                return False
            await add_button.click()
            douyin_logger.debug(_msg("🥳", "已点击“添加链接”按钮"))

            await page.wait_for_timeout(2000)
            error_modal = page.locator("text=未搜索到对应商品")
            if await error_modal.count():
                confirm_button = page.locator('button:has-text("确定")')
                await confirm_button.click()
                douyin_logger.error(_msg("😢", "这个商品链接无效"))
                return False

            if not await self.handle_product_dialog(page, product_title):
                return False

            douyin_logger.debug(_msg("🥳", "商品链接设置好了"))
            return True
        except Exception as e:
            douyin_logger.error(_msg("😢", f"设置商品链接时出错: {str(e)}"))
            return False

    async def set_self_declaration(self, page: Page, declaration: str) -> bool:
        """抖音「自主声明」：打开声明弹窗 → 单选声明类型 → 确定。

        真实弹窗（用户 F12 实测）：header「请选择声明类型（单选）」，选项为
        label.semi-radio 内 span.semi-radio-addon 文本，「内容由AI生成」与
        「内容为转载信息」「内容为个人观点或见解」等并列；底部 footer 的
        semi-button-primary =「确定」。

        入口/弹窗异步渲染；且填完话题后残留的 mention-wrapper/semi-portal 浮层会盖住入口，
        必须先清浮层再点。失败返回 False。

        Args:
            declaration: 声明类型文本（调用方显式传入）
        """
        try:
            # 清掉会遮挡入口的浮层（话题下拉/引导层），并让输入框失焦
            await self._clear_blocking_overlays(page)

            # 入口：点开声明弹窗（多个候选文案，native 仅作兜底）
            entry = None
            for etext in ["请选择自主声明", "请选择声明类型", "添加自主声明", "自主声明", "作品声明"]:
                cand = page.get_by_text(etext).first
                if await cand.count():
                    entry = cand
                    break
            if entry is not None:
                try:
                    await entry.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
                try:
                    await entry.click(timeout=6000)
                except Exception:
                    await _native_click(page, entry)
                await page.wait_for_timeout(1200)

            # 弹窗：header「请选择声明类型（单选）」
            dialog = page.locator(".semi-modal-content").filter(has_text="请选择声明类型").first
            if await dialog.count() == 0:
                dialog = page.locator(".semi-modal-body").filter(has_text="请选择声明类型").first
            if await dialog.count() == 0:
                douyin_logger.warning(_msg("🧾", "自主声明弹窗未打开，跳过声明继续发布"))
                return False
            await dialog.first.wait_for(state="visible", timeout=6000)

            # 选项：label.semi-radio 内 span.semi-radio-addon 精确匹配
            option = dialog.locator("label.semi-radio").filter(
                has=page.locator(f'.semi-radio-addon:text-is("{declaration}")')
            ).first
            if await option.count() == 0:
                option = dialog.locator("label.semi-radio").filter(has_text=declaration).first
            if await option.count():
                try:
                    await option.click(timeout=6000)
                except Exception:
                    await _native_click(page, option)
            else:
                await dialog.get_by_text(declaration, exact=True).first.click(timeout=6000, force=True)
            await page.wait_for_timeout(400)

            # 确定：footer 的 primary 按钮
            confirm_btn = dialog.locator("button.semi-button-primary").filter(has_text="确定").first
            if await confirm_btn.count() == 0:
                confirm_btn = dialog.get_by_role("button", name="确定").first
            if await confirm_btn.count() == 0:
                confirm_btn = page.get_by_role("button", name="确定").first
            try:
                await confirm_btn.click(timeout=6000)
            except Exception:
                await _native_click(page, confirm_btn)
            try:
                await dialog.first.wait_for(state="hidden", timeout=6000)
            except Exception:
                pass
            douyin_logger.success(_msg("🧾", f"自主声明已选择「{declaration}」"))
            return True
        except Exception as exc:
            douyin_logger.warning(_msg("🧾", f"自主声明设置失败，跳过该步骤继续发布：{exc}"))
            return False

    async def select_bgm(self, page: Page, bgm_name: str) -> bool:
        """为图文发布选择 BGM：可选增强功能，搜索无结果或异常均跳过不中断发布。"""
        try:
            # 点击「选择音乐」按钮
            music_entry = page.locator('text="选择音乐"').nth(1)
            if not await music_entry.count():
                music_entry = page.locator('text="选择音乐"').first
            await music_entry.wait_for(state="visible", timeout=10000)
            await music_entry.click()

            # 等待侧边栏出现并搜索
            sidesheet = page.locator(".semi-sidesheet-content").first
            await sidesheet.wait_for(state="visible", timeout=8000)
            search_input = sidesheet.locator('input.semi-input[placeholder="搜索音乐"]').first
            await search_input.wait_for(state="visible", timeout=5000)
            await search_input.fill(bgm_name)
            await search_input.press("Enter")

            # 等待搜索结果
            await asyncio.sleep(2)
            first_card = sidesheet.locator(".card-container-tmocjc").first
            try:
                await first_card.wait_for(state="visible", timeout=8000)
            except Exception:
                douyin_logger.warning(_msg("🎵", f"音乐「{bgm_name}」搜索结果为空，小人跳过"))
                await self._close_music_sidesheet(page)
                return False

            # 打印找到的音乐名称
            try:
                song_name_el = first_card.locator(".song-name-oRge4d").first
                if await song_name_el.count():
                    song_name = await song_name_el.inner_text()
                    douyin_logger.info(_msg("🎵", f"小人找到了: {song_name}"))
            except Exception:
                pass

            # JS 点击「使用」（按钮 visibility:hidden，普通 click 无效）
            apply_btn = first_card.locator(".apply-btn-LUPP0D").first
            await apply_btn.evaluate("el => el.click()")
            douyin_logger.info(_msg("🥳", f"BGM「{bgm_name}」已应用"))

            # 等待侧边栏关闭，超时则手动关闭
            try:
                await sidesheet.wait_for(state="hidden", timeout=5000)
            except Exception:
                await self._close_music_sidesheet(page)

            return True
        except Exception as exc:
            douyin_logger.warning(_msg("🎵", f"添加 BGM 时出错，跳过该步骤继续发布：{exc}"))
            try:
                await self._close_music_sidesheet(page)
            except Exception:
                pass
            return False

    async def _close_music_sidesheet(self, page: Page) -> None:
        try:
            close_btn = page.locator(".semi-sidesheet-close").first
            if await close_btn.count() and await close_btn.is_visible():
                await close_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass


class DouYinVideo(DouYinBaseUploader):
    def __init__(
        self,
        title,
        file_path,
        tags,
        publish_date: datetime | int,
        account_file,
        thumbnail_landscape_path=None,
        productLink="",
        productTitle="",
        thumbnail_portrait_path=None,
        desc: str | None = None,
        publish_strategy: str = DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
        debug: bool = DEBUG_MODE,
        headless: bool = LOCAL_CHROME_HEADLESS,
        declaration: str | None = None,
        collection_name: str | None = None,
        progress_callback=None,
    ):
        super().__init__(
            publish_date=publish_date,
            account_file=account_file,
            publish_strategy=publish_strategy,
            debug=debug,
            headless=headless,
        )
        self.title = title
        self.file_path = file_path
        self.tags = tags
        self.thumbnail_landscape_path = thumbnail_landscape_path
        self.thumbnail_portrait_path = thumbnail_portrait_path
        self.productLink = productLink
        self.productTitle = productTitle
        self.desc = desc or ""
        self.collection_name = collection_name
        self.declaration = declaration.strip() if declaration and declaration.strip() else None
        self.progress_callback = progress_callback

    async def apply_self_declaration(self, page: Page) -> None:
        if not self.declaration:
            return
        if not await self.set_self_declaration(page, self.declaration):
            raise RuntimeError(f"自主声明「{self.declaration}」设置失败，拒绝继续发布")

    async def _clear_blocking_overlays(self, page: Page) -> None:
        """清除会拦截点击的浮层：填完话题后残留的话题/@提及下拉(publish-mention-wrapper)
        及其所在 semi-portal、其它非模态 semi-portal(tooltip/popover)、shepherd 引导层，
        并让当前输入框失焦。合集/声明下拉自身的 portal 是"点开后"才创建，故此处清理不误伤。

        根因见 recorder.log 2026-08-10 05:31：apply_collection 点合集下拉时，
        publish-mention-wrapper / semi-portal 拦截 pointer events → click 超时 → 归集被跳过。
        """
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            await page.evaluate(
                """() => {
                    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
                    document.querySelectorAll('.shepherd-element,.shepherd-modal-overlay-container').forEach(e=>e.remove());
                    document.querySelectorAll('[class*="mention-wrapper"]').forEach(e=>{ const p=e.closest('.semi-portal'); (p||e).remove(); });
                    // 关闭残留的非模态 Semi 浮层 portal（保留模态框，如声明弹窗）
                    document.querySelectorAll('.semi-portal').forEach(e=>{ if(!e.querySelector('.semi-modal, .semi-modal-content')) e.remove(); });
                }"""
            )
        except Exception:
            pass
        await page.wait_for_timeout(400)

    async def apply_collection(self, page: Page) -> None:
        """在发布表单页"添加合集"区选择目标合集（Semi Design select，字节组件库）。

        结构与快手（Ant Design）不同：合集名是纯文本 span.option-title-*，无 label 属性，
        用文本精确匹配。触发器用专属 class .select-collection-* 定位（页面唯一，第一级
        "合集/系列"类型下拉与此无关，不会误选）。找不到匹配合集时按 Escape 收起下拉，
        保持未选状态直接发布（界面允许留空，不阻断主发布流程）。
        """
        if not self.collection_name:
            return
        try:
            # 关键修复：填完话题后残留的话题/@提及下拉(publish-mention-wrapper)及 semi-portal
            # 浮层盖在"添加合集"下拉上，普通 click 全点在遮罩上→超时→归集被跳过。先清浮层再点。
            await self._clear_blocking_overlays(page)

            trigger = page.locator('[class*="select-collection-"]').first
            if await trigger.count() == 0:
                douyin_logger.warning(_msg("😵", "未找到\"添加合集\"下拉框，跳过归集"))
                return
            selection = trigger.locator(".semi-select-selection")
            try:
                await selection.click(timeout=5000)
            except Exception:
                await self._clear_blocking_overlays(page)
                await _native_click(page, selection)
            await page.wait_for_timeout(800)

            option = page.locator(".semi-select-option.collection-option").filter(
                has=page.locator(f'[class*="option-title-"]:text-is("{self.collection_name}")')
            )
            if await option.count() == 0:
                douyin_logger.warning(
                    _msg("😵", f"合集下拉框未找到「{self.collection_name}」，跳过归集，保持未选状态")
                )
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
                return

            try:
                await option.first.click(timeout=5000)
            except Exception:
                await _native_click(page, option.first)
            await page.wait_for_timeout(500)
            douyin_logger.success(_msg("🥳", f"已选择合集：{self.collection_name}"))
        except Exception as exc:
            douyin_logger.warning(_msg("😵", f"选择合集失败，跳过归集继续发布: {exc}"))
            try:
                await page.keyboard.press("Escape")
            except Exception:
                pass

    async def _submit_sms_verify_code(self, page: Page, sms_input, code: str, code_file: str) -> bool:
        douyin_logger.info(_msg("✍️", f"已获取验证码，准备填入: {code}"))
        await sms_input.click()
        await sms_input.fill(code)
        douyin_logger.info(_msg("✅", "验证码已填入输入框"))
        await page.wait_for_timeout(500)

        verify_btn = page.locator('div.uc-ui-verify_sms-verify_button:has-text("验证")').first
        if await verify_btn.count() and await verify_btn.is_visible():
            try:
                await verify_btn.click(force=True)
                douyin_logger.success(_msg("✅", "已点击「验证」按钮(force)"))
            except Exception:
                await page.eval_on_selector('div.uc-ui-verify_sms-verify_button', 'el => el.click()')
                douyin_logger.success(_msg("✅", "已点击「验证」按钮(JS)"))
        else:
            verify_by_text = page.get_by_text("验证", exact=True).first
            if await verify_by_text.count():
                await verify_by_text.click(force=True)
                douyin_logger.success(_msg("✅", "已点击「验证」按钮(text)"))
            else:
                douyin_logger.warning(_msg("⚠️", "未找到验证按钮，尝试按Enter"))
                await page.keyboard.press("Enter")

        if os.path.exists(code_file):
            os.remove(code_file)
            douyin_logger.info(_msg("🧹", "验证码文件已清理"))

        await page.wait_for_timeout(3000)
        douyin_logger.info(_msg("🔄", "验证码处理完成，继续发布流程"))
        return True

    async def validate_upload_args(self):
        await self.validate_base_args()
        if not self.title or not str(self.title).strip():
            raise ValueError("视频模式下，title 是必须的")

        self.file_path = str(self.validate_video_file(self.file_path))
        if self.thumbnail_landscape_path:
            self.thumbnail_landscape_path = str(self.validate_image_file(self.thumbnail_landscape_path))
        if self.thumbnail_portrait_path:
            self.thumbnail_portrait_path = str(self.validate_image_file(self.thumbnail_portrait_path))

    async def handle_upload_error(self, page):
        douyin_logger.warning(_msg("😵", "视频上传摔了一跤，小人马上重新上传"))
        await page.locator('div.progress-div [class^="upload-btn-input"]').set_input_files(self.file_path)

    async def handle_auto_video_cover(self, page):
        if await page.get_by_text("请设置封面后再发布").first.is_visible():
            douyin_logger.info(_msg("🧍", "发布前还得先把封面弄好"))
            recommend_cover = page.locator('[class^="recommendCover-"]').first
            if await recommend_cover.count():
                douyin_logger.info(_msg("🏃", "小人去选第一个推荐封面"))
                try:
                    await recommend_cover.click()
                    await asyncio.sleep(1)
                    confirm_text = "是否确认应用此封面？"
                    if await page.get_by_text(confirm_text).first.is_visible():
                        douyin_logger.info(_msg("🪟", f"弹出确认框了: {confirm_text}"))
                        await page.get_by_role("button", name="确定").click()
                        douyin_logger.info(_msg("🥳", "推荐封面已经应用"))
                        await asyncio.sleep(1)
                    douyin_logger.info(_msg("🥳", "封面选择流程完成"))
                    return True
                except Exception as e:
                    douyin_logger.warning(_msg("😵", f"推荐封面没选成功: {e}"))
        return False

    async def set_thumbnail(self, page: Page):
        if not self.thumbnail_landscape_path and not self.thumbnail_portrait_path:
            return

        douyin_logger.info(_msg("🏃", "小人正在设置视频封面"))
        # 先清掉 shepherd 新手引导浮层，否则它会拦截封面点击导致弹窗打不开
        await page.evaluate(
            "() => document.querySelectorAll('.shepherd-element,.shepherd-modal-overlay-container').forEach(e=>e.remove())"
        )

        cover_area = page.locator('[class*="cover-"]').filter(has=page.locator("img")).first
        if not await cover_area.count():
            cover_area = page.locator('[class*="cover"]').first

        # 打开封面弹窗：抖音组件对普通/force click 常静默无效（和"完成"按钮同病），
        # 统一用 _native_click 派发完整原生事件序列；点后校验弹窗是否出现，没出现就重试。
        cover_locator_str = 'div.dy-creator-content-modal'
        cover_locator = page.locator(cover_locator_str).first
        opened = False
        # 刚上传完页面还在过渡，先等封面区渲染稳定，去掉"页面没稳就点空"这个诱因
        try:
            await cover_area.wait_for(state="visible", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        for attempt in range(5):
            # hover 若干次，等「编辑封面/选择封面」入口真正浮现，避免回退到封面区中心点空
            trigger = None
            trigger_txt = "封面区域"
            for _ in range(3):
                try:
                    await cover_area.hover(force=True)
                    await page.wait_for_timeout(600)
                except Exception:
                    pass
                for txt in ["编辑封面", "选择封面", "设置封面"]:
                    t = page.get_by_text(txt, exact=True).first
                    if await t.count() and await t.is_visible():
                        trigger, trigger_txt = t, txt
                        break
                if trigger is not None:
                    break
            if trigger is None:
                trigger = cover_area
            # 每轮都用 _native_click（force click 对抖音自定义组件常静默失效，白耗时间）
            await _native_click(page, trigger)
            douyin_logger.info(_msg("🖼️", f"已点「{trigger_txt}」尝试打开封面弹窗(第{attempt + 1}次)"))
            try:
                await page.wait_for_selector(cover_locator_str, timeout=5000)
                opened = True
                break
            except Exception:
                continue
        if not opened:
            douyin_logger.warning(_msg("⚠️", "封面弹窗打不开，跳过自定义封面继续发布（交给推荐封面兜底）"))
            return

        await page.wait_for_timeout(1500)

        # 封面弹窗内有两个 input.semi-upload-hidden-input（各自还带一个 -replace 兄弟）：
        #   ① 左侧「生成参考图」(AI封面参考图)——drag 区是 semi-upload-drag-area-custom，只有个 + 图标；
        #   ② 帧选择区「上传封面」——drag 区含 .semi-upload-drag-area-main-text「点击上传文件或拖拽…」。
        # 旧代码用 .first 取到了①，封面被塞进 AI 参考图槽→真封面没设上、检测/AI生成一直转，
        # 「完成」永远关不掉弹窗→挡住发布→超时（用户 F12 实测的真根因）。
        # 改为按 main-text 拖拽区精确定位②的上传 input，取不到再 .last 兜底。
        cover_upload = cover_locator.locator(
            '.semi-upload:has(.semi-upload-drag-area-main-text) input.semi-upload-hidden-input'
        ).first
        if await cover_upload.count() == 0:
            cover_upload = cover_locator.locator("input.semi-upload-hidden-input").last

        if self.thumbnail_portrait_path:
            # 弹窗默认就在“设置竖封面”页；防御性点一下 tab（已激活则忽略）
            try:
                await cover_locator.get_by_text("设置竖封面", exact=True).first.click(timeout=3000)
                await page.wait_for_timeout(800)
            except Exception:
                pass
            await cover_upload.set_input_files(self.thumbnail_portrait_path)
            await page.wait_for_timeout(3000)
            douyin_logger.info(_msg("🖼️", "竖版封面已上传到预览"))
        elif self.thumbnail_landscape_path:
            try:
                await cover_locator.get_by_text("设置横封面", exact=True).first.click(timeout=3000)
                await page.wait_for_timeout(800)
            except Exception:
                pass
            await cover_upload.set_input_files(self.thumbnail_landscape_path)
            await page.wait_for_timeout(3000)
            douyin_logger.info(_msg("🖼️", "横版封面已上传到预览"))

        # ── 等"完成"按钮解禁：封面图处理完成前，"完成"是 semi-button-disabled，点了无效 ──
        def _finish_btn():
            return cover_locator.get_by_role("button", name="完成", exact=True).first

        for _ in range(30):  # 最多 ~15s 等图片处理、按钮解禁
            try:
                b = _finish_btn()
                if await b.count():
                    cls = await b.get_attribute("class") or ""
                    if "semi-button-disabled" not in cls:
                        break
            except Exception:
                pass
            await page.wait_for_timeout(500)

        # ── 点"完成"并验证弹窗真正 detach ──
        # 抖音自定义组件普通 click 可能不抛异常也不生效，所以每轮点后都校验弹窗是否消失：
        # 消失才算成功；否则升级 _native_click、处理可能的二次确认、最后 Esc 兜底。
        closed = False
        for attempt in range(4):
            btn = _finish_btn()
            if not await btn.count():
                btn = cover_locator.locator("button.semi-button").filter(has_text="完成").first
            if await btn.count() and await btn.is_visible():
                try:
                    await btn.click(timeout=4000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                if await cover_locator.count() == 0:
                    closed = True
                    break
                # 普通点没关掉 → 派发完整原生事件序列
                await _native_click(page, btn)
                await page.wait_for_timeout(1500)
                if await cover_locator.count() == 0:
                    closed = True
                    break

            # 点"完成"后抖音可能弹二次确认（如未设横封面时问"确定完成？"）→ 点确认类按钮
            for cname in ["确定", "确认", "仍然完成", "仍要完成", "继续"]:
                confirm = page.locator(".semi-modal-content").get_by_role("button", name=cname, exact=True).first
                if await confirm.count() and await confirm.is_visible():
                    await _native_click(page, confirm)
                    await page.wait_for_timeout(1500)
                    break
            if await cover_locator.count() == 0:
                closed = True
                break

            # 仍没关掉：Esc 兜底后再验证一次
            douyin_logger.debug(_msg("🖼️", f"封面「完成」后弹窗未关，重试(第{attempt + 1}次)"))
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
            if await cover_locator.count() == 0:
                closed = True
                break

        if closed:
            douyin_logger.info(_msg("🥳", "视频封面设置完成，弹窗已关闭"))
        else:
            douyin_logger.warning(_msg("⚠️", "封面弹窗未能关闭，可能挡住自主声明/发布"))


    async def upload(self, playwright: Playwright) -> None:
        await self.validate_upload_args()
        # Reuse the same browser family as account login.
        browser = await playwright.chromium.launch(headless=self.headless, channel="msedge")
        context = None
        try:
            context = await browser.new_context(storage_state=str(self.account_file))
            context = await set_init_script(context)
            page = await context.new_page()
            return await asyncio.wait_for(self._upload_page(page, context), timeout=1200)
        except Exception as exc:
            if 'page' in locals():
                from publishing.diagnostics import capture
                screenshot = await capture(page, 'douyin', 'failure')
                if self.progress_callback:
                    self.progress_callback({'screenshot': screenshot, 'error_type': type(exc).__name__})
            raise
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    douyin_logger.warning("抖音浏览器上下文关闭失败，继续关闭浏览器")
            try:
                await browser.close()
            except Exception:
                douyin_logger.warning("抖音浏览器已断开，保留投稿阶段及原始结果")

    async def _upload_page(self, page, context):
        await page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded", timeout=90000)
        douyin_logger.info(_msg("🏃", f"小人开始搬运视频: {self.title}.mp4"))
        douyin_logger.info(_msg("🧭", "小人正在赶往上传主页"))
        await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload", timeout=90000)

        # ── 进入页面后可能弹身份验证（短信验证码）或被踢到登录页 ──
        await page.wait_for_timeout(2000)

        # 确认已经在上传页（非登录页），再找上传 input
        # 用更精确的选择器避免匹配到登录表单的 input
        upload_input = page.locator("input.upload-btn-input, div[class^='container'] input[accept]").first
        if not await upload_input.count():
            # 兜底：排除登录页的 input
            upload_input = page.locator("div[class^='container'] input[type='file'], div[class^='container'] input.upload-input").first
        if not await upload_input.count():
            # 最终兜底
            upload_input = page.locator("div[class^='container'] input").first
        await upload_input.wait_for(state="attached", timeout=60000)
        await upload_input.set_input_files(self.file_path)
        if self.progress_callback:
            self.progress_callback({"stage": "uploading", "message": "抖音网页正在上传视频；等待平台确认完成"})

        while True:
            try:
                await page.wait_for_url(
                    "https://creator.douyin.com/creator-micro/content/publish?enter_from=publish_page",
                    timeout=3000,
                )
                douyin_logger.info(_msg("🥳", "已经进入 version_1 发布页面"))
                break
            except Exception:
                try:
                    await page.wait_for_url(
                        "https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page",
                        timeout=3000,
                    )
                    douyin_logger.info(_msg("🥳", "已经进入 version_2 发布页面"))
                    break
                except Exception:
                    douyin_logger.debug(_msg("🧍", "还没进到视频发布页面，小人继续等一会"))
                    await asyncio.sleep(0.5)

        await asyncio.sleep(1)
        douyin_logger.info(_msg("✍️", "小人开始填标题、描述和话题"))
        await self.fill_title_and_description(page, self.title, self.desc, self.tags)
        douyin_logger.info(_msg("🏷️", f"小人一共贴了 {len(self.tags)} 个话题"))

        last_media_percent = None
        while True:
            try:
                if self.progress_callback:
                    from publishing.progress import read_widget_percent
                    percent = await read_widget_percent(page, 'div.progress-div')
                    if percent is not None and percent != last_media_percent:
                        self.progress_callback({"stage": "uploading", "message": f"抖音页面显示上传 {percent}%",
                                                "media_percent": percent})
                        last_media_percent = percent
                    elif percent is None and last_media_percent is not None:
                        self.progress_callback({"stage": "uploading", "message": "抖音页面暂未提供当前上传百分比"})
                        last_media_percent = None
                number = await page.locator('[class^="long-card"] div:has-text("重新上传")').count()
                if number > 0:
                    douyin_logger.success(_msg("🥳", "视频已经传完啦"))
                    break
                douyin_logger.info(_msg("🏃", "小人正在努力上传视频"))
                await asyncio.sleep(2)
                if await page.locator('div.progress-div > div:has-text("上传失败")').count():
                    douyin_logger.error(_msg("😵", "检测到上传失败，小人准备重试"))
                    last_media_percent = None
                    if self.progress_callback:
                        self.progress_callback({"stage": "uploading", "message": "抖音页面提示上传失败，正在重试"})
                    await self.handle_upload_error(page)
            except Exception:
                douyin_logger.debug(_msg("🧍", "小人还在等视频上传完成"))
                await asyncio.sleep(2)

        if self.progress_callback:
            self.progress_callback({"stage": "metadata", "message": "视频已上传，正在完善抖音稿件信息"})
        if self.productLink and self.productTitle:
            douyin_logger.info(_msg("🛒", "小人正在设置商品链接"))
            await self.set_product_link(page, self.productLink, self.productTitle)
            douyin_logger.info(_msg("🥳", "商品链接设置完成"))

        # 自主声明：本项目成片含 AI 生成内容（TTS 配音 / AI 字幕 / AI 前贴片），
        # 按平台合规如实选「内容由AI生成」（与转载等并列，单选，无二级选项、无需填来源）。
        await self.apply_self_declaration(page)

        # 先归集：此时尚未打开封面弹窗，避免 dy-creator-content-portal 封面浮层拦截合集下拉
        # （实测：封面弹窗在 headless 下常滞留"检测中"未关闭，会盖住"添加合集"下拉）
        await self.apply_collection(page)

        # 再设封面（放最后，关掉弹窗，避免残留浮层挡住发布按钮）
        if self.progress_callback:
            self.progress_callback({"stage": "cover", "message": "正在设置抖音封面"})
        await self.set_thumbnail(page)

        third_part_element = '[class^="info"] > [class^="first-part"] div div.semi-switch'
        if await page.locator(third_part_element).count():
            if "semi-switch-checked" not in await page.eval_on_selector(third_part_element, "div => div.className"):
                await page.locator(third_part_element).locator("input.semi-switch-native-control").click()

        if self.publish_strategy == DOUYIN_PUBLISH_STRATEGY_SCHEDULED and self.publish_date != 0:
            await self.set_schedule_time_douyin(page, self.publish_date)

        from publishing.browser_submit import submit_once
        await submit_once(page, progress=getattr(self, "progress_callback", None))
        await context.storage_state(path=self.account_file)
        from publishing.browser_readback import scan_context
        from datetime import datetime, timezone, timedelta
        return await scan_context(context, 'douyin', self.title, self.desc,
                                  datetime.now(timezone(timedelta(hours=8))).date().isoformat())

    async def douyin_upload_video(self):
        async with async_playwright() as playwright:
            return await self.upload(playwright)

    async def main(self):
        await self.douyin_upload_video()


class DouYinNote(DouYinBaseUploader):
    def __init__(
        self,
        image_paths,
        note,
        tags,
        publish_date: datetime | int,
        account_file,
        title: str | None = None,
        publish_strategy: str = DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
        debug: bool = DEBUG_MODE,
        headless: bool = LOCAL_CHROME_HEADLESS,
        bgm: str = "",
    ):
        super().__init__(
            publish_date=publish_date,
            account_file=account_file,
            publish_strategy=publish_strategy,
            debug=debug,
            headless=headless,
        )
        self.image_paths = image_paths
        self.note = note or ""
        self.title = title or (self.note[:30] if self.note else "")
        self.tags = tags or []
        self.bgm = bgm or ""

    async def validate_upload_args(self):
        await self.validate_base_args()
        if not self.title or not str(self.title).strip():
            raise ValueError("图文模式下，title 是必须的")

        if len(self.title) > 20:
            raise ValueError(f"标题不能超过20字符，当前: {len(self.title)}字符")

        if not self.image_paths:
            raise ValueError("图文模式下，图片是必须的")

        if isinstance(self.image_paths, (str, Path)):
            self.image_paths = [self.image_paths]

        if len(self.image_paths) > 35:
            raise ValueError("图文模式下最多只支持上传 35 张图片")

        note_len = len(self.note) if self.note else 0
        if note_len > 1000:
            raise ValueError(f"正文不能超过1000字符，当前: {note_len}字符")

        normalized_image_paths = []
        for image_path in self.image_paths:
            normalized_image_paths.append(str(self.validate_image_file(image_path)))
        self.image_paths = normalized_image_paths

    async def upload_note_content(self, page: Page) -> None:
        douyin_logger.info(_msg("🏃", f"小人开始搬运图文，共 {len(self.image_paths)} 张图片"))
        douyin_logger.info(_msg("🔀", "小人正在切换到图文发布"))
        await page.get_by_text("发布图文", exact=True).click()
        await page.wait_for_timeout(1000)

        douyin_logger.info(_msg("📤", "小人正在上传图片"))
        await page.locator("div[class^='container'] input[accept*='image']").set_input_files(self.image_paths)

        while True:
            try:
                await page.wait_for_url(
                    "**/creator-micro/content/post/image?**",
                    timeout=3000,
                )
                douyin_logger.info(_msg("🥳", "已经进入图文发布页面"))
                break
            except Exception:
                douyin_logger.debug(_msg("🧍", "小人还在等图片上传完成"))
                await asyncio.sleep(0.5)

        await asyncio.sleep(1)
        douyin_logger.info(_msg("✍️", "小人开始填标题、描述和话题"))
        await self.fill_title_and_description(page, self.title, self.note, self.tags)
        title_len = len(self.title) if self.title else 0
        tags_text = " ".join(f"#{t}" for t in self.tags) if self.tags else ""
        desc_and_tags_len = len(self.note or "") + (len(tags_text) + 2 if self.tags else 0)
        douyin_logger.info(_msg("📝", f"标题总字数: {title_len}，描述+话题总字数: {desc_and_tags_len}"))
        douyin_logger.info(_msg("🏷️", f"小人一共贴了 {len(self.tags)} 个话题"))

        if self.bgm:
            await self.select_bgm(page, self.bgm)

        if self.publish_strategy == DOUYIN_PUBLISH_STRATEGY_SCHEDULED and self.publish_date != 0:
            await self.set_schedule_time_douyin(page, self.publish_date)

        while True:
            try:
                publish_button = page.get_by_role("button", name="发布", exact=True)
                if await publish_button.count():
                    await publish_button.click()
                await page.wait_for_url(
                    "**/creator-micro/content/manage?enter_from=publish**",
                    timeout=3000,
                )
                douyin_logger.success(_msg("🥳", "图文发布成功，小人开心收工"))
                break
            except Exception:
                douyin_logger.info(_msg("🏃", "小人正在冲刺发布图文"))
                await asyncio.sleep(0.5)

    async def upload(self, playwright: Playwright) -> None:
        douyin_logger.info(_msg("🧍", "小人先检查 cookie、图片和发布时间"))
        await self.validate_upload_args()
        douyin_logger.info(_msg("🥳", "图文上传前检查通过"))

        browser = await playwright.chromium.launch(headless=self.headless, channel="chromium", args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(
            storage_state=f"{self.account_file}",
            permissions=["geolocation"],
        )
        context = await set_init_script(context)

        upload_success = False
        try:
            page = await context.new_page()
            await page.goto("https://creator.douyin.com/creator-micro/content/upload", wait_until="domcontentloaded", timeout=90000)
            douyin_logger.info(_msg("🧭", "小人正在赶往图文发布页"))
            await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload", timeout=90000)

            await self.upload_note_content(page)
            upload_success = True
        finally:
            if upload_success:
                await context.storage_state(path=self.account_file)
                douyin_logger.success(_msg("🥳", "cookie 更新完毕"))
                await asyncio.sleep(2)
            await context.close()
            await browser.close()

    async def douyin_upload_note(self):
        async with async_playwright() as playwright:
            await self.upload(playwright)
