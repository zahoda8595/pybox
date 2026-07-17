"""Blueprint: routes_agent - split from the original monolithic backend_app.py."""


from flask import Blueprint, jsonify, request

import agent
import connectors
import theme
from auth import require_auth
from error_manager import safe_route

bp_agent = Blueprint("routes_agent", __name__)



# ---------------------------------------------------------------------
# AI Agent (agent.py) — describe a task, an LLM (local engine or a cloud
# connector) writes a script, you review what it would do, THEN it runs.
# /agent/api/plan only generates + analyzes — nothing executes until a
# separate, explicit /agent/api/execute call against that exact plan_id.
# ---------------------------------------------------------------------

@bp_agent.route("/agent")
@safe_route("agent-page")
def agent_page():
    return theme.render(_AGENT_HTML, active="agent")


@bp_agent.route("/agent/api/plan", methods=["POST"])
@require_auth
@safe_route("agent-plan")
def agent_plan():
    body = request.get_json(force=True)
    try:
        plan = agent.create_plan(
            body.get("task", ""),
            backend=body.get("backend", "local"),
            connector_name=body.get("connector_name"),
            adapter=body.get("adapter", "anthropic"),
        )
    except agent.AgentError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(plan)


@bp_agent.route("/agent/api/execute", methods=["POST"])
@require_auth
@safe_route("agent-execute")
def agent_execute():
    body = request.get_json(force=True)
    plan_id = body.get("plan_id", "")
    try:
        result = agent.execute_plan(plan_id, ack_high_risk=bool(body.get("ack_high_risk")))
    except agent.AgentError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp_agent.route("/agent/api/schedule", methods=["POST"])
@require_auth
@safe_route("agent-schedule")
def agent_schedule():
    body = request.get_json(force=True)
    plan_id = body.get("plan_id", "")
    interval_seconds = body.get("interval_seconds")
    try:
        interval_seconds = int(interval_seconds)
    except (TypeError, ValueError):
        return jsonify({"error": "interval_seconds must be an integer number of seconds"}), 400
    try:
        result = agent.schedule_plan(plan_id, interval_seconds, ack_high_risk=bool(body.get("ack_high_risk")))
    except agent.AgentError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp_agent.route("/agent/api/reject", methods=["POST"])
@require_auth
@safe_route("agent-reject")
def agent_reject():
    body = request.get_json(force=True)
    try:
        result = agent.reject_plan(body.get("plan_id", ""))
    except agent.AgentError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(result)


@bp_agent.route("/agent/api/history", methods=["GET"])
@require_auth
@safe_route("agent-history")
def agent_history():
    return jsonify(agent.list_history())


@bp_agent.route("/agent/api/connectors", methods=["GET"])
@require_auth
@safe_route("agent-connectors")
def agent_connectors():
    """Lets the /agent page populate a connector dropdown for backend='cloud'
    without duplicating connectors.list_connectors()'s secret-redaction."""
    return jsonify(connectors.list_connectors())


_AGENT_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox Agent</title>
<style>
  body { margin:0; background:#0d0d0d; color:#e8e8e8; font-family:-apple-system,Roboto,sans-serif; }
  .topbar { padding:14px 16px 6px; }
  h1 { font-size:19px; margin:0 0 4px; }
  .sub { color:#888; font-size:12px; }
  .wrap { padding:0 16px 16px; display:flex; flex-direction:column; gap:12px; }
  .card { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:12px; padding:12px; }
  textarea#taskBox { width:100%; min-height:70px; background:#111; border:1px solid #333; color:#e8e8e8;
    border-radius:8px; padding:10px; font-size:14px; resize:vertical; }
  select, input[type=text] { width:100%; background:#111; border:1px solid #333; color:#e8e8e8;
    border-radius:8px; padding:9px; font-size:13px; }
  .row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .row > * { flex:1 1 140px; }
  button { background:#2e7d4f; color:#fff; border:none; border-radius:8px; padding:10px 14px; font-size:13px; }
  button.secondary { background:#333; }
  button.danger { background:#8a3030; }
  button:disabled { opacity:.5; }
  pre#codePreview { background:#0a0a0a; border:1px solid #2a2a2a; border-radius:8px; padding:12px;
    font-family:"Menlo","Consolas",monospace; font-size:12.5px; line-height:1.5; white-space:pre-wrap;
    word-break:break-word; max-height:320px; overflow:auto; }
  .flags { display:flex; flex-direction:column; gap:6px; }
  .flag { border-radius:8px; padding:8px 10px; font-size:12.5px; }
  .flag.low { background:#16261a; border:1px solid #24402c; color:#a8d8b6; }
  .flag.medium { background:#2a2312; border:1px solid #4a3c18; color:#e8c878; }
  .flag.high { background:#2a1414; border:1px solid #4a1c1c; color:#f0a0a0; }
  .risk-badge { display:inline-block; border-radius:999px; padding:3px 10px; font-size:11px; font-weight:600; }
  .risk-badge.low { background:#1e3a26; color:#8fe0a8; }
  .risk-badge.medium { background:#3a3016; color:#e8c878; }
  .risk-badge.high { background:#3a1a1a; color:#f0a0a0; }
  .output { background:#0a0a0a; border:1px solid #2a2a2a; border-radius:8px; padding:10px; font-family:monospace;
    font-size:12px; white-space:pre-wrap; word-break:break-word; max-height:220px; overflow:auto; }
  .output .stderr { color:#ff8080; }
  .empty { color:#666; font-size:13px; text-align:center; padding:14px; }
  .hist-item { display:flex; justify-content:space-between; gap:8px; padding:8px 6px; border-bottom:1px solid #262626; font-size:12.5px; }
  .hist-item:last-child { border-bottom:none; }
  .hist-meta { color:#888; font-size:11px; }
  label.ack { display:flex; align-items:center; gap:8px; font-size:12.5px; color:#f0a0a0; }
  .hidden { display:none; }
</style>
</head>
<body>
<div class="topbar">
  <h1>🤖 AI Agent</h1>
  <div class="sub">Describe a task. It writes Python, you review exactly what it would do, then you decide whether it runs. Nothing executes until you tap Run.</div>
</div>
<div class="wrap">
  <div class="card">
    <div class="row" style="margin-bottom:8px">
      <select id="backendSelect">
        <option value="local">Local LLM engine</option>
        <option value="cloud">Cloud connector</option>
      </select>
      <select id="connectorSelect" class="hidden"></select>
      <select id="adapterSelect" class="hidden">
        <option value="anthropic">Anthropic (Messages API)</option>
        <option value="openai">OpenAI-compatible (Chat Completions)</option>
      </select>
    </div>
    <textarea id="taskBox" placeholder="e.g. Read every .txt file in scripts/ and print a word count for each one"></textarea>
    <div class="row" style="margin-top:8px">
      <button id="planBtn">✨ Generate plan</button>
    </div>
  </div>

  <div class="card hidden" id="planCard">
    <div class="row" style="justify-content:space-between; margin-bottom:8px">
      <div><span class="sub">Risk level:</span> <span class="risk-badge" id="riskBadge"></span></div>
    </div>
    <div class="flags" id="flagsBox"></div>
    <div class="sub" style="margin:10px 0 4px">Generated code (nothing has run yet)</div>
    <pre id="codePreview"></pre>
    <label class="ack hidden" id="ackRow">
      <input type="checkbox" id="ackCheck"> I've reviewed the high-risk flags above and want to run this anyway
    </label>
    <div class="row" style="margin-top:10px">
      <button id="runBtn">▶ Run this</button>
      <button class="secondary" id="rejectBtn">✕ Discard</button>
    </div>
    <div class="card" style="margin-top:10px">
      <div class="sub" style="margin-bottom:6px">Output</div>
      <div class="output" id="outputBox">Not run yet.</div>
    </div>
  </div>

  <div class="card">
    <div class="sub" style="margin-bottom:8px">Recent plans</div>
    <div id="historyBox"><div class="empty">No plans yet</div></div>
  </div>
</div>
<script>
let currentPlan = null;

document.getElementById("backendSelect").onchange = (e) => {
  const cloud = e.target.value === "cloud";
  document.getElementById("connectorSelect").classList.toggle("hidden", !cloud);
  document.getElementById("adapterSelect").classList.toggle("hidden", !cloud);
  if (cloud) loadConnectors();
};

async function loadConnectors() {
  const r = await fetch("/agent/api/connectors", { headers: authHeaders() });
  const list = await r.json();
  const sel = document.getElementById("connectorSelect");
  if (!list.length) {
    sel.innerHTML = '<option value="">No connectors yet — add one in Admin</option>';
    return;
  }
  sel.innerHTML = list.map(c => `<option value="${c.name}">${c.name}${c.has_secret ? '' : ' (no secret set)'}</option>`).join("");
}

document.getElementById("planBtn").onclick = async () => {
  const task = document.getElementById("taskBox").value.trim();
  if (!task) { alert("Describe a task first"); return; }
  const backend = document.getElementById("backendSelect").value;
  const connector_name = document.getElementById("connectorSelect").value || null;
  const adapter = document.getElementById("adapterSelect").value;

  const btn = document.getElementById("planBtn");
  btn.disabled = true; btn.textContent = "Thinking…";
  try {
    const r = await fetch("/agent/api/plan", {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ task, backend, connector_name, adapter }),
    });
    const d = await r.json();
    if (d.error) { alert("Couldn't generate a plan: " + d.error); return; }
    showPlan(d);
    loadHistory();
  } catch (e) {
    alert("Request failed: " + e);
  } finally {
    btn.disabled = false; btn.textContent = "✨ Generate plan";
  }
};

function showPlan(plan) {
  currentPlan = plan;
  document.getElementById("planCard").classList.remove("hidden");
  document.getElementById("codePreview").textContent = plan.code;
  const badge = document.getElementById("riskBadge");
  badge.textContent = plan.risk_level.toUpperCase();
  badge.className = "risk-badge " + plan.risk_level;

  const flagsBox = document.getElementById("flagsBox");
  flagsBox.innerHTML = plan.flags.length
    ? plan.flags.map(f => `<div class="flag ${f.severity}">⚠ ${f.message}</div>`).join("")
    : '<div class="flag low">No risk flags — looks like plain read/print logic.</div>';

  const ackRow = document.getElementById("ackRow");
  const ackCheck = document.getElementById("ackCheck");
  ackCheck.checked = false;
  ackRow.classList.toggle("hidden", plan.risk_level !== "high");

  document.getElementById("outputBox").textContent = "Not run yet.";
}

document.getElementById("runBtn").onclick = async () => {
  if (!currentPlan) return;
  if (currentPlan.risk_level === "high" && !document.getElementById("ackCheck").checked) {
    alert("This plan is high-risk — check the acknowledgement box first if you want to proceed.");
    return;
  }
  const btn = document.getElementById("runBtn");
  btn.disabled = true; btn.textContent = "Running…";
  try {
    const r = await fetch("/agent/api/execute", {
      method: "POST", headers: authHeaders(),
      body: JSON.stringify({ plan_id: currentPlan.plan_id, ack_high_risk: document.getElementById("ackCheck").checked }),
    });
    const d = await r.json();
    const outBox = document.getElementById("outputBox");
    if (d.error) { outBox.textContent = "Error: " + d.error; return; }
    outBox.innerHTML = "";
    if (d.stdout) outBox.appendChild(document.createTextNode(d.stdout));
    if (d.stderr) {
      const span = document.createElement("span");
      span.className = "stderr";
      span.textContent = d.stderr;
      outBox.appendChild(span);
    }
    if (d.error) outBox.appendChild(document.createTextNode("\\n" + d.error));
    if (d.timed_out) outBox.appendChild(document.createTextNode("\\n[timed out]"));
    if (!d.stdout && !d.stderr && !d.error) outBox.textContent = "(no output)";
    currentPlan = null;
    loadHistory();
  } catch (e) {
    alert("Request failed: " + e);
  } finally {
    btn.disabled = false; btn.textContent = "▶ Run this";
  }
};

document.getElementById("rejectBtn").onclick = async () => {
  if (!currentPlan) return;
  await fetch("/agent/api/reject", { method: "POST", headers: authHeaders(), body: JSON.stringify({ plan_id: currentPlan.plan_id }) });
  document.getElementById("planCard").classList.add("hidden");
  currentPlan = null;
  loadHistory();
};

async function loadHistory() {
  const r = await fetch("/agent/api/history", { headers: authHeaders() });
  const rows = await r.json();
  const box = document.getElementById("historyBox");
  if (!rows.length) { box.innerHTML = '<div class="empty">No plans yet</div>'; return; }
  box.innerHTML = rows.map(p => `
    <div class="hist-item">
      <span>${escapeHtml(p.task).slice(0,60)}</span>
      <span class="hist-meta">${p.risk_level} · ${p.status}</span>
    </div>`).join("");
}

loadHistory();
</script>
</body>
</html>"""
