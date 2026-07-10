"""
=====================================================================
 PASTE YOUR PYTHON BACKEND CODE INTO THIS FILE
=====================================================================
This runs INSIDE the Android app via Chaquopy (an embedded CPython
interpreter). It behaves like a normal Flask app - your existing
routes, SQLite calls, etc. can go straight in here.

RULES:
  1. Keep the Flask object named `app` (already created below) -
     just add routes to it, don't create a second Flask() instance.
  2. Do NOT call app.run() yourself. start_server() at the bottom
     does that, bound to 127.0.0.1 (loopback only - never exposed
     off the phone).
  3. For files/databases (SQLite, ChromaDB, etc.), write inside
     FILES_DIR (set automatically below) - that's the app's private,
     persistent storage on the phone and survives app restarts.
  4. If your code needs extra pip packages, add them to the
     chaquopy { pip { install(...) } } block in app/build.gradle.
     NOTE: only pure-Python or Android-prebuilt packages work here -
     Chaquopy's pip can't install native binaries. llama.cpp itself is
     NOT installed this way: it's cross-compiled separately by
     app/src/main/cpp/CMakeLists.txt into a standalone binary that
     LlamaEngineService.kt runs as a background process on
     127.0.0.1:8081. Use the /llm/* routes below to reach it from here.
  5. You can also drop extra .py files in this same folder
     (app/src/main/python/) and `import` them normally from here.
  6. Decorate every route with @safe_route("some-name") (imported
     below). A crash in that route then gets logged and isolated
     instead of taking the whole backend down - and if a route
     crashes 3 times in a row it auto-disables itself for a minute
     rather than crash-looping. See error_manager.py for details.
=====================================================================
"""

import json
import logging
import os
import traceback
import urllib.request

from flask import Flask, Response, request

import error_manager
from error_manager import safe_route

app = Flask(__name__)

FILES_DIR = None  # set by start_server() below - use for db/file paths

# Where LlamaEngineService.kt binds the compiled llama-server process.
LLM_BASE_URL = "http://127.0.0.1:8081"


# ---------------------------------------------------------------------
# Safety net for anything NOT wrapped in @safe_route (e.g. errors in
# Flask's own dispatch): log it, return a friendly page.
# ---------------------------------------------------------------------
@app.errorhandler(Exception)
def handle_any_error(e):
    error_manager.log_error("unhandled", e)
    return (
        "<h3>Something went wrong in the backend.</h3>"
        "<p>Details were written to errors.jsonl - open it from the "
        "settings button in the app.</p>",
        500,
    )

# =====================================================================
# >>> PASTE YOUR FLASK ROUTES / LOGIC BELOW THIS LINE <<<
# =====================================================================

@app.route("/")
@safe_route("home")
def home():
    return (
        "<h2>PyBox is running locally on your phone.</h2>"
        "<p>This is a placeholder. Replace the code between the "
        "PASTE markers in backend_app.py with your own routes.</p>"
    )


@app.route("/llm/status")
@safe_route("llm-status")
def llm_status():
    """Checks whether LlamaEngineService's process is up and responding."""
    try:
        with urllib.request.urlopen(f"{LLM_BASE_URL}/health", timeout=1.5) as r:
            return Response(r.read(), status=r.status, mimetype="application/json")
    except Exception as e:
        return Response(
            json.dumps({"running": False, "error": str(e)}),
            status=503,
            mimetype="application/json",
        )


@app.route("/llm/generate", methods=["POST"])
@safe_route("llm-generate")
def llm_generate():
    """
    Proxies to the local llama-server /completion endpoint. Body is passed
    straight through - see llama.cpp's server docs for accepted fields
    (prompt, n_predict, temperature, stop, stream, etc).
    Engine must be started first (settings -> Start LLM Engine, or have
    your own automation call LlamaEngineService's ACTION_START intent).
    """
    payload = request.get_data()
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/completion",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return Response(r.read(), status=r.status, mimetype="application/json")
    except urllib.error.URLError as e:
        return Response(
            json.dumps({
                "error": "LLM engine unreachable. Is it started? "
                         "(settings -> Start LLM Engine)",
                "detail": str(e),
            }),
            status=503,
            mimetype="application/json",
        )

# =====================================================================
# >>> PASTE YOUR FLASK ROUTES / LOGIC ABOVE THIS LINE <<<
# =====================================================================


def start_server(files_dir):
    global FILES_DIR
    FILES_DIR = files_dir
    error_manager.init(files_dir)

    logging.basicConfig(
        filename=os.path.join(files_dir, "pybox.log"),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("PyBox backend starting. FILES_DIR=%s", files_dir)

    try:
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    except Exception:
        logging.error("Backend failed to start:\n%s", traceback.format_exc())
        raise
