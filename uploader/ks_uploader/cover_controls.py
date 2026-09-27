"""Validate the uploaded image and the selected cover before allowing submission."""
import asyncio
import base64
import mimetypes
from pathlib import Path


COVER_TIMEOUT_SECONDS = 30
MAIN_COVER = '[class*="_default-cover_"] img'
RAW_COVER = 'canvas[class*="_cutter-raw_"]'

_COMPARE_IMAGE = """async (element, expected) => {
    const reference = new Image();
    reference.src = expected;
    await reference.decode();
    if (element instanceof HTMLImageElement && (!element.complete || !element.naturalWidth))
        return {ready: false};
    const width = element.naturalWidth || element.width;
    const height = element.naturalHeight || element.height;
    if (!width || !height) return {ready: false};
    const sample = (source) => {
        // Normalize image decoding before downsampling: Chromium uses different
        // sampling paths for an Image and an existing canvas at small sizes.
        const decoded = document.createElement('canvas');
        decoded.width = source.naturalWidth || source.width;
        decoded.height = source.naturalHeight || source.height;
        decoded.getContext('2d').drawImage(source, 0, 0);
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 64;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(decoded, 0, 0, 64, 64);
        return ctx.getImageData(0, 0, 64, 64).data;
    };
    let a, b;
    try { a = sample(reference); b = sample(element); }
    catch (error) {
        // The old remote video frame may remain until the new local preview is applied.
        if (error.name === 'SecurityError') return {ready: false, reason: '旧远端预览尚未替换'};
        throw error;
    }
    let error = 0;
    for (let i = 0; i < a.length; i += 4)
        for (let channel = 0; channel < 3; channel++) error += Math.abs(a[i+channel] - b[i+channel]);
    return {ready: true, error: error / (64*64*3),
        ratioError: Math.abs(width / height - reference.naturalWidth / reference.naturalHeight)};
}"""


async def wait_matching_cover(locator, expected, stage, *, tolerance=8):
    deadline = asyncio.get_running_loop().time() + COVER_TIMEOUT_SECONDS
    result = {"ready": False}
    while asyncio.get_running_loop().time() < deadline:
        if await locator.count() == 1 and await locator.is_visible():
            result = await locator.evaluate(_COMPARE_IMAGE, expected)
            if result.get('ready') and result['error'] <= tolerance and result['ratioError'] < .02:
                return result
        await asyncio.sleep(.2)
    raise RuntimeError(f'快手封面校验失败（{stage}）：选用图片与上传文件不一致或尚未加载，{result}')


async def set_custom_cover(page, path):
    image_path = Path(path)
    mime = mimetypes.guess_type(image_path.name)[0] or 'image/jpeg'
    expected = f'data:{mime};base64,' + base64.b64encode(image_path.read_bytes()).decode('ascii')
    preview = page.locator(MAIN_COVER)
    await preview.wait_for(state='visible', timeout=30000)
    await page.locator('[class*="_default-cover_"]').click()
    modal = page.locator('div[role="document"].ant-modal')
    await modal.wait_for(state='visible', timeout=30000)
    await modal.get_by_text('上传封面', exact=True).click()
    await modal.locator('input[type="file"]').set_input_files(str(image_path))
    # A fixed sleep can confirm the previous video frame while decoding is still pending.
    await wait_matching_cover(modal.locator(RAW_COVER), expected, '上传图片加载', tolerance=2)
    await modal.get_by_role('button', name='确认', exact=True).click()
    await modal.wait_for(state='hidden', timeout=30000)
    await wait_matching_cover(preview, expected, '保存后的选用封面')
