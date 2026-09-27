"""Local screenshots for failed publishing stages; no cookies or request bodies."""
from datetime import datetime
from pathlib import Path
import logging


async def capture(page, platform, stage):
    from conf import BASE_DIR
    folder = Path(BASE_DIR) / 'Temp' / 'publish-diagnostics'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f'{platform}-{stage}-{datetime.now():%Y%m%d-%H%M%S-%f}.png'
    try:
        await page.screenshot(path=str(path), full_page=True, timeout=5000)
        return str(path)
    except Exception as exc:
        logging.getLogger(__name__).warning('投稿诊断截图未保存: %s', type(exc).__name__)
        return None
