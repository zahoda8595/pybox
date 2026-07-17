"""
=====================================================================
 PyBox backend orchestrator
=====================================================================
This runs INSIDE the Android app via Chaquopy (an embedded CPython
interpreter). It used to be a single 2,752-line file with 85 routes,
seven embedded HTML page templates, and all business logic in one
place. That's been split into per-feature blueprint modules:

    routes_core.py          /  , /llm/*
    routes_automation.py     /automation/* (scheduler + watcher jobs)
    routes_contacts.py       /contacts*
    routes_usage.py          /usage/*
    routes_encryption.py     /encryption/*
    routes_connectors.py     /admin/connectors*, /admin/intelligence*
    routes_plugins.py        /plugins/<name>, /admin/plugins/*
    routes_scrape_osint.py   /scrape, /osint/*
    routes_drive.py          /drive/*
    routes_search.py         /search/fast, /search/deep*, /search/global
    routes_browser.py        /browser/*
    routes_scripts.py        /scripts*
    routes_agent.py          /agent*
    routes_settings.py       /settings*
    routes_admin.py          /admin (page + status/config/logs)

Shared mutable state that used to be bare module globals (FILES_DIR,
PLUGIN_ROUTES, the in-memory search-job registry) now lives in
appstate.py - see that file's docstring for why.

RULES (unchanged from before):
  1. Keep the Flask object named `app`. Add new feature areas as a new
     routes_<name>.py blueprint + one line in BLUEPRINTS below, rather
     than back into this file.
  2. Do NOT call app.run() yourself. start_server() at the bottom does
     that, bound to 127.0.0.1 (loopback only - never exposed off the
     phone).
  3. For files/databases (SQLite, ChromaDB, etc.), write inside
     appstate.FILES_DIR - that's the app's private, persistent storage
     on the phone and survives app restarts.
  4. If your code needs extra pip packages, add them to the
     chaquopy { pip { install(...) } } block in app/build.gradle.
     NOTE: only pure-Python or Android-prebuilt packages work here -
     Chaquopy's pip can't install native binaries. llama.cpp itself is
     NOT installed this way: it's cross-compiled separately by
     app/src/main/cpp/CMakeLists.txt into a standalone binary that
     LlamaEngineService.kt runs as a background process on
     127.0.0.1:8081 (appstate.LLM_BASE_URL). Use the /llm/* routes in
     routes_core.py to reach it from here.
  5. You can also drop extra .py files in this same folder
     (app/src/main/python/) and `import` them normally.
  6. Decorate every route with @safe_route("some-name") (imported from
     error_manager). A crash in that route then gets logged and
     isolated instead of taking the whole backend down - and if a
     route crashes 3 times in a row it auto-disables itself for a
     minute rather than crash-looping. See error_manager.py.
  7. Automation is built in: scheduler.py (periodic background jobs)
     and watcher.py (polling-based folder watching), both SQLite-backed
     in appstate.FILES_DIR/automation.db and both driven from
     /automation/* routes in routes_automation.py. Register your own
     job/event handlers by adding to scheduler.JOB_HANDLERS /
     watcher.EVENT_HANDLERS - see those files' docstrings. Every
     mutating /automation/* route requires the X-PyBox-Token header
     (auth.py) - see MainActivity.kt for how the WebView gets it
     automatically via window.PyBoxAuth.
=====================================================================
"""

import logging
import os
import traceback

from flask import Flask, jsonify

import agent
import appstate
import auth
import browser
import config
import contacts
import encryption
import error_manager
import gdrive
import global_search
import intelligence
import plugin_loader
import scheduler
import scripts_runner
import usage_stats
import watcher
from auth import require_auth

from routes_admin import bp_admin
from routes_agent import bp_agent
from routes_automation import bp_automation
from routes_browser import bp_browser
from routes_connectors import bp_connectors
from routes_contacts import bp_contacts
from routes_core import bp_core
from routes_drive import bp_drive
from routes_encryption import bp_encryption
from routes_plugins import bp_plugins
from routes_scrape_osint import bp_scrape_osint
from routes_scripts import bp_scripts
from routes_search import bp_search
from routes_settings import bp_settings
from routes_usage import bp_usage

app = Flask(__name__)

# static/app.css + static/app.js are now loaded once per WebView session
# instead of being re-sent as inline markup on every page nav - see
# theme.render(). A day is a safe default since both files only change
# on an app update (which changes the APK's asset bundle, invalidating
# any cached copy of the old ones automatically).
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

# Hard cap on request body size (25 MB) - this is a loopback-only,
# single-user backend, so this isn't defending against a remote attacker;
# it's defending against a WebView upload gone wrong (e.g. a huge file
# picked by mistake) from parking the whole process's memory and taking
# every other concurrent request down with it now that threaded=True
# means requests genuinely run in parallel.
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


@app.errorhandler(413)
def handle_too_large(e):
    return jsonify({"error": "request body too large (25MB limit)"}), 413

# Every blueprint mounts at "" - each one's own route strings (e.g.
# "/contacts/api/list") already carry their full path, same URLs as the
# old monolith, so registering them costs nothing but a name and keeps
# every existing bookmark / WebView link / test working unchanged.
BLUEPRINTS = [
    bp_core, bp_automation, bp_contacts, bp_usage, bp_encryption,
    bp_connectors, bp_plugins, bp_scrape_osint, bp_drive, bp_search,
    bp_browser, bp_scripts, bp_agent, bp_settings, bp_admin,
]
for bp in BLUEPRINTS:
    app.register_blueprint(bp)


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


def create_app(files_dir, plugins_dir=None, encryption_key_hex=None):
    """Does everything start_server() used to do EXCEPT call app.run() -
    inits every subsystem against files_dir and returns the configured
    Flask `app` object. Split out specifically so pytest can call this
    directly (against a throwaway tmp_path) and get a fully wired app to
    test with Flask's test client, without ever binding a real socket.
    start_server() below is now just this plus the blocking app.run()."""
    appstate.FILES_DIR = files_dir

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
        ("intelligence", lambda: intelligence.init(files_dir)),
        ("auth", lambda: auth.init(files_dir)),
        ("config", lambda: config.init(files_dir)),
        ("scheduler", lambda: scheduler.init(files_dir)),
        ("watcher", lambda: watcher.init(files_dir)),
        ("browser", lambda: browser.init(files_dir)),
        ("contacts", lambda: contacts.init(files_dir)),
        ("encryption", lambda: encryption.init(encryption_key_hex)),
        ("usage_stats", lambda: usage_stats.init(files_dir)),
        ("scripts_runner", lambda: scripts_runner.init(files_dir)),
        ("global_search", lambda: global_search.init(files_dir)),
        ("agent", lambda: agent.init(files_dir)),
    ]:
        try:
            fn()
        except Exception:
            logging.error("Subsystem '%s' failed to init:\n%s", name, traceback.format_exc())

    if plugins_dir:
        try:
            plugin_loader.init(plugins_dir, {
                "app": app,
                "plugin_routes": appstate.PLUGIN_ROUTES,
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

    return app


def start_server(files_dir, plugins_dir=None, encryption_key_hex=None):
    create_app(files_dir, plugins_dir, encryption_key_hex)
    try:
        # threaded=True: concurrent requests now actually hit SQLite
        # concurrently instead of one route freezing every other page.
        # Safe because every module goes through dbcore's pooled, WAL-mode
        # connections (see dbcore.py) rather than raw sqlite3.connect().
        app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False,
                threaded=True)
    except Exception:
        logging.error("Backend failed to start:\n%s", traceback.format_exc())
        raise
