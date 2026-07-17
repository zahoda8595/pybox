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
import gdrive
import osint_tools
import plugin_loader
import scheduler
import scraper
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
# Plugin dispatch: Flask 3.x refuses app.route()/add_url_rule() calls
# once the server has handled its first request ("setup method ... can
# no longer be called"). That breaks hot-reloading plugins that try to
# register routes directly - the FIRST load (at app startup, before any
# request) works, but every later "Reload plugins" click would fail.
#
# Fix: register ONE real Flask route here, at startup, before app.run().
# Plugins never touch app.route() themselves - they register a plain
# function into PLUGIN_ROUTES by name, and this one route looks it up
# and dispatches to it on every request. Reloading a plugin just swaps
# the dict entry, which works at any time since it isn't a Flask
# setup-method call at all.
# ---------------------------------------------------------------------

PLUGIN_ROUTES = {}


@app.route("/plugins/<path:name>", methods=["GET", "POST", "PUT", "DELETE"])
@safe_route("plugin-dispatch")
def plugin_dispatch(name):
    handler = PLUGIN_ROUTES.get(name)
    if handler is None:
        return jsonify({"error": f"no plugin route registered for '{name}'"}), 404
    return handler()


# ---------------------------------------------------------------------
# Web scraping (scraper.py) - public pages only, see that file's docstring.
# ---------------------------------------------------------------------

@app.route("/scrape", methods=["POST"])
@require_auth
@safe_route("scrape")
def scrape_route():
    body = request.get_json(force=True)
    url = body["url"]
    want = body.get("want", ["text", "links", "metadata"])
    return jsonify(scraper.scrape(url, want=want))


# ---------------------------------------------------------------------
# OSINT (osint_tools.py) - passive, public-records lookups only.
# ---------------------------------------------------------------------

@app.route("/osint/whois")
@require_auth
@safe_route("osint-whois")
def osint_whois():
    domain = request.args.get("domain")
    return jsonify(osint_tools.whois_lookup(domain))


@app.route("/osint/dns")
@require_auth
@safe_route("osint-dns")
def osint_dns():
    domain = request.args.get("domain")
    return jsonify(osint_tools.dns_lookup(domain))


@app.route("/osint/fingerprint")
@require_auth
@safe_route("osint-fingerprint")
def osint_fingerprint():
    url = request.args.get("url")
    return jsonify(osint_tools.http_fingerprint(url))


@app.route("/osint/subdomains")
@require_auth
@safe_route("osint-subdomains")
def osint_subdomains():
    domain = request.args.get("domain")
    return jsonify(osint_tools.subdomain_search(domain))


@app.route("/osint/file-metadata")
@require_auth
@safe_route("osint-file-metadata")
def osint_file_metadata():
    path = request.args.get("path")
    return jsonify(osint_tools.file_metadata(path))


# ---------------------------------------------------------------------
# Google Drive (gdrive.py) - OAuth-authorized by you, see that file's
# docstring for the one-time Google Cloud setup this needs.
# /drive/authorize and /drive/oauth2callback are unprotected on purpose:
# they're navigated to directly (not fetch()'d), so there's no reliable
# way to attach the X-PyBox-Token header to them anyway - same reasoning
# as /admin and /automation/token above.
# ---------------------------------------------------------------------

@app.route("/drive/authorize")
@safe_route("drive-authorize")
def drive_authorize():
    if not gdrive.has_client_secrets():
        return (
            "client_secrets.json not found at PyBox/client_secrets.json. "
            "See gdrive.py's docstring for the one-time Google Cloud setup "
            "steps.", 400
        )
    from flask import redirect
    return redirect(gdrive.build_authorize_url())


@app.route("/drive/oauth2callback")
@safe_route("drive-oauth2callback")
def drive_oauth2callback():
    gdrive.handle_callback(request.url)
    return "Google Drive authorized. You can close this and return to PyBox."


@app.route("/drive/status")
@require_auth
@safe_route("drive-status")
def drive_status():
    creds = gdrive.get_credentials()
    return jsonify({
        "client_secrets_configured": gdrive.has_client_secrets(),
        "authorized": creds is not None,
    })


@app.route("/drive/files")
@require_auth
@safe_route("drive-files")
def drive_files():
    query = request.args.get("q")
    return jsonify(gdrive.list_files(query=query))


@app.route("/drive/download/<file_id>")
@require_auth
@safe_route("drive-download")
def drive_download(file_id):
    content, error = gdrive.download_file(file_id)
    if error:
        return jsonify({"error": error}), 400
    return Response(content, mimetype="application/octet-stream")


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
  * { box-sizing: border-box; }
  body { font-family: -apple-system, sans-serif; background:#0d0d0d; color:#e8e8e8; margin:0; padding:14px; line-height:1.4; }
  h1 { font-size:19px; margin:0 0 14px; }
  .card { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:10px; padding:14px; margin-bottom:14px; }
  .card-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; }
  .card-header h2 { font-size:14px; color:#7ec8f2; margin:0; font-weight:600; }
  .count { background:#263238; color:#8bd; font-size:11px; padding:2px 8px; border-radius:10px; }
  .hint { color:#888; font-size:12px; margin:0 0 10px; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  td,th { text-align:left; padding:6px 6px; border-bottom:1px solid #262626; }
  th { color:#999; font-weight:500; font-size:11px; text-transform:uppercase; letter-spacing:.03em; }
  tr:last-child td { border-bottom:none; }
  .empty-row td { color:#666; font-style:italic; padding:10px 6px; }
  button { background:#2e7d4f; color:#fff; border:none; border-radius:6px; padding:7px 12px; font-size:13px; cursor:pointer; }
  button.danger { background:#8a3030; }
  button.secondary { background:#333; }
  input,select { background:#141414; color:#eee; border:1px solid #333; border-radius:6px; padding:5px 7px; font-size:12.5px; width:100%; }
  .cfg-row { display:flex; align-items:center; gap:10px; padding:6px 0; border-bottom:1px solid #262626; }
  .cfg-row label { flex:0 0 42%; color:#aaa; font-size:12.5px; word-break:break-word; }
  .cfg-row input { flex:1; }
  pre { background:#000; color:#8fbf8f; padding:10px; border-radius:8px; overflow-x:auto; font-size:11px; max-height:260px; overflow-y:auto; margin:6px 0 0; }
  details { margin-top:4px; }
  summary { cursor:pointer; color:#e88; font-size:12px; }
  .status-ok { color:#6fcf6f; font-weight:600; }
  .status-error { color:#e26666; font-weight:600; }
  .actions { text-align:right; white-space:nowrap; }
  .toolbar { display:flex; gap:8px; margin-bottom:10px; }
</style>
</head>
<body>
<h1>PyBox Admin</h1>

<div class="card">
  <div class="card-header"><h2>Config / Customization</h2></div>
  <div id="config"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Scheduled Jobs</h2><span class="count" id="jobs-count">0</span></div>
  <p class="hint">Jobs only run handlers registered in scheduler.JOB_HANDLERS - create new ones via a plugin.</p>
  <div id="jobs"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Folder Watchers</h2><span class="count" id="watchers-count">0</span></div>
  <p class="hint">Only scans folders registered here - never the whole phone.</p>
  <div id="watchers"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Plugins</h2><span class="count" id="plugins-count">0</span></div>
  <p class="hint">Drop .py files at PyBox/plugins on your SD card, then reload - no rebuild needed.</p>
  <div class="toolbar"><button onclick="reloadPlugins()">Reload plugins</button></div>
  <div id="plugins"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Log</h2></div>
  <div class="toolbar">
    <button class="secondary" onclick="loadLogs(30)">Last 30</button>
    <button class="secondary" onclick="loadLogs(200)">Last 200</button>
  </div>
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
  let html = "";
  for (const [k, v] of Object.entries(cfg)) {
    html += `<div class="cfg-row"><label>${k}</label><input id="cfg_${k}" value='${JSON.stringify(v)}'></div>`;
  }
  html += '<div class="toolbar" style="margin-top:10px"><button onclick="saveConfig()">Save</button></div>';
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
  document.getElementById("jobs-count").textContent = jobs.length;
  if (!jobs.length) {
    document.getElementById("jobs").innerHTML = '<table><tr class="empty-row"><td>No jobs yet.</td></tr></table>';
    return;
  }
  let html = "<table><tr><th>Name</th><th>Handler</th><th>Every</th><th>Status</th><th></th></tr>";
  jobs.forEach(j => {
    html += `<tr><td>${j.name}</td><td>${j.handler}</td><td>${j.interval_seconds}s</td>` +
            `<td class="status-${j.last_status||''}">${j.last_status||'pending'}</td>` +
            `<td class="actions"><button class="danger" onclick="deleteJob(${j.id})">Delete</button></td></tr>`;
  });
  html += "</table>";
  document.getElementById("jobs").innerHTML = html;
}

async function deleteJob(id) {
  await fetch(`/automation/jobs/${id}`, { method: "DELETE", headers: authHeaders() });
  loadStatus();
}

function renderWatchers(watchers) {
  document.getElementById("watchers-count").textContent = watchers.length;
  if (!watchers.length) {
    document.getElementById("watchers").innerHTML = '<table><tr class="empty-row"><td>No watchers yet.</td></tr></table>';
    return;
  }
  let html = "<table><tr><th>Path</th><th>Ext</th><th>Recursive</th><th></th></tr>";
  watchers.forEach(w => {
    html += `<tr><td style="word-break:break-all">${w.path}</td><td>${w.extensions||'any'}</td><td>${w.recursive ? 'yes' : 'no'}</td>` +
            `<td class="actions"><button class="danger" onclick="deleteWatcher(${w.id})">Delete</button></td></tr>`;
  });
  html += "</table>";
  document.getElementById("watchers").innerHTML = html;
}

async function deleteWatcher(id) {
  await fetch(`/automation/watchers/${id}`, { method: "DELETE", headers: authHeaders() });
  loadStatus();
}

function renderPlugins(plugins) {
  const names = Object.keys(plugins);
  document.getElementById("plugins-count").textContent = names.length;
  if (!names.length) {
    document.getElementById("plugins").innerHTML = '<table><tr class="empty-row"><td>No plugins loaded.</td></tr></table>';
    return;
  }
  let html = "<table><tr><th>File</th><th>Status</th><th>register()</th></tr>";
  for (const [name, info] of Object.entries(plugins)) {
    html += `<tr><td>${name}</td><td class="status-${info.status}">${info.status}</td>` +
            `<td>${info.has_register ? 'yes' : 'no'}</td></tr>`;
    if (info.detail) {
      html += `<tr><td colspan="3"><details><summary>show error</summary><pre>${info.detail}</pre></details></td></tr>`;
    }
  }
  html += "</table>";
  document.getElementById("plugins").innerHTML = html;
}

async function reloadPlugins() {
  await fetch("/admin/plugins/reload", { method: "POST", headers: authHeaders() });
  loadStatus();
}

async function loadLogs(n) {
  const r = await fetch(`/admin/logs?lines=${n}`, { headers: authHeaders() });
  const d = await r.json();
  document.getElementById("logs").textContent = d.lines.join("") || "(empty)";
}

loadStatus();
loadLogs(30);
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

    # Logging goes first, unconditionally, so every failure below actually
    # gets recorded somewhere instead of silently killing startup.
    logging.basicConfig(
        filename=os.path.join(files_dir, "pybox.log"),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logging.info("PyBox backend starting. FILES_DIR=%s", files_dir)

    # Each subsystem init is wrapped separately on purpose: a failure in
    # ANY one of these (e.g. plugin_loader losing SD-card access because
    # "All files access" got reset on a reinstall) must not prevent Flask
    # itself from starting. Losing the admin panel's plugin list is a
    # much smaller problem than the entire server never binding to port
    # 5000 and every page hanging forever, which is what happened before
    # this was wrapped.
    for name, fn in [
        ("error_manager", lambda: error_manager.init(files_dir)),
        ("auth", lambda: auth.init(files_dir)),
        ("config", lambda: config.init(files_dir)),
        ("scheduler", lambda: scheduler.init(files_dir)),
        ("watcher", lambda: watcher.init(files_dir)),
    ]:
        try:
            fn()
        except Exception:
            logging.error("Subsystem '%s' failed to init:\n%s", name, traceback.format_exc())

    if plugins_dir:
        try:
            plugin_loader.init(plugins_dir, {
                "app": app,
                "plugin_routes": PLUGIN_ROUTES,
                "scheduler": scheduler,
                "watcher": watcher,
                "config": config,
                "require_auth": require_auth,
            })
        except Exception:
            logging.error("plugin_loader failed to init:\n%s", traceback.format_exc())

        # client_secrets.json lives next to the plugins folder, i.e.
        # PyBox/client_secrets.json alongside PyBox/plugins/ - see gdrive.py
        # for the one-time Google Cloud setup this requires.
        try:
            pybox_root = os.path.dirname(plugins_dir.rstrip("/"))
            client_secrets_path = os.path.join(pybox_root, "client_secrets.json")
            gdrive.init(files_dir, client_secrets_path)
        except Exception:
            logging.error("gdrive failed to init:\n%s", traceback.format_exc())

    try:
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    except Exception:
        logging.error("Backend failed to start:\n%s", traceback.format_exc())
        raise
