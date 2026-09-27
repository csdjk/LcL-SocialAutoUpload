"""Entry point independent of the caller's working directory."""
import argparse
from pathlib import Path
import daily_publish as daily
from publishing.queue import serve


def main():
    parser = argparse.ArgumentParser(description="本机发布后台执行器")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--base", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.database: daily.DB_PATH = args.database
    if args.settings: daily.SETTINGS_PATH = args.settings
    if args.base: daily.BASE_DIR = args.base
    serve(once=args.once)


if __name__ == "__main__":
    main()
