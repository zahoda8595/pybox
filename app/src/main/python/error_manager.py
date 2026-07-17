"""
error_manager.py — centralized error handling for your pasted backend code.

WHAT THIS GIVES YOU:
  - Every error gets logged as a structured line in errors.jsonl (timestamp,
    route, error type, message, traceback) instead of just vanishing.
  - Wrap any Flask route with @safe_route("name") and a crash in that route
    will never take the whole app down - it returns a friendly error page
    and the crash is logged.
  - If a specific route crashes 3 times in a row, it auto-disables itself
    (serves a "temporarily disabled" message instead of running your code
    again) so a broken route can't crash-loop and drain the battery or
    spam errors. It re-enables automatically after a successful reset.

HOW TO USE IN backend_app.py:
    from error_manager import safe_route

    @app.route("/my-thing")
    @safe_route("my-thing")
    def my_thing():
        ... your code ...
"""

import functools
import json
import os
import time
import traceback

_FILES_DIR = None
_FAILURE_COUNTS = {}          # route_name -> consecutive failure count
_DISABLED_ROUTES = {}         # route_name -> timestamp disabled
FAILURE_THRESHOLD = 3         # consecutive failures before auto-disabling
DISABLE_COOLDOWN_SECONDS = 60  # how long a route stays disabled before retry


def init(files_dir):
    """Call once, from start_server(), before anything else uses this module."""
    global _FILES_DIR
    _FILES_DIR = files_dir


def _errors_path():
    return os.path.join(_FILES_DIR or ".", "errors.jsonl")


def log_error(route_name, exc):
    """Append a structured error record. Never raises."""
    try:
        record = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "route": route_name,
            "error_type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        with open(_errors_path(), "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass  # logging must never itself crash the backend


def get_recent_errors(limit=20):
    """Returns the most recent error records, newest last."""
    path = _errors_path()
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def is_route_disabled(route_name):
    disabled_at = _DISABLED_ROUTES.get(route_name)
    if disabled_at is None:
        return False
    if time.time() - disabled_at > DISABLE_COOLDOWN_SECONDS:
        # cooldown expired - give it another chance
        _DISABLED_ROUTES.pop(route_name, None)
        _FAILURE_COUNTS[route_name] = 0
        return False
    return True


def safe_route(route_name):
    """Decorator: isolates a Flask view function's crashes."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if is_route_disabled(route_name):
                return (
                    f"<h3>'{route_name}' is temporarily disabled.</h3>"
                    f"<p>It crashed {FAILURE_THRESHOLD} times in a row and was "
                    f"paused to protect the app. It will retry automatically "
                    f"in under a minute, or check the log for the cause.</p>",
                    503,
                )
            try:
                result = fn(*args, **kwargs)
                _FAILURE_COUNTS[route_name] = 0  # success resets the streak
                return result
            except Exception as e:
                log_error(route_name, e)
                count = _FAILURE_COUNTS.get(route_name, 0) + 1
                _FAILURE_COUNTS[route_name] = count
                if count >= FAILURE_THRESHOLD:
                    _DISABLED_ROUTES[route_name] = time.time()
                return (
                    f"<h3>Error in '{route_name}'</h3>"
                    f"<p>{type(e).__name__}: {e}</p>"
                    f"<p>Logged to errors.jsonl - view it from the "
                    f"settings button in the app.</p>",
                    500,
                )
        return wrapper
    return decorator
