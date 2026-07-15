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
import threading
import time
import traceback
import urllib.request
import uuid

from flask import Flask, Response, jsonify, request

import auth
import browser
import config
import contacts
import encryption
import error_manager
import gdrive
import osint_tools
import plugin_loader
import scheduler
import scraper
import search_engine
import usage_stats
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

_HOME_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, sans-serif; background:#0d0d0d; color:#e8e8e8; margin:0; padding:18px; }
  h1 { font-size:20px; margin:6px 0 4px; }
  .sub { color:#888; font-size:12.5px; margin-bottom:18px; }
  .grid { display:grid; grid-template-columns:repeat(2, 1fr); gap:12px; }
  a.tile { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:12px; padding:16px 12px; text-decoration:none; color:#eee; display:flex; flex-direction:column; gap:6px; }
  a.tile .icon { font-size:26px; }
  a.tile .label { font-size:13.5px; font-weight:600; }
  a.tile .desc { font-size:11px; color:#999; }
  .status { margin-top:18px; font-size:11.5px; color:#666; text-align:center; }
</style>
</head>
<body>
<h1>PyBox</h1>
<div class="sub">Running locally on your phone.</div>
<div class="grid">
  <a class="tile" href="/contacts">
    <div class="icon">👤</div>
    <div class="label">Contacts</div>
    <div class="desc">Folders, links, dedup</div>
  </a>
  <a class="tile" href="/admin">
    <div class="icon">🖥️</div>
    <div class="label">Command Center</div>
    <div class="desc">Jobs, watchers, plugins</div>
  </a>
</div>
<div class="status">Use the ⚙️ settings icon in the app for Browser, File Explorer, Screen Time, and Backups.</div>
</body>
</html>"""


@app.route("/")
@safe_route("home")
def home():
    return _HOME_HTML


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

# Contacts automation: point a watcher (POST /automation/watchers) at your
# vCard/CSV import folder to auto-ingest new drops, and create scheduled
# jobs (POST /automation/jobs) using these handler names to run dedup or
# link-refresh on a cadence — no new endpoints needed, reuses the
# scheduler/watcher infra already in this app.
watcher.EVENT_HANDLERS.append(contacts.watch_handler)
scheduler.JOB_HANDLERS["contacts_dedup"] = contacts.job_dedup
scheduler.JOB_HANDLERS["contacts_refresh_links"] = contacts.job_refresh_links


def _encrypted_backup_job(params):
    """Register a job with handler='encrypted_backup', params={'db_name': 'contacts.db'}
    to run periodic encrypted snapshots of a local DB via WorkManager/scheduler."""
    db_name = (params or {}).get("db_name", "contacts.db")
    result = encryption.encrypted_backup(
        os.path.join(FILES_DIR, db_name),
        os.path.join(FILES_DIR, "backups"),
    )
    logging.info("encrypted_backup job: %s", result)


scheduler.JOB_HANDLERS["encrypted_backup"] = _encrypted_backup_job


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
# Contacts (contacts.py) — one folder per contact, built from vCard/CSV
# imports and links you paste in yourself. See that file's docstring for
# what's in scope and what's deliberately not.
# ---------------------------------------------------------------------

@app.route("/contacts")
@safe_route("contacts-page")
def contacts_page():
    return _CONTACTS_HTML


@app.route("/contacts/api/list")
@require_auth
@safe_route("contacts-list")
def contacts_list():
    return jsonify(contacts.list_contacts(search=request.args.get("q")))


@app.route("/contacts/api/<contact_id>")
@require_auth
@safe_route("contacts-get")
def contacts_get(contact_id):
    c = contacts.get_contact(contact_id)
    if not c:
        return jsonify({"error": "not found"}), 404
    return jsonify(c)


@app.route("/contacts/api", methods=["POST"])
@require_auth
@safe_route("contacts-create")
def contacts_create():
    body = request.get_json(force=True)
    contact_id = contacts.create_contact(
        name=body.get("name"), phone=body.get("phone"),
        email=body.get("email"), notes=body.get("notes"),
    )
    return jsonify(contacts.get_contact(contact_id))


@app.route("/contacts/api/<contact_id>", methods=["POST"])
@require_auth
@safe_route("contacts-update")
def contacts_update(contact_id):
    body = request.get_json(force=True)
    updated = contacts.update_contact(contact_id, **body)
    if not updated:
        return jsonify({"error": "not found"}), 404
    return jsonify(updated)


@app.route("/contacts/api/<contact_id>", methods=["DELETE"])
@require_auth
@safe_route("contacts-delete")
def contacts_delete(contact_id):
    contacts.delete_contact(contact_id)
    return jsonify({"deleted": contact_id})


@app.route("/contacts/api/<contact_id>/photo", methods=["POST"])
@require_auth
@safe_route("contacts-set-photo")
def contacts_set_photo(contact_id):
    """Body: {"source_path": "/storage/emulated/0/Pictures/whatever.jpg"} —
    copies a LOCAL file already on the phone. No network fetch."""
    body = request.get_json(force=True)
    result = contacts.set_photo_from_path(contact_id, body["source_path"])
    return jsonify(result)


@app.route("/contacts/api/<contact_id>/photo/file")
@require_auth
@safe_route("contacts-get-photo")
def contacts_get_photo(contact_id):
    path = os.path.join(FILES_DIR, "contacts", contact_id, "profile.jpg")
    if not os.path.exists(path):
        return jsonify({"error": "no photo set"}), 404
    with open(path, "rb") as f:
        return Response(f.read(), mimetype="image/jpeg")


@app.route("/contacts/api/<contact_id>/links", methods=["POST"])
@require_auth
@safe_route("contacts-add-link")
def contacts_add_link(contact_id):
    """Body: {"url": "https://..."} — YOU supply the link; this fetches
    just that one page to pull title/photo/description and files it
    under the contact. Never searches for links on its own."""
    body = request.get_json(force=True)
    result = contacts.add_link(contact_id, body["url"])
    if isinstance(result, dict) and result.get("error"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/contacts/api/links/<link_id>", methods=["DELETE"])
@require_auth
@safe_route("contacts-remove-link")
def contacts_remove_link(link_id):
    return jsonify(contacts.remove_link(link_id))


@app.route("/contacts/api/links/<link_id>/refresh", methods=["POST"])
@require_auth
@safe_route("contacts-refresh-link")
def contacts_refresh_link(link_id):
    return jsonify(contacts.refresh_link(link_id))


@app.route("/contacts/api/import/vcard", methods=["POST"])
@require_auth
@safe_route("contacts-import-vcard")
def contacts_import_vcard():
    """Body: {"path": "/storage/emulated/0/PyBox/import/contacts.vcf"} —
    a vCard file already on the phone (e.g. exported from your own
    contacts app)."""
    body = request.get_json(force=True)
    return jsonify(contacts.import_vcard(body["path"]))


@app.route("/contacts/api/import/csv", methods=["POST"])
@require_auth
@safe_route("contacts-import-csv")
def contacts_import_csv():
    body = request.get_json(force=True)
    return jsonify(contacts.import_csv(body["path"]))


@app.route("/contacts/api/dedup", methods=["POST"])
@require_auth
@safe_route("contacts-dedup")
def contacts_dedup():
    return jsonify(contacts.dedup_contacts())


# ---------------------------------------------------------------------
# Screen time (usage_stats.py) — aggregate per-app foreground time
# reported from Android's UsageStatsManager (UsageStatsHelper.kt). No
# on-screen content, just durations - same category as Digital Wellbeing.
# ---------------------------------------------------------------------

@app.route("/usage/report", methods=["POST"])
@require_auth
@safe_route("usage-report")
def usage_report():
    body = request.get_json(force=True)
    return jsonify(usage_stats.record_batch(body["entries"]))


@app.route("/usage/summary")
@require_auth
@safe_route("usage-summary")
def usage_summary():
    days = int(request.args.get("days", 7))
    return jsonify(usage_stats.summary(days=days))


@app.route("/usage/daily")
@require_auth
@safe_route("usage-daily")
def usage_daily():
    day = request.args.get("day")
    return jsonify(usage_stats.daily(day))


# ---------------------------------------------------------------------
# Encryption (encryption.py) — AES-256-GCM backups of local DBs, keyed by
# SecureKeyManager.kt's Keystore-wrapped key. Encrypts data at rest on
# this device only; nothing here transmits anything anywhere.
# ---------------------------------------------------------------------

@app.route("/encryption/status")
@require_auth
@safe_route("encryption-status")
def encryption_status():
    return jsonify({"available": encryption.available()})


@app.route("/encryption/backup", methods=["POST"])
@require_auth
@safe_route("encryption-backup")
def encryption_backup():
    body = request.get_json(force=True)
    db_name = body.get("db_name", "contacts.db")
    result = encryption.encrypted_backup(
        os.path.join(FILES_DIR, db_name), os.path.join(FILES_DIR, "backups")
    )
    return jsonify(result)


@app.route("/encryption/backups")
@require_auth
@safe_route("encryption-list-backups")
def encryption_list_backups():
    backups_dir = os.path.join(FILES_DIR, "backups")
    if not os.path.isdir(backups_dir):
        return jsonify({"backups": []})
    files = sorted(os.listdir(backups_dir), reverse=True)
    return jsonify({"backups": files})


@app.route("/encryption/restore", methods=["POST"])
@require_auth
@safe_route("encryption-restore")
def encryption_restore():
    """Body: {"backup_name": "contacts.db.1234567.enc", "dest_name": "contacts_restored.db"}
    Decrypts a backup back into FILES_DIR under a NEW name - never
    silently overwrites the live DB."""
    body = request.get_json(force=True)
    src = os.path.join(FILES_DIR, "backups", body["backup_name"])
    dest = os.path.join(FILES_DIR, body.get("dest_name", "restored.db"))
    if not os.path.exists(src):
        return jsonify({"error": "no such backup"}), 404
    encryption.decrypt_file(src, dest)
    return jsonify({"restored_to": dest})


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
# Web search (search_engine.py) - multi-engine, two modes:
#   fast -> search_fast(), synchronous, returns in ~seconds.
#   deep -> search_deep() actually fetches + reads top result pages and
#           optionally synthesizes via the local LLM, which can take
#           10s-60s+. Run as a background job so the caller polls
#           instead of holding a request open that long. Jobs live only
#           in memory (FILES_DIR/automation.db is for scheduler.py's
#           recurring jobs, not one-off searches) - a restart clears
#           any in-flight search, which is fine since it's not
#           persistent automation.
# ---------------------------------------------------------------------

_SEARCH_JOBS = {}
_SEARCH_JOBS_LOCK = threading.Lock()


def _run_deep_search_job(job_id, query, max_results, synthesize):
    with _SEARCH_JOBS_LOCK:
        _SEARCH_JOBS[job_id]["status"] = "running"
    try:
        result = search_engine.search_deep(query, max_results=max_results, synthesize=synthesize)
        with _SEARCH_JOBS_LOCK:
            _SEARCH_JOBS[job_id]["status"] = "done"
            _SEARCH_JOBS[job_id]["result"] = result
    except Exception as e:
        error_manager.log_error("search-deep-job", e)
        with _SEARCH_JOBS_LOCK:
            _SEARCH_JOBS[job_id]["status"] = "error"
            _SEARCH_JOBS[job_id]["error"] = str(e)


@app.route("/search/fast", methods=["POST"])
@require_auth
@safe_route("search-fast")
def search_fast_route():
    body = request.get_json(force=True)
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    max_results = int(body.get("max_results", 10))
    return jsonify(search_engine.search_fast(query, max_results=max_results))


@app.route("/search/deep", methods=["POST"])
@require_auth
@safe_route("search-deep-start")
def search_deep_start():
    """Kicks off a deep search job and returns immediately with a job_id.
    Poll GET /search/deep/<job_id> for progress/result."""
    body = request.get_json(force=True)
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    max_results = int(body.get("max_results", 6))
    synthesize = bool(body.get("synthesize", True))

    job_id = uuid.uuid4().hex[:12]
    with _SEARCH_JOBS_LOCK:
        _SEARCH_JOBS[job_id] = {"status": "queued", "query": query, "created_at": time.time()}
    threading.Thread(
        target=_run_deep_search_job, args=(job_id, query, max_results, synthesize), daemon=True
    ).start()
    return jsonify({"job_id": job_id, "status": "queued"})


@app.route("/search/deep/<job_id>", methods=["GET"])
@require_auth
@safe_route("search-deep-poll")
def search_deep_poll(job_id):
    with _SEARCH_JOBS_LOCK:
        job = _SEARCH_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job_id"}), 404
    return jsonify(job)


# ---------------------------------------------------------------------
# In-app browser (browser.py + BrowserActivity.kt) - extraction runs on
# ALREADY-RENDERED HTML sent from the WebView after JS has executed,
# which is what makes this different from /scrape (raw HTTP fetch only).
# ---------------------------------------------------------------------

@app.route("/browser/extract", methods=["POST"])
@require_auth
@safe_route("browser-extract")
def browser_extract():
    body = request.get_json(force=True)
    return jsonify(browser.extract(body["url"], body["html"]))


@app.route("/browser/rules", methods=["GET"])
@require_auth
@safe_route("browser-rules-get")
def browser_rules_get():
    domain = request.args.get("domain", "")
    return jsonify(browser.get_rules(domain))


@app.route("/browser/rules", methods=["POST"])
@require_auth
@safe_route("browser-rules-set")
def browser_rules_set():
    body = request.get_json(force=True)
    browser.set_rule(body["domain"], body["field_name"], body["css_selector"])
    return jsonify(browser.get_rules(body["domain"]))


@app.route("/browser/rules", methods=["DELETE"])
@require_auth
@safe_route("browser-rules-delete")
def browser_rules_delete():
    body = request.get_json(force=True)
    browser.delete_rule(body["domain"], body["field_name"])
    return jsonify(browser.get_rules(body["domain"]))


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
  .result-card { background:#141414; border:1px solid #262626; border-radius:8px; padding:10px; margin-bottom:8px; }
  .result-card a { color:#7ec8f2; text-decoration:none; font-size:13px; font-weight:600; word-break:break-word; }
  .result-card .snippet { color:#bbb; font-size:12px; margin-top:4px; }
  .result-card .meta { color:#666; font-size:10.5px; margin-top:4px; }
  .synthesis { background:#12241a; border:1px solid #234; border-radius:8px; padding:10px; margin-bottom:10px; font-size:12.5px; color:#c9e8c9; white-space:pre-wrap; }
</style>
</head>
<body>
<h1>PyBox Admin</h1>

<div class="card">
  <div class="card-header"><h2>Web Search</h2><span class="count" id="search-elapsed"></span></div>
  <p class="hint">Fast = multi-engine snippets, back in seconds. Deep = also reads the top pages and asks the local LLM (if running) for a source-grounded answer - slower on purpose.</p>
  <div class="toolbar">
    <input id="search-query" placeholder="Search the web..." style="flex:1" onkeydown="if(event.key==='Enter')runSearch()">
    <select id="search-mode" style="max-width:110px"><option value="fast">Fast</option><option value="deep">Deep</option></select>
    <button onclick="runSearch()">Go</button>
  </div>
  <div id="search-status" class="hint"></div>
  <div id="search-results"></div>
</div>

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

let searchPollTimer = null;

async function runSearch() {
  const query = document.getElementById("search-query").value.trim();
  if (!query) return;
  const mode = document.getElementById("search-mode").value;
  if (searchPollTimer) { clearInterval(searchPollTimer); searchPollTimer = null; }
  document.getElementById("search-results").innerHTML = "";
  document.getElementById("search-elapsed").textContent = "";

  if (mode === "fast") {
    setSearchStatus("Searching (fast)...");
    const r = await fetch("/search/fast", { method: "POST", headers: authHeaders(), body: JSON.stringify({ query }) });
    const d = await r.json();
    renderSearch(d);
  } else {
    setSearchStatus("Starting deep search - fetching pages, this can take a bit...");
    const r = await fetch("/search/deep", { method: "POST", headers: authHeaders(), body: JSON.stringify({ query }) });
    const d = await r.json();
    if (d.error) { setSearchStatus("Error: " + d.error); return; }
    searchPollTimer = setInterval(() => pollDeepSearch(d.job_id), 1500);
  }
}

async function pollDeepSearch(jobId) {
  const r = await fetch(`/search/deep/${jobId}`, { headers: authHeaders() });
  const job = await r.json();
  if (job.status === "queued" || job.status === "running") {
    setSearchStatus(`Deep search ${job.status}...`);
    return;
  }
  clearInterval(searchPollTimer);
  searchPollTimer = null;
  if (job.status === "error") { setSearchStatus("Error: " + job.error); return; }
  renderSearch(job.result);
}

function setSearchStatus(text) {
  document.getElementById("search-status").textContent = text;
}

function renderSearch(d) {
  setSearchStatus(`${d.mode} search - engines: ${(d.engines_used||[]).join(", ")}`);
  document.getElementById("search-elapsed").textContent = d.elapsed_seconds ? d.elapsed_seconds + "s" : "";
  let html = "";

  if (d.mode === "deep") {
    if (d.synthesis) {
      html += `<div class="synthesis"><strong>Answer (from fetched sources):</strong>\\n${escapeHtml(d.synthesis)}</div>`;
    } else if (!d.llm_available) {
      html += `<p class="hint">LLM engine isn't running, so no synthesized answer - showing fetched source text below. (Settings -> Start LLM Engine to enable synthesis.)</p>`;
    }
    (d.sources || []).forEach(s => { html += renderResultCard(s, true); });
  } else {
    (d.results || []).forEach(s => { html += renderResultCard(s, false); });
  }

  document.getElementById("search-results").innerHTML = html || '<p class="hint">No results.</p>';
}

function renderResultCard(s, isDeep) {
  const meta = isDeep
    ? (s.fetched ? `read ${s.full_text ? s.full_text.length : 0} chars - relevance ${s.relevance_score||0}` : `not fetched: ${s.error||'unknown error'}`)
    : `seen on: ${(s.seen_on||[]).join(", ")} - score ${s.score ? s.score.toFixed(2) : ""}`;
  let html = `<div class="result-card">` +
    `<a href="${s.url}" target="_blank" rel="noopener">${escapeHtml(s.title||s.url)}</a>` +
    `<div class="snippet">${escapeHtml(s.snippet||"")}</div>` +
    `<div class="meta">${escapeHtml(s.url)} - ${meta}</div>`;
  if (isDeep && s.fetched && s.full_text) {
    html += `<details><summary>full extracted text</summary><pre>${escapeHtml(s.full_text)}</pre></details>`;
  }
  html += `</div>`;
  return html;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

loadStatus();
loadLogs(30);
setInterval(loadStatus, 10000);
</script>
</body>
</html>"""

_CONTACTS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox Contacts</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, sans-serif; background:#0d0d0d; color:#e8e8e8; margin:0; padding:14px; line-height:1.4; }
  h1 { font-size:19px; margin:0 0 14px; display:flex; align-items:center; justify-content:space-between; }
  .toolbar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
  input,select,textarea { background:#141414; color:#eee; border:1px solid #333; border-radius:6px; padding:7px 9px; font-size:13px; }
  button { background:#2e7d4f; color:#fff; border:none; border-radius:6px; padding:7px 12px; font-size:13px; cursor:pointer; }
  button.danger { background:#8a3030; }
  button.secondary { background:#333; }
  #search { flex:1; min-width:140px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px,1fr)); gap:10px; }
  .card { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:10px; padding:10px; cursor:pointer; text-align:center; }
  .card img, .avatar { width:56px; height:56px; border-radius:50%; object-fit:cover; background:#333; margin:0 auto 6px; display:block; }
  .avatar { display:flex; align-items:center; justify-content:center; font-size:20px; color:#888; }
  .card .name { font-size:12.5px; font-weight:600; word-break:break-word; }
  .card .sub { font-size:10.5px; color:#888; margin-top:2px; }
  .badge { display:inline-block; background:#263238; color:#8bd; font-size:10px; padding:1px 6px; border-radius:8px; margin-top:4px; }
  .modal-bg { position:fixed; inset:0; background:rgba(0,0,0,.6); display:none; align-items:flex-start; justify-content:center; padding:16px; overflow-y:auto; z-index:10; }
  .modal { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:12px; padding:16px; max-width:480px; width:100%; margin-top:20px; }
  .modal h2 { font-size:15px; margin:0 0 10px; }
  .field { margin-bottom:8px; }
  .field label { display:block; font-size:11px; color:#999; margin-bottom:3px; text-transform:uppercase; letter-spacing:.03em; }
  .field input, .field textarea { width:100%; }
  .link-row { background:#141414; border:1px solid #262626; border-radius:8px; padding:8px; margin-bottom:8px; display:flex; gap:8px; align-items:flex-start; }
  .link-row img { width:36px; height:36px; border-radius:6px; object-fit:cover; background:#333; flex:none; }
  .link-row .info { flex:1; min-width:0; }
  .link-row a { color:#7ec8f2; text-decoration:none; font-size:12.5px; font-weight:600; word-break:break-word; }
  .link-row .platform { font-size:10px; color:#8bd; }
  .link-row .desc { font-size:11px; color:#999; margin-top:2px; }
  .link-row .actions { display:flex; flex-direction:column; gap:4px; }
  .link-row button { font-size:10.5px; padding:4px 7px; }
  .close-x { float:right; background:none; color:#999; padding:2px 8px; }
  .hint { color:#888; font-size:11.5px; margin:0 0 10px; }
  .empty { color:#666; font-style:italic; grid-column:1/-1; text-align:center; padding:30px 0; }
</style>
</head>
<body>
<h1>Contacts <button onclick="openNew()">+ New</button></h1>
<div class="toolbar">
  <input id="search" placeholder="Search name / phone / email..." oninput="loadList()">
  <button class="secondary" onclick="runDedup()">Dedup</button>
</div>
<p class="hint">Import a vCard/CSV or paste social links per contact from a contact's detail view. Each contact lives in its own folder on the device.</p>
<div class="grid" id="grid"></div>

<div class="modal-bg" id="detail-modal">
  <div class="modal" id="detail-content"></div>
</div>

<div class="modal-bg" id="new-modal">
  <div class="modal">
    <button class="close-x" onclick="closeNew()">x</button>
    <h2>New Contact</h2>
    <div class="field"><label>Name</label><input id="new-name"></div>
    <div class="field"><label>Phone</label><input id="new-phone"></div>
    <div class="field"><label>Email</label><input id="new-email"></div>
    <div class="field"><label>Notes</label><textarea id="new-notes" rows="2"></textarea></div>
    <button onclick="createContact()">Create</button>
  </div>
</div>

<script>
function authHeaders(json) {
  const token = (window.PyBoxAuth && window.PyBoxAuth.getToken) ? window.PyBoxAuth.getToken() : "";
  const h = { "X-PyBox-Token": token };
  if (json) h["Content-Type"] = "application/json";
  return h;
}
function escapeHtml(str) {
  return String(str||"").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));
}

async function loadList() {
  const q = document.getElementById("search").value.trim();
  const r = await fetch("/contacts/api/list" + (q ? "?q=" + encodeURIComponent(q) : ""), { headers: authHeaders() });
  const list = await r.json();
  const grid = document.getElementById("grid");
  if (!list.length) { grid.innerHTML = '<div class="empty">No contacts yet. Import a vCard/CSV or add one.</div>'; return; }
  grid.innerHTML = list.map(c => `
    <div class="card" onclick="openDetail('${c.id}')">
      ${c.has_photo ? `<img src="/contacts/api/${c.id}/photo/file">` : `<div class="avatar">${(c.name||'?')[0].toUpperCase()}</div>`}
      <div class="name">${escapeHtml(c.name || '(no name)')}</div>
      <div class="sub">${escapeHtml(c.phone || c.email || '')}</div>
      <div class="badge">${c.link_count} link${c.link_count===1?'':'s'}</div>
    </div>
  `).join("");
}

async function runDedup() {
  const r = await fetch("/contacts/api/dedup", { method: "POST", headers: authHeaders() });
  const d = await r.json();
  alert(`Merged ${d.merged_groups} duplicate group(s).`);
  loadList();
}

function openNew() { document.getElementById("new-modal").style.display = "flex"; }
function closeNew() { document.getElementById("new-modal").style.display = "none"; }

async function createContact() {
  const body = {
    name: document.getElementById("new-name").value.trim(),
    phone: document.getElementById("new-phone").value.trim(),
    email: document.getElementById("new-email").value.trim(),
    notes: document.getElementById("new-notes").value.trim(),
  };
  await fetch("/contacts/api", { method: "POST", headers: authHeaders(true), body: JSON.stringify(body) });
  closeNew();
  ["new-name","new-phone","new-email","new-notes"].forEach(id => document.getElementById(id).value = "");
  loadList();
}

async function openDetail(id) {
  const r = await fetch(`/contacts/api/${id}`, { headers: authHeaders() });
  const c = await r.json();
  const links = (c.links||[]).map(l => `
    <div class="link-row">
      ${l.image_url ? `<img src="${escapeHtml(l.image_url)}">` : `<img>`}
      <div class="info">
        <div class="platform">${escapeHtml(l.platform||'')}</div>
        <a href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.title || l.url)}</a>
        <div class="desc">${escapeHtml((l.description||'').slice(0,120))}</div>
      </div>
      <div class="actions">
        <button class="secondary" onclick="refreshLink('${l.id}','${id}')">Refresh</button>
        <button class="danger" onclick="removeLink('${l.id}','${id}')">Remove</button>
      </div>
    </div>
  `).join("") || '<p class="hint">No links yet — paste one below.</p>';

  document.getElementById("detail-content").innerHTML = `
    <button class="close-x" onclick="closeDetail()">x</button>
    <h2>${escapeHtml(c.name || '(no name)')}</h2>
    <div class="field"><label>Name</label><input id="d-name" value="${escapeHtml(c.name||'')}"></div>
    <div class="field"><label>Phone</label><input id="d-phone" value="${escapeHtml(c.phone||'')}"></div>
    <div class="field"><label>Email</label><input id="d-email" value="${escapeHtml(c.email||'')}"></div>
    <div class="field"><label>Notes</label><textarea id="d-notes" rows="2">${escapeHtml(c.notes||'')}</textarea></div>
    <button onclick="saveDetail('${id}')">Save</button>
    <button class="danger" onclick="deleteContact('${id}')">Delete</button>
    <h2 style="margin-top:16px">Linked profiles</h2>
    ${links}
    <div class="field"><label>Add a link (you supply the URL)</label>
      <input id="new-link-url" placeholder="https://...">
      <button style="margin-top:6px" onclick="addLink('${id}')">Add & extract</button>
    </div>
  `;
  document.getElementById("detail-modal").style.display = "flex";
}
function closeDetail() { document.getElementById("detail-modal").style.display = "none"; loadList(); }

async function saveDetail(id) {
  const body = {
    name: document.getElementById("d-name").value.trim(),
    phone: document.getElementById("d-phone").value.trim(),
    email: document.getElementById("d-email").value.trim(),
    notes: document.getElementById("d-notes").value.trim(),
  };
  await fetch(`/contacts/api/${id}`, { method: "POST", headers: authHeaders(true), body: JSON.stringify(body) });
  openDetail(id);
}

async function deleteContact(id) {
  if (!confirm("Delete this contact and its folder?")) return;
  await fetch(`/contacts/api/${id}`, { method: "DELETE", headers: authHeaders() });
  closeDetail();
}

async function addLink(id) {
  const url = document.getElementById("new-link-url").value.trim();
  if (!url) return;
  await fetch(`/contacts/api/${id}/links`, { method: "POST", headers: authHeaders(true), body: JSON.stringify({ url }) });
  openDetail(id);
}

async function refreshLink(linkId, contactId) {
  await fetch(`/contacts/api/links/${linkId}/refresh`, { method: "POST", headers: authHeaders() });
  openDetail(contactId);
}

async function removeLink(linkId, contactId) {
  await fetch(`/contacts/api/links/${linkId}`, { method: "DELETE", headers: authHeaders() });
  openDetail(contactId);
}

loadList();
</script>
</body>
</html>"""

# =====================================================================
# >>> PASTE YOUR FLASK ROUTES / LOGIC ABOVE THIS LINE <<<
# =====================================================================


def start_server(files_dir, plugins_dir=None, encryption_key_hex=None):
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
        ("browser", lambda: browser.init(files_dir)),
        ("contacts", lambda: contacts.init(files_dir)),
        ("encryption", lambda: encryption.init(encryption_key_hex)),
        ("usage_stats", lambda: usage_stats.init(files_dir)),
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
                "files_dir": files_dir,
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
