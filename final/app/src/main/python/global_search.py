"""
global_search.py — powers the /search page: one query box that checks
contacts, saved scripts, files in app storage, and installed plugins in
a single pass, plus a one-tap handoff into the existing web search
(/search/fast, /search/deep in backend_app.py — this module does not
touch the network itself, it only searches things already on-device).

Kept deliberately dumb: substring matching, no ranking model, no index
to keep in sync. On-device storage here is small enough (contacts,
scripts, a handful of plugins, app-private files) that a fresh scan per
query is fast and never goes stale.
"""

import json
import math
import os
import urllib.error
import urllib.request

import appstate
import contacts
import plugin_loader
import scripts_runner

FILES_DIR = None

# Folders under FILES_DIR that already have their own dedicated search
# UI (contacts, scripts) or that shouldn't be crawled (backups are
# binary/encrypted, __pycache__ is noise).
_SKIP_DIRNAMES = {"backups", "__pycache__", ".git"}
_MAX_FILE_RESULTS = 40
_CONTENT_SEARCHABLE_EXT = {".txt", ".md", ".json", ".csv", ".log", ".py"}
_CONTENT_SCAN_CAP = 200_000  # bytes - skip content search on anything bigger

# --- semantic search (Phase 3 differentiator) --------------------------
# Reuses LlamaEngineService.kt's already-embedded llama.cpp server for
# embeddings, instead of adding a second on-device ML runtime. Falls back
# to nothing (not an error) if the server doesn't have an embedding model
# loaded - substring search above still works either way.
_EMBED_CACHE = {}  # (path, mtime) -> vector; process-lifetime only, no DB
_EMBED_CANDIDATE_CAP = 150  # files+scripts scanned per query - keeps a slow embed call from making search itself feel hung
_EMBED_TEXT_CAP = 1000      # chars sent per embedding call
_EMBED_TIMEOUT = 4.0        # seconds - short, since this runs inline in a search request


def _embed(text):
    """Returns a list[float] or None (timeout, unreachable, no embedding
    model loaded) - callers treat None as 'skip semantic ranking for this
    item', never as an error to surface to the user."""
    if not text or not text.strip():
        return None
    payload = json.dumps({"content": text[:_EMBED_TEXT_CAP]}).encode()
    req = urllib.request.Request(
        f"{appstate.LLM_BASE_URL}/embedding", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_EMBED_TIMEOUT) as r:
            body = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    vec = body.get("embedding")
    # llama.cpp's /embedding sometimes wraps a batch as [[...]] - unwrap once.
    if vec and isinstance(vec[0], list):
        vec = vec[0]
    return vec if isinstance(vec, list) and vec else None


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _cached_embed(path, mtime, text):
    key = (path, mtime)
    if key in _EMBED_CACHE:
        return _EMBED_CACHE[key]
    vec = _embed(text)
    if vec is not None:
        _EMBED_CACHE[key] = vec
        if len(_EMBED_CACHE) > 2000:  # simple unbounded-growth guard, not LRU-precise
            _EMBED_CACHE.pop(next(iter(_EMBED_CACHE)))
    return vec


def _iter_semantic_candidates():
    """Yields (kind, title, path_or_name, mtime, text_for_embedding) for
    scripts + small text files - the same universe search_files/
    search_scripts_ already cover with substrings, so semantic search is
    additive ranking, not a new corpus."""
    n = 0
    try:
        for s in scripts_runner.list_scripts():
            if n >= _EMBED_CANDIDATE_CAP:
                return
            try:
                code = scripts_runner.read_script(s["name"])
            except Exception:
                continue
            yield ("script", s["name"], s["name"], 0, code)
            n += 1
    except Exception:
        pass

    if FILES_DIR and os.path.isdir(FILES_DIR):
        for root, dirs, files in os.walk(FILES_DIR):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRNAMES and not d.startswith(".")]
            for fname in files:
                if n >= _EMBED_CANDIDATE_CAP:
                    return
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _CONTENT_SEARCHABLE_EXT:
                    continue
                full = os.path.join(root, fname)
                try:
                    if os.path.getsize(full) > _CONTENT_SCAN_CAP:
                        continue
                    mtime = os.path.getmtime(full)
                    with open(full, "r", errors="ignore") as f:
                        text = f.read(_EMBED_TEXT_CAP)
                except OSError:
                    continue
                yield ("file", fname, os.path.relpath(full, FILES_DIR), mtime, text)
                n += 1


def search_semantic(query, limit=10, min_score=0.55):
    """Meaning-based ranking on top of the substring corpus, using the
    local LLM's embeddings. Returns [] (not an error) if the embedding
    server is unreachable or has no embedding model loaded - this is a
    best-effort enhancement layered over search_all(), never a
    replacement for it."""
    query = (query or "").strip()
    if not query:
        return []
    q_vec = _embed(query)
    if q_vec is None:
        return []

    scored = []
    for kind, title, ref, mtime, text in _iter_semantic_candidates():
        vec = _cached_embed(ref, mtime, text)
        if vec is None:
            continue
        score = _cosine(q_vec, vec)
        if score >= min_score:
            scored.append((score, kind, title, ref, text))

    scored.sort(key=lambda t: t[0], reverse=True)
    out = []
    for score, kind, title, ref, text in scored[:limit]:
        snippet = text.strip().replace("\n", " ")[:160]
        out.append({
            "title": title,
            "subtitle": snippet,
            "score": round(score, 3),
            "kind": kind,
            "url": ("/scripts?open=" + ref) if kind == "script" else None,
            "path": ref if kind == "file" else None,
        })
    return out
# -------------------------------------------------------------------


def init(files_dir):
    global FILES_DIR
    FILES_DIR = files_dir


def search_contacts(query, limit=15):
    try:
        rows = contacts.list_contacts(search=query)
    except Exception:
        return []
    out = []
    for c in rows[:limit]:
        out.append({
            "id": c["id"],
            "title": c.get("name") or "(unnamed)",
            "subtitle": c.get("phone") or c.get("email") or "",
            "url": f"/contacts#{c['id']}",
        })
    return out


def search_scripts_(query, limit=15):
    try:
        hits = scripts_runner.search_scripts(query)
    except Exception:
        return []
    out = []
    for h in hits[:limit]:
        out.append({
            "title": h["name"],
            "subtitle": h["snippet"],
            "url": "/scripts?open=" + h["name"],
        })
    return out


def search_plugins(query, limit=15):
    plugin_dir = plugin_loader.get_plugin_dir()
    if not plugin_dir or not os.path.isdir(plugin_dir) or not query:
        return []
    q = query.lower()
    status = plugin_loader.status()
    out = []
    for fname in sorted(os.listdir(plugin_dir)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        name_hit = q in fname.lower()
        snippet = None
        path = os.path.join(plugin_dir, fname)
        try:
            with open(path, "r", errors="ignore") as f:
                for line_no, line in enumerate(f, start=1):
                    if q in line.lower():
                        snippet = f"L{line_no}: {line.strip()[:120]}"
                        break
        except OSError:
            continue
        if name_hit or snippet:
            state = status.get(fname, {}).get("status", "not loaded")
            out.append({
                "title": fname,
                "subtitle": f"[{state}] {snippet or '(name match)'}",
                "url": "/admin#plugins",
            })
        if len(out) >= limit:
            break
    return out


def search_files(query, limit=_MAX_FILE_RESULTS):
    if not FILES_DIR or not os.path.isdir(FILES_DIR) or not query:
        return []
    q = query.lower()
    out = []
    for root, dirs, files in os.walk(FILES_DIR):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRNAMES and not d.startswith(".")]
        for fname in files:
            if len(out) >= limit:
                return out
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, FILES_DIR)
            name_hit = q in fname.lower()
            snippet = None
            ext = os.path.splitext(fname)[1].lower()
            if not name_hit and ext in _CONTENT_SEARCHABLE_EXT:
                try:
                    if os.path.getsize(full) <= _CONTENT_SCAN_CAP:
                        with open(full, "r", errors="ignore") as f:
                            for line_no, line in enumerate(f, start=1):
                                if q in line.lower():
                                    snippet = f"L{line_no}: {line.strip()[:120]}"
                                    break
                except OSError:
                    continue
            if name_hit or snippet:
                out.append({
                    "title": fname,
                    "subtitle": snippet or rel,
                    "url": None,  # no web route serves files - open via native File Explorer
                    "path": rel,
                })
    return out


def search_all(query):
    query = (query or "").strip()
    if not query:
        return {"contacts": [], "scripts": [], "plugins": [], "files": [], "semantic": [], "query": query}
    return {
        "query": query,
        "contacts": search_contacts(query),
        "scripts": search_scripts_(query),
        "plugins": search_plugins(query),
        "files": search_files(query),
        # Meaning-based results on top of the substring matches above -
        # empty list (not an error) if the local LLM has no embedding
        # model loaded, so this never breaks plain search.
        "semantic": search_semantic(query),
    }
