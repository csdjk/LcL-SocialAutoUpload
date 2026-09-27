"""Isolated fixtures: no real account, browser publication or worker launch."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import patch, Mock
import pytest
import daily_publish as daily
from publishing import service, queue, transports, douyin_api
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


def enqueue(case, platform="bilibili"):
    bundle = service.today(cached=False)
    result = service.publish_daily(bundle["selection_id"], [platform])
    assert not result["errors"]
    return result["jobs"][0]["task_id"]


def test_mcp_and_manual_share_atomic_ledger(case):
    token = service.today(cached=False)["selection_id"]
    payload = daily.load_package(case.path)["platforms"]["bilibili"]
    def call(index):
        try:
            if index % 2:
                return service.submit_one(case.path, "bilibili", "123", payload)
            return service.publish_daily(token, ["bilibili"])
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(call, range(6)))
    with daily._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM delivery_queue").fetchone()[0] == 1
    assert service.list_tasks()[0]["state"] == "queued"


@pytest.mark.parametrize("change", ["draft", "account", "package"])
def test_stale_mcp_selection_never_publishes(case, change):
    token = service.today(cached=False)["selection_id"]
    if change == "draft":
        material = daily.load_package(case.path)["platforms"]["bilibili"]
        daily.save_draft(case.path, "bilibili", "123", {**material, "title": "用户的新文案"})
    elif change == "account":
        config = daily.settings(); config["accounts"]["bilibili"]["account_id"] = "another"
        case.settings_path.write_text(json.dumps(config), encoding="utf-8")
    else:
        case.make_package("2026-09-23", "r002")
    with pytest.raises(ValueError, match="重新调用"):
        service.publish_daily(token, ["bilibili"])
    assert service.list_tasks() == []


def test_queue_recovery_resumes_only_unclaimed_attempt(case):
    ident = enqueue(case)
    with daily._connection() as conn:
        conn.execute("UPDATE delivery_queue SET state='running'")
        conn.execute("UPDATE attempts SET state='uploading'")
    queue.recover_interrupted()
    assert daily.task(ident)["state"] == "queued"
    with patch.object(daily, "_upload", return_value={"status": "found", "remote_id": "fixture", "source": "official_api_receipt"}) as upload:
        assert queue.process_next()
        assert not queue.process_next()
        upload.assert_called_once()
    result = service.task(ident)
    assert result["state"] == "processing"
    assert result["remote_id"] == "fixture"
    assert "payload" not in result


def test_queue_recovery_never_replays_claimed_attempt(case):
    ident = enqueue(case)
    with daily._connection() as conn:
        conn.execute("UPDATE delivery_queue SET state='running'")
        conn.execute("UPDATE attempts SET state='uploading'")
        conn.execute("INSERT INTO run_claims VALUES(?,?)", (ident, "fixture"))
    queue.recover_interrupted()
    with patch.object(daily, "_upload") as upload:
        assert not queue.process_next()
        upload.assert_not_called()
    assert daily.task(ident)["state"] == "unknown"
    assert "bilibili" not in service.today()["publishable_platforms"]


def test_package_changes_after_enqueue_fail_before_upload(case):
    ident = enqueue(case)
    package = json.loads(case.path.read_text(encoding="utf-8"))
    package["platforms"]["bilibili"]["description"] = "之后改过的文案"
    case.path.write_text(json.dumps(package), encoding="utf-8")
    with patch.object(daily, "_upload") as upload:
        queue.process_next()
        upload.assert_not_called()
    assert daily.task(ident)["state"] == "failed"
    assert daily.task(ident)["evidence"]["upload_started"] is False


def test_queue_lock_and_active_reconciliation(case):
    ident = enqueue(case)
    with queue.worker_lock() as first:
        assert first
        with queue.worker_lock() as second:
            assert not second
    with pytest.raises(daily.ReconcileConflictError, match="排队"):
        daily.reconcile(ident, "failed", {})


def test_channel_mcp_explicit_request_is_supported(case):
    ident = enqueue(case, "wechat_channels")
    assert daily.task(ident)["source"] == "mcp"
    assert daily.task(ident)["payload"]["cover_mode"] == "custom"


@pytest.mark.parametrize("mode", ["custom", "video_frame"])
def test_channel_mcp_respects_explicit_draft_cover_mode(case, mode):
    material = daily.load_package(case.path)["platforms"]["wechat_channels"]
    daily.save_draft(case.path, "wechat_channels", "123", {**material, "cover_mode": mode})
    ident = enqueue(case, "wechat_channels")
    assert daily.task(ident)["payload"]["cover_mode"] == mode


def test_channel_mcp_passes_package_dual_covers_to_uploader(case):
    from unittest.mock import AsyncMock
    ident = enqueue(case, "wechat_channels")
    job = daily.task(ident)
    package = daily.load_package(case.path)
    cookie = case.base / "cookies" / "tencent_test.json"
    with patch.object(daily, "_web_channels_cookie", return_value=cookie), \
         patch("uploader.tencent_uploader.main.TencentVideo") as uploader:
        uploader.return_value.tencent_upload_video = AsyncMock(return_value={"status": "found", "remote_id": "fixture"})
        daily._upload(job, package)
    options = uploader.call_args.kwargs
    assert options["cover_mode"] == "custom"
    for orientation in ("landscape", "portrait"):
        asset = package["assets"][job["payload"][f"cover_{orientation}"]]
        path = Path(options[f"thumbnail_{orientation}_path"])
        assert path.resolve() == (case.path.parent / asset["path"]).resolve()
        assert daily.hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]


def test_batch_preserves_partial_success_and_deduplicates(case):
    import sau_backend
    materials = daily.load_package(case.path)["platforms"]
    targets = [{"platform": key, "account_id": "123", "payload": material} for key, material in materials.items() if key != "wechat_channels"]
    targets[1]["account_id"] = "wrong"
    client = sau_backend.app.test_client()
    result = client.post("/daily/publish-batch", json={"package_path": str(case.path), "targets": targets}, headers={"X-SAU-Local": "1"})
    assert result.status_code == 200
    assert len(result.json["data"]["jobs"]) == 1
    assert "douyin" in result.json["data"]["errors"]
    assert client.post("/daily/publish-batch", json={"targets": targets}, headers={"X-SAU-Local": "1", "Origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize('platform,kind', [('bilibili',5),('douyin',3),('youtube',6),('toutiao',7),('kuaishou',4)])
def test_all_platforms_bind_existing_tool_accounts_without_cookie_copy(case, platform, kind):
    from contextlib import closing
    (case.base/'db').mkdir(exist_ok=True)
    (case.base/'cookiesFile').mkdir(exist_ok=True)
    cookie=case.base/'cookiesFile'/'fixture.json';cookie.write_text('{}')
    if platform == 'youtube':
        from tests.test_youtube_api import credential_fixture
        cookie.write_text(json.dumps(credential_fixture()))
    with closing(daily.sqlite3.connect(case.base/'db/database.db')) as conn:
        conn.execute('CREATE TABLE user_info(id INTEGER PRIMARY KEY,type INTEGER,userName TEXT,status INTEGER,filePath TEXT)')
        conn.execute('INSERT INTO user_info VALUES(7,?,?,1,?)',(kind,'fixture','fixture.json'));conn.commit()
    options=daily.account_binding_options(platform)
    assert options['accounts'][0]['selectable']
    bound=daily.bind_account(platform,7,options['revision'])
    assert bound['account']['account_id']==f'web:{platform}:7'
    assert daily._web_account_cookie(platform,daily._account(platform))==cookie.resolve()
    assert daily.platform_access(platform,daily._account(platform),daily.settings())=='ready'
    ident=enqueue(case,platform)
    with pytest.raises(ValueError,match='正在执行'):
        daily.bind_account(platform,7,daily.account_binding_options(platform)['revision'])
    with pytest.raises(ValueError):
        daily._web_account_cookie('wechat_channels',daily._account(platform))
    assert daily.task(ident)['account_id']==f'web:{platform}:7'


def credentials(case):
    path = case.base / "credentials.json"
    data = {"access_token": "fixture-secret-never-logged", "open_id": "fixture-open",
            "expires_at": "2099-01-01T00:00:00+00:00", "scopes": ["video.create.bind"]}
    path.write_text(json.dumps(data), encoding="utf-8")
    config = daily.settings()
    config["platforms"]["douyin"]["api"] = {"credentials_file": str(path)}
    config["accounts"]["douyin"]["open_id"] = data["open_id"]
    return config, path, data


def test_api_preference_and_explicit_capability_limit(case):
    config, path, _ = credentials(case)
    material = {"cover_mode": "video_frame"}
    assert transports.resolve("douyin", material, config, case.base)["id"] == "douyin_api"
    material["ai_declaration"] = "内容由AI生成"
    route = transports.resolve("douyin", material, config, case.base)
    assert route["id"] == "browser" and "AI" in route["reason"]
    config["platforms"]["douyin"]["transport"] = "douyin_api"
    with pytest.raises(ValueError, match="AI"):
        transports.resolve("douyin", material, config, case.base)
    config["platforms"]["douyin"]["transport"] = "auto"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="授权文件"):
        transports.resolve("douyin", {"cover_mode": "video_frame"}, config, case.base)


def response(data):
    return Mock(status_code=200, json=Mock(return_value={"data": {"error_code": 0, **data}}))


@pytest.mark.parametrize("failure", [None, "upload", "create"])
def test_api_publication_boundary_and_no_retry(case, failure):
    from uploader.tencent_uploader.flow import SubmissionNotStarted
    config, path, data = credentials(case)
    session = Mock()
    session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False)
    responses = [response({"video": {"video_id": "uploaded-id"}}), response({"video_id": "123456"})]
    if failure == "upload": responses[0] = douyin_api.requests.Timeout("fixture-secret-never-logged")
    if failure == "create": responses[1] = douyin_api.requests.Timeout("fixture-secret-never-logged")
    session.post.side_effect = responses
    material = {"title": "API 测试", "description": "离线测试不投稿", "cover_mode": "video_frame"}
    with patch.object(douyin_api.requests, "Session", return_value=session):
        if failure:
            error = SubmissionNotStarted if failure == "upload" else RuntimeError
            with pytest.raises(error) as caught:
                douyin_api.publish(case.path.parent / "video.bin", material, data, lambda value: None)
            assert "fixture-secret" not in str(caught.value)
            if failure == "create": assert not isinstance(caught.value, SubmissionNotStarted)
        else:
            result = douyin_api.publish(case.path.parent / "video.bin", material, data, lambda value: None)
            assert result["remote_id"] == "123456" and result["source"] == "official_api_receipt"
    assert session.post.call_count == (1 if failure == "upload" else 2)


def test_mcp_tool_calls_use_shared_service(case):
    import publisher_mcp
    bundle = publisher_mcp.get_daily_package()
    result = publisher_mcp.publish_daily(bundle["selection_id"], ["bilibili"])
    ident = result["jobs"][0]["task_id"]
    assert publisher_mcp.get_publish_task(ident)["state"] == "queued"
    assert publisher_mcp.list_publish_tasks()[0]["id"] == ident
    assert publisher_mcp.publish_daily(bundle["selection_id"], ["bilibili"])["errors"]
