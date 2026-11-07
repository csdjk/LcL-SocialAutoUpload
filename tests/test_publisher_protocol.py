"""Real stdio handshake and browser fixtures; no platform publication."""
import asyncio
import json
from pathlib import Path
import sys
from unittest.mock import AsyncMock, Mock
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from publishing.browser_submit import submit_once
from publishing.bilibili_receipt import parse_receipt


def test_real_stdio_protocol_and_invalid_publish(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / "输出"; output.mkdir()
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"output_root": str(output)}), encoding="utf-8")
    params = StdioServerParameters(command=sys.executable,
        args=["-X", "utf8", str(root / "publisher_mcp.py"), "--settings", str(settings),
              "--database", str(tmp_path / "jobs.db"), "--base", str(tmp_path)])
    async def scenario():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "get_daily_package", "publish_daily", "get_publish_task", "list_publish_tasks", "sync_publish_result",
                    "preview_daily_update", "update_daily_work", "get_work_update", "sync_work_update", "list_work_updates",
                    "preview_history_publish", "publish_history",
                    "preview_work_replacement", "replace_daily_work", "get_work_replacement",
                    "list_work_replacements", "sync_work_replacement", "resume_work_replacement"}
                descriptors = {tool.name: tool for tool in tools.tools}
                assert 'edition_id' in descriptors['preview_daily_update'].inputSchema['properties']
                assert descriptors['replace_daily_work'].annotations.destructiveHint is True
                assert descriptors['resume_work_replacement'].annotations.destructiveHint is True
                assert 'retry_existing_delete' in descriptors['resume_work_replacement'].inputSchema['properties']
                assert 'target_remote_id' in descriptors['preview_work_replacement'].inputSchema['properties']
                assert 'target_remote_id' in descriptors['replace_daily_work'].inputSchema['properties']
                result = await session.call_tool('preview_work_replacement', {'edition_id': '2026-09-23', 'platform': 'youtube'})
                assert result.isError
                result = await session.call_tool("get_daily_package", {})
                assert not result.isError
                assert json.loads(result.content[0].text)["package"] is None
                result = await session.call_tool("publish_daily", {"selection_id": "stale", "platforms": ["bilibili"]})
                assert result.isError
                result = await session.call_tool("list_publish_tasks", {})
                assert not result.isError
                assert not result.content
    asyncio.run(scenario())


def test_bilibili_receipt_needs_successful_structured_id():
    value = '{"code":0,"data":{"aid":123,"bvid":"BV1234567890"}}'
    assert parse_receipt("INFO " + value)["remote_id"] == "BV1234567890"
    assert parse_receipt("上传成功 BV1234567890") is None
    assert parse_receipt(value.replace('"code":0', '"code":1')) is None
    assert parse_receipt(value + value.replace("BV1234567890", "BV0987654321")) is None


def test_mcp_browser_update_does_not_nest_event_loops(monkeypatch):
    import publisher_mcp
    from publishing import updates
    def browser_preview(*args):
        async def read():
            return {'preview_id':'checked','payload':{}}
        return asyncio.run(read())
    monkeypatch.setattr(updates,'preview',browser_preview)
    assert asyncio.run(publisher_mcp.preview_daily_update('xiaohongshu')) == {'preview_id':'checked'}


def test_browser_final_click_timeout_is_never_retried():
    button = Mock(count=AsyncMock(return_value=1), wait_for=AsyncMock(), click=AsyncMock(side_effect=TimeoutError("uncertain click")))
    page = Mock(get_by_role=Mock(return_value=button))
    with pytest.raises(TimeoutError):
        asyncio.run(submit_once(page))
    button.click.assert_awaited_once()


def test_real_browser_final_button_clicked_once():
    from playwright.async_api import async_playwright
    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="msedge", headless=True)
            try:
                page = await browser.new_page()
                await page.set_content('<body data-clicks="0"><button onclick="document.body.dataset.clicks=Number(document.body.dataset.clicks)+1">发布</button></body>')
                with pytest.raises(RuntimeError, match="不会自动再次点击"):
                    await submit_once(page, timeout=.1)
                assert await page.locator("body").get_attribute("data-clicks") == "1"
            finally:
                await browser.close()
    asyncio.run(scenario())
