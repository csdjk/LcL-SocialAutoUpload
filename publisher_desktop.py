"""Windows desktop window and tray for the loopback publishing service."""
import argparse
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.request import Request, build_opener, ProxyHandler

from flask import jsonify, request
from werkzeug.serving import make_server

from publishing import queue
from publishing.service import list_tasks
from publishing.desktop_window import DesktopWindow, set_app_identity


ROOT = Path(__file__).resolve().parent
SESSION = ROOT / "db" / "desktop-session.json"
LOCK = ROOT / "db" / "desktop.lock"
APP_ICON = ROOT / 'sau_frontend/public/publisher.ico'


def activate_existing() -> bool:
    # This is a loopback-only control endpoint; system proxies must not intercept it.
    local_http = build_opener(ProxyHandler({}))
    for _ in range(10):
        try:
            data = json.loads(SESSION.read_text(encoding="utf-8"))
            url = f"http://127.0.0.1:{int(data['port'])}/desktop/show"
            req = Request(url, data=b"{}", headers={"X-Desktop-Token": data["token"],
                                                       "Content-Type": "application/json"}, method="POST")
            with local_http.open(req, timeout=1) as response:
                return response.status == 200
        except (OSError, ValueError, KeyError, TypeError):
            time.sleep(.5)
    return False


def tray_image():
    from PIL import Image
    with Image.open(APP_ICON) as image:
        return image.convert('RGBA').resize((64, 64))


def run(port: int = 5409):
    if not (ROOT / "sau_frontend/dist/index.html").is_file():
        raise RuntimeError("请先在 sau_frontend 运行 npm run build")
    if not (1 <= port <= 65535):
        raise ValueError("端口号无效")
    with queue.file_lock(LOCK) as owner:
        if not owner:
            if activate_existing():
                return
            raise RuntimeError("桌面版已经启动，但无法唤醒原窗口；请检查后台日志")
        import pystray
        import webview
        from sau_backend import app
        set_app_identity()

        token = secrets.token_urlsafe(32)
        exiting = threading.Event()
        window_holder = {}

        @app.post('/desktop/show')
        def show_desktop():
            if request.remote_addr not in ('127.0.0.1', '::1') or request.headers.get('X-Desktop-Token') != token:
                return jsonify({"code": 403}), 403
            window = window_holder.get('window')
            if window:
                window.show()
                window.restore()
            return jsonify({"code": 200})

        server = make_server('127.0.0.1', port, app, threaded=True)
        SESSION.parent.mkdir(parents=True, exist_ok=True)
        SESSION.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
        icon = None
        try:
            thread = threading.Thread(target=server.serve_forever, name="publisher-http", daemon=True)
            thread.start()
            queue.ensure_worker()

            desktop = DesktopWindow()
            window = webview.create_window('视频发布工作台', f'http://127.0.0.1:{port}/',
                                       width=1320, height=880, min_size=(780, 560),
                                       background_color='#e8eef4', zoomable=True,
                                       frameless=True, easy_drag=False, shadow=True, js_api=desktop)
            desktop._bind(window)
            window_holder['window'] = window

            def closing():
                if exiting.is_set():
                    return True
                window.hide()
                return False

            window.events.closing += closing

            def open_window(icon, item):
                window.show()
                window.restore()

            def toggle_pause(icon, item):
                queue.set_paused(not queue.pause_path().is_file())
                icon.update_menu()

            def quit_app(icon, item):
                exiting.set()
                queue.request_stop()
                window.destroy()

            icon = pystray.Icon('local-video-publisher', tray_image(), '视频发布工作台', menu=pystray.Menu(
            pystray.MenuItem('打开工作台', open_window, default=True),
            pystray.MenuItem(lambda item: '恢复新任务' if queue.pause_path().is_file() else '暂停新任务', toggle_pause),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('退出程序', quit_app)))

            def watch_tasks():
                known = {}
                first = True
                while not exiting.wait(15):
                    try:
                        tasks = list_tasks(30)
                        for task in tasks:
                            old = known.get(task['id'])
                            state = task['state']
                            if not first and old != state and state in {'published', 'failed', 'unknown', 'needs_action'} and icon.HAS_NOTIFICATION:
                                icon.notify(f"{task['platform']}：{state}" + (f"（{task['error']}）" if task['error'] else ''), '视频发布状态')
                            known[task['id']] = state
                        first = False
                    except Exception:
                        import traceback
                        traceback.print_exc()

            tray_thread = threading.Thread(target=icon.run, name="publisher-tray", daemon=True)
            tray_thread.start()
            threading.Thread(target=watch_tasks, name="publisher-notifications", daemon=True).start()
            webview.start(gui='edgechromium', private_mode=False,
                          storage_path=str(ROOT / 'db' / 'desktop-webview'), icon=str(APP_ICON))
        finally:
            exiting.set()
            queue.request_stop()
            if icon is not None:
                icon.stop()
            server.shutdown()
            server.server_close()
            SESSION.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='启动本机视频发布桌面版')
    parser.add_argument('--port', type=int, default=5409)
    args = parser.parse_args()
    run(args.port)


if __name__ == '__main__':
    main()
