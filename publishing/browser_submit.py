"""One final click, bounded waiting, human verification stays in the official UI."""
import asyncio
import re
from urllib.parse import urlsplit
from uploader.tencent_uploader.flow import SubmissionNotStarted


async def submit_once(page, *, timeout=180, progress=None):
    async def report(stage, message, started):
        if progress:
            progress({"stage": stage, "message": message, "submission_started": started})
    button = page.get_by_role("button", name="发布", exact=True)
    try:
        await button.wait_for(state="visible", timeout=30000)
        if await button.count() != 1:
            raise ValueError("未找到唯一的发布按钮")
        await report("submitting", "准备提交抖音投稿", False)
    except Exception:
        raise SubmissionNotStarted("发布按钮尚未就绪，请检查官方窗口", stage="submitting", upload_started=True) from None
    # A click timeout may mean that the platform accepted the click. Never retry it.
    await report("submitting", "正在提交抖音投稿；最终发布只点击一次", True)
    await button.click(timeout=30000)
    await report("verification", "等待平台回应；如有验证，请在官方窗口完成", True)
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if urlsplit(page.url).path.startswith('/creator-micro/content/manage'):
            return
        errors = page.locator('[role="alert"]:visible, .semi-toast-content:visible').filter(
            has_text=re.compile('发布失败|提交失败|请.*验证|标题.*(为空|超出)|封面.*失败'))
        if await errors.count():
            await report('verification', '平台提示发布校验未通过，请核对官方窗口', True)
            raise RuntimeError('已点击发布，平台提示校验未通过；请核对后台，不会自动再次点击')
        dialog = page.get_by_role('dialog').filter(visible=True)
        if await dialog.count():
            await report('verification', '平台要求进一步确认或验证，请在官方窗口处理；不会自动重复发布', True)
        await asyncio.sleep(.5)
    raise RuntimeError("已点击发表，但未确认结果；请核对平台内容管理，不会自动再次点击")
