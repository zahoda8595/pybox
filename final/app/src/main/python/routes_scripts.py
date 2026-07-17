"""Blueprint: routes_scripts - split from the original monolithic backend_app.py."""

import json

from flask import Blueprint, jsonify, Response, request, stream_with_context

import scripts_runner
import theme
from auth import require_auth
from error_manager import safe_route

bp_scripts = Blueprint("routes_scripts", __name__)



# ---------------------------------------------------------------------
# Python script runner (scripts_runner.py) — a small in-app IDE: write,
# save, and run one-off or reusable Python scripts on-device. GET /scripts
# is unprotected (just the shell page, same reasoning as /admin); every
# save/delete/run action calls a protected /scripts/api/* route.
# ---------------------------------------------------------------------

@bp_scripts.route("/scripts")
@safe_route("scripts-page")
def scripts_page():
    return theme.render(_SCRIPTS_HTML, active="scripts")


@bp_scripts.route("/scripts/api/list", methods=["GET"])
@require_auth
@safe_route("scripts-list")
def scripts_list():
    return jsonify(scripts_runner.list_scripts())


@bp_scripts.route("/scripts/api/file", methods=["GET"])
@require_auth
@safe_route("scripts-get")
def scripts_get():
    name = request.args.get("name", "")
    code = scripts_runner.read_script(name)
    if code is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"name": name, "code": code})


@bp_scripts.route("/scripts/api/file", methods=["POST"])
@require_auth
@safe_route("scripts-save")
def scripts_save():
    body = request.get_json(force=True)
    try:
        saved = scripts_runner.write_script(body.get("name", ""), body.get("code", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"saved": saved})


@bp_scripts.route("/scripts/api/file", methods=["DELETE"])
@require_auth
@safe_route("scripts-delete")
def scripts_delete():
    body = request.get_json(force=True)
    try:
        ok = scripts_runner.delete_script(body.get("name", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"deleted": ok})


@bp_scripts.route("/scripts/api/run", methods=["POST"])
@require_auth
@safe_route("scripts-run")
def scripts_run():
    body = request.get_json(force=True)
    result = scripts_runner.run_script(
        body.get("code", ""),
        script_name=body.get("name") or "script.py",
    )
    return jsonify(result)


@bp_scripts.route("/scripts/api/run_stream", methods=["POST"])
@require_auth
@safe_route("scripts-run-stream")
def scripts_run_stream():
    """Streams output live as newline-delimited JSON (application/x-ndjson)
    instead of waiting for the script to finish - see scripts_runner
    .run_script_stream() docstring for the framing details. The frontend
    reads this with fetch()'s ReadableStream rather than EventSource, so
    it can still attach the auth header normally."""
    body = request.get_json(force=True)
    code = body.get("code", "")
    name = body.get("name") or "script.py"

    def generate():
        for kind, payload in scripts_runner.run_script_stream(code, script_name=name):
            yield json.dumps({"type": kind, "text": payload}) + "\n"

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


_SCRIPTS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox Scripts</title>
<style>
  body { margin:0; background:#0d0d0d; color:#e8e8e8; font-family:-apple-system,Roboto,sans-serif; }
  .topbar { padding:14px 16px 6px; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px; }
  h1 { font-size:19px; margin:0; }
  .toolbar { display:flex; gap:8px; flex-wrap:wrap; }
  button { background:#2e7d4f; color:#fff; border:none; border-radius:8px; padding:9px 14px; font-size:13px; }
  button.secondary { background:#333; }
  button.danger { background:#8a3030; }
  button:disabled { opacity:.5; }
  .wrap { padding:0 16px 16px; display:flex; gap:12px; flex-direction:column; }
  .layout { display:flex; gap:12px; flex-wrap:wrap; }
  .sidebar { flex:1 1 220px; min-width:200px; }
  .editor-pane { flex:3 1 380px; min-width:280px; display:flex; flex-direction:column; gap:8px; }
  .card { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:12px; padding:12px; }
  .script-item { display:flex; justify-content:space-between; align-items:center; padding:8px 6px; border-bottom:1px solid #262626; font-size:13px; cursor:pointer; }
  .script-item:last-child { border-bottom:none; }
  .script-item:hover { background:#222; }
  .script-item.active { background:#20301f; }
  .script-meta { color:#888; font-size:11px; }
  input[type=text] { width:100%; background:#111; border:1px solid #333; color:#e8e8e8; border-radius:8px; padding:9px; font-size:13px; }
  textarea#codebox {
    width:100%; min-height:340px; background:#0a0a0a; color:#d7f4d7; border:1px solid #2a2a2a;
    border-radius:8px; padding:12px; font-family:"Menlo","Consolas",monospace; font-size:13px; line-height:1.5;
    resize:vertical; white-space:pre; tab-size:4;
  }
  .output { background:#0a0a0a; border:1px solid #2a2a2a; border-radius:8px; padding:10px; font-family:monospace;
    font-size:12px; white-space:pre-wrap; word-break:break-word; max-height:260px; overflow:auto; }
  .output .stderr { color:#ff8080; }
  .output .meta-line { color:#7ec8f2; margin-bottom:6px; }
  .empty { color:#666; font-size:13px; text-align:center; padding:20px; }
  .sub { color:#888; font-size:12px; }
</style>
</head>
<body>
<div class="topbar">
  <h1>🐍 Python Scripts</h1>
  <div class="toolbar">
    <button class="secondary" id="newBtn">＋ New</button>
    <button class="secondary" id="saveBtn">💾 Save</button>
    <button id="runBtn">▶ Run</button>
    <button class="danger" id="stopBtn" disabled>■ Stop</button>
    <button class="danger" id="deleteBtn">🗑 Delete</button>
  </div>
</div>
<div class="wrap">
  <div class="sub">Scripts run on-device with full Python (same trust level as this device's other apps) — <code>import scraper</code>, <code>import connectors</code>, <code>import contacts</code>, <code>import intelligence</code>, or any other PyBox module directly, no extra setup. Default timeout comes from Settings → Admin config (scripts_timeout_seconds).</div>
  <div class="layout">
    <div class="sidebar card">
      <div class="sub" style="margin-bottom:8px">Saved scripts</div>
      <div id="scriptList"><div class="empty">No scripts yet</div></div>
    </div>
    <div class="editor-pane">
      <input type="text" id="nameBox" placeholder="script_name.py" value="script.py">
      <textarea id="codebox" spellcheck="false">print("Hello from PyBox!")</textarea>
      <div class="card">
        <div class="sub" style="margin-bottom:6px">Output</div>
        <div class="output" id="outputBox">Run a script to see output here.</div>
      </div>
    </div>
  </div>
</div>
<script>
let currentName = null;

async function loadList() {
  const r = await fetch("/scripts/api/list", { headers: authHeaders() });
  const scripts = await r.json();
  const box = document.getElementById("scriptList");
  if (!scripts.length) { box.innerHTML = '<div class="empty">No scripts yet</div>'; return; }
  box.innerHTML = scripts.map(s => `
    <div class="script-item ${s.name === currentName ? 'active' : ''}" onclick="openScript('${s.name}')">
      <span>${s.name}</span>
      <span class="script-meta">${s.modified}</span>
    </div>`).join("");
}

async function openScript(name) {
  const r = await fetch(`/scripts/api/file?name=${encodeURIComponent(name)}`, { headers: authHeaders() });
  if (!r.ok) { alert("Could not open " + name); return; }
  const d = await r.json();
  currentName = d.name;
  document.getElementById("nameBox").value = d.name;
  document.getElementById("codebox").value = d.code;
  loadList();
}

document.getElementById("newBtn").onclick = () => {
  currentName = null;
  document.getElementById("nameBox").value = "script.py";
  document.getElementById("codebox").value = '# New script\nprint("Hello from PyBox!")\n';
  document.getElementById("outputBox").textContent = "Run a script to see output here.";
  loadList();
};

document.getElementById("saveBtn").onclick = async () => {
  const name = document.getElementById("nameBox").value.trim();
  const code = document.getElementById("codebox").value;
  if (!name.endsWith(".py")) { alert("Name must end in .py"); return; }
  const r = await fetch("/scripts/api/file", { method: "POST", headers: authHeaders(), body: JSON.stringify({ name, code }) });
  const d = await r.json();
  if (d.error) { alert("Save failed: " + d.error); return; }
  currentName = d.saved;
  loadList();
};

document.getElementById("deleteBtn").onclick = async () => {
  if (!currentName) { alert("Open a saved script first"); return; }
  if (!confirm(`Delete ${currentName}?`)) return;
  await fetch("/scripts/api/file", { method: "DELETE", headers: authHeaders(), body: JSON.stringify({ name: currentName }) });
  currentName = null;
  document.getElementById("nameBox").value = "script.py";
  document.getElementById("codebox").value = "";
  loadList();
};

let activeAbort = null;

document.getElementById("runBtn").onclick = async () => {
  const name = document.getElementById("nameBox").value.trim() || "script.py";
  const code = document.getElementById("codebox").value;
  const runBtn = document.getElementById("runBtn");
  const stopBtn = document.getElementById("stopBtn");
  const outBox = document.getElementById("outputBox");
  runBtn.disabled = true;
  stopBtn.disabled = false;
  outBox.innerHTML = '<div class="meta-line">Running…</div>';

  activeAbort = new AbortController();
  let sawOutput = false;

  try {
    const r = await fetch("/scripts/api/run_stream", {
      method: "POST", headers: authHeaders(), body: JSON.stringify({ name, code }),
      signal: activeAbort.signal,
    });
    if (!r.ok || !r.body) throw new Error("stream failed (" + r.status + ")");

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl);
        buf = buf.slice(nl + 1);
        if (!line.trim()) continue;
        const evt = JSON.parse(line);
        sawOutput = true;
        appendOutput(evt);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      appendOutput({ type: "stderr", text: "\n[stopped by user]" });
    } else {
      appendOutput({ type: "stderr", text: "Request failed: " + e });
    }
  } finally {
    if (!sawOutput) outBox.innerHTML = '<div class="meta-line">(no output)</div>';
    runBtn.disabled = false;
    stopBtn.disabled = true;
    activeAbort = null;
  }
};

document.getElementById("stopBtn").onclick = () => {
  if (activeAbort) activeAbort.abort();
};

function appendOutput(evt) {
  const outBox = document.getElementById("outputBox");
  if (outBox.querySelector(".meta-line") && outBox.children.length === 1 && !outBox.dataset.started) {
    outBox.innerHTML = "";
    outBox.dataset.started = "1";
  }
  if (evt.type === "stdout") {
    outBox.appendChild(document.createTextNode(evt.text));
  } else if (evt.type === "stderr") {
    const span = document.createElement("span");
    span.className = "stderr";
    span.textContent = evt.text;
    outBox.appendChild(span);
  } else if (evt.type === "timeout") {
    const div = document.createElement("div");
    div.className = "meta-line";
    div.textContent = "⏱ " + evt.text;
    outBox.appendChild(div);
  } else if (evt.type === "done") {
    const div = document.createElement("div");
    div.className = "meta-line";
    div.textContent = evt.text ? "✖ " + evt.text : "✔ finished";
    outBox.appendChild(div);
  }
  outBox.scrollTop = outBox.scrollHeight;
}

loadList();
</script>
</body>
</html>"""
