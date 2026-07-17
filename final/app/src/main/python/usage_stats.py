"""
usage_stats.py — stores your OWN device's per-app screen time, as reported
by Android's sanctioned UsageStatsManager API (see UsageStatsHelper.kt).

This is aggregate foreground-time data (package name + minutes used, per
day) - the same category of data Android's own Digital Wellbeing screen
shows you. It does NOT read what's on screen, what you typed, or any
in-app content - UsageStatsManager doesn't expose any of that, by design.
"""

import logging
import os
import time

import dbcore

_DB = None


def init(files_dir):
    global _DB
    _DB = os.path.join(files_dir, "usage_stats.db")
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_name TEXT NOT NULL,
            app_label TEXT,
            day TEXT NOT NULL,
            foreground_ms INTEGER NOT NULL,
            recorded_at REAL NOT NULL,
            UNIQUE(package_name, day)
        )
    """)
    dbcore.ensure_indexes(conn, "usage", [
        ("idx_usage_day", "day"),
        ("idx_usage_package", "package_name"),
    ])
    conn.commit()
    conn.close()
    logging.info("usage_stats: initialized at %s", _DB)


def _conn():
    return dbcore.get_connection(_DB)


def record_batch(entries):
    """entries: list of {package_name, app_label, day (YYYY-MM-DD), foreground_ms}
    Upserts so re-reporting the same day just updates the total."""
    now = time.time()
    conn = _conn()
    for e in entries:
        conn.execute(
            "INSERT INTO usage (package_name, app_label, day, foreground_ms, recorded_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(package_name, day) DO UPDATE SET "
            "foreground_ms=excluded.foreground_ms, app_label=excluded.app_label, recorded_at=excluded.recorded_at",
            (e["package_name"], e.get("app_label"), e["day"], e["foreground_ms"], now),
        )
    conn.commit()
    conn.close()
    logging.info("usage_stats: recorded %d entries", len(entries))
    return {"recorded": len(entries)}


def summary(days=7):
    """Total foreground time per app over the last N days."""
    conn = _conn()
    rows = conn.execute("""
        SELECT package_name, app_label, SUM(foreground_ms) total_ms, COUNT(DISTINCT day) days_seen
        FROM usage
        WHERE day >= date('now', ?)
        GROUP BY package_name
        ORDER BY total_ms DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def daily(day):
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM usage WHERE day=? ORDER BY foreground_ms DESC", (day,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
