"""Local-only validated credential import; no secrets in URLs or responses."""
import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, request
from utils.account_identity import account_name, AccountNameError
from utils.douyin_credentials import (
    MAX_CREDENTIAL_BYTES, CredentialFormatError, normalize_douyin_credentials, validate_douyin_credentials,
)


from uploader.bilibili_uploader.web_login import normalize_bilibili_credentials, prepare_bilibili_credentials


def local_app_request():
    if request.remote_addr not in ('127.0.0.1', '::1') or request.headers.get('X-SAU-Local') != '1':
        return False
    try:
        host = urlsplit(request.host_url)
        origin = urlsplit(request.headers.get('Origin', request.host_url.rstrip('/')))
        return (host.hostname in ('localhost', '127.0.0.1', '::1')
                and origin.scheme == 'http' and origin.hostname in ('localhost', '127.0.0.1', '::1')
                and (origin.netloc == host.netloc or origin.port in (5173, 5409, 4173))
                and not any((origin.username, origin.password, origin.path, origin.query, origin.fragment)))
    except ValueError:
        return False


def make_credential_import_blueprint(base_dir):
    root = Path(base_dir)
    blueprint = Blueprint('credential_import', __name__)

    def reply(code, message, data=None):
        response = jsonify({'code': code, 'msg': message, 'data': data})
        response.headers['Cache-Control'] = 'no-store'
        return response, code

    @blueprint.route('/accounts/import-bilibili', methods=['POST'])
    @blueprint.route('/accounts/import-douyin', methods=['POST'])
    async def import_credentials():
        bilibili = request.path.endswith('/import-bilibili')
        platform_type = 5 if bilibili else 3
        if not local_app_request():
            return reply(403, '请通过本机账号管理页面导入登录态')
        platform_name = 'B站' if bilibili else '抖音'
        normalizer = normalize_bilibili_credentials if bilibili else normalize_douyin_credentials
        validator = prepare_bilibili_credentials if bilibili else validate_douyin_credentials
        if not request.is_json:
            return reply(415, '请通过本机页面提交 JSON 请求')
        if request.content_length is None or request.content_length > MAX_CREDENTIAL_BYTES * 2 + 4096:
            return reply(413, f'登录态文件过大，请仅导出{platform_name}网站的登录态')
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return reply(400, '请求内容格式不正确')
        name = payload.get('name', '')
        if not isinstance(name, str) or len(name.strip()) > 80 or any(ord(c) < 32 for c in name):
            return reply(400, '账号名称格式无效')
        name = name.strip()
        account_id = payload.get('account_id')
        if account_id is not None:
            if isinstance(account_id, bool) or not str(account_id).isdigit() or int(account_id) < 1:
                return reply(400, '账号编号无效')
            account_id = int(account_id)
        try:
            state = normalizer(payload.get('credentials'))
        except CredentialFormatError as exc:
            return reply(400, str(exc))
        except (ValueError, TypeError, RecursionError, OverflowError):
            return reply(400, '登录态格式不正确，请重新导出')
        database = root / 'db' / 'database.db'
        previous = None
        new_file = None
        committed = False
        try:
            with closing(sqlite3.connect(database)) as conn:
                if account_id is not None:
                    previous = conn.execute('SELECT filePath FROM user_info WHERE id=? AND type=?', (account_id, platform_type)).fetchone()
                    if previous is None:
                        return reply(404, f'未找到需要更新的{platform_name}账号')
                elif conn.execute('SELECT id FROM user_info WHERE type=? AND userName=?', (platform_type, name)).fetchone():
                    return reply(409, f'已有同名{platform_name}账号，请使用该账号的“上传Cookie”更新登录态')
            directory = root / 'cookiesFile'
            directory.mkdir(parents=True, exist_ok=True)
            new_file = directory / f'{uuid.uuid4()}.json'
            # Path is generated here, never supplied by a client or export filename.
            with new_file.open('x', encoding='utf-8') as output:
                json.dump(state, output, ensure_ascii=False)
            # Empty names are the automatic-name contract used by new clients.
            # Older explicitly named clients remain compatible; replacements keep their name.
            auto_name = account_id is None and not name
            result = await validator(new_file, require_name=True) if auto_name else await validator(new_file)
            if not result.get('success'):
                return reply(422, result.get('message') or '登录态未验证通过，账号未保存')
            if auto_name:
                name = account_name(result.get('account_name'))
            with closing(sqlite3.connect(database)) as conn:
                conn.execute('BEGIN IMMEDIATE')
                if account_id is not None:
                    cursor = conn.execute('UPDATE user_info SET filePath=?, status=1 WHERE id=? AND type=? AND filePath=?',
                        (new_file.name, account_id, platform_type, previous[0]))
                    if cursor.rowcount != 1:
                        conn.rollback()
                        return reply(409, '账号已被删除或更新，请刷新列表后重试')
                else:
                    if conn.execute('SELECT id FROM user_info WHERE type=? AND userName=?', (platform_type, name)).fetchone():
                        conn.rollback()
                        return reply(409, f'已有同名{platform_name}账号，请刷新列表后更新该账号')
                    cursor = conn.execute('INSERT INTO user_info (type, filePath, userName, status) VALUES (?,?,?,1)',
                        (platform_type, new_file.name, name))
                    account_id = cursor.lastrowid
                conn.commit()
                committed = True
                row = conn.execute('SELECT id,type,filePath,userName,status FROM user_info WHERE id=?', (account_id,)).fetchone()
            return reply(200, f'{platform_name}登录态已验证并保存', list(row))
        except AccountNameError as exc:
            return reply(422, str(exc))
        except Exception as exc:
            # Do not log request objects, credentials, file contents or exception text.
            current_app.logger.warning('%s登录态导入未完成: %s', platform_name, type(exc).__name__)
            return reply(500, '导入未完成，请检查本机服务后重试；原有账号未被覆盖')
        finally:
            if new_file is not None and not committed:
                new_file.unlink(missing_ok=True)

    return blueprint
