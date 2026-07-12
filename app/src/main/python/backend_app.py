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
  7. Automation is built in: scheduler.py (periodic background jobs)
     and watcher.py (polling-based folder watching), both SQLite-backed
     in FILES_DIR/automation.db and both driven from /automation/* routes
     below. Register your own job/event handlers by adding to
     scheduler.JOB_HANDLERS / watcher.EVENT_HANDLERS - see those files'
     docstrings. Every mutating /automation/* route requires the
     X-PyBox-Token header (auth.py) - see MainActivity.kt for how the
     WebView gets it automatically via window.PyBoxAuth.
=====================================================================
"""

import json
import logging
import os
import traceback
import urllib.request

from flask import Flask, Response, jsonify, request

import auth
import config
import error_manager
import plugin_loader
import scheduler
import watcher
from auth import require_auth
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


# ---------------------------------------------------------------------
# Automation: scheduled jobs (scheduler.py) and folder watchers (watcher.py)
# Every mutating route here requires the X-PyBox-Token header (auth.py) -
# see MainActivity.kt for how the WebView gets that token automatically.
# ---------------------------------------------------------------------

def _log_event_job(params):
    """Default job handler, registered below - proves the scheduler works
    without needing you to wire anything else up first. Safe to delete
    once you've registered your own handlers."""
    logging.info("scheduler heartbeat job ran. params=%s", params)


def _log_watch_event(path):
    """Default watch handler, registered below - logs any file change in
    a watched folder. Safe to delete/replace once you have real handlers."""
    logging.info("watcher detected file: %s", path)


scheduler.JOB_HANDLERS["log_event"] = _log_event_job
watcher.EVENT_HANDLERS.append(_log_watch_event)


@app.route("/automation/jobs", methods=["GET"])
@require_auth
@safe_route("automation-list-jobs")
def list_jobs():
    return jsonify(scheduler.list_jobs())


@app.route("/automation/jobs", methods=["POST"])
@require_auth
@safe_route("automation-create-job")
def create_job():
    body = request.get_json(force=True)
    job_id = scheduler.create_job(
        name=body["name"],
        handler=body["handler"],
        interval_seconds=int(body["interval_seconds"]),
        params=body.get("params", {}),
        enabled=body.get("enabled", True),
    )
    return jsonify({"id": job_id})


@app.route("/automation/jobs/<int:job_id>", methods=["DELETE"])
@require_auth
@safe_route("automation-delete-job")
def delete_job(job_id):
    scheduler.delete_job(job_id)
    return jsonify({"deleted": job_id})


@app.route("/automation/jobs/<int:job_id>/runs", methods=["GET"])
@require_auth
@safe_route("automation-job-runs")
def job_runs(job_id):
    return jsonify(scheduler.recent_runs(job_id))


@app.route("/automation/watchers", methods=["GET"])
@require_auth
@safe_route("automation-list-watchers")
def list_watchers():
    return jsonify(watcher.list_watches())


@app.route("/automation/watchers", methods=["POST"])
@require_auth
@safe_route("automation-create-watcher")
def create_watcher():
    body = request.get_json(force=True)
    watcher.add_watch(
        path=body["path"],
        extensions=body.get("extensions", []),
        recursive=body.get("recursive", False),
    )
    return jsonify({"ok": True})


@app.route("/automation/watchers/<int:watch_id>", methods=["DELETE"])
@require_auth
@safe_route("automation-delete-watcher")
def delete_watcher(watch_id):
    watcher.remove_watch(watch_id)
    return jsonify({"deleted": watch_id})


@app.route("/automation/events", methods=["GET"])
@require_auth
@safe_route("automation-events")
def automation_events():
    return jsonify(watcher.recent_events())


@app.route("/automation/token", methods=["GET"])
@safe_route("automation-token")
def automation_token():
    """
    Deliberately NOT behind @require_auth - it's how the WebView (running
    inside this same app) discovers the token in the first place. It's
    reachable only via loopback by definition of how Flask is bound, and
    the value itself doesn't grant anything beyond what this app can
    already do to itself. Prefer the JS interface (window.PyBoxAuth) that
    MainActivity.kt injects when possible; this route exists as a fallback.
    """
    return jsonify({"token": auth.get_token()})


# ---------------------------------------------------------------------
# Admin panel: settings, plugin management, logs, all in one page.
# GET /admin itself is unprotected (it's just the shell page - same
# reasoning as /automation/token above); every action button on the
# page calls a protected /admin/* API route with the token attached
# via window.PyBoxAuth, same pattern as the /automation/* routes.
# ---------------------------------------------------------------------

@app.route("/admin")
@safe_route("admin-page")
def admin_page():
    return _ADMIN_HTML


@app.route("/admin/status")
@require_auth
@safe_route("admin-status")
def admin_status():
    return jsonify({
        "config": config.get_all(),
        "jobs": scheduler.list_jobs(),
        "watchers": watcher.list_watches(),
        "plugins": plugin_loader.status(),
    })


@app.route("/admin/config", methods=["POST"])
@require_auth
@safe_route("admin-config-update")
def admin_config_update():
    body = request.get_json(force=True)
    config.set_many(body)
    return jsonify(config.get_all())


@app.route("/admin/plugins/reload", methods=["POST"])
@require_auth
@safe_route("admin-plugins-reload")
def admin_plugins_reload():
    plugin_loader.load_all()
    return jsonify(plugin_loader.status())


@app.route("/admin/logs")
@require_auth
@safe_route("admin-logs")
def admin_logs():
    n = int(request.args.get("lines", 200))
    log_path = os.path.join(FILES_DIR, "pybox.log")
    if not os.path.exists(log_path):
        return jsonify({"lines": []})
    with open(log_path) as f:
        lines = f.readlines()[-n:]
    return jsonify({"lines": lines})


_ADMIN_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox Admin</title>
<style>
  body { font-family: -apple-system, sans-serif; background:#111; color:#eee; margin:0; padding:16px; }
  h1 { font-size:20px; } h2 { font-size:15px; color:#8bd; margin-top:24px; }
  .card { background:#1c1c1c; border-radius:8px; padding:12px; margin-bottom:12px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  td,th { text-align:left; padding:4px 6px; border-bottom:1px solid #333; }
  button { background:#3a5; color:#fff; border:none; border-radius:4px; padding:6px 10px; margin:2px 0; font-size:13px; }
  button.danger { background:#a33; }
  input,select { background:#222; color:#eee; border:1px solid #444; border-radius:4px; padding:4px; margin:2px 0; }
  pre { background:#000; color:#9d9; padding:8px; border-radius:6px; overflow-x:auto; font-size:11px; max-height:300px; overflow-y:auto; }
  .status-ok { color:#6d6; } .status-error { color:#e66; }
</style>
</head>
<body>
<h1>PyBox Admin</h1>

<div class="card">
  <h2>Config / Customization</h2>
  <div id="config"></div>
</div>

<div class="card">
  <h2>Scheduled Jobs</h2>
  <div id="jobs"></div>
</div>

<div class="card">
  <h2>Folder Watchers</h2>
  <div id="watchers"></div>
</div>

<div class="card">
  <h2>Plugins (drop .py files at the PyBox/plugins folder on your SD card)</h2>
  <button onclick="reloadPlugins()">Reload plugins</button>
  <div id="plugins"></div>
</div>

<div class="card">
  <h2>Log (last 200 lines)</h2>
  <button onclick="loadLogs()">Refresh</button>
  <pre id="logs"></pre>
</div>

<script>
function authHeaders() {
  const token = (window.PyBoxAuth && window.PyBoxAuth.getToken) ? window.PyBoxAuth.getToken() : "";
  return { "X-PyBox-Token": token, "Content-Type": "application/json" };
}

async function loadStatus() {
  const r = await fetch("/admin/status", { headers: authHeaders() });
  const d = await r.json();
  renderConfig(d.config);
  renderJobs(d.jobs);
  renderWatchers(d.watchers);
  renderPlugins(d.plugins);
}

function renderConfig(cfg) {
  let html = "<table>";
  for (const [k, v] of Object.entries(cfg)) {
    html += `<tr><td>${k}</td><td><input id="cfg_${k}" value='${JSON.stringify(v)}'></td></tr>`;
  }
  html += "</table><button onclick=\\"saveConfig()\\">Save</button>";
  document.getElementById("config").innerHTML = html;
}

async function saveConfig() {
  const inputs = document.querySelectorAll("[id^=cfg_]");
  const updates = {};
  inputs.forEach(i => {
    const key = i.id.slice(4);
    try { updates[key] = JSON.parse(i.value); } catch (e) { updates[key] = i.value; }
  });
  await fetch("/admin/config", { method: "POST", headers: authHeaders(), body: JSON.stringify(updates) });
  loadStatus();
}

function renderJobs(jobs) {
  let html = "<table><tr><th>Name</th><th>Handler</th><th>Interval(s)</th><th>Last status</th><th></th></tr>";
  jobs.forEach(j => {
    html += `<tr><td>${j.name}</td><td>${j.handler}</td><td>${j.interval_seconds}</td>` +
            `<td class="status-${j.last_status||''}">${j.last_status||'never run'}</td>` +
            `<td><button class="danger" onclick="deleteJob(${j.id})">Delete</button></td></tr>`;
  });
  html += "</table>";
  document.getElementById("jobs").innerHTML = html;
}

async function deleteJob(id) {
  await fetch(`/automation/jobs/${id}`, { method: "DELETE", headers: authHeaders() });
  loadStatus();
}

function renderWatchers(watchers) {
  let html = "<table><tr><th>Path</th><th>Extensions</th><th>Recursive</th><th></th></tr>";
  watchers.forEach(w => {
    html += `<tr><td>${w.path}</td><td>${w.extensions}</td><td>${w.recursive ? 'yes' : 'no'}</td>` +
            `<td><button class="danger" onclick="deleteWatcher(${w.id})">Delete</button></td></tr>`;
  });
  html += "</table>";
  document.getElementById("watchers").innerHTML = html;
}

async function deleteWatcher(id) {
  await fetch(`/automation/watchers/${id}`, { method: "DELETE", headers: authHeaders() });
  loadStatus();
}

function renderPlugins(plugins) {
  let html = "<table><tr><th>File</th><th>Status</th><th>Has register()</th></tr>";
  for (const [name, info] of Object.entries(plugins)) {
    html += `<tr><td>${name}</td><td class="status-${info.status}">${info.status}</td>` +
            `<td>${info.has_register ? 'yes' : 'no'}</td></tr>`;
    if (info.detail) html += `<tr><td colspan="3"><pre>${info.detail}</pre></td></tr>`;
  }
  html += "</table>";
  document.getElementById("plugins").innerHTML = html;
}

async function reloadPlugins() {
  await fetch("/admin/plugins/reload", { method: "POST", headers: authHeaders() });
  loadStatus();
}

async function loadLogs() {
  const r = await fetch("/admin/logs?lines=200", { headers: authHeaders() });
  const d = await r.json();
  document.getElementById("logs").textContent = d.lines.join("");
}

loadStatus();
loadLogs();
setInterval(loadStatus, 10000);
</script>
</body>
</html>"""

# =====================================================================
# >>> PASTE YOUR FLASK ROUTES / LOGIC ABOVE THIS LINE <<<
# =====================================================================


def start_server(files_dir, plugins_dir=None):
    global FILES_DIR
    FILES_DIR = files_dir
    error_manager.init(files_dir)
    auth.init(files_dir)
    config.init(files_dir)
    scheduler.init(files_dir)
    watcher.init(files_dir)

    logging.basicConfig(
        filename=os.path.join(files_dir, "pybox.log"),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("PyBox backend starting. FILES_DIR=%s", files_dir)

    # Plugins load LAST, after everything they might reference (app,
    # scheduler, watcher, config, auth) already exists.
    if plugins_dir:
        plugin_loader.init(plugins_dir, {
            "app": app,
            "scheduler": scheduler,
            "watcher": watcher,
            "config": config,
            "require_auth": require_auth,
        })

    try:
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    except Exception:
        logging.error("Backend failed to start:\n%s", traceback.format_exc())
        raise
