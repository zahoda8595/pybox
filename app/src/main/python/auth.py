"""
auth.py — local-only request authentication for PyBox's automation API.

WHY THIS EXISTS:
  Flask binds to 127.0.0.1 (loopback), which keeps it unreachable from
  your Wi-Fi/network - but loopback is shared across ALL apps on the same
  Android device. Any other app on your phone could, in principle, open
  a socket to 127.0.0.1:5000 and hit these routes. A random per-install
  token, stored only in this app's private storage (which Android
  sandboxes from every other app without root), closes that gap for
  anything beyond the WebView this app itself controls.

HOW IT WORKS:
  - A random 32-byte token is generated once per install and written to
    FILES_DIR/auth_token.txt (app-private internal storage — Android's
    /data/data/com.khan.pybox/files, not the shared SD card, so nothing
    else can read it without root).
  - MainActivity.kt reads that same file after the backend comes up and
    injects it into the WebView via a JavaScript interface, so the page
    can attach it to fetch() calls automatically.
  - @require_auth checks the X-PyBox-Token header against it.

HOW TO USE IN backend_app.py:
    from auth import require_auth

    @app.route("/automation/jobs", methods=["POST"])
    @require_auth
    @safe_route("automation-create-job")
    def create_job():
        ...
"""

import functools
import os
import secrets

from flask import request, jsonify

_TOKEN = None


def init(files_dir):
    """Call once from start_server(), before any route needs the token."""
    global _TOKEN
    path = os.path.join(files_dir, "auth_token.txt")
    if os.path.exists(path):
        with open(path) as f:
            _TOKEN = f.read().strip()
    if not _TOKEN:
        _TOKEN = secrets.token_hex(32)
        with open(path, "w") as f:
            f.write(_TOKEN)
    return _TOKEN


def get_token():
    return _TOKEN


def require_auth(fn):
    """Decorator: rejects requests missing a matching X-PyBox-Token header."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        supplied = request.headers.get("X-PyBox-Token", "")
        if not _TOKEN or not secrets.compare_digest(supplied, _TOKEN):
            return jsonify({"error": "missing or invalid X-PyBox-Token header"}), 401
        return fn(*args, **kwargs)
    return wrapper
