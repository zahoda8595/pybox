"""
auto_summarize.py — PyBox plugin: on-device auto-summarization.

Watches PyBox/inbox for .txt files. When one appears, sends it to the
LOCAL llama-server (the one LlamaEngineService.kt runs on your phone -
nothing leaves the device) for a summary + suggested tags, and stores
the result in a small SQLite table you can query or search.

Why this is hard to find elsewhere: it's not a generic "summarize with
an API" tool - it's wired directly into the same on-device inference
engine already running in PyBox, so it works completely offline, with
no per-request cost and no data leaving your phone. Most "AI document
summarizer" apps/plugins are cloud-API wrappers; this one specifically
isn't.

SETUP:
  1. Copy this file to /sdcard/PyBox/plugins/auto_summarize.py
  2. mkdir -p /sdcard/PyBox/inbox
  3. Make sure the LLM engine is running (Settings -> Start LLM Engine)
  4. Reload plugins in the admin panel
  5. Drop a .txt file into PyBox/inbox - within ~10s (the watcher's scan
     interval) it gets summarized automatically.

USE:
  GET /plugins/summaries        - list everything summarized so far
  GET /plugins/summaries/<id>   - one summary in full
"""

import json
import logging
import os
import sqlite3
import time
import urllib.request

LLM_URL = "http://127.0.0.1:8081/completion"
_DB = None


def _init_db(files_dir):
    global _DB
    _DB = os.path.join(files_dir, "auto_summarize.db")
    conn = sqlite3.connect(_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            summary TEXT,
            tags TEXT,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _summarize_file(path):
    try:
        with open(path, errors="ignore") as f:
            content = f.read()[:6000]  # keep prompts small on a phone LLM

        prompt = (
            "Summarize the following text in 2-3 sentences, then suggest "
            "3-5 short comma-separated tags. Format as:\n"
            "SUMMARY: ...\nTAGS: tag1, tag2, tag3\n\nTEXT:\n" + content
        )
        payload = json.dumps({
            "prompt": prompt, "n_predict": 220, "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            LLM_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            result = json.loads(r.read())
        raw = result.get("content", "")

        summary, tags = raw, ""
        for line in raw.splitlines():
            if line.upper().startswith("SUMMARY:"):
                summary = line.split(":", 1)[1].strip()
            elif line.upper().startswith("TAGS:"):
                tags = line.split(":", 1)[1].strip()

        conn = sqlite3.connect(_DB)
        conn.execute(
            "INSERT INTO summaries (source_path, summary, tags, created_at) "
            "VALUES (?, ?, ?, ?)",
            (path, summary, tags, time.time()),
        )
        conn.commit()
        conn.close()
        logging.info("auto_summarize: summarized %s", path)
    except Exception as e:
        logging.error("auto_summarize failed for %s: %s", path, e)


def register(ctx):
    _init_db(ctx["files_dir"])

    ctx["watcher"].EVENT_HANDLERS.append(
        lambda path: _summarize_file(path) if path.endswith(".txt") else None
    )

    def list_summaries():
        conn = sqlite3.connect(_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, source_path, summary, tags, created_at "
            "FROM summaries ORDER BY id DESC"
        ).fetchall()
        conn.close()
        return {"summaries": [dict(r) for r in rows]}

    ctx["plugin_routes"]["summaries"] = list_summaries

    # Register the inbox folder as a watch, if not already present.
    ctx["watcher"].add_watch(
        "/storage/emulated/0/PyBox/inbox", extensions=[".txt"], recursive=False
    )
    logging.info("auto_summarize plugin loaded - watching PyBox/inbox")
