"""Blueprint: routes_core - split from the original monolithic backend_app.py."""

import json
import urllib.request

from flask import Blueprint, Response, request

import appstate
import theme
from error_manager import safe_route

bp_core = Blueprint("routes_core", __name__)

_HOME_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox</title>
<style>
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
  <a class="tile" href="/admin#search-card">
    <div class="icon">🔍</div>
    <div class="label">Web Search</div>
    <div class="desc">Fast &amp; deep multi-engine</div>
  </a>
  <a class="tile" href="/scripts">
    <div class="icon">🐍</div>
    <div class="label">Python Scripts</div>
    <div class="desc">Write, save, run</div>
  </a>
  <a class="tile" href="/agent">
    <div class="icon">🤖</div>
    <div class="label">AI Agent</div>
    <div class="desc">Describe it, review it, run it</div>
  </a>
  <a class="tile" href="/settings">
    <div class="icon">🎨</div>
    <div class="label">Settings</div>
    <div class="desc">Theme &amp; UI, no rebuild</div>
  </a>
</div>
<div class="status">Use the ⚙️ settings icon in the app for Browser, File Explorer, Screen Time, and Backups.</div>
</body>
</html>"""


@bp_core.route("/")
@safe_route("home")
def home():
    return theme.render(_HOME_HTML, active="home")


@bp_core.route("/llm/status")
@safe_route("llm-status")
def llm_status():
    """Checks whether LlamaEngineService's process is up and responding."""
    try:
        with urllib.request.urlopen(f"{appstate.LLM_BASE_URL}/health", timeout=1.5) as r:
            return Response(r.read(), status=r.status, mimetype="application/json")
    except Exception as e:
        return Response(
            json.dumps({"running": False, "error": str(e)}),
            status=503,
            mimetype="application/json",
        )


@bp_core.route("/llm/generate", methods=["POST"])
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
        f"{appstate.LLM_BASE_URL}/completion",
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
