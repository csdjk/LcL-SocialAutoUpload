"""One explicit click submits one platform; result sync never submits anything."""
import asyncio
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from flask import Blueprint, jsonify, request
import daily_publish as daily
from publishing import service
from uploader.tencent_uploader.readback import readback_file

ALLOWED = {'title','description','tags','short_title','category','ai_declaration','cover_mode'}


def submit_one(path, platform, account_id, payload):
    return service.submit_one(path, platform, account_id, payload)


def task_cookie(current):
    prefix = 'web:wechat_channels:'
    aid = current.get('account_id','')
    if current.get('platform') != 'wechat_channels' or not aid.startswith(prefix) or not aid[len(prefix):].isdigit():
        raise ValueError('该任务暂不支持自动回读，请保留人工核对')
    with closing(sqlite3.connect(Path(daily.BASE_DIR)/'db/database.db')) as conn:
        row = conn.execute('SELECT filePath FROM user_info WHERE id=? AND type=2',(int(aid[len(prefix):]),)).fetchone()
    if not row: raise ValueError('原任务账号已不存在，请先恢复对应账号')
    base = (Path(daily.BASE_DIR)/'cookiesFile').resolve(); path = (base/row[0]).resolve()
    if not path.is_relative_to(base) or not path.is_file():raise ValueError('原任务账号登录文件不可用，请重新登录')
    return path


def sync_result(job_id):
    current = daily.task(job_id)
    if current['state'] in ('queued','uploading'):raise ValueError('投稿仍在排队或执行，请查看进度，不要重复提交')
    if current['state'] == 'published': return {'task':current,'sync':{'status':'found','message':'任务已记录为发布成功'}}
    cookie = task_cookie(current); material = current['payload']
    result = asyncio.run(readback_file(cookie,material.get('title',''),material.get('description',''),current['edition_id']))
    if result.get('status') != 'found':
        # Absence is not enough to unlock a possibly submitted historical attempt.
        return {'task':current,'sync':result}
    state = 'published' if result.get('state') == 'published' else 'processing'
    evidence = {**(current.get('evidence') or {}),
                'source':'official_content_list','platform':current['platform'],'account_id':current['account_id'],
                'remote_id':result['remote_id'],'checked_at':datetime.now(daily.BEIJING).isoformat(),
                'remote_created_at':result.get('created_at'),'match':result.get('match'),
                'prior_state':current['state'], 'prior_error':current.get('error'),
                'platform_status':result.get('platform_status'), 'visible_type':result.get('visible_type'),
                'stage':'verifying', 'message':'官方后台已确认公开发布' if state == 'published' else '等待平台确认发布状态',
                'note':result.get('note') or '经原任务账号的官方内容列表回读，找到同日期同简介的唯一作品。只同步结果，未再次上传或发表。'}
    with daily._connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        latest = conn.execute('SELECT state,updated_at,remote_id FROM attempts WHERE id=?',(job_id,)).fetchone()
        if not latest or latest['updated_at'] != current['updated_at'] or latest['state']=='uploading':
            raise ValueError('任务在回读期间已变化，请刷新后重试')
        if latest['remote_id'] and latest['remote_id'] != result['remote_id']:
            raise ValueError('后台作品与已有任务记录不一致，未覆盖记录')
        conn.execute('UPDATE attempts SET state=?,remote_id=?,error=NULL,evidence=?,updated_at=? WHERE id=?',
                     (state,result['remote_id'],json.dumps(evidence,ensure_ascii=False),datetime.now(daily.BEIJING).isoformat(),job_id))
    return {'task':daily.task(job_id),'sync':result}


def make_oneclick_blueprint():
    bp=Blueprint('daily_oneclick',__name__)
    def check_local():
        if request.remote_addr not in ('127.0.0.1','::1') or request.headers.get('X-SAU-Local') != '1':
            raise ValueError('请从本机投稿页面操作')
        origin=request.headers.get('Origin')
        if origin:
            parsed=urlsplit(origin)
            if parsed.scheme!='http' or parsed.hostname not in ('127.0.0.1','localhost','::1') or (parsed.netloc != request.host and parsed.port not in (5173,5409,4173)) or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                raise ValueError('不接受此网页来源的投稿操作')
    def response(value,code=200):
        r=jsonify(value);r.headers['Cache-Control']='no-store';return r,code
    @bp.post('/daily/publish-one')
    def publish_one_route():
        try:
            check_local();body=request.get_json(silent=True)
            if not isinstance(body,dict):raise ValueError('投稿请求格式无效')
            value=submit_one(body.get('package_path'),body.get('platform'),body.get('account_id'),body.get('payload'))
            return response({'code':200,'data':value})
        except (ValueError,TypeError,KeyError,OSError) as exc:
            return response({'code':400,'msg':str(exc),'data':None},400)
    @bp.post('/daily/publish-batch')
    def publish_batch_route():
        try:
            check_local(); body=request.get_json(silent=True)
            if not isinstance(body,dict) or not isinstance(body.get('targets'),list) or not 1 <= len(body['targets']) <= len(daily.PLATFORMS):
                raise ValueError(f'请选择 1–{len(daily.PLATFORMS)} 个投稿平台')
            keys=[item.get('platform') for item in body['targets'] if isinstance(item,dict)]
            if len(keys)!=len(body['targets']) or any(key not in daily.PLATFORMS for key in keys) or len(set(keys))!=len(keys):
                raise ValueError('平台列表无效或重复')
            jobs=[]; errors={}
            for item in body['targets']:
                try:
                    value=submit_one(body.get('package_path'),item['platform'],item.get('account_id'),item.get('payload'))
                    jobs.append({'platform':item['platform'],**value})
                except (ValueError,TypeError,KeyError,OSError) as exc:
                    errors[item['platform']]=str(exc)
            return response({'code':200,'data':{'jobs':jobs,'errors':errors}})
        except (ValueError,TypeError,KeyError,OSError) as exc:
            return response({'code':400,'msg':str(exc),'data':None},400)
    @bp.post('/daily/sync-result')
    def sync_route():
        try:
            check_local();body=request.get_json(silent=True)
            if not isinstance(body,dict) or not isinstance(body.get('task_id'),str):raise ValueError('请选择需要同步的任务')
            return response({'code':200,'data':service.sync_result(body['task_id'])})
        except (ValueError,TypeError,KeyError,OSError) as exc:
            return response({'code':400,'msg':str(exc),'data':None},400)
        except Exception:
            return response({'code':502,'msg':'后台结果暂时无法读取，请检查网络或登录状态；未重新投稿','data':None},502)
    return bp
