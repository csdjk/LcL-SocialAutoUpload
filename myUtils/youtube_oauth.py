"""Local configuration and cancellable YouTube authorization worker."""
import asyncio
import sqlite3

from flask import Blueprint, jsonify, request
from myUtils.credential_import import local_app_request
from myUtils.login_session import report_failure
from publishing import youtube_api


def make_youtube_blueprint(base):
    blueprint = Blueprint('youtube_oauth', __name__)

    @blueprint.route('/accounts/youtube-oauth/config', methods=['GET', 'POST'])
    def config():
        def reply(code, msg, data=None):
            response = jsonify(code=code, msg=msg, data=data)
            response.headers['Cache-Control'] = 'no-store'
            return response, code
        if not local_app_request():
            return reply(403, '请通过本机账号管理配置 YouTube')
        try:
            path = youtube_api.config_path(base)
            if request.method == 'POST':
                if request.content_length is None or request.content_length > 65536:
                    return reply(413, '请选择小于 64 KiB 的 OAuth 客户端 JSON')
                payload = request.get_json(silent=True)
                if not isinstance(payload, dict):
                    return reply(400, '客户端 JSON 格式无效')
                client = youtube_api.client_config(payload)
                youtube_api.atomic_json(path, {'installed': client})
            elif path.is_file():
                youtube_api.client_config(youtube_api.read_json(path))
            return reply(200, '已保存桌面 OAuth 客户端' if path.is_file() else '请先配置桌面 OAuth 客户端',
                         {'configured': path.is_file()})
        except youtube_api.YouTubeError as exc:
            return reply(400, str(exc))
        except (OSError, ValueError, TypeError):
            return reply(400, '无法读取或保存客户端配置，请检查文件格式与本机目录权限')

    return blueprint


async def get_youtube_oauth(name, queue, *, account_id=None, base=None):
    if base is None:
        from conf import BASE_DIR
        base = BASE_DIR
    try:
        await asyncio.to_thread(youtube_api.authorize, base, queue, account_id)
    except asyncio.CancelledError:
        queue.cancelled.set()
        raise
    except youtube_api.YouTubeError as exc:
        report_failure(queue, str(exc))
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        report_failure(queue, 'YouTube 授权未能保存，请检查客户端配置、网络和本机账号数据')
