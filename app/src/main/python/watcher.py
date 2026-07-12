"""
watcher.py — watch folders for new/changed files, SQLite-backed.

WHY POLLING INSTEAD OF inotify:
  Real filesystem watching (inotify) needs a native extension that
  Chaquopy's pip can't reliably install on Android. Polling every few
  seconds is simple, has zero native dependencies, and is plenty fast
  for "a file landed in a folder" automation - not for sub-second
  reactions, but that's not what this kind of automation needs anyway.

WHAT THIS GIVES YOU:
  - Register one or more folders to watch (each with its own file
    extension filter and recursive option).
  - On each scan, new files and files whose modified-time changed since
    last scan get passed to every registered handler.
  - A snapshot of what's been seen (path -> mtime) persists in SQLite,
    so restarting the app doesn't cause every existing file to be
    treated as "new" again.
  - Only scans folders you explicitly register - it does NOT crawl the
    whole phone even though the app holds broad storage permission.

HOW TO USE:
  1. In backend_app.py, register a handler:
        import watcher
        def on_new_file(path):
            logging.info("new file: %s", path)
        watcher.EVENT_HANDLERS.append(on_new_file)

  2. Register a folder to watch (via the /automation/watchers POST route,
     or directly):
        watcher.add_watch("/storage/emulated/0/PyBox/inbox",
                           extensions=[".gguf", ".txt"], recursive=False)
"""

import logging
import os
import sqlite3
import threading
import time
import traceback

_DB_PATH = None
_LOCK = threading.Lock()
_SCAN_INTERVAL_SECONDS = 10

# list of callables(path: str) -> None. Populate this from backend_app.py.
EVENT_HANDLERS = []


def _conn():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init(files_dir):
    """Call once from start_server(). Creates tables, starts the scan thread."""
    global _DB_PATH
    _DB_PATH = os.path.join(files_dir, "automation.db")
    with _LOCK:
        conn = _conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                extensions TEXT NOT NULL DEFAULT '',
                recursive INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_files (
                watch_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                mtime REAL NOT NULL,
                PRIMARY KEY (watch_id, path)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watch_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                detected_at REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    t = threading.Thread(target=_scan_loop, daemon=True)
    t.start()


def add_watch(path, extensions=None, recursive=False):
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO watches (path, extensions, recursive, "
            "created_at) VALUES (?, ?, ?, ?)",
            (path, ",".join(extensions or []), 1 if recursive else 0, time.time()),
        )
        conn.commit()
        conn.close()


def remove_watch(watch_id):
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        conn.execute("DELETE FROM seen_files WHERE watch_id = ?", (watch_id,))
        conn.commit()
        conn.close()


def list_watches():
    with _LOCK:
        conn = _conn()
        rows = conn.execute("SELECT * FROM watches ORDER BY id DESC").fetchall()
        conn.close()
    return [dict(r) for r in rows]


def recent_events(limit=50):
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT watch_events.*, watches.path AS watch_path FROM watch_events "
            "JOIN watches ON watches.id = watch_events.watch_id "
            "ORDER BY watch_events.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def _iter_candidate_files(root, extensions, recursive):
    if not os.path.isdir(root):
        return
    if recursive:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if not extensions or os.path.splitext(f)[1].lower() in extensions:
                    yield os.path.join(dirpath, f)
    else:
        try:
            for f in os.listdir(root):
                full = os.path.join(root, f)
                if os.path.isfile(full) and (
                    not extensions or os.path.splitext(f)[1].lower() in extensions
                ):
                    yield full
        except OSError:
            return


def _scan_once():
    with _LOCK:
        conn = _conn()
        watches = conn.execute(
            "SELECT * FROM watches WHERE enabled = 1"
        ).fetchall()
        conn.close()

    for w in watches:
        watch = dict(w)
        extensions = [e for e in watch["extensions"].split(",") if e]
        with _LOCK:
            conn = _conn()
            seen = {
                r["path"]: r["mtime"]
                for r in conn.execute(
                    "SELECT path, mtime FROM seen_files WHERE watch_id = ?",
                    (watch["id"],),
                ).fetchall()
            }
            conn.close()

        new_or_changed = []
        current_paths = set()
        for path in _iter_candidate_files(watch["path"], extensions, bool(watch["recursive"])):
            current_paths.add(path)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if seen.get(path) != mtime:
                new_or_changed.append((path, mtime))

        if not new_or_changed:
            continue

        with _LOCK:
            conn = _conn()
            now = time.time()
            for path, mtime in new_or_changed:
                conn.execute(
                    "INSERT OR REPLACE INTO seen_files (watch_id, path, mtime) "
                    "VALUES (?, ?, ?)",
                    (watch["id"], path, mtime),
                )
                conn.execute(
                    "INSERT INTO watch_events (watch_id, path, detected_at) "
                    "VALUES (?, ?, ?)",
                    (watch["id"], path, now),
                )
            conn.commit()
            conn.close()

        for path, _mtime in new_or_changed:
            for handler in EVENT_HANDLERS:
                try:
                    handler(path)
                except Exception:
                    logging.error(
                        "watcher handler failed for %s:\n%s",
                        path, traceback.format_exc(),
                    )


def _scan_loop():
    while True:
        try:
            _scan_once()
        except Exception:
            logging.error("watcher scan failed:\n%s", traceback.format_exc())
        time.sleep(_SCAN_INTERVAL_SECONDS)
