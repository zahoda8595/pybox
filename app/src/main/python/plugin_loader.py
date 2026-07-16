"""
plugin_loader.py — run new Python code without recompiling the APK.

WHY THIS EXISTS:
  Everything in app/src/main/python/ gets bundled INTO the APK at build
  time - changing it means a new GitHub Actions run, which takes real
  minutes. This module scans a folder on your SD card
  (/sdcard/PyBox/plugins/ by default) that is NOT part of the APK, and
  loads any .py files it finds at runtime. Drop a file there, hit
  "Reload plugins" in the admin panel (or POST /admin/plugins/reload),
  and it's running - no build, no push, no CI.

HOW TO WRITE A PLUGIN:
  Any .py file in the plugins folder that defines a top-level
  `register(ctx)` function gets it called on load/reload. `ctx` is a
  dict with:
      ctx["plugin_routes"] - dict, register HTTP routes here (see below)
      ctx["scheduler"]     - scheduler module, for JOB_HANDLERS[...] = fn
      ctx["watcher"]       - watcher module, for EVENT_HANDLERS.append(fn)
      ctx["config"]        - config.py module, for get()/set() settings
      ctx["files_dir"]     - app-private storage path, for a plugin's own
                              SQLite DB or files (same folder auth.py,
                              scheduler.py etc. use)
      ctx["require_auth"]  - the @require_auth decorator/function, call
                              it yourself inside a route if you need to
                              gate a plugin route the same way built-in
                              /automation/* routes are gated
      ctx["app"]           - the raw Flask app object. Present for
                              advantage but with a hard limit: calling
                              ctx["app"].route(...) or add_url_rule(...)
                              ONLY works the very first time plugins load
                              (at app startup, before any request has
                              been served). Flask 3.x refuses that call
                              on every later "Reload plugins" click with
                              "setup method ... can no longer be called".
                              Use ctx["plugin_routes"] instead - it works
                              on every reload, not just the first.

  Route example (reload-safe - use this, not ctx["app"].route):

      def register(ctx):
          def hello():
              return {"message": "loaded without a rebuild"}
          ctx["plugin_routes"]["hello"] = hello

  That's reachable at /plugins/hello (any file at /plugins/<name> is
  dispatched to whatever function is registered under that name).

  A plugin that adds a scheduled job:

      def register(ctx):
          def my_job(params):
              import logging
              logging.info("plugin job ran with %s", params)
          ctx["scheduler"].JOB_HANDLERS["my_plugin_job"] = my_job

SAFETY:
  Each plugin loads in its own try/except - a broken plugin logs an
  error and gets skipped, it does not take down the rest of the app.
  Plugins run with the same permissions as the rest of the app (this
  isn't a sandboxed subset of Python) - only drop in code you wrote or
  trust, same as any other Python you'd run.
"""

import importlib.util
import logging
import os
import sys
import time
import traceback

_PLUGIN_DIR = None
_CONTEXT = None
_LOADED = {}  # filename -> {"loaded_at": ..., "status": "ok"|"error", "detail": ...}


def init(plugin_dir, context):
    global _PLUGIN_DIR, _CONTEXT
    _PLUGIN_DIR = plugin_dir
    _CONTEXT = context
    os.makedirs(_PLUGIN_DIR, exist_ok=True)
    load_all()


def load_all():
    """(Re)loads every .py file in the plugins folder. Safe to call repeatedly."""
    if not _PLUGIN_DIR or not os.path.isdir(_PLUGIN_DIR):
        return

    for fname in sorted(os.listdir(_PLUGIN_DIR)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        _load_one(fname)


def _load_one(fname):
    path = os.path.join(_PLUGIN_DIR, fname)
    module_name = f"pybox_plugin_{os.path.splitext(fname)[0]}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        if hasattr(module, "register"):
            module.register(_CONTEXT)

        _LOADED[fname] = {
            "loaded_at": time.time(),
            "status": "ok",
            "detail": None,
            "has_register": hasattr(module, "register"),
        }
        logging.info("plugin loaded: %s", fname)
    except Exception:
        detail = traceback.format_exc()
        _LOADED[fname] = {
            "loaded_at": time.time(),
            "status": "error",
            "detail": detail,
            "has_register": False,
        }
        logging.error("plugin '%s' failed to load:\n%s", fname, detail)


def status():
    return _LOADED
