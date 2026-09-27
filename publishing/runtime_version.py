"""Detect stale services before accepting a publication after a local upgrade."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_revision():
    paths = [ROOT / name for name in ('daily_publish.py', 'sau_cli.py', 'sau_backend.py', 'publisher_mcp.py')]
    paths += list((ROOT / 'publishing').glob('*.py'))
    for folder in ('tencent_uploader', 'douyin_uploader', 'bilibili_uploader', 'ks_uploader'):
        paths += list((ROOT / 'uploader' / folder).glob('*.py'))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


LOADED_REVISION = source_revision()


def require_current():
    if source_revision() != LOADED_REVISION:
        raise ValueError('投稿代码已更新，请重启本机服务并重新连接 MCP 后再提交；尚未创建新任务')
