"""
scheduler.py — periodic background jobs for PyBox, SQLite-backed.

WHAT THIS GIVES YOU:
  - Register a Python function once (JOB_HANDLERS), then create jobs that
    call it on an interval - jobs and their schedule persist in SQLite,
    so they survive an app restart.
  - Runs on a single background daemon thread inside the same process as
    the Flask server (started by start_server() in backend_app.py) -
    nothing external, nothing that needs the phone to stay unlocked.
  - Every run is logged (success/failure, duration) to the same DB so you
    can see history via GET /automation/jobs.

HOW TO USE:
  1. In backend_app.py, register a handler:
        import scheduler
        def my_cleanup_job(params):
            ...
        scheduler.JOB_HANDLERS["cleanup"] = my_cleanup_job

  2. Create a job (via the /automation/jobs POST route, or directly):
        scheduler.create_job(name="nightly cleanup", handler="cleanup",
                              interval_seconds=86400, params={})

  Jobs only run handlers that are registered by name in JOB_HANDLERS -
  there is deliberately no "run arbitrary shell command" handler built
  in, so a compromised or buggy job spec can't do more than whatever
  Python functions you've explicitly wired up.
"""

import json
import logging
import os
import threading
import time
import traceback

import config
import dbcore
import executor

_DB_PATH = None
_LOCK = threading.Lock()
_TICK_SECONDS = 5

# name -> callable(params: dict) -> None. Populate this from backend_app.py.
JOB_HANDLERS = {}


def _conn():
    return dbcore.get_connection(_DB_PATH)


def init(files_dir):
    """Call once from start_server(). Creates tables, starts the tick thread."""
    global _DB_PATH
    _DB_PATH = os.path.join(files_dir, "automation.db")
    with _LOCK:
        conn = _conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                handler TEXT NOT NULL,
                params TEXT NOT NULL DEFAULT '{}',
                interval_seconds INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run REAL,
                next_run REAL NOT NULL,
                last_status TEXT,
                created_at REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                started_at REAL NOT NULL,
                duration_ms INTEGER,
                status TEXT NOT NULL,
                detail TEXT
            )
        """)
        dbcore.ensure_indexes(conn, "jobs", [
            ("idx_jobs_enabled_next_run", "enabled, next_run"),
            ("idx_jobs_created_at", "created_at"),
        ])
        dbcore.ensure_indexes(conn, "job_runs", [
            ("idx_job_runs_job_id", "job_id"),
            ("idx_job_runs_started_at", "started_at"),
        ])
        conn.commit()
        conn.close()

    t = threading.Thread(target=_tick_loop, daemon=True)
    t.start()


def create_job(name, handler, interval_seconds, params=None, enabled=True):
    if handler not in JOB_HANDLERS:
        raise ValueError(
            f"No handler registered for '{handler}'. "
            f"Known handlers: {list(JOB_HANDLERS.keys())}"
        )
    now = time.time()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO jobs (name, handler, params, interval_seconds, "
            "enabled, next_run, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, handler, json.dumps(params or {}), interval_seconds,
             1 if enabled else 0, now + interval_seconds, now),
        )
        conn.commit()
        job_id = cur.lastrowid
        conn.close()
    return job_id


def list_jobs():
    with _LOCK:
        conn = _conn()
        rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
        conn.close()
    return [dict(r) for r in rows]


def delete_job(job_id):
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        conn.execute("DELETE FROM job_runs WHERE job_id = ?", (job_id,))
        conn.commit()
        conn.close()


def set_enabled(job_id, enabled):
    with _LOCK:
        conn = _conn()
        conn.execute("UPDATE jobs SET enabled = ? WHERE id = ?",
                     (1 if enabled else 0, job_id))
        conn.commit()
        conn.close()


def recent_runs(job_id, limit=10):
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM job_runs WHERE job_id = ? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


def _run_due_jobs():
    try:
        if not config.get("automation_enabled", True):
            return
    except Exception:
        pass  # config not initialized yet, or plugin removed it - fail open

    now = time.time()
    with _LOCK:
        conn = _conn()
        due = conn.execute(
            "SELECT * FROM jobs WHERE enabled = 1 AND next_run <= ?", (now,)
        ).fetchall()
        conn.close()

    for row in due:
        job = dict(row)
        handler = JOB_HANDLERS.get(job["handler"])
        started = time.time()
        if handler is None:
            status, detail = "error", f"unregistered handler '{job['handler']}'"
            duration_ms = 0
        else:
            # JOB_TIMEOUT_SECONDS (default 60s) caps a single job run so one
            # slow/hung handler can never block every future tick - see
            # executor.py's docstring for why this used to be unbounded.
            try:
                job_timeout = config.get("job_timeout_seconds", 60)
            except Exception:
                job_timeout = 60
            result = executor.run(
                handler, args=(json.loads(job["params"]),),
                timeout=job_timeout,
                name=job["name"],
            )
            duration_ms = int(result.elapsed_seconds * 1000)
            if result.timed_out:
                status, detail = "timeout", f"job exceeded its timeout and was abandoned"
                logging.error("scheduler job '%s' timed out after %ss", job["name"], result.elapsed_seconds)
            elif result.error:
                status, detail = "error", result.error
                logging.error("scheduler job '%s' failed:\n%s", job["name"], detail)
            else:
                status, detail = "ok", None

        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE jobs SET last_run = ?, next_run = ?, last_status = ? "
                "WHERE id = ?",
                (started, started + job["interval_seconds"], status, job["id"]),
            )
            conn.execute(
                "INSERT INTO job_runs (job_id, started_at, duration_ms, status, "
                "detail) VALUES (?, ?, ?, ?, ?)",
                (job["id"], started, duration_ms, status, detail),
            )
            conn.commit()
            conn.close()


def _tick_loop():
    while True:
        try:
            _run_due_jobs()
        except Exception:
            logging.error("scheduler tick failed:\n%s", traceback.format_exc())
        time.sleep(_TICK_SECONDS)
