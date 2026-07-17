"""Blueprint: routes_search - split from the original monolithic backend_app.py."""

import threading
import time
import uuid

from flask import Blueprint, jsonify, request

import appstate
import error_manager
import global_search
import search_engine
import theme
from auth import require_auth
from error_manager import safe_route

bp_search = Blueprint("routes_search", __name__)



# ---------------------------------------------------------------------
# Web search (search_engine.py) - multi-engine, two modes:
#   fast -> search_fast(), synchronous, returns in ~seconds.
#   deep -> search_deep() actually fetches + reads top result pages and
#           optionally synthesizes via the local LLM, which can take
#           10s-60s+. Run as a background job so the caller polls
#           instead of holding a request open that long. Jobs live only
#           in memory (appstate.FILES_DIR/automation.db is for scheduler.py's
#           recurring jobs, not one-off searches) - a restart clears
#           any in-flight search, which is fine since it's not
#           persistent automation.
# ---------------------------------------------------------------------


def _run_deep_search_job(job_id, query, max_results, synthesize):
    with appstate.SEARCH_JOBS_LOCK:
        appstate.SEARCH_JOBS[job_id]["status"] = "running"
    try:
        result = search_engine.search_deep(query, max_results=max_results, synthesize=synthesize)
        with appstate.SEARCH_JOBS_LOCK:
            appstate.SEARCH_JOBS[job_id]["status"] = "done"
            appstate.SEARCH_JOBS[job_id]["result"] = result
    except Exception as e:
        error_manager.log_error("search-deep-job", e)
        with appstate.SEARCH_JOBS_LOCK:
            appstate.SEARCH_JOBS[job_id]["status"] = "error"
            appstate.SEARCH_JOBS[job_id]["error"] = str(e)


@bp_search.route("/search/fast", methods=["POST"])
@require_auth
@safe_route("search-fast")
def search_fast_route():
    body = request.get_json(force=True)
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    max_results = int(body.get("max_results", 10))
    return jsonify(search_engine.search_fast(query, max_results=max_results))


@bp_search.route("/search/deep", methods=["POST"])
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
    with appstate.SEARCH_JOBS_LOCK:
        appstate.SEARCH_JOBS[job_id] = {"status": "queued", "query": query, "created_at": time.time()}
    threading.Thread(
        target=_run_deep_search_job, args=(job_id, query, max_results, synthesize), daemon=True
    ).start()
    return jsonify({"job_id": job_id, "status": "queued"})


@bp_search.route("/search/deep/<job_id>", methods=["GET"])
@require_auth
@safe_route("search-deep-poll")
def search_deep_poll(job_id):
    with appstate.SEARCH_JOBS_LOCK:
        job = appstate.SEARCH_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "unknown job_id"}), 404
    return jsonify(job)





# saved scripts, plugins, and app-private files, with a one-tap handoff
# into the existing web search (/search/fast, /search/deep) for anything
# that needs to leave the device. GET /search is unprotected (shell page
# only); the actual query goes through a protected API route since it
# reads contact data.
# ---------------------------------------------------------------------

@bp_search.route("/search")
@safe_route("global-search-page")
def global_search_page():
    return theme.render(_SEARCH_HTML, active="search")


@bp_search.route("/search/global", methods=["GET"])
@require_auth
@safe_route("global-search-api")
def global_search_api():
    q = request.args.get("q", "")
    return jsonify(global_search.search_all(q))


_SEARCH_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox Search</title>
<style>
  body { margin:0; background:#0d0d0d; color:#e8e8e8; font-family:-apple-system,Roboto,sans-serif; padding:16px; }
  h1 { font-size:19px; margin:0 0 10px; }
  .search-row { display:flex; gap:8px; margin-bottom:6px; }
  input[type=text] { flex:1; background:#1a1a1a; border:1px solid #2a2a2a; color:#e8e8e8; border-radius:10px; padding:12px; font-size:14px; }
  button { background:#2e7d4f; color:#fff; border:none; border-radius:10px; padding:0 16px; font-size:14px; }
  button.secondary { background:#333; }
  .hint { color:#888; font-size:12px; margin-bottom:14px; }
  .section { margin-bottom:16px; }
  .section-title { font-size:13px; color:#7ec8f2; margin-bottom:6px; display:flex; justify-content:space-between; }
  .result { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:10px; padding:10px 12px; margin-bottom:6px; }
  .result a { color:#e8e8e8; text-decoration:none; font-size:14px; display:block; }
  .result .path-only { font-size:14px; color:#ccc; }
  .result .sub { color:#888; font-size:12px; margin-top:2px; font-family:monospace; }
  .empty-state { color:#666; font-size:13px; text-align:center; padding:30px 10px; }
  .web-cta { background:#182620; border:1px solid #26392f; border-radius:10px; padding:14px; text-align:center; margin-top:6px; }
  .web-cta button { margin-top:8px; }
</style>
</head>
<body>
<h1>🔍 Search PyBox</h1>
<div class="hint">Searches contacts, saved scripts, plugins, and app files together. This does not touch the network — for the open web, use the button at the bottom.</div>
<div class="search-row">
  <input type="text" id="q" placeholder="Search everything on-device…" autofocus>
  <button id="goBtn">Go</button>
</div>
<div id="results"><div class="empty-state">Start typing to search contacts, scripts, plugins, and files.</div></div>

<script>
let debounceTimer = null;
document.getElementById("q").addEventListener("input", () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(runSearch, 300);
});
document.getElementById("goBtn").onclick = runSearch;

const SECTIONS = [
  { key: "contacts", label: "Contacts", icon: "👤" },
  { key: "scripts", label: "Scripts", icon: "🐍" },
  { key: "plugins", label: "Plugins", icon: "🧩" },
  { key: "files", label: "Files", icon: "📎" },
];

async function runSearch() {
  const q = document.getElementById("q").value.trim();
  const box = document.getElementById("results");
  if (!q) { box.innerHTML = '<div class="empty-state">Start typing to search contacts, scripts, plugins, and files.</div>'; return; }
  box.innerHTML = '<div class="empty-state">Searching…</div>';
  const r = await fetch(`/search/global?q=${encodeURIComponent(q)}`, { headers: authHeaders() });
  const d = await r.json();

  let totalHits = 0;
  let html = "";
  SECTIONS.forEach(s => {
    const items = d[s.key] || [];
    totalHits += items.length;
    if (!items.length) return;
    html += `<div class="section"><div class="section-title"><span>${s.icon} ${s.label}</span><span>${items.length}</span></div>`;
    items.forEach(item => {
      if (item.url) {
        html += `<div class="result"><a href="${item.url}">${escapeHtml(item.title)}</a><div class="sub">${escapeHtml(item.subtitle || "")}</div></div>`;
      } else {
        html += `<div class="result"><div class="path-only">${escapeHtml(item.title)}</div><div class="sub">${escapeHtml(item.subtitle || "")} — open via File Explorer</div></div>`;
      }
    });
    html += `</div>`;
  });

  if (!totalHits) {
    html = '<div class="empty-state">No on-device matches.</div>';
  }

  html += `<div class="web-cta">Want to search the open web instead?<br>
    <button onclick="location.href='/admin#search-card'">🌐 Search the web for "${escapeHtml(q)}"</button></div>`;

  box.innerHTML = html;
}

</script>
</body>
</html>"""
