"""Serve the built management page and API on this computer only."""
import argparse
from pathlib import Path
import webbrowser
from werkzeug.serving import make_server
from publishing.queue import ensure_worker


def main():
    parser = argparse.ArgumentParser(description="启动本机视频发布工具")
    parser.add_argument("--port", type=int, default=5409)
    parser.add_argument("--open", action="store_true", help="打开管理页面")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if not (root / "sau_frontend/dist/index.html").is_file():
        parser.error("请先在 sau_frontend 运行 npm run build")
    from sau_backend import app
    # Bind first: do not start another worker when a server is already listening.
    server = make_server("127.0.0.1", args.port, app, threaded=True)
    ensure_worker()
    url = f"http://127.0.0.1:{args.port}"
    print(f"本机发布工具：{url}（仅本机可访问）", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
