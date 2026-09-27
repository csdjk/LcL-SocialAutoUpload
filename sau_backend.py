import asyncio
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from myUtils.browser_login import get_browser_cookie
from myUtils.login_session import LoginStatusQueue, run_login, sse_stream
from flask_cors import CORS
from myUtils.auth import check_cookie
from myUtils.account_validation import validate_accounts
from flask import Flask, request, jsonify, Response, render_template, send_from_directory, send_file
from werkzeug.utils import secure_filename
from conf import BASE_DIR
from myUtils.login import get_tencent_cookie, get_douyin_cookie, get_ks_cookie, xiaohongshu_cookie_gen
from myUtils.postVideo import post_video_tencent, post_video_DouYin, post_video_ks, post_video_xhs
from uploader.bilibili_uploader.web_login import get_bilibili_cookie
from uploader.bilibili_uploader.web_publish import post_video_bilibili
import daily_publish as daily
from myUtils.credential_import import make_credential_import_blueprint
from myUtils.youtube_oauth import make_youtube_blueprint, get_youtube_oauth

app = Flask(__name__)
app.register_blueprint(make_credential_import_blueprint(BASE_DIR))
app.register_blueprint(make_youtube_blueprint(BASE_DIR))

#允许所有来源跨域访问
CORS(app)

# 限制上传文件大小为160MB
app.config['MAX_CONTENT_LENGTH'] = 160 * 1024 * 1024

# 获取当前目录（假设 index.html 和 assets 在这里）
current_dir = str(Path(__file__).resolve().parent / 'sau_frontend' / 'dist')

# 处理所有静态资源请求（未来打包用）
@app.route('/assets/<filename>')
def custom_static(filename):
    return send_from_directory(os.path.join(current_dir, 'assets'), filename)

# 处理 favicon.ico 静态资源（未来打包用）
@app.route('/favicon.ico')
@app.route('/publisher.ico')
def favicon():
    return send_from_directory(current_dir, 'publisher.ico')


@app.route('/publisher.svg')
def publisher_icon():
    return send_from_directory(current_dir, 'publisher.svg')

@app.route('/vite.svg')
def vite_svg():
    return send_from_directory(os.path.join(current_dir, 'assets'), 'vite.svg')

# （未来打包用）
@app.route('/')
def index():  # put application's code here
    return send_from_directory(current_dir, 'index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400
    try:
        # 保存文件到指定位置
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")
        safe_name = secure_filename(file.filename)
        if not safe_name:
            return jsonify({"code": 400, "data": None, "msg": "Invalid filename"}), 400
        filepath = Path(BASE_DIR / "videoFile" / f"{uuid_v1}_{safe_name}")
        file.save(filepath)
        return jsonify({"code":200,"msg": "File uploaded successfully", "data": f"{uuid_v1}_{safe_name}"}), 200
    except Exception as e:
        return jsonify({"code":500,"msg": str(e),"data":None}), 500

@app.route('/getFile', methods=['GET'])
def get_file():
    # 获取 filename 参数
    filename = request.args.get('filename')

    if not filename:
        return jsonify({"code": 400, "msg": "filename is required", "data": None}), 400

    # 防止路径穿越攻击
    if '..' in filename or filename.startswith('/'):
        return jsonify({"code": 400, "msg": "Invalid filename", "data": None}), 400

    # 拼接完整路径
    file_path = str(Path(BASE_DIR / "videoFile"))

    # 返回文件
    return send_from_directory(file_path,filename)


@app.route('/uploadSave', methods=['POST'])
def upload_save():
    if 'file' not in request.files:
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No file part in the request"
        }), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({
            "code": 400,
            "data": None,
            "msg": "No selected file"
        }), 400

    # 获取表单中的自定义文件名（可选）
    custom_filename = request.form.get('filename', None)
    if custom_filename:
        filename = secure_filename(custom_filename + "." + file.filename.split('.')[-1])
    else:
        filename = secure_filename(file.filename)
    if not filename:
        return jsonify({"code": 400, "data": None, "msg": "Invalid filename"}), 400

    try:
        # 生成 UUID v1
        uuid_v1 = uuid.uuid1()
        print(f"UUID v1: {uuid_v1}")

        # 构造文件名和路径
        final_filename = f"{uuid_v1}_{filename}"
        filepath = Path(BASE_DIR / "videoFile" / f"{uuid_v1}_{filename}")

        # 保存文件
        file.save(filepath)

        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                                INSERT INTO file_records (filename, filesize, file_path)
            VALUES (?, ?, ?)
                                ''', (filename, round(float(os.path.getsize(filepath)) / (1024 * 1024),2), final_filename))
            conn.commit()
            print("✅ 上传文件已记录")

        return jsonify({
            "code": 200,
            "msg": "File uploaded and saved successfully",
            "data": {
                "filename": filename,
                "filepath": final_filename
            }
        }), 200

    except Exception as e:
        print(f"Upload failed: {e}")
        return jsonify({
            "code": 500,
            "msg": f"upload failed: {e}",
            "data": None
        }), 500

@app.route('/getFiles', methods=['GET'])
def get_all_files():
    try:
        # 使用 with 自动管理数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row  # 允许通过列名访问结果
            cursor = conn.cursor()

            # 查询所有记录
            cursor.execute("SELECT * FROM file_records")
            rows = cursor.fetchall()

            # 将结果转为字典列表，并提取UUID
            data = []
            for row in rows:
                row_dict = dict(row)
                # 从 file_path 中提取 UUID (文件名的第一部分，下划线前)
                if row_dict.get('file_path'):
                    file_path_parts = row_dict['file_path'].split('_', 1)  # 只分割第一个下划线
                    if len(file_path_parts) > 0:
                        row_dict['uuid'] = file_path_parts[0]  # UUID 部分
                    else:
                        row_dict['uuid'] = ''
                else:
                    row_dict['uuid'] = ''
                data.append(row_dict)

            return jsonify({
                "code": 200,
                "msg": "success",
                "data": data
            }), 200
    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("get file failed!"),
            "data": None
        }), 500


@app.route("/getAccounts", methods=['GET'])
def getAccounts():
    """快速获取所有账号信息，不进行cookie验证"""
    try:
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('''
            SELECT * FROM user_info''')
            rows = cursor.fetchall()
            rows_list = [list(row) for row in rows]

            print("\n📋 当前数据表内容（快速获取）：")
            for row in rows:
                print(row)

            return jsonify(
                {
                    "code": 200,
                    "msg": None,
                    "data": rows_list
                }), 200
    except Exception as e:
        print(f"获取账号列表时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"获取账号列表失败: {str(e)}",
            "data": None
        }), 500


@app.route("/getValidAccounts", methods=['GET'])
async def getValidAccounts():
    rows, errors = await validate_accounts(Path(BASE_DIR) / 'db' / 'database.db', check_cookie)
    return jsonify({'code': 200, 'msg': None, 'data': rows, 'validation_errors': errors}), 200

@app.route('/deleteFile', methods=['GET'])
def delete_file():
    file_id = request.args.get('id')

    if not file_id or not file_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing file ID",
            "data": None
        }), 400

    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM file_records WHERE id = ?", (file_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "File not found",
                    "data": None
                }), 404

            record = dict(record)

            # 获取文件路径并删除实际文件
            file_path = Path(BASE_DIR / "videoFile" / record['file_path'])
            if file_path.exists():
                try:
                    file_path.unlink()  # 删除文件
                    print(f"✅ 实际文件已删除: {file_path}")
                except Exception as e:
                    print(f"⚠️ 删除实际文件失败: {e}")
                    # 即使删除文件失败，也要继续删除数据库记录，避免数据不一致
            else:
                print(f"⚠️ 实际文件不存在: {file_path}")

            # 删除数据库记录
            cursor.execute("DELETE FROM file_records WHERE id = ?", (file_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "File deleted successfully",
            "data": {
                "id": record['id'],
                "filename": record['filename']
            }
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("delete failed!"),
            "data": None
        }), 500

@app.route('/deleteAccount', methods=['GET'])
def delete_account():
    account_id = request.args.get('id')

    if not account_id or not account_id.isdigit():
        return jsonify({
            "code": 400,
            "msg": "Invalid or missing account ID",
            "data": None
        }), 400

    account_id = int(account_id)

    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 查询要删除的记录
            cursor.execute("SELECT * FROM user_info WHERE id = ?", (account_id,))
            record = cursor.fetchone()

            if not record:
                return jsonify({
                    "code": 404,
                    "msg": "account not found",
                    "data": None
                }), 404

            record = dict(record)

            # 删除关联的cookie文件
            if record.get('filePath'):
                cookie_file_path = Path(BASE_DIR / "cookiesFile" / record['filePath'])
                if cookie_file_path.exists():
                    try:
                        cookie_file_path.unlink()
                        print(f"✅ Cookie文件已删除: {cookie_file_path}")
                    except Exception as e:
                        print(f"⚠️ 删除Cookie文件失败: {e}")

            # 删除数据库记录
            cursor.execute("DELETE FROM user_info WHERE id = ?", (account_id,))
            if cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='browser_profiles'").fetchone():
                cursor.execute('DELETE FROM browser_profiles WHERE account_id=?', (account_id,))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account deleted successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": f"delete failed: {str(e)}",
            "data": None
        }), 500


# SSE 登录接口
@app.route('/login')
def login():
    # 1 小红书 2 视频号 3 抖音 4 快手 5 B站
    type = request.args.get('type')
    # 账号名
    id = request.args.get('id', '')
    mode = request.args.get('mode', 'qr')
    if type not in {'1', '2', '3', '4', '5', '6', '7'}:
        return jsonify({'code': 400, 'msg': '平台无效'}), 400
    if mode not in {'qr', 'browser'} or (type in {'6', '7'} and mode != 'browser'):
        return jsonify({'code': 400, 'msg': '该平台不支持此登录方式'}), 400

    if mode == 'browser':
        # Only this local UI may launch native browser windows. Do not accept
        # arbitrary login URLs, profiles or cross-site window-spawning requests.
        if request.remote_addr not in ('127.0.0.1', '::1'):
            return jsonify({'code': 403, 'msg': '请在本机工具中打开登录窗口'}), 403
        origin = request.headers.get('Origin')
        if origin:
            try:
                value = urlsplit(origin)
                allowed = (value.scheme == 'http' and value.hostname in ('127.0.0.1', 'localhost', '::1')
                           and (value.netloc == request.host or value.port in (5173, 5409, 4173)) and not value.username and not value.password
                           and not value.path and not value.query and not value.fragment)
            except ValueError:
                allowed = False
            if not allowed:
                return jsonify({'code': 403, 'msg': '请在本机工具中打开登录窗口'}), 403
    if any(ord(c) < 32 for c in id) or len(id.strip()) > 80:
        return jsonify({'code': 400, 'msg': '账号名称不能超过80字'}), 400
    account_id = request.args.get('account_id')
    if account_id is not None:
        if (mode != 'browser' and type != '5') or not account_id.isdigit() or int(account_id) < 1:
            return jsonify({'code': 400, 'msg': '重新登录账号编号无效'}), 400
        account_id = int(account_id)
    status_queue = LoginStatusQueue()
    status_queue.account_id = account_id
    # 启动异步任务线程
    thread = threading.Thread(target=run_async_function, args=(type,id,status_queue,mode), daemon=True)
    thread.start()
    response = Response(sse_stream(status_queue), mimetype='text/event-stream')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['X-Accel-Buffering'] = 'no'  # 关键：禁用 Nginx 缓冲
    response.headers['Content-Type'] = 'text/event-stream'
    response.headers['Connection'] = 'keep-alive'
    response.call_on_close(status_queue.cancelled.set)
    return response

def _post_bilibili_response(data):
    try:
        result = post_video_bilibili(data)
        return jsonify({'code': 200, 'msg': result['message'], 'data': result}), 200
    except ValueError as exc:
        return jsonify({'code': 400, 'msg': str(exc), 'data': None}), 400
    except Exception:
        # Do not echo process output; third-party logs can include credentials.
        return jsonify({'code': 500, 'msg': 'B 站投稿未完成；请先到创作中心核对，避免重复投稿', 'data': None}), 500


@app.route('/postVideo', methods=['POST'])
def postVideo():
    # 获取JSON数据
    data = request.get_json()

    if not data:
        return jsonify({"code": 400, "msg": "请求数据不能为空", "data": None}), 400

    if not isinstance(data, dict):
        return jsonify({'code': 400, 'msg': '请求数据必须为对象'}), 400
    if data.get('type') in (5, '5'):
        return _post_bilibili_response(data)

    # 从JSON数据中提取fileList和accountList
    file_list = data.get('fileList', [])
    account_list = data.get('accountList', [])
    type = data.get('type')
    title = data.get('title')
    tags = data.get('tags')
    category = data.get('category')
    enableTimer = data.get('enableTimer')
    if category == 0:
        category = None
    productLink = data.get('productLink', '')
    productTitle = data.get('productTitle', '')
    thumbnail_path = data.get('thumbnail', '')
    is_draft = data.get('isDraft', False)  # 新增参数：是否保存为草稿

    videos_per_day = data.get('videosPerDay')
    daily_times = data.get('dailyTimes')
    start_days = data.get('startDays')

    # 参数校验
    if not file_list:
        return jsonify({"code": 400, "msg": "文件列表不能为空", "data": None}), 400
    if not account_list:
        return jsonify({"code": 400, "msg": "账号列表不能为空", "data": None}), 400
    if not type:
        return jsonify({"code": 400, "msg": "平台类型不能为空", "data": None}), 400
    if not title:
        return jsonify({"code": 400, "msg": "标题不能为空", "data": None}), 400

    # 打印获取到的数据（仅作为示例）
    print("File List:", file_list)
    print("Account List:", account_list)

    try:
        match type:
            case 1:
                post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days)
            case 2:
                post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days, is_draft)
            case 3:
                post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, thumbnail_path, productLink, productTitle)
            case 4:
                post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days)
            case _:
                return jsonify({"code": 400, "msg": f"不支持的平台类型: {type}", "data": None}), 400

        # 返回响应给客户端
        return jsonify(
            {
                "code": 200,
                "msg": "发布任务已提交",
                "data": None
            }), 200
    except Exception as e:
        print(f"发布视频时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"发布失败: {str(e)}",
            "data": None
        }), 500


@app.route('/updateUserinfo', methods=['POST'])
def updateUserinfo():
    # 获取JSON数据
    data = request.get_json()

    # 从JSON数据中提取 type 和 userName
    user_id = data.get('id')
    type = data.get('type')
    userName = data.get('userName')
    try:
        # 获取数据库连接
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 更新数据库记录
            cursor.execute('''
                           UPDATE user_info
                           SET type     = ?,
                               userName = ?
                           WHERE id = ?;
                           ''', (type, userName, user_id))
            conn.commit()

        return jsonify({
            "code": 200,
            "msg": "account update successfully",
            "data": None
        }), 200

    except Exception as e:
        return jsonify({
            "code": 500,
            "msg": str("update failed!"),
            "data": None
        }), 500

@app.route('/postVideoBatch', methods=['POST'])
def postVideoBatch():
    data_list = request.get_json()

    if not isinstance(data_list, list):
        return jsonify({"code": 400, "msg": "Expected a JSON array", "data": None}), 400
    for data in data_list:
        if not isinstance(data, dict) or data.get('type') not in (1, 2, 3, 4, 5, '5'):
            return jsonify({'code': 400, 'msg': '批次包含无效平台或数据'}), 400
    for data in data_list:
        if data.get('type') in (5, '5'):
            response, status = _post_bilibili_response(data)
            if status != 200:
                return response, status
            continue
        # 从JSON数据中提取fileList和accountList
        file_list = data.get('fileList', [])
        account_list = data.get('accountList', [])
        type = data.get('type')
        title = data.get('title')
        tags = data.get('tags')
        category = data.get('category')
        enableTimer = data.get('enableTimer')
        if category == 0:
            category = None
        productLink = data.get('productLink', '')
        productTitle = data.get('productTitle', '')
        is_draft = data.get('isDraft', False)

        videos_per_day = data.get('videosPerDay')
        daily_times = data.get('dailyTimes')
        start_days = data.get('startDays')
        # 打印获取到的数据（仅作为示例）
        print("File List:", file_list)
        print("Account List:", account_list)
        match type:
            case 1:
                post_video_xhs(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                               start_days)
            case 2:
                post_video_tencent(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                                   start_days, is_draft)
            case 3:
                post_video_DouYin(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days, productLink, productTitle)
            case 4:
                post_video_ks(title, file_list, tags, account_list, category, enableTimer, videos_per_day, daily_times,
                          start_days)
    # 返回响应给客户端
    return jsonify(
        {
            "code": 200,
            "msg": None,
            "data": None
        }), 200

# Cookie文件上传API
@app.route('/uploadCookie', methods=['POST'])
def upload_cookie():
    try:
        if 'file' not in request.files:
            return jsonify({
                "code": 400,
                "msg": "没有找到Cookie文件",
                "data": None
            }), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({
                "code": 400,
                "msg": "Cookie文件名不能为空",
                "data": None
            }), 400

        if not file.filename.endswith('.json'):
            return jsonify({
                "code": 400,
                "msg": "Cookie文件必须是JSON格式",
                "data": None
            }), 400

        # 获取账号信息
        account_id = request.form.get('id')
        platform = request.form.get('platform')

        if not account_id or not platform:
            return jsonify({
                "code": 400,
                "msg": "缺少账号ID或平台信息",
                "data": None
            }), 400

        # 从数据库获取账号的文件路径
        with sqlite3.connect(Path(BASE_DIR / "db" / "database.db")) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute('SELECT filePath FROM user_info WHERE id = ?', (account_id,))
            result = cursor.fetchone()

        if not result:
            return jsonify({
                "code": 500,
                "msg": "账号不存在",
                "data": None
            }), 404

        # 保存上传的Cookie文件到对应路径
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / result['filePath'])
        cookie_file_path.parent.mkdir(parents=True, exist_ok=True)

        file.save(str(cookie_file_path))

        # 更新数据库中的账号信息（可选，比如更新更新时间）
        # 这里可以根据需要添加额外的处理逻辑

        return jsonify({
            "code": 200,
            "msg": "Cookie文件上传成功",
            "data": None
        }), 200

    except Exception as e:
        print(f"上传Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"上传Cookie文件失败: {str(e)}",
            "data": None
        }), 500


# Cookie文件下载API
@app.route('/downloadCookie', methods=['GET'])
def download_cookie():
    try:
        file_path = request.args.get('filePath')
        if not file_path:
            return jsonify({
                "code": 500,
                "msg": "缺少文件路径参数",
                "data": None
            }), 400

        # 验证文件路径的安全性，防止路径遍历攻击
        cookie_file_path = Path(BASE_DIR / "cookiesFile" / file_path).resolve()
        base_path = Path(BASE_DIR / "cookiesFile").resolve()

        if not cookie_file_path.is_relative_to(base_path):
            return jsonify({
                "code": 500,
                "msg": "非法文件路径",
                "data": None
            }), 400

        if not cookie_file_path.exists():
            return jsonify({
                "code": 500,
                "msg": "Cookie文件不存在",
                "data": None
            }), 404

        # 返回文件
        return send_from_directory(
            directory=str(cookie_file_path.parent),
            path=cookie_file_path.name,
            as_attachment=True
        )

    except Exception as e:
        print(f"下载Cookie文件时出错: {str(e)}")
        return jsonify({
            "code": 500,
            "msg": f"下载Cookie文件失败: {str(e)}",
            "data": None
        }), 500


# 包装函数：在线程中运行异步函数
def run_async_function(type,id,status_queue,mode='qr'):
    login_functions = {
        '1': xiaohongshu_cookie_gen,
        '2': get_tencent_cookie,
        '3': get_douyin_cookie,
        '4': get_ks_cookie,
        '5': get_bilibili_cookie,
    }
    if type == '6':
        async def login_function(name, queue):
            await get_youtube_oauth(name, queue, account_id=getattr(queue, 'account_id', None))
    elif mode == 'browser':
        async def login_function(name, queue):
            await get_browser_cookie(int(type), name, queue, account_id=getattr(queue, 'account_id', None))
    elif type == '5' and getattr(status_queue, 'account_id', None) is not None:
        async def login_function(name, queue):
            await get_bilibili_cookie(name, queue, account_id=queue.account_id)
    else:
        login_function = login_functions[type]
    run_login(login_function, id, status_queue, logger=app.logger)


def _daily_error(error):
    return jsonify({"code": 400, "msg": str(error), "data": None}), 400


@app.before_request
def daily_local_origin():
    if not request.path.startswith('/daily/'):
        return None
    if request.remote_addr not in ('127.0.0.1', '::1'):
        return jsonify({"code": 403, "msg": "发布管理仅允许本机访问", "data": None}), 403
    origin = request.headers.get('Origin')
    if origin:
        try:
            parsed = urlsplit(origin)
            allowed = (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', '::1')
                       and (parsed.netloc == request.host or parsed.port in (5173, 4173, 5409))
                       and not parsed.username and not parsed.password and not parsed.path
                       and not parsed.query and not parsed.fragment)
        except ValueError:
            allowed = False
        if not allowed:
            return jsonify({"code": 403, "msg": "不接受此网页来源的发布管理操作", "data": None}), 403


@app.route('/daily/today', methods=['GET'])
def daily_today():
    try:
        from publishing.service import today
        response = jsonify({"code": 200, "data": today()})
        response.headers['Cache-Control'] = 'no-store'
        return response
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        return _daily_error(error)


@app.route('/daily/tasks', methods=['GET'])
def daily_tasks():
    try:
        from publishing.service import list_tasks
        limit = int(request.args.get('limit', '30'))
        return jsonify({"code": 200, "data": list_tasks(limit)})
    except (ValueError, OSError, sqlite3.Error) as error:
        return _daily_error(error)


@app.route('/daily/library', methods=['GET'])
def daily_library():
    try:
        from publishing.library import list_videos
        return jsonify({"code": 200, "data": list_videos(int(request.args.get('limit', '30')))})
    except (ValueError, OSError, sqlite3.Error) as error:
        return _daily_error(error)


@app.route('/daily/library/import', methods=['POST'])
def daily_library_import():
    if request.headers.get('X-SAU-Local') != '1' or not request.headers.get('Origin'):
        return jsonify({"code": 403, "msg": "请从本机管理页导入视频", "data": None}), 403
    from publishing.library import import_video, MAX_VIDEO_BYTES
    request.max_content_length = MAX_VIDEO_BYTES + 20 * 1024 * 1024
    if request.content_length and request.content_length > MAX_VIDEO_BYTES + 20 * 1024 * 1024:
        return _daily_error(ValueError('视频超过 2 GiB 导入上限'))
    video = request.files.get('video')
    if video is None:
        return _daily_error(ValueError('请选择 MP4 视频'))
    try:
        tags = [item.strip() for item in request.form.get('tags', '').replace('，', ',').split(',') if item.strip()]
        cover = request.files.get('cover')
        result = import_video(video.stream, video.filename, request.form.get('title', ''),
                              request.form.get('description', ''), tags,
                              cover.stream if cover else None, request.form.get('ai_declaration', ''))
        return jsonify({"code": 200, "data": {"id": result['package']['edition_id'],
                                                 "existing": result['existing']}})
    except (ValueError, OSError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        return _daily_error(error)


@app.route('/daily/library/<ident>/asset/<key>', methods=['GET'])
def daily_library_asset(ident, key):
    try:
        from publishing.library import root
        package_path = root() / ident / 'package.json'
        package = daily.load_package(package_path)
        asset = package['assets'][key]
        return send_file(package_path.parent / asset['path'], conditional=True)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        return _daily_error(error)


@app.route('/daily/library/submit', methods=['POST'])
def daily_library_submit():
    if request.headers.get('X-SAU-Local') != '1' or not request.headers.get('Origin'):
        return jsonify({"code": 403, "msg": "请从本机管理页预约投稿", "data": None}), 403
    try:
        from publishing.library import submit
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('投稿请求格式无效')
        return jsonify({"code": 200, "data": submit(body.get('id'), body.get('platforms'),
                                                       body.get('scheduled_for'), body.get('payloads'))})
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        return _daily_error(error)


@app.route('/daily/automation', methods=['GET', 'PUT'])
def daily_automation():
    from publishing import automation
    try:
        if request.method == 'GET':
            return jsonify({"code": 200, "data": automation.status()})
        if request.headers.get('X-SAU-Local') != '1' or not request.headers.get('Origin'):
            return jsonify({"code": 403, "msg": "请从本机管理页修改排程", "data": None}), 403
        data = request.get_json(silent=True)
        saved = automation.save_settings(data)
        return jsonify({"code": 200, "data": {"settings": saved}})
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        return _daily_error(error)


@app.route('/daily/worker', methods=['GET', 'PUT'])
def daily_worker_status():
    from publishing import queue
    try:
        if request.method == 'PUT':
            if request.headers.get('X-SAU-Local') != '1' or not request.headers.get('Origin'):
                return jsonify({"code": 403, "msg": "请从本机管理页控制后台", "data": None}), 403
            data = request.get_json(silent=True)
            if not isinstance(data, dict) or type(data.get('paused')) is not bool:
                raise ValueError('请明确指定是否暂停新任务')
            queue.set_paused(data['paused'])
        with queue.worker_lock() as available:
            running = not available
        return jsonify({"code": 200, "data": {"running": running, "paused": queue.pause_path().is_file()}})
    except (ValueError, OSError) as error:
        return _daily_error(error)


@app.route('/daily/asset/<date>/<revision>/<key>', methods=['GET'])
def daily_asset(date, revision, key):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) or not re.fullmatch(r'r\d{3}', revision):
            raise ValueError('资源包日期或版本无效')
        path = daily.output_root() / date / revision / 'package.json'
        package = daily.load_package(path)
        asset = package['assets'][key]
        return send_file(path.parent / asset['path'], conditional=True)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        return _daily_error(error)


@app.route('/daily/channels-account', methods=['GET', 'POST'])
@app.route('/daily/accounts/<platform>', methods=['GET', 'POST'])
def daily_channels_account(platform='wechat_channels'):
    if platform not in daily.PLATFORMS:
        return _daily_error(ValueError("发布平台无效"))
    if request.method == 'GET':
        try:
            return jsonify({"code": 200, "data": daily.account_binding_options(platform)})
        except (ValueError, OSError, sqlite3.Error):
            return jsonify({"code": 500, "msg": "无法读取视频号绑定配置", "data": None}), 500
    # Explicit local-origin request: third-party pages cannot switch publishing targets.
    try:
        origin = urlsplit(request.headers.get('Origin', ''))
        allowed = (origin.scheme == 'http' and origin.hostname in ('127.0.0.1', 'localhost', '::1')
                   and (origin.netloc == request.host or origin.port in (5173, 4173, 5409)) and not origin.username and not origin.password
                   and not origin.path and not origin.query and not origin.fragment)
    except ValueError:
        allowed = False
    if request.remote_addr not in ('127.0.0.1', '::1') or not allowed or request.headers.get('X-SAU-Local') != '1':
        return jsonify({"code": 403, "msg": "请在本机页面选择发布账号", "data": None}), 403
    if not request.is_json:
        return jsonify({"code": 415, "msg": "请求格式不正确", "data": None}), 415
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"code": 400, "msg": "请选择发布账号", "data": None}), 400
    try:
        result = daily.bind_account(platform, body.get('web_account_id'), body.get('revision'))
        return jsonify({"code": 200, "msg": "发布账号已绑定，尚未上传或投稿", "data": result})
    except ValueError as exc:
        return jsonify({"code": 409, "msg": str(exc), "data": None}), 409
    except (OSError, sqlite3.Error):
        return jsonify({"code": 500, "msg": "绑定保存失败，请刷新配置后检查", "data": None}), 500


@app.route('/daily/draft', methods=['PUT'])
def daily_draft():
    try:
        body = request.get_json(force=True)
        result = daily.save_draft(Path(body['package_path']), body['platform'],
                                  str(body['account_id']), body['payload'])
        return jsonify({"code": 200, "data": result})
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        return _daily_error(error)


@app.route('/daily/bilibili/categories', methods=['GET'])
def daily_bilibili_categories():
    try:
        from publishing.bilibili_metadata import load_categories
        return jsonify({'code': 200, 'data': load_categories()})
    except (ValueError, OSError) as error:
        return _daily_error(error)


@app.route('/daily/submit', methods=['POST'])
def daily_submit():
    try:
        body = request.get_json(force=True)
        result = daily.submit(Path(body['package_path']), body['platforms'], 'manual')
        return jsonify({"code": 200, "data": result})
    except (ValueError, KeyError, OSError, json.JSONDecodeError, TypeError) as error:
        return _daily_error(error)


@app.route('/daily/task/<job_id>', methods=['GET'])
def daily_task(job_id):
    try:
        return jsonify({"code": 200, "data": daily.task(job_id)})
    except ValueError as error:
        return _daily_error(error)


@app.route('/daily/reconcile', methods=['POST'])
def daily_reconcile():
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('核对请求格式无效，请通过核对窗口提交')
        if 'confirmed' in body:
            if body['confirmed'] is not True:
                raise ValueError('请确认所选状态')
            result = daily.confirm_task_status(body.get('task_id'), body.get('state'),
                                               expected_updated_at=body.get('expected_updated_at'))
        else:
            result = daily.reconcile(body.get('task_id'), body.get('state'), body.get('evidence'),
                                     expected_updated_at=body.get('expected_updated_at'))
        return jsonify({"code": 200, "data": result})
    except daily.ReconcileConflictError as error:
        return jsonify({"code": 409, "msg": str(error), "data": None}), 409
    except (ValueError, KeyError, TypeError) as error:
        return _daily_error(error)


from myUtils.daily_oneclick import make_oneclick_blueprint
app.register_blueprint(make_oneclick_blueprint())

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5409)
