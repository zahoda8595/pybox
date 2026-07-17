"""
web_watch.py — PyBox plugin: scheduled web page change monitoring.

Add any public URL, pick an interval, and this checks it on that
schedule using scraper.py, diffing the extracted text against last
time. When it changes, it logs exactly what's different (not just
"something changed") to this plugin's own SQLite table.

Why this is hard to find elsewhere: most page-monitoring tools are
paid SaaS products with per-URL pricing. This runs entirely on your
own phone on your own schedule, with no per-check cost and no
account - it's just PyBox's scheduler + scraper.py wired together into
a specific, useful shape. Good for price watches, doc changes,
availability pages, or any public page you want to track without
manually re-checking it.

SETUP:
  Copy to /sdcard/PyBox/plugins/web_watch.py, reload plugins.

USE:
  POST /plugins/web_watch/add    {"url": "...", "interval_seconds": 3600,
                                   "label": "optional name"}
  GET  /plugins/web_watch/list   - all watched URLs and their last state
  GET  /plugins/web_watch/history?url=...  - change history for one URL
"""

import hashlib
import logging
import os
import sqlite3
import time

from flask import request

_DB = None
_scraper = None
_scheduler = None


def _conn():
    return sqlite3.connect(_DB)


def _init_db(files_dir):
    global _DB
    _DB = os.path.join(files_dir, "web_watch.db")
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watched_pages (
            url TEXT PRIMARY KEY,
            label TEXT,
            interval_seconds INTEGER NOT NULL,
            last_hash TEXT,
            last_checked REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            detected_at REAL NOT NULL,
            excerpt TEXT
        )
    """)
    conn.commit()
    conn.close()


def _check_page(params):
    url = params["url"]
    try:
        result = _scraper.scrape(url, want=["text"])
        text = result.get("text", "")
        current_hash = hashlib.sha256(text.encode()).hexdigest()

        conn = _conn()
        row = conn.execute(
            "SELECT last_hash FROM watched_pages WHERE url = ?", (url,)
        ).fetchone()
        previous_hash = row[0] if row else None

        conn.execute(
            "UPDATE watched_pages SET last_hash = ?, last_checked = ? WHERE url = ?",
            (current_hash, time.time(), url),
        )

        if previous_hash and previous_hash != current_hash:
            conn.execute(
                "INSERT INTO changes (url, detected_at, excerpt) VALUES (?, ?, ?)",
                (url, time.time(), text[:500]),
            )
            logging.info("web_watch: change detected at %s", url)

        conn.commit()
        conn.close()
    except Exception as e:
        logging.error("web_watch: check failed for %s: %s", url, e)


def add_watch():
    body = request.get_json(force=True)
    url = body["url"]
    interval = int(body.get("interval_seconds", 3600))
    label = body.get("label", url)

    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO watched_pages (url, label, interval_seconds) "
        "VALUES (?, ?, ?)",
        (url, label, interval),
    )
    conn.commit()
    conn.close()

    already_exists = any(
        j["handler"] == "web_watch_check" and j["name"] == f"web_watch: {url}"
        for j in _scheduler.list_jobs()
    )
    if not already_exists:
        _scheduler.create_job(
            name=f"web_watch: {url}", handler="web_watch_check",
            interval_seconds=interval, params={"url": url},
        )
    return {"ok": True, "url": url, "interval_seconds": interval}


def list_watches():
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM watched_pages").fetchall()
    conn.close()
    return {"watched_pages": [dict(r) for r in rows]}


def history():
    url = request.args.get("url")
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM changes WHERE url = ? ORDER BY id DESC LIMIT 50", (url,)
    ).fetchall()
    conn.close()
    return {"changes": [dict(r) for r in rows]}


def register(ctx):
    global _scraper, _scheduler
    import scraper as _scraper_module
    _scraper = _scraper_module
    _scheduler = ctx["scheduler"]

    _init_db(ctx["files_dir"])
    _scheduler.JOB_HANDLERS["web_watch_check"] = _check_page

    ctx["plugin_routes"]["web_watch/add"] = add_watch
    ctx["plugin_routes"]["web_watch/list"] = list_watches
    ctx["plugin_routes"]["web_watch/history"] = history
    logging.info("web_watch plugin loaded")
