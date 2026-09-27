"""Offline scheduler checks; platform uploaders are never called."""
from datetime import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import daily_publish as daily
from publishing import automation, queue, service
from tests.test_daily_publish import DailyPublishTests


@pytest.fixture
def case():
    fixture = DailyPublishTests()
    fixture.setUp()
    try:
        with patch.object(queue, "ensure_worker"):
            yield fixture
    finally:
        fixture.doCleanups()


def configure(case):
    data = automation.read_settings()
    data["enabled"] = True
    for item in data["platforms"].values():
        item["enabled"] = True
        item["verified"] = True
    automation.save_settings(data)
    return data


def test_automation_is_opt_in_and_requires_platform_verification(case):
    assert automation.read_settings()["enabled"] is False
    assert automation.tick(datetime(2026, 9, 23, 7, tzinfo=daily.BEIJING)) == []
    data = automation.read_settings()
    data["enabled"] = True
    data["platforms"]["wechat_channels"]["enabled"] = True
    with pytest.raises(ValueError, match="验收"):
        automation.save_settings(data)
    path = Path(service.today()["package_path"])
    with pytest.raises(ValueError, match="验收"):
        daily.reserve(path, "wechat_channels", "automation", queued=True)


def test_three_platforms_reserve_once_and_share_ledger(case):
    configure(case)
    now = datetime(2026, 9, 23, 8, tzinfo=daily.BEIJING)
    jobs = automation.tick(now)
    assert len(jobs) == 3
    assert automation.tick(now) == []
    with daily._connection() as conn:
        attempts = conn.execute("SELECT platform,source,payload FROM attempts ORDER BY platform").fetchall()
        assert len(attempts) == 3
        assert all(row["source"] == "automation" and json.loads(row["payload"])["one_click"] for row in attempts)
        assert conn.execute("SELECT COUNT(*) FROM delivery_queue").fetchone()[0] == 3
    status = automation.status(now)
    assert set(status["events"]) == set(daily.PLATFORMS)
    token = service.today()["selection_id"]
    result = service.publish_daily(token, ["bilibili"])
    assert not result["jobs"] and result["errors"]["bilibili"]


def test_scheduler_waits_for_window_and_does_not_take_worker_pause_as_permission(case):
    configure(case)
    assert automation.tick(datetime(2026, 9, 23, 5, 59, tzinfo=daily.BEIJING)) == []
    assert automation.tick(datetime(2026, 9, 23, 23, 1, tzinfo=daily.BEIJING)) == []
    with daily._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_reserved_automation_expiring_in_queue_never_uploads(case):
    config = configure(case)
    config["deadline"] = "06:30"
    automation.save_settings(config)
    jobs = automation.tick(datetime(2026, 9, 23, 6, 20, tzinfo=daily.BEIJING))
    assert jobs
    with patch.object(daily, "_upload") as upload:
        assert queue.process_next() is True
    upload.assert_not_called()
    expired = [daily.task(ident) for ident in jobs if daily.task(ident)["state"] == "failed"]
    assert len(expired) == 1
    assert "截止" in expired[0]["error"]


def test_worker_readback_selects_only_supported_original_tasks(case):
    bundle = service.today(cached=False)
    result = service.publish_daily(bundle['selection_id'], ['douyin', 'bilibili'])
    ids = {job['platform']: job['task_id'] for job in result['jobs']}
    with daily._connection() as conn:
        for platform, ident in ids.items():
            row = conn.execute('SELECT payload FROM attempts WHERE id=?', (ident,)).fetchone()
            payload = json.loads(row['payload'])
            if platform == 'douyin':
                payload['_delivery']['id'] = 'douyin_api'
            conn.execute('UPDATE attempts SET state=?,remote_id=?,payload=? WHERE id=?',
                         ('processing', 'remote-fixture', json.dumps(payload), ident))
    with patch.object(service, 'sync_result', return_value={'sync': {'status': 'found'}}) as sync:
        assert queue.sync_pending() == 2
        assert {call.args[0] for call in sync.call_args_list} == {ids['douyin'], ids['bilibili']}
