"""Blueprint: routes_plugins - split from the original monolithic backend_app.py."""

import os

from flask import Blueprint, jsonify, request

import appstate
import plugin_loader
from auth import require_auth
from error_manager import safe_route

bp_plugins = Blueprint("routes_plugins", __name__)



# ---------------------------------------------------------------------
# Plugin dispatch: Flask 3.x refuses app.route()/add_url_rule() calls
# once the server has handled its first request ("setup method ... can
# no longer be called"). That breaks hot-reloading plugins that try to
# register routes directly - the FIRST load (at app startup, before any
# request) works, but every later "Reload plugins" click would fail.
#
# Fix: register ONE real Flask route here, at startup, before app.run().
# Plugins never touch app.route() themselves - they register a plain
# function into appstate.PLUGIN_ROUTES by name, and this one route looks it up
# and dispatches to it on every request. Reloading a plugin just swaps
# the dict entry, which works at any time since it isn't a Flask
# setup-method call at all.
# ---------------------------------------------------------------------



@bp_plugins.route("/plugins/<path:name>", methods=["GET", "POST", "PUT", "DELETE"])
@safe_route("plugin-dispatch")
def plugin_dispatch(name):
    handler = appstate.PLUGIN_ROUTES.get(name)
    if handler is None:
        return jsonify({"error": f"no plugin route registered for '{name}'"}), 404
    return handler()


@bp_plugins.route("/admin/plugins/save", methods=["POST"])
@require_auth
@safe_route("admin-plugins-save")
def admin_plugins_save():
    """Writes a .py file straight into the plugins folder from the admin
    UI - no Termux/file-manager round trip needed to author a plugin."""
    body = request.get_json(force=True)
    name = body["name"]
    if not name.endswith(".py") or "/" in name or ".." in name:
        return jsonify({"error": "invalid filename"}), 400
    plugins_dir = plugin_loader.get_plugin_dir()
    if not plugins_dir:
        return jsonify({"error": "plugins directory not initialized"}), 500
    path = os.path.join(plugins_dir, name)
    with open(path, "w") as f:
        f.write(body["code"])
    plugin_loader.load_all()
    return jsonify({"saved": name, "plugins": plugin_loader.status()})


@bp_plugins.route("/admin/plugins/template")
@require_auth
@safe_route("admin-plugins-template")
def admin_plugins_template():
    """A working starter plugin matching the CURRENT context keys (app,
    plugin_routes, scheduler, watcher, config, require_auth) - avoids the
    exact stale-example mismatch that caused the old hello.py KeyError."""
    template = '''"""
Starter PyBox plugin. register(ctx) is called once at load/reload time.
ctx keys available: app, plugin_routes, scheduler, watcher, config, require_auth
"""

def my_route():
    return {"message": "hello from my first plugin"}


def register(ctx):
    # Registers GET /plugins/my-plugin -> my_route()
    ctx["plugin_routes"]["my-plugin"] = my_route

    # Optional: register a scheduled-job handler, then create a job for it
    # from the admin panel's Scheduled Jobs section.
    # def my_job(params):
    #     pass
    # ctx["scheduler"].JOB_HANDLERS["my_job"] = my_job
'''
    return jsonify({"template": template})


@bp_plugins.route("/admin/plugins/reload", methods=["POST"])
@require_auth
@safe_route("admin-plugins-reload")
def admin_plugins_reload():
    plugin_loader.load_all()
    return jsonify(plugin_loader.status())
