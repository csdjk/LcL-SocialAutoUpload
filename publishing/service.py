"""Shared use cases for the local UI and stdio MCP server."""
import hashlib
import json
from pathlib import Path
import daily_publish as daily
from publishing import queue, transports
from publishing.platforms import material_for, package_view
from publishing.runtime_version import LOADED_REVISION

EDITABLE = {"title", "description", "tags", "short_title", "category", "ai_declaration", "cover_mode", "visibility", "made_for_kids"}
PUBLIC_ACCOUNT = {"account_id", "alias", "display_name", "web_account_id", "identity_source", "open_id"}


def _material(path, package, platform, account):
    draft = daily.get_draft(path, platform, str(account.get("account_id", ""))) or {}
    material = {**material_for(package, platform), **{key: value for key, value in draft.items() if key in EDITABLE}}
    if platform == "wechat_channels":
        material.setdefault("cover_mode", "custom")
    if platform == 'bilibili':
        from publishing.bilibili_metadata import category_for
        material['category'] = category_for(str(account.get('account_id', '')), material, daily.settings())
    return material


def today(*, cached=True):
    daily.import_legacy(daily.output_root().parent / "publications.json")
    path, package, errors = daily.find_today_cached() if cached else daily.find_today()
    if not package:
        return {"package_path": None, "package": None, "status": {}, "drafts": {},
                "errors": errors, "selection_id": None, "publishable_platforms": []}
    config = daily.settings()
    statuses = daily.status_for(package)
    drafts, selection, publishable = {}, {}, []
    for platform, item in statuses.items():
        account = item["account"]
        drafts[platform] = daily.get_draft(path, platform, str(account.get("account_id", "")))
        material = _material(path, package, platform, account)
        item["account"] = {k: v for k, v in account.items() if k in PUBLIC_ACCOUNT}
        if drafts[platform]:
            drafts[platform] = {k: v for k, v in drafts[platform].items() if k in EDITABLE}
        try:
            channel = transports.resolve(platform, material, config, daily.BASE_DIR)
            item["transport"] = channel
            selection[platform] = {"material": material, "channel": channel["fingerprint"], "account": item["account"]}
            if item["access_status"] == "ready":
                daily._account(platform)
                if channel["id"] != "douyin_api" and platform != "wechat_channels":
                    cookie = daily._web_account_cookie(platform, account) or Path(daily.BASE_DIR) / "cookies" / f"{platform}_{account.get('alias', '')}.json"
                    if not cookie.is_file():
                        item["access_status"] = "cookie_missing"
                if platform == "bilibili":
                    options = config.get("platforms", {}).get(platform, {})
                    from publishing.bilibili_metadata import category_for
                    category = category_for(str(account['account_id']), material, config)
                    item['category'] = category
                    if not str(category or "").isdigit() or int(category) <= 0:
                        raise ValueError("请选择B站分区，可自动沿用最近成功的AI日报分区")
        except ValueError as exc:
            item["access_status"] = "configuration_required"
            item["access_message"] = str(exc)
            item.setdefault("transport", {"id": "unavailable", "label": "通道待配置", "reason": str(exc)})
            selection[platform] = {"error": str(exc), "account": item["account"], "material": material}
        if (item["access_status"] == "ready" and not item["account_mismatch"]
                and (item["state"] == "ready" or (item["state"] == "failed" and item["retry_allowed"] and not item["remote_id"]))):
            publishable.append(platform)
    # Receipt/task changes do not invalidate a selection, but text, account, route
    # or package changes do. Repeated submissions still hit the atomic ledger gate.
    snapshot = {"manifest": daily.package_digest(package), "platforms": selection}
    token = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"package_path": str(path), "package": package_view(package), "status": statuses, "drafts": drafts,
            "errors": errors, "selection_id": token, "publishable_platforms": publishable,
            'runtime_revision': LOADED_REVISION}


def _start(jobs):
    if not jobs:
        return None
    try:
        queue.ensure_worker()
    except OSError:
        # The reservation still exists. Do not mark it failed or create a second job.
        return "任务已保存，但后台执行器未启动。请重启本机发布工具恢复队列；不要重复投稿。"
    return None


def publish_daily(selection_id, platforms):
    if (not isinstance(platforms, list) or not platforms or len(platforms) != len(set(platforms))
            or any(key not in daily.PLATFORMS for key in platforms)):
        raise ValueError("请明确指定不重复的平台列表")
    bundle = today(cached=False)
    if not selection_id or not bundle["package"] or selection_id != bundle["selection_id"]:
        raise ValueError("今日资源、草稿或账号已变化，请先重新调用 get_daily_package")
    jobs, errors = [], {}
    for platform in platforms:
        status = bundle["status"][platform]
        if platform not in bundle["publishable_platforms"]:
            errors[platform] = status.get("access_message") or f"当前状态 {status['state']} / {status['access_status']} 不允许再次投稿"
            continue
        material = _material(Path(bundle["package_path"]), bundle["package"], platform, status["account"])
        material["one_click"] = True
        try:
            ident = daily.reserve(Path(bundle["package_path"]), platform, "mcp", material, queued=True,
                                  expected_transport=status["transport"]["fingerprint"])
            jobs.append({"task_id": ident, "platform": platform, "state": "queued"})
        except (ValueError, OSError) as exc:
            errors[platform] = str(exc)
    warning = _start(jobs)
    return {"jobs": jobs, "errors": errors, "warning": warning,
            "message": "入队仅表示接受任务；请调用 get_publish_task 查看投稿结果。"}


def submit_one(path, platform, account_id, payload):
    if platform not in daily.PLATFORMS or not isinstance(payload, dict):
        raise ValueError("投稿平台或内容无效")
    if str(daily._account(platform).get("account_id")) != str(account_id):
        raise ValueError("发布账号已变化，请刷新并确认当前账号")
    clean = {key: value for key, value in payload.items() if key in EDITABLE}
    if platform == "wechat_channels" and clean.get("cover_mode") not in ("video_frame", "custom"):
        raise ValueError("请选择视频画面或自定义封面")
    if not isinstance(clean.get("title"), str) or not clean["title"].strip():
        raise ValueError("请输入标题")
    if not isinstance(clean.get("description"), str) or not clean["description"].strip():
        raise ValueError("请输入简介")
    if not isinstance(clean.get("tags", []), list) or any(not isinstance(x, str) for x in clean.get("tags", [])):
        raise ValueError("话题格式无效")
    ident = daily.reserve(Path(path), platform, "manual", {**clean, "one_click": True}, queued=True)
    # The task already owns the submitted material even if draft persistence fails.
    draft_warning = None
    try:
        daily.save_draft(Path(path), platform, str(account_id), clean)
    except (ValueError, OSError, daily.sqlite3.Error):
        draft_warning = "任务已保存；编辑草稿未能另存，任务仍使用此次提交的文案。"
    warning = _start([ident]) or draft_warning
    return {"task_id": ident, "state": "queued", "warning": warning,
            "message": warning or "任务已入队，可查看卡片进度；关闭页面不影响后台执行"}


def task(ident):
    row = daily.task(ident)
    return {key: row[key] for key in ("id", "edition_id", "platform", "account_id", "revision", "source",
                                     "state", "remote_id", "url", "evidence", "error", "created_at", "updated_at")}


def list_tasks(limit=20):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")
    with daily._connection() as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM attempts ORDER BY created_at DESC LIMIT ?", (limit,))]
    return [task(ident) for ident in ids]


def sync_result(ident):
    current = daily.task(ident)
    if current["state"] in {"queued", "uploading"}:
        raise ValueError("任务仍在排队或执行，请勿同时核对或重复投稿")
    if current['platform'] == 'bilibili':
        from publishing.bilibili_metadata import readback
        account = daily._account('bilibili')
        if str(account['account_id']) != current['account_id']:
            raise ValueError('发布账号已变化，无法核对原账号稿件')
        cookie = daily._web_account_cookie('bilibili', account) or Path(daily.BASE_DIR) / 'cookies' / f"bilibili_{account['alias']}.json"
        package = daily.load_package(Path(current['package_path']))
        result = readback(cookie, current['remote_id'], title=current['payload']['title'], day=package['date'])
        if result.get('status') in ('found', 'absent'):
            _save_readback(current, result)
        return {'task': task(ident), 'sync': result}
    if current["platform"] == "wechat_channels":
        from myUtils.daily_oneclick import sync_result as channels_sync
        result = channels_sync(ident)
        return {"task": task(ident), "sync": result["sync"]}
    if current['platform'] in ('douyin', 'toutiao', 'kuaishou', 'xiaohongshu') and current['payload'].get('_delivery', {}).get('id') == 'browser':
        from publishing.browser_readback import readback
        platform = current['platform']
        account = daily._account(platform)
        if str(account['account_id']) != current['account_id']:
            raise ValueError('发布账号已变化，无法核对原账号作品')
        cookie = daily._web_account_cookie(platform, account) or Path(daily.BASE_DIR) / 'cookies' / f"{platform}_{account['alias']}.json"
        package = daily.load_package(Path(current['package_path']))
        if platform == 'xiaohongshu':
            from publishing.xiaohongshu_browser import readback as xhs_readback
            result = daily.asyncio.run(xhs_readback(cookie, current['payload'], package['date'], current['remote_id']))
        else:
            result = daily.asyncio.run(readback(platform, cookie, current['payload'], package['date'], current['remote_id']))
        if result.get('status') == 'absent' and current['state'] == 'unknown':
            elapsed = (daily.datetime.now(daily.BEIJING) - daily.datetime.fromisoformat(current['updated_at'])).total_seconds()
            if elapsed < 600:
                result = {'status': 'unknown', 'message': '提交结果仍可能延迟出现在后台，暂不解除防重复保护；请稍后核对'}
        if result.get('status') in ('found', 'absent'):
            _save_readback(current, result)
        return {'task': task(ident), 'sync': result}
    if current['platform'] == 'youtube' and current['payload'].get('_delivery', {}).get('id') == 'youtube_api' and current['remote_id']:
        from publishing.youtube_api import account_path, readback
        config = daily.settings()
        channel = transports.resolve('youtube', current['payload'], config, daily.BASE_DIR)
        if channel['fingerprint'] != current['payload']['_delivery']['fingerprint']:
            raise ValueError('原任务账号或通道已变化，无法核对原稿件')
        result = readback(current['remote_id'], account_path(daily.BASE_DIR, daily._account('youtube')))
        if result.get('status') == 'found':
            warnings = (current.get('evidence') or {}).get('warnings', [])
            state = 'needs_action' if warnings and result['state'] == 'published' else result['state']
            # Reconciliation preserves the original video ID and prevents a second insert.
            proof = {'source': 'official_api_query', 'platform': 'youtube', 'account_id': current['account_id'],
                     'remote_id': current['remote_id'], 'url': f"https://www.youtube.com/watch?v={current['remote_id']}",
                     'checked_at': result['checked_at'], 'note': result['note'] + (' ' + ' '.join(warnings) if warnings else ''),
                     'message': result['note'] + (' ' + ' '.join(warnings) if warnings else ''),
                     'warnings': warnings, 'privacy': result['privacy'], 'processing': result['processing']}
            if current['state'] == 'published' and state != 'published':
                result['message'] = '平台状态已变化，请在 Studio 核对；保留已发布记录。'
            else:
                with daily._connection() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    changed = conn.execute('UPDATE attempts SET state=?,error=NULL,evidence=?,updated_at=? '
                                           'WHERE id=? AND updated_at=? AND state=? AND remote_id=?',
                                           (state, json.dumps(proof, ensure_ascii=False), result['checked_at'], ident,
                                            current['updated_at'], current['state'], current['remote_id']))
                    if changed.rowcount != 1:
                        raise ValueError('任务在回读期间发生变化，请刷新后重试')
        return {'task': task(ident), 'sync': result}
    if current["payload"].get("_delivery", {}).get("id") == "douyin_api" and current["remote_id"]:
        from publishing.douyin_api import readback, read_credentials
        config = daily.settings()
        channel = transports.resolve("douyin", current["payload"], config, daily.BASE_DIR)
        if channel["fingerprint"] != current["payload"]["_delivery"]["fingerprint"]:
            raise ValueError("原任务账号或通道已变化，无法核对原稿件")
        credentials = read_credentials(transports.credentials_path(config["platforms"]["douyin"]["api"], daily.BASE_DIR),
                                       daily._account("douyin"))
        result = readback(current["remote_id"], credentials)
        if result.get("status") == "found":
            now = daily.datetime.now(daily.BEIJING).isoformat()
            proof = {"source": "official_api_query", "platform": "douyin",
                     "account_id": current["account_id"], "remote_id": current["remote_id"],
                     "platform_status": result.get("platform_status"), "checked_at": now,
                     "note": "官方接口找到原作品；状态码保留原值，未推断为公开发布。"}
            with daily._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                fresh = conn.execute("SELECT state,updated_at,remote_id FROM attempts WHERE id=?", (ident,)).fetchone()
                if not fresh or fresh["updated_at"] != current["updated_at"] or fresh["state"] in {"queued", "uploading"}:
                    raise ValueError("任务在回读期间发生变化，请刷新后重试")
                if fresh["remote_id"] != current["remote_id"]:
                    raise ValueError("平台作品 ID 与原任务回执不一致")
                conn.execute("UPDATE attempts SET state=?,evidence=?,updated_at=? WHERE id=?",
                             ("published" if fresh["state"] == "published" else "processing",
                              json.dumps(proof, ensure_ascii=False), now, ident))
        return {"task": task(ident), "sync": result}
    return {"task": task(ident), "sync": {"status": "unavailable", "message": "此通道暂需在官方内容管理核对作品，未再次上传或投稿"}}


def _save_readback(current, result):
    now = daily.datetime.now(daily.BEIJING).isoformat()
    proof = {**(current.get('evidence') or {}), **result, 'source': 'official_content_list',
             'platform': current['platform'], 'account_id': current['account_id'], 'checked_at': now}
    if current.get('error'):
        proof['previous_error'] = current['error']
    state = result.get('state', 'processing')
    if current['state'] == 'published' and state != 'published':
        return
    if current['remote_id'] and current['remote_id'] != result['remote_id']:
        raise ValueError('回读作品 ID 与原回执不一致')
    with daily._connection() as conn:
        changed = conn.execute('UPDATE attempts SET state=?,error=?,evidence=?,remote_id=?,url=?,updated_at=? '
                               'WHERE id=? AND updated_at=? AND state=?',
                               (state, current.get('error') if state == 'failed' else None,
                                json.dumps(proof, ensure_ascii=False), result['remote_id'], result.get('url'), now,
                                current['id'], current['updated_at'], current['state']))
        if changed.rowcount != 1:
            raise ValueError('任务在核对期间发生变化，请刷新后重试')
