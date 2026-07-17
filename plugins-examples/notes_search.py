"""
notes_search.py — PyBox plugin: quick notes with real full-text search.

A tiny, fully offline note-capture tool with actual search - not just
"scroll through a list." Uses SQLite's built-in FTS5 (full-text search)
extension, which ships inside Android's SQLite already, so there's no
extra dependency at all: everything here is Python standard library.

Why this is hard to find elsewhere: most "quick capture" note apps
either need an account/cloud sync, or do naive substring search that
falls apart past a few hundred notes. FTS5 gives you ranked, indexed
search over potentially thousands of notes, instantly, entirely on-device.

SETUP:
  Copy to /sdcard/PyBox/plugins/notes_search.py, reload plugins. No
  external folders needed - everything lives in the app's own storage.

USE:
  POST /plugins/notes/add   {"text": "...", "tags": "optional,tags"}
  GET  /plugins/notes/search?q=some+words
  GET  /plugins/notes/recent?limit=20
"""

import logging
import os
import sqlite3
import time

from flask import request

_DB = None


def _conn():
    return sqlite3.connect(_DB)


def _init_db(files_dir):
    global _DB
    _DB = os.path.join(files_dir, "notes_search.db")
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            tags TEXT DEFAULT '',
            created_at REAL NOT NULL
        )
    """)
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                text, tags, content='notes', content_rowid='id'
            )
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
                INSERT INTO notes_fts(rowid, text, tags)
                VALUES (new.id, new.text, new.tags);
            END
        """)
        _FTS_AVAILABLE = True
    except sqlite3.OperationalError:
        # FTS5 not compiled into this SQLite build - fall back to LIKE
        # search below. Rare on Android, but don't crash if it happens.
        _FTS_AVAILABLE = False
    conn.commit()
    conn.close()
    return _FTS_AVAILABLE


_fts_available = True


def add_note():
    body = request.get_json(force=True)
    text = body["text"]
    tags = body.get("tags", "")
    conn = _conn()
    conn.execute(
        "INSERT INTO notes (text, tags, created_at) VALUES (?, ?, ?)",
        (text, tags, time.time()),
    )
    conn.commit()
    note_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"id": note_id}


def search_notes():
    q = request.args.get("q", "")
    conn = _conn()
    conn.row_factory = sqlite3.Row
    if _fts_available and q:
        try:
            rows = conn.execute(
                "SELECT notes.id, notes.text, notes.tags, notes.created_at "
                "FROM notes_fts JOIN notes ON notes.id = notes_fts.rowid "
                "WHERE notes_fts MATCH ? ORDER BY rank LIMIT 50",
                (q,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT id, text, tags, created_at FROM notes "
                "WHERE text LIKE ? OR tags LIKE ? ORDER BY id DESC LIMIT 50",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, text, tags, created_at FROM notes "
            "WHERE text LIKE ? OR tags LIKE ? ORDER BY id DESC LIMIT 50",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    conn.close()
    return {"results": [dict(r) for r in rows]}


def recent_notes():
    limit = int(request.args.get("limit", 20))
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, text, tags, created_at FROM notes ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return {"notes": [dict(r) for r in rows]}


def register(ctx):
    global _fts_available
    _fts_available = _init_db(ctx["files_dir"])
    ctx["plugin_routes"]["notes/add"] = add_note
    ctx["plugin_routes"]["notes/search"] = search_notes
    ctx["plugin_routes"]["notes/recent"] = recent_notes
    logging.info(
        "notes_search plugin loaded (FTS5 %s)",
        "available" if _fts_available else "unavailable, using LIKE fallback",
    )
