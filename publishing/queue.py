"""Durable local jobs with a single OS-locked worker per ledger.

The queue row and reservation are inserted in one transaction. A crashed attempt
that already obtained its run claim is never automatically replayed.
"""
from contextlib import contextmanager
from pathlib import Path
import os
import hashlib
import subprocess
import sys
import time
import traceback
import daily_publish as daily


@contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            yield False
            return
        try:
            yield True
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def worker_lock():
    return file_lock(daily.DB_PATH.with_suffix(".worker.lock"))


def pause_path():
    return daily.DB_PATH.with_suffix(".worker.pause")


def stop_path():
    return daily.DB_PATH.with_suffix(".worker.stop")


def set_paused(paused: bool):
    path = pause_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if paused:
        path.touch()
    else:
        path.unlink(missing_ok=True)
        ensure_worker()


def request_stop():
    path = stop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def account_lock(platform, account_id):
    key = hashlib.sha256(f"{platform}:{account_id}".encode()).hexdigest()[:24]
    return file_lock(daily.DB_PATH.parent / f"account-{key}.worker.lock")


def ensure_worker():
    # No window, no shell, and no dependence on the MCP client's lifetime/stdout.
    stop_path().unlink(missing_ok=True)
    with worker_lock() as available:
        if not available:
            return
    root = Path(__file__).resolve().parents[1]
    log = daily.DB_PATH.with_suffix(".worker.log")
    with log.open("ab") as output:
        subprocess.Popen([sys.executable, str(root / "publisher_worker.py"),
                          "--database", str(daily.DB_PATH.resolve()),
                          "--settings", str(daily.SETTINGS_PATH.resolve()),
                          "--base", str(Path(daily.BASE_DIR).resolve())],
                         cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                         start_new_session=os.name != "nt")


def recover_interrupted():
    """Only call while owning the worker lock; never touches legacy direct runs."""
    now = daily.datetime.now(daily.BEIJING).isoformat()
    with daily._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("""SELECT q.job_id, a.state, r.job_id AS claimed FROM delivery_queue q
            JOIN attempts a ON a.id=q.job_id LEFT JOIN run_claims r ON r.job_id=q.job_id
            WHERE q.state='running'""").fetchall()
        for row in rows:
            ident = row["job_id"]
            if row["state"] == "uploading" and row["claimed"]:
                conn.execute("UPDATE attempts SET state='unknown',error=?,updated_at=? WHERE id=?",
                             ("执行器中断；请核对平台结果，不会自动重发", now, ident))
                conn.execute("UPDATE delivery_queue SET state='done',updated_at=? WHERE job_id=?", (now, ident))
            elif row["state"] in {"queued", "uploading"} and not row["claimed"]:
                conn.execute("UPDATE attempts SET state='queued',updated_at=? WHERE id=?", (now, ident))
                conn.execute("UPDATE delivery_queue SET state='queued',updated_at=? WHERE job_id=?", (now, ident))
            else:
                conn.execute("UPDATE delivery_queue SET state='done',updated_at=? WHERE job_id=?", (now, ident))


def process_next():
    now = daily.datetime.now(daily.BEIJING).isoformat()
    with daily._connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT q.job_id FROM delivery_queue q JOIN attempts a ON a.id=q.job_id
            WHERE q.state='queued' AND a.state='queued' AND (q.available_at IS NULL OR q.available_at<=?)
            ORDER BY a.created_at,a.id LIMIT 1""", (now,)).fetchone()
        if row is None:
            return False
        ident = row["job_id"]
        conn.execute("UPDATE attempts SET state='uploading',updated_at=? WHERE id=?", (now, ident))
        conn.execute("UPDATE delivery_queue SET state='running',updated_at=? WHERE job_id=?", (now, ident))
    # daily.run owns the durable single-execution claim and all failure boundaries.
    daily.run(ident)
    with daily._connection() as conn:
        conn.execute("UPDATE delivery_queue SET state='done',updated_at=? WHERE job_id=?",
                     (daily.datetime.now(daily.BEIJING).isoformat(), ident))
    return True


def sync_pending(limit=3):
    """Read existing works only. Unsupported channels keep their pending state."""
    from publishing import service
    with daily._connection() as conn:
        rows = conn.execute("""SELECT id,platform,account_id,remote_id,payload FROM attempts
            WHERE state IN ('processing','unknown') ORDER BY updated_at LIMIT ?""", (limit * 5,)).fetchall()
    checked = 0
    for row in rows:
        if checked >= limit:
            break
        payload = daily.json.loads(row["payload"])
        api = (row["platform"] in ("douyin", "youtube") and
               payload.get("_delivery", {}).get("id") == row["platform"] + "_api" and row["remote_id"])
        # Reconcile already submitted work independently of permission to create
        # future automatic posts. Browser readback never invokes publication.
        channels = row['platform'] == 'wechat_channels' and row['account_id'].startswith('web:wechat_channels:')
        creator_browser = (row['platform'] in ('douyin', 'toutiao', 'xiaohongshu')
                           and (bool(row['remote_id']) or row['platform'] == 'xiaohongshu')
                           and payload.get('_delivery', {}).get('id') == 'browser')
        bili = row['platform'] == 'bilibili' and bool(row['remote_id'])
        if not api and not channels and not bili and not creator_browser:
            continue
        checked += 1
        try:
            service.sync_result(row["id"])
        except Exception:
            traceback.print_exc()
    return checked


def serve(*, once=False):
    with worker_lock() as acquired:
        if not acquired:
            return
        recover_interrupted()
        next_scan = 0.0
        next_sync = time.monotonic() + 120
        while True:
            if stop_path().is_file():
                return
            if pause_path().is_file():
                if once:
                    return
                time.sleep(2)
                continue
            if not once and time.monotonic() >= next_scan:
                next_scan = time.monotonic() + 30
                try:
                    from publishing.automation import tick
                    tick()
                except Exception:
                    # Keep existing reserved work running; the worker log retains the cause.
                    traceback.print_exc()
            did_work = process_next()
            if not once and not did_work and time.monotonic() >= next_sync:
                next_sync = time.monotonic() + 300
                try:
                    sync_pending()
                except Exception:
                    traceback.print_exc()
            if once and not did_work:
                return
            if not did_work:
                time.sleep(2)
