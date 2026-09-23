"""每日交付消费端的本地验证；绝不触发平台上传。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import daily_publish as daily


class DailyPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "发布输出"
        self.root.mkdir()
        self.settings_path = self.base / "daily-settings.json"
        self.settings_path.write_text(json.dumps({
            "output_root": str(self.root),
            "accounts": {name: {"alias": "test", "account_id": "123"} for name in daily.PLATFORMS},
            "platforms": {name: {"access_status": "ready", "category": 95,
                                 "ai_declaration_fields": {"ai_label": 1}} for name in daily.PLATFORMS},
        }), encoding="utf-8")
        (self.base / "publications.json").write_text("{}", encoding="utf-8")
        (self.base / "publishing.json").write_text(json.dumps({"platforms": {"wechat_channels": {
            "enabled": True, "access_status": "ready", "account_id": "123", "verified_on": "2026-09-23"}}}), encoding="utf-8")
        (self.base / "cookies").mkdir()
        for name in ("bilibili", "douyin", "tencent"):
            (self.base / "cookies" / f"{name}_test.json").write_text("{}", encoding="utf-8")
        self.patches = [patch.object(daily, "DB_PATH", self.base / "daily.db"),
                        patch.object(daily, "SETTINGS_PATH", self.settings_path),
                        patch.object(daily, "BASE_DIR", self.base)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.path = self.make_package("2026-09-23", "r001")

    def make_package(self, day, revision):
        directory = self.root / day / revision
        directory.mkdir(parents=True)
        files = {key: f"{key}.bin" for key in ("video", "subtitles", "chapters", "sources", "bilibili", "landscape", "portrait")}
        assets = {}
        for key, name in files.items():
            data = f"{day}-{revision}-{key}".encode()
            (directory / name).write_bytes(data)
            assets[key] = {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        production = {"technical_passed": True, "input_hash": "input", "video_hash": assets["video"]["sha256"],
                      "editorial_review": {"status": "pass", "input_hash": "input"},
                      "visual_review": {"status": "pass", "input_hash": "input", "video_hash": assets["video"]["sha256"]}}
        platforms = {name: {"title": "标题", "description": "简介", "tags": ["AI日报"], "cover": "bilibili",
                            "cover_landscape": "landscape", "cover_portrait": "portrait", "ai_declaration": "内容由AI生成"}
                     for name in daily.PLATFORMS}
        (directory / "package.json").write_text(json.dumps({"schema_version": 1, "timezone": "Asia/Shanghai",
            "edition_id": day, "date": day, "revision": revision, "cover_review": "实际检查通过",
            "assets": assets, "production": production, "platforms": platforms}), encoding="utf-8")
        return directory / "package.json"

    def test_package_integrity_and_today_revision(self):
        second = self.make_package("2026-09-23", "r002")
        path, _, errors = daily.find_today(datetime(2026, 9, 23, 4, tzinfo=timezone.utc))
        self.assertEqual(path, second.resolve())
        self.assertFalse(errors)
        (second.parent / "video.bin").write_bytes(b"changed")
        path, _, errors = daily.find_today(datetime(2026, 9, 23, 4, tzinfo=timezone.utc))
        self.assertEqual(path, self.path.resolve())
        self.assertEqual(len(errors), 1)
        path, _, _ = daily.find_today(datetime(2026, 9, 24, 4, tzinfo=timezone.utc))
        self.assertIsNone(path)

    def test_legacy_and_cross_account_block(self):
        entries = {"2026-09-23::bilibili::123": {"edition_id": "2026-09-23", "platform": "bilibili",
                                                   "account_id": "123", "state": "published", "remote_id": "BV123"},
                   "2026-09-23-preview::bilibili::123": {"edition_id": "2026-09-23-preview", "platform": "bilibili",
                                                            "account_id": "123", "state": "published", "remote_id": "BV123"}}
        (self.base / "publications.json").write_text(json.dumps(entries), encoding="utf-8")
        self.assertEqual(daily.import_legacy(self.base / "publications.json"), 1)
        with self.assertRaisesRegex(ValueError, "历史账本"):
            daily.reserve(self.path, "bilibili", "manual")
        cfg = json.loads(self.settings_path.read_text(encoding="utf-8"))
        cfg["accounts"]["bilibili"]["account_id"] = "999"
        self.settings_path.write_text(json.dumps(cfg), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "其他账号"):
            daily.reserve(self.path, "bilibili", "manual")

    def test_double_click_and_unknown_result_do_not_retry(self):
        def reserve():
            try:
                return daily.reserve(self.path, "douyin", "manual")
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)
        job_id = next(value for value in results if value)
        with patch.object(daily, "_upload"):
            self.assertEqual(daily.run(job_id)["state"], "unknown")
        with self.assertRaisesRegex(ValueError, "不能重发"):
            daily.run(job_id)
        with self.assertRaisesRegex(ValueError, "已预约或提交"):
            daily.reserve(self.path, "douyin", "automation")

    def test_interrupted_upload_becomes_unknown_without_retry(self):
        job_id = daily.reserve(self.path, "douyin", "manual")
        with daily._connection() as conn:
            conn.execute("UPDATE attempts SET updated_at='2026-09-23T00:00:00+08:00' WHERE id=?", (job_id,))
        self.assertEqual(daily.recover_stale_uploads(datetime(2026, 9, 23, 9, tzinfo=timezone.utc)), 1)
        self.assertEqual(daily.task(job_id)["state"], "unknown")
        with self.assertRaisesRegex(ValueError, "已预约或提交"):
            daily.reserve(self.path, "douyin", "manual")

    def test_reconcile_requires_remote_evidence(self):
        job_id = daily.reserve(self.path, "bilibili", "manual")
        with self.assertRaisesRegex(ValueError, "作品 ID"):
            daily.reconcile(job_id, "published", {"platform": "bilibili", "account_id": "123",
                                                       "note": "平台内容管理", "checked_at": "2026-09-23T12:00:00+08:00"})
        result = daily.reconcile(job_id, "processing", {"platform": "bilibili", "account_id": "123",
                          "note": "平台内容管理显示审核中", "remote_id": "BV123", "checked_at": "2026-09-23T12:00:00+08:00"})
        self.assertEqual(result["state"], "processing")
        with self.assertRaisesRegex(ValueError, "已预约或提交"):
            daily.reserve(self.path, "bilibili", "manual")

    def test_failed_result_requires_verified_remote_absence_before_retry(self):
        job_id = daily.reserve(self.path, "douyin", "manual")
        evidence = {"platform": "douyin", "account_id": "123", "note": "内容管理检索该期",
                    "checked_at": "2026-09-23T12:00:00+08:00"}
        with self.assertRaisesRegex(ValueError, "remote_absent"):
            daily.reconcile(job_id, "failed", evidence)
        daily.reconcile(job_id, "failed", {**evidence, "remote_absent": True})
        self.assertNotEqual(daily.reserve(self.path, "douyin", "manual"), job_id)

    def test_unverified_legacy_failure_remains_unknown(self):
        (self.base / "publications.json").write_text(json.dumps({"old": {
            "edition_id": "2026-09-23", "platform": "douyin", "account_id": "123", "state": "failed"}}), encoding="utf-8")
        daily.import_legacy(self.base / "publications.json")
        self.assertEqual(daily.status_for(daily.load_package(self.path))["douyin"]["state"], "unknown")
        with self.assertRaisesRegex(ValueError, "历史账本"):
            daily.reserve(self.path, "douyin", "manual")

    def test_channels_workspace_block_cannot_be_overridden_by_tool_config(self):
        (self.base / "publishing.json").write_text(json.dumps({"platforms": {"wechat_channels": {
            "enabled": True, "access_status": "blocked_browser_policy", "account_id": "123"}}}), encoding="utf-8")
        package = daily.load_package(self.path)
        self.assertEqual(daily.status_for(package)["wechat_channels"]["access_status"], "blocked_browser_policy")
        with self.assertRaisesRegex(ValueError, "访问或账号核验"):
            daily.reserve(self.path, "wechat_channels", "manual")

    def test_manual_draft_cannot_remove_required_ai_declaration(self):
        data = daily.load_package(self.path)["platforms"]["douyin"]
        draft = daily.save_draft(self.path, "douyin", "123", {**data, "ai_declaration": ""})
        self.assertEqual(daily.get_draft(self.path, "douyin", "123"), draft)
        with self.assertRaisesRegex(ValueError, "AI 内容声明"):
            daily.reserve(self.path, "douyin", "manual")

    def test_adapters_receive_description_cover_and_declaration(self):
        import sau_cli
        package = daily.load_package(self.path)
        cases = [
            ("bilibili", "upload_bilibili_video", "extra_fields"),
            ("douyin", "upload_video", "declaration"),
            ("wechat_channels", "upload_tencent_video", "require_content_label"),
        ]
        for platform, function, field in cases:
            with self.subTest(platform=platform):
                mocked = AsyncMock()
                with patch.object(sau_cli, function, mocked):
                    daily._upload({"platform": platform, "package_path": str(self.path),
                                   "payload": package["platforms"][platform]}, package)
                request = mocked.call_args.args[0]
                self.assertEqual(request.description, "简介")
                self.assertTrue(request.video_file.is_file())
                self.assertTrue(getattr(request, field))
                if platform == "wechat_channels":
                    self.assertTrue(request.require_thumbnail)

    def test_channels_can_use_verified_web_account_without_copying_cookie(self):
        from uploader.tencent_uploader import main as tencent
        cfg = json.loads(self.settings_path.read_text(encoding="utf-8"))
        cfg["accounts"]["wechat_channels"] = {"web_account_id": 7, "account_id": "123"}
        self.settings_path.write_text(json.dumps(cfg), encoding="utf-8")
        (self.base / "db").mkdir()
        conn = sqlite3.connect(self.base / "db" / "database.db")
        try:
            conn.execute("CREATE TABLE user_info(id INTEGER,type INTEGER,filePath TEXT,status INTEGER)")
            conn.execute("INSERT INTO user_info VALUES(7,2,'web.json',1)")
            conn.commit()
        finally:
            conn.close()
        (self.base / "cookiesFile").mkdir()
        (self.base / "cookiesFile" / "web.json").write_text("{}", encoding="utf-8")
        job_id = daily.reserve(self.path, "wechat_channels", "manual")
        with patch.object(tencent, "cookie_auth", AsyncMock(return_value=True)), \
             patch.object(tencent, "TencentVideo") as uploader:
            uploader.return_value.tencent_upload_video = AsyncMock()
            self.assertEqual(daily.run(job_id)["state"], "unknown")
            self.assertEqual(Path(uploader.call_args.kwargs["account_file"]), (self.base / "cookiesFile" / "web.json").resolve())
            self.assertTrue(uploader.call_args.kwargs["require_content_label"])
        with self.assertRaisesRegex(ValueError, "已预约或提交"):
            daily.reserve(self.path, "wechat_channels", "manual")


if __name__ == "__main__":
    unittest.main()
