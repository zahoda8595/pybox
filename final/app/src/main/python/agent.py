"""
agent.py — an in-app AI coding agent: describe a task in plain English,
an LLM (local llama.cpp engine or a cloud connector you've registered)
writes a Python script to do it, and PyBox runs it for you.

THE CORE DESIGN DECISION — plan, then execute, never both at once:
  This module deliberately does NOT offer a single "do the thing" call
  that generates code and immediately runs it. Two-step, always:

    1. create_plan(task, ...)   generates code, runs it through a static
                                 safety scan, and SAVES it — nothing on
                                 the device has been touched yet. You get
                                 back the code and a plain-English list
                                 of what it would do if run.
    2. execute_plan(plan_id)    runs EXACTLY the code from step 1 (hash-
                                 checked, so it can't have been swapped),
                                 and only after you've looked at step 1's
                                 output. High-risk plans need an extra
                                 explicit acknowledgement flag.

  This is the same reason Claude Code and other coding agents show a
  diff before touching files: an LLM writing code that then silently
  executes with no human in the loop is how a bad generation (wrong file
  path, misread instruction, hallucinated API) turns into deleted data
  instead of a caught mistake. A log entry written *after* the fact
  doesn't prevent anything — it just tells you what already broke. The
  gate has to be BEFORE execution, which is what this module enforces:
  execute_plan() flatly refuses to run anything that didn't come from a
  matching create_plan() call.

SAFETY SCAN (_analyze_safety):
  Static AST inspection of the generated code — no execution involved —
  that flags file writes/deletes, subprocess/os.system, network calls,
  and use of sensitive PyBox modules (contacts, encryption, gdrive), and
  rolls them up into a risk level (low/medium/high). It's necessarily
  incomplete (dynamic code can hide intent from static analysis), so
  treat it as a second opinion to actually read the code, not a
  guarantee — same caveat as any linter.

EXECUTION:
  Reuses scripts_runner's existing sandboxed thread + timeout executor
  (same trust level as a script you wrote yourself and ran from
  /scripts — full Python, app-private storage) rather than building a
  second, weaker execution path.

STORAGE:
  FILES_DIR/agent.db (SQLite) — one row per plan, covering both the
  pending-approval and already-decided states, so /agent's history view
  is just "every row", no separate log file to keep in sync.
"""

import ast
import difflib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

import config
import connectors
import dbcore
import scripts_runner

_DB_PATH = None
_LOCK = threading.Lock()

LLM_BASE_URL = "http://127.0.0.1:8081"
PLAN_EXPIRY_SECONDS = 900  # a stale, unreviewed plan can't be executed later


def init(files_dir):
    global _DB_PATH
    _DB_PATH = os.path.join(files_dir, "agent.db")
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                backend TEXT NOT NULL,
                code TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                flags TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                decided_at REAL,
                stdout TEXT,
                stderr TEXT,
                error TEXT,
                timed_out INTEGER
            )
        """)
        # Migration for DBs created before the diff-view feature - ADD
        # COLUMN has no "IF NOT EXISTS" in SQLite, so probe-and-ignore.
        for col in ("write_targets TEXT", "before_snapshots TEXT", "diffs TEXT"):
            try:
                c.execute(f"ALTER TABLE plans ADD COLUMN {col}")
            except Exception:
                pass  # column already exists
        dbcore.ensure_indexes(c, "plans", [
            ("idx_plans_status", "status"),
            ("idx_plans_created_at", "created_at"),
        ])

    # Lets an already-approved plan become a recurring scheduler job instead
    # of a one-off run - see schedule_plan() below. Registered here (not at
    # import time) so it only exists once agent.py's own DB is ready.
    import scheduler
    scheduler.JOB_HANDLERS["agent_recurring_plan"] = _run_scheduled_plan


def _conn():
    return dbcore.get_connection(_DB_PATH)


class AgentError(Exception):
    pass


# ---------------------------------------------------------------------
# Step 1a: ask an LLM (local engine or a registered cloud connector) to
# write the code.
# ---------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You write short, self-contained Python scripts for PyBox, a "
    "personal Android automation app. Rules:\n"
    "1. Respond with ONE python code block and nothing else — no "
    "explanation before or after.\n"
    "2. You may `import` any PyBox module already on the device "
    "(config, contacts, scraper, connectors, encryption, gdrive, "
    "browser, intelligence, osint_tools) as well as the standard "
    "library and requests.\n"
    "3. print() whatever the user should see — stdout is the only "
    "thing that comes back to them.\n"
    "4. Never invent file paths outside the app's own storage; use "
    "config.FILES_DIR-relative paths or ask via a comment if unsure.\n"
    "5. Don't include destructive operations (deleting files, wiping "
    "data) unless the task explicitly asked for exactly that."
)


def _extract_code(text):
    """Pulls the first ```python ... ``` (or bare ``` ... ```) block out
    of an LLM response. Falls back to the whole response if the model
    didn't fence it, so a slightly-off-format reply still produces
    something reviewable instead of an opaque failure."""
    if "```" not in text:
        return text.strip()
    parts = text.split("```")
    for i in range(1, len(parts), 2):
        block = parts[i]
        if block.lower().startswith("python"):
            block = block[len("python"):]
        stripped = block.strip()
        if stripped:
            return stripped
    return text.strip()


def _recent_failures_note(limit=3):
    """Differentiator #4: pulls the agent's own last few failed runs (real
    execution errors, not rejected/pending plans) so the prompt can nudge
    the LLM away from repeating a mistake it already made. Best-effort -
    swallows its own errors since a broken context note must never block
    plan generation."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT task, error FROM plans WHERE status='executed' AND error IS NOT NULL "
                "ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = ["Note - these recent attempts failed; avoid repeating the same mistake:"]
    for r in rows:
        err_line = (r["error"] or "").strip().splitlines()[-1] if r["error"] else "unknown error"
        lines.append(f"- Task \"{r['task'][:80]}\" failed with: {err_line[:200]}")
    return "\n".join(lines) + "\n\n"


def _generate_local(task, timeout=120, context_note=""):
    prompt = f"{_SYSTEM_PROMPT}\n\n{context_note}Task: {task}\n\nPython code:\n```python\n"
    payload = json.dumps({
        "prompt": prompt,
        "n_predict": 800,
        "temperature": 0.2,
        "stop": ["```"],
    }).encode()
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/completion", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
    except urllib.error.URLError as e:
        raise AgentError(
            "Local LLM engine unreachable — start it from Settings "
            f"first ({e})"
        ) from e
    text = body.get("content", "")
    return _extract_code(text if "```" in text else f"```python\n{text}\n```")


def _generate_cloud(task, connector_name, adapter, context_note=""):
    """adapter picks the request/response shape for the connector's API.
    'anthropic' -> POST {base_url}/v1/messages (Messages API shape).
    'openai'    -> POST {base_url}/chat/completions (Chat Completions shape).
    The connector itself (base_url + auth header/value) is whatever you
    registered in /admin -> Connectors — this module never sees or
    stores the API key, connectors.py does (encrypted, if a key is
    loaded)."""
    if adapter == "anthropic":
        body = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1200,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": f"{context_note}Task: {task}"}],
        }
        path = "/v1/messages"
    elif adapter == "openai":
        body = {
            "model": "gpt-4o-mini",
            "max_tokens": 1200,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"{context_note}Task: {task}"},
            ],
        }
        path = "/chat/completions"
    else:
        raise AgentError(f"unknown adapter '{adapter}' — use 'anthropic' or 'openai'")

    try:
        resp = connectors.call(connector_name, "POST", path, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except connectors.ConnectorError as e:
        raise AgentError(str(e)) from e
    except Exception as e:
        raise AgentError(f"cloud request failed: {e}") from e

    if adapter == "anthropic":
        text = "".join(
            block.get("text", "") for block in data.get("content", [])
            if block.get("type") == "text"
        )
    else:
        choices = data.get("choices", [])
        text = choices[0]["message"]["content"] if choices else ""
    return _extract_code(text)


# ---------------------------------------------------------------------
# Step 1b: static safety scan of the generated code (no execution).
# ---------------------------------------------------------------------

_SENSITIVE_MODULES = {
    "contacts": "reads/writes your contacts database",
    "encryption": "touches encryption keys / encrypted backups",
    "gdrive": "talks to Google Drive (cloud upload/download)",
    "connectors": "can call any API you've registered a connector for",
    "browser": "can control the in-app browser",
    "osint_tools": "makes outbound lookups against a domain/URL you give it",
    "subprocess": "can launch other processes",
    "shutil": "can bulk-copy or recursively delete files/folders",
    "socket": "opens raw network sockets",
}

_NETWORK_MODULES = {"requests", "urllib", "http"}


def _analyze_safety(code):
    """Returns {risk_level, flags: [{severity, message}]}. Pure static
    analysis (ast.walk) — the code is never run to produce this."""
    flags = []

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "risk_level": "high",
            "flags": [{"severity": "high", "message": f"Code doesn't parse: {e}"}],
        }

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for n in names:
                top = (n or "").split(".")[0]
                if top in _SENSITIVE_MODULES:
                    flags.append({"severity": "medium", "message": f"Imports `{top}` — {_SENSITIVE_MODULES[top]}"})
                elif top in _NETWORK_MODULES:
                    flags.append({"severity": "low", "message": f"Imports `{top}` — makes network requests"})

        elif isinstance(node, ast.Call):
            fn = node.func
            fn_name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
            owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else ""

            if fn_name in ("system", "popen") or f"{owner}.{fn_name}" in ("os.system", "os.popen"):
                flags.append({"severity": "high", "message": "Runs a shell command (os.system/popen)"})
            elif fn_name in ("remove", "unlink", "rmdir") or f"{owner}.{fn_name}" == "shutil.rmtree":
                flags.append({"severity": "high", "message": f"Deletes files/folders ({fn_name})"})
            elif f"{owner}.{fn_name}" == "shutil.rmtree":
                flags.append({"severity": "high", "message": "Recursively deletes a folder (shutil.rmtree)"})
            elif fn_name == "open":
                # mode is the 2nd positional arg or 'mode' kwarg
                mode = None
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                if mode and any(m in str(mode) for m in ("w", "a", "x")):
                    flags.append({"severity": "medium", "message": f"Opens a file for writing (mode='{mode}')"})
            elif fn_name in ("eval", "exec"):
                flags.append({"severity": "high", "message": f"Uses {fn_name}() on dynamic content"})
            elif fn_name in ("get", "post", "put", "delete", "patch", "request") and owner in ("requests", "connectors"):
                flags.append({"severity": "low", "message": f"Makes an outbound HTTP {fn_name.upper()} request"})

    severities = [f["severity"] for f in flags]
    if "high" in severities:
        risk_level = "high"
    elif "medium" in severities:
        risk_level = "medium"
    elif severities:
        risk_level = "low"
    else:
        risk_level = "low"

    # de-dupe identical messages (same import flagged from multiple nodes)
    seen = set()
    deduped = []
    for f in flags:
        key = (f["severity"], f["message"])
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    return {"risk_level": risk_level, "flags": deduped}


# ---------------------------------------------------------------------
# Step 1: create_plan — generate + analyze + save. Nothing executes.
# ---------------------------------------------------------------------

_SNAPSHOT_CAP = 50_000  # chars - big enough for real config/code files, capped to keep the plans DB small


def _extract_file_targets(tree):
    """Walks the same AST _analyze_safety() already parsed, looking for
    LITERAL string paths passed to open(path, 'w'/'a'/'x'), os.remove/
    os.unlink, or shutil.rmtree. Only literal strings are caught - a path
    built at runtime (f-strings, variables, os.path.join(...)) can't be
    resolved statically, so it's silently skipped rather than guessed at.
    That's a real scope limit, not a bug: this is a preview aid, not a
    sandbox enforcement mechanism (safe_route/the plan-gate already are).
    Returns [{"path": str, "action": "write"|"delete"}, ...], deduped."""
    targets = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        fn_name = fn.attr if isinstance(fn, ast.Attribute) else (fn.id if isinstance(fn, ast.Name) else "")
        owner = fn.value.id if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) else ""

        if fn_name == "open" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if mode and any(m in str(mode) for m in ("w", "a", "x")):
                targets.append({"path": node.args[0].value, "action": "write"})

        elif (fn_name in ("remove", "unlink") or f"{owner}.{fn_name}" == "shutil.rmtree") \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            targets.append({"path": node.args[0].value, "action": "delete"})

    seen = set()
    deduped = []
    for t in targets:
        key = (t["path"], t["action"])
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    return deduped


def _snapshot(path):
    """Reads current file content for the diff preview, capped and never
    raising - a missing/unreadable file just means 'before' is None
    (shown as '(file does not exist yet)' by the UI)."""
    try:
        with open(path, "r", errors="replace") as f:
            return f.read(_SNAPSHOT_CAP)
    except (OSError, IOError):
        return None


def create_plan(task, backend="local", connector_name=None, adapter="anthropic"):
    if not task or not task.strip():
        raise AgentError("task description is required")

    context_note = _recent_failures_note()
    if backend == "local":
        code = _generate_local(task, context_note=context_note)
    elif backend == "cloud":
        if not connector_name:
            raise AgentError("connector_name is required for backend='cloud' — "
                              "add one at /admin -> Connectors first")
        code = _generate_cloud(task, connector_name, adapter, context_note=context_note)
    else:
        raise AgentError("backend must be 'local' or 'cloud'")

    if not code.strip():
        raise AgentError("model returned no code")

    analysis = _analyze_safety(code)
    try:
        write_targets = _extract_file_targets(ast.parse(code))
    except SyntaxError:
        write_targets = []
    before_snapshots = {t["path"]: _snapshot(t["path"]) for t in write_targets}

    plan_id = uuid.uuid4().hex
    code_hash = _hash(code)
    now = time.time()

    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO plans (id, task, backend, code, code_hash, risk_level, "
            "flags, status, created_at, write_targets, before_snapshots) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (plan_id, task, backend, code, code_hash, analysis["risk_level"],
             json.dumps(analysis["flags"]), "pending", now,
             json.dumps(write_targets), json.dumps(before_snapshots)),
        )

    return {
        "plan_id": plan_id,
        "task": task,
        "backend": backend,
        "code": code,
        "risk_level": analysis["risk_level"],
        "flags": analysis["flags"],
        "expires_in_seconds": PLAN_EXPIRY_SECONDS,
        # Preview shown before the user taps Run - which files this plan
        # will touch, and what they currently contain (None = doesn't
        # exist yet, so it'd be a create rather than an overwrite).
        "write_targets": write_targets,
        "before_snapshots": before_snapshots,
    }


def _hash(code):
    import hashlib
    return hashlib.sha256(code.encode()).hexdigest()


# ---------------------------------------------------------------------
# Step 2: execute_plan / reject_plan — the only way generated code ever
# actually runs.
# ---------------------------------------------------------------------

def get_plan(plan_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    return dict(row) if row else None


def execute_plan(plan_id, ack_high_risk=False):
    plan = get_plan(plan_id)
    if not plan:
        raise AgentError("no such plan — it may have already been decided, or the app restarted")
    if plan["status"] != "pending":
        raise AgentError(f"this plan is already '{plan['status']}' — create a new one to run it again")
    if time.time() - plan["created_at"] > PLAN_EXPIRY_SECONDS:
        _mark(plan_id, "expired")
        raise AgentError("plan expired — review and create a new one (generated code isn't re-run blind after a long gap)")
    if plan["risk_level"] == "high" and not ack_high_risk:
        raise AgentError("this plan is flagged high-risk — resend with ack_high_risk=true after reviewing the flags to run it")

    result = scripts_runner.run_script(plan["code"], script_name=f"agent_{plan_id[:8]}.py")

    diffs = {}
    try:
        write_targets = json.loads(plan["write_targets"] or "[]")
        before_snapshots = json.loads(plan["before_snapshots"] or "{}")
    except (TypeError, ValueError):
        write_targets, before_snapshots = [], {}

    for t in write_targets:
        path = t["path"]
        before = before_snapshots.get(path)
        after = _snapshot(path)  # None if deleted or still doesn't exist
        before_lines = (before or "").splitlines(keepends=True)
        after_lines = (after or "").splitlines(keepends=True)
        diff_text = "".join(difflib.unified_diff(
            before_lines, after_lines, fromfile=f"{path} (before)", tofile=f"{path} (after)",
        ))
        if diff_text:
            diffs[path] = diff_text
        elif before is None and after is not None:
            diffs[path] = f"(created {path}, {len(after)} chars)"
        elif before is not None and after is None:
            diffs[path] = f"(deleted {path})"

    with _LOCK, _conn() as c:
        c.execute(
            "UPDATE plans SET status=?, decided_at=?, stdout=?, stderr=?, error=?, timed_out=?, diffs=? WHERE id=?",
            ("executed", time.time(), result["stdout"], result["stderr"],
             result["error"], int(result["timed_out"]), json.dumps(diffs), plan_id),
        )

    return {"plan_id": plan_id, "diffs": diffs, **result}


def schedule_plan(plan_id, interval_seconds, ack_high_risk=False):
    """Turn an approved-but-not-yet-run plan into a recurring scheduler job.

    Deliberately reuses the SAME plan/execute safety gate as execute_plan()
    (pending-only, not expired, high-risk needs ack) - the only difference
    is what happens after the gate passes: instead of running once and
    marking the plan 'executed', it hands the plan's already-reviewed code
    to scheduler.py and marks it 'scheduled'. Every tick after that re-runs
    the EXACT code a human looked at here - it is never re-generated by the
    LLM, so there's no way for a later run to silently do something
    different from what was approved.
    """
    if interval_seconds < 60:
        raise AgentError("minimum interval is 60 seconds - this isn't for sub-minute polling")

    plan = get_plan(plan_id)
    if not plan:
        raise AgentError("no such plan — it may have already been decided, or the app restarted")
    if plan["status"] != "pending":
        raise AgentError(f"this plan is already '{plan['status']}' — create a new one to schedule it")
    if time.time() - plan["created_at"] > PLAN_EXPIRY_SECONDS:
        _mark(plan_id, "expired")
        raise AgentError("plan expired — review and create a new one before scheduling")
    if plan["risk_level"] == "high" and not ack_high_risk:
        raise AgentError("this plan is flagged high-risk — resend with ack_high_risk=true after reviewing the flags to schedule it")

    import scheduler
    job_id = scheduler.create_job(
        name=f"agent_plan_{plan_id[:8]}",
        handler="agent_recurring_plan",
        interval_seconds=interval_seconds,
        params={"plan_id": plan_id},
    )
    _mark(plan_id, "scheduled")
    return {"plan_id": plan_id, "job_id": job_id, "interval_seconds": interval_seconds}


def _run_scheduled_plan(params):
    """scheduler.py's JOB_HANDLERS entry point - runs the exact code stored
    against params['plan_id']. Raises on failure so scheduler.py's own
    job_runs table (already built in Phase 1) records it - no separate
    history table needed, the agent piggybacks on the general one."""
    plan = get_plan(params["plan_id"])
    if not plan:
        raise AgentError(f"scheduled plan {params['plan_id']} no longer exists")
    result = scripts_runner.run_script(plan["code"], script_name=f"agent_{params['plan_id'][:8]}_recurring.py")
    if result.get("error") or result.get("timed_out"):
        raise AgentError(f"recurring run failed: {result.get('error') or 'timed out'}")


def reject_plan(plan_id):
    plan = get_plan(plan_id)
    if not plan:
        raise AgentError("no such plan")
    if plan["status"] != "pending":
        raise AgentError(f"plan is already '{plan['status']}'")
    _mark(plan_id, "rejected")
    return {"plan_id": plan_id, "status": "rejected"}


def _mark(plan_id, status):
    with _LOCK, _conn() as c:
        c.execute("UPDATE plans SET status=?, decided_at=? WHERE id=?", (status, time.time(), plan_id))


def list_history(limit=50):
    with _conn() as c:
        rows = c.execute(
            "SELECT id, task, backend, risk_level, status, created_at, decided_at "
            "FROM plans ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
