"""Blueprint: routes_admin - split from the original monolithic backend_app.py."""

import os
import time

from flask import Blueprint, jsonify, request

import appstate
import config
import dbcore
import plugin_loader
import scheduler
import theme
import watcher
from auth import require_auth
from error_manager import safe_route, disabled_routes

bp_admin = Blueprint("routes_admin", __name__)



# ---------------------------------------------------------------------
# Admin panel: settings, plugin management, logs, all in one page.
# GET /admin itself is unprotected (it's just the shell page - same
# reasoning as /automation/token above); every action button on the
# page calls a protected /admin/* API route with the token attached
# via window.PyBoxAuth, same pattern as the /automation/* routes.
# ---------------------------------------------------------------------

@bp_admin.route("/admin")
@safe_route("admin-page")
def admin_page():
    return theme.render(_ADMIN_HTML, active="admin")


@bp_admin.route("/admin/status")
@require_auth
@safe_route("admin-status")
def admin_status():
    return jsonify({
        "config": config.get_all(),
        "jobs": scheduler.list_jobs(),
        "watchers": watcher.list_watches(),
        "plugins": plugin_loader.status(),
        "db_pools": dbcore.pool_stats(),
        "disabled_routes": disabled_routes(),
    })


@bp_admin.route("/admin/healthz")
@safe_route("admin-healthz")
def admin_healthz():
    """Unauthenticated, minimal liveness check - no token required, since
    the WebView (and any external monitor) should be able to tell the
    server is up before it has a token to send. Deliberately returns
    nothing sensitive: no config, no job details, no file paths."""
    return jsonify({"ok": True, "time": time.time()})


@bp_admin.route("/admin/config", methods=["POST"])
@require_auth
@safe_route("admin-config-update")
def admin_config_update():
    body = request.get_json(force=True)
    config.set_many(body)
    return jsonify(config.get_all())


@bp_admin.route("/admin/logs")
@require_auth
@safe_route("admin-logs")
def admin_logs():
    n = int(request.args.get("lines", 200))
    log_path = os.path.join(appstate.FILES_DIR, "pybox.log")
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
<div class="toolbar" style="margin-bottom:14px; flex-wrap:wrap">
  <a href="/" style="text-decoration:none"><button class="secondary">Home</button></a>
  <a href="/contacts" style="text-decoration:none"><button class="secondary">Contacts</button></a>
  <a href="/scripts" style="text-decoration:none"><button class="secondary">Scripts</button></a>
  <a href="/settings" style="text-decoration:none"><button class="secondary">Settings</button></a>
</div>

<div class="card" id="search-card">
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
  <p class="hint">Jobs run handlers already registered in scheduler.JOB_HANDLERS - pick one below, or write a plugin that registers a new one.</p>
  <div class="toolbar">
    <input id="job-name" placeholder="Job name">
    <select id="job-handler">
      <option value="log_event">log_event</option>
      <option value="contacts_dedup">contacts_dedup</option>
      <option value="contacts_refresh_links">contacts_refresh_links</option>
      <option value="encrypted_backup">encrypted_backup</option>
    </select>
    <input id="job-interval" placeholder="Seconds" style="max-width:90px" value="3600">
    <button onclick="createJob()">Add</button>
  </div>
  <div id="jobs"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Folder Watchers</h2><span class="count" id="watchers-count">0</span></div>
  <p class="hint">Only scans folders registered here - never the whole phone.</p>
  <div class="toolbar">
    <input id="watch-path" placeholder="/storage/emulated/0/PyBox/import">
    <input id="watch-ext" placeholder="vcf,csv" style="max-width:110px">
    <button onclick="createWatcher()">Add</button>
  </div>
  <div id="watchers"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Encryption &amp; Backups</h2><span id="enc-status" class="count"></span></div>
  <p class="hint">AES-256-GCM, key wrapped by the Android Keystore (see SecureKeyManager.kt). Encrypts data at rest on this device only.</p>
  <div class="toolbar">
    <select id="backup-db">
      <option value="contacts.db">contacts.db</option>
      <option value="usage_stats.db">usage_stats.db</option>
    </select>
    <button onclick="runBackup()">Backup now</button>
    <button class="secondary" onclick="runFullBackup()">Full backup (scripts + theme/config)</button>
    <button class="secondary" onclick="loadBackups()">Refresh list</button>
  </div>
  <div id="backups"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Connectors</h2><span class="count" id="connectors-count">0</span></div>
  <p class="hint">Named external API connections scripts can call via <code>connectors.call("name", ...)</code> — no rebuild needed to add one.</p>
  <div id="connectors"></div>
  <div class="toolbar" style="margin-top:8px; flex-wrap:wrap">
    <input type="text" id="conn-name" placeholder="name (e.g. openai)" style="flex:1; min-width:100px">
    <input type="text" id="conn-url" placeholder="base URL" style="flex:2; min-width:140px">
  </div>
  <div class="toolbar" style="margin-top:6px; flex-wrap:wrap">
    <input type="text" id="conn-auth-header" placeholder="auth header (e.g. Authorization)" style="flex:1; min-width:140px">
    <input type="text" id="conn-auth-value" placeholder="auth value / API key" style="flex:1; min-width:140px">
  </div>
  <div class="toolbar" style="margin-top:6px">
    <button onclick="addConnector()">＋ Add connector</button>
  </div>
</div>

<div class="card">
  <div class="card-header"><h2>Self-Healing / Intelligence</h2><span class="count" id="intel-degraded-count">0</span></div>
  <p class="hint">Retry + fallback health for scraping and connector calls. A capability is flagged degraded below 50% recent success.</p>
  <div class="toolbar"><button class="secondary" onclick="loadIntelligence()">Refresh</button></div>
  <div id="intelligence"></div>
</div>

<div class="card">
  <div class="card-header"><h2>Plugins</h2><span class="count" id="plugins-count">0</span></div>
  <p class="hint">Drop .py files at PyBox/plugins on your SD card, or write one right here and save - no rebuild, no Termux needed.</p>
  <div class="toolbar"><button onclick="reloadPlugins()">Reload plugins</button></div>
  <div id="plugins"></div>
  <details style="margin-top:10px">
    <summary>Write a new plugin</summary>
    <div class="field" style="margin-top:8px">
      <input id="plugin-filename" placeholder="my_plugin.py" style="margin-bottom:6px">
      <textarea id="plugin-code" rows="10" style="width:100%; font-family:monospace; font-size:11.5px; background:#141414; color:#ddd; border:1px solid #333; border-radius:6px; padding:8px;"></textarea>
      <div class="toolbar" style="margin-top:6px">
        <button class="secondary" onclick="loadPluginTemplate()">Insert starter template</button>
        <button onclick="savePlugin()">Save &amp; reload</button>
      </div>
    </div>
  </details>
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
      html += `<div class="synthesis"><strong>Answer (from fetched sources):</strong>\n${escapeHtml(d.synthesis)}</div>`;
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

async function createJob() {
  const name = document.getElementById("job-name").value.trim();
  const handler = document.getElementById("job-handler").value;
  const interval_seconds = document.getElementById("job-interval").value.trim() || "3600";
  if (!name) { alert("Give the job a name."); return; }
  await fetch("/automation/jobs", {
    method: "POST", headers: authHeaders(),
    body: JSON.stringify({ name, handler, interval_seconds }),
  });
  document.getElementById("job-name").value = "";
  loadStatus();
}

async function createWatcher() {
  const path = document.getElementById("watch-path").value.trim();
  const extRaw = document.getElementById("watch-ext").value.trim();
  if (!path) { alert("Enter a folder path to watch."); return; }
  const extensions = extRaw ? extRaw.split(",").map(s => s.trim()).filter(Boolean) : [];
  await fetch("/automation/watchers", {
    method: "POST", headers: authHeaders(),
    body: JSON.stringify({ path, extensions }),
  });
  document.getElementById("watch-path").value = "";
  document.getElementById("watch-ext").value = "";
  loadStatus();
}

async function runBackup() {
  const db_name = document.getElementById("backup-db").value;
  const r = await fetch("/encryption/backup", {
    method: "POST", headers: authHeaders(), body: JSON.stringify({ db_name }),
  });
  const d = await r.json();
  if (d.error) alert("Backup failed: " + d.error);
  loadBackups();
}

async function runFullBackup() {
  const r = await fetch("/encryption/full_backup", { method: "POST", headers: authHeaders(), body: "{}" });
  const d = await r.json();
  if (d.error) { alert("Full backup failed: " + d.error); return; }
  alert(`Backed up ${d.included.length} file(s): ${d.included.join(", ")}`);
  loadBackups();
}

async function loadConnectors() {
  const r = await fetch("/admin/connectors", { headers: authHeaders() });
  const list = await r.json();
  document.getElementById("connectors-count").textContent = list.length;
  if (!list.length) {
    document.getElementById("connectors").innerHTML = '<p class="hint">No connectors added yet.</p>';
    return;
  }
  let html = "<table><tr><th>Name</th><th>Base URL</th><th>Auth</th><th></th></tr>";
  list.forEach(c => {
    html += `<tr>
      <td>${c.name}</td>
      <td style="word-break:break-all">${c.base_url}</td>
      <td>${c.has_secret ? "🔒 " + c.auth_header : "—"}</td>
      <td>
        <button class="secondary" onclick="testConnector('${c.name}')">Test</button>
        <button class="danger" onclick="deleteConnector('${c.name}')">Delete</button>
      </td>
    </tr>`;
  });
  html += "</table>";
  document.getElementById("connectors").innerHTML = html;
}

async function addConnector() {
  const name = document.getElementById("conn-name").value.trim();
  const base_url = document.getElementById("conn-url").value.trim();
  const auth_header = document.getElementById("conn-auth-header").value.trim();
  const auth_value = document.getElementById("conn-auth-value").value.trim();
  if (!name || !base_url) { alert("Name and base URL are required"); return; }
  const r = await fetch("/admin/connectors", {
    method: "POST", headers: authHeaders(),
    body: JSON.stringify({ name, base_url, auth_header, auth_value }),
  });
  const d = await r.json();
  if (d.error) { alert("Save failed: " + d.error); return; }
  ["conn-name", "conn-url", "conn-auth-header", "conn-auth-value"].forEach(id => document.getElementById(id).value = "");
  loadConnectors();
}

async function deleteConnector(name) {
  if (!confirm(`Delete connector "${name}"?`)) return;
  await fetch(`/admin/connectors/${encodeURIComponent(name)}`, { method: "DELETE", headers: authHeaders() });
  loadConnectors();
}

async function testConnector(name) {
  const r = await fetch(`/admin/connectors/${encodeURIComponent(name)}/test`, { method: "POST", headers: authHeaders() });
  const d = await r.json();
  alert(d.ok ? `✔ Reachable (HTTP ${d.status_code})` : `✖ ${d.error || "unreachable"}`);
}

async function loadIntelligence() {
  const r = await fetch("/admin/intelligence", { headers: authHeaders() });
  const d = await r.json();
  document.getElementById("intel-degraded-count").textContent = d.degraded.length;
  if (!d.capabilities.length) {
    document.getElementById("intelligence").innerHTML = '<p class="hint">No activity tracked yet — this fills in as scraping/connector calls happen.</p>';
    return;
  }
  let html = "<table><tr><th>Capability</th><th>Score</th><th>Attempts</th><th>Last error</th><th></th></tr>";
  d.capabilities.forEach(c => {
    const scoreLabel = c.score === null ? "—" : c.score + "%";
    const rowStyle = (c.score !== null && c.score < 50) ? ' style="color:#ff8080"' : "";
    html += `<tr${rowStyle}>
      <td>${c.capability}</td>
      <td>${scoreLabel}</td>
      <td>${c.attempts} (${c.failures} failed)</td>
      <td style="max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">${c.last_error || "—"}</td>
      <td><button class="secondary" onclick="resetIntelligence('${c.capability}')">Reset</button></td>
    </tr>`;
  });
  html += "</table>";
  document.getElementById("intelligence").innerHTML = html;
}

async function resetIntelligence(capability) {
  await fetch(`/admin/intelligence/${encodeURIComponent(capability)}/reset`, { method: "POST", headers: authHeaders() });
  loadIntelligence();
}

async function loadBackups() {
  const statusR = await fetch("/encryption/status", { headers: authHeaders() });
  const status = await statusR.json();
  document.getElementById("enc-status").textContent = status.available ? "key loaded" : "no key";

  const r = await fetch("/encryption/backups", { headers: authHeaders() });
  const d = await r.json();
  const list = d.backups || [];
  if (!list.length) {
    document.getElementById("backups").innerHTML = '<table><tr class="empty-row"><td>No backups yet.</td></tr></table>';
    return;
  }
  let html = "<table><tr><th>File</th></tr>";
  list.forEach(name => { html += `<tr><td style="word-break:break-all">${escapeHtml(name)}</td></tr>`; });
  html += "</table>";
  document.getElementById("backups").innerHTML = html;
}

async function loadPluginTemplate() {
  const r = await fetch("/admin/plugins/template", { headers: authHeaders() });
  const d = await r.json();
  document.getElementById("plugin-code").value = d.template;
  if (!document.getElementById("plugin-filename").value.trim()) {
    document.getElementById("plugin-filename").value = "my_plugin.py";
  }
}

async function savePlugin() {
  const name = document.getElementById("plugin-filename").value.trim();
  const code = document.getElementById("plugin-code").value;
  if (!name.endsWith(".py")) { alert("Filename must end in .py"); return; }
  const r = await fetch("/admin/plugins/save", {
    method: "POST", headers: authHeaders(), body: JSON.stringify({ name, code }),
  });
  const d = await r.json();
  if (d.error) { alert("Save failed: " + d.error); return; }
  loadStatus();
  alert("Saved and reloaded. Check the Plugins list above for its status.");
}

loadStatus();
loadLogs(30);
loadBackups();
loadConnectors();
loadIntelligence();
setInterval(loadStatus, 10000);
</script>
</body>
</html>"""
