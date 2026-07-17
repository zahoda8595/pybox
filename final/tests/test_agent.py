"""Tests for agent.py — the plan-then-execute AI agent.

Covers three things the whole feature depends on:
  1. The safety scanner correctly buckets obviously-benign vs
     obviously-dangerous code (unit tests, no Flask, no LLM).
  2. create_plan() never executes anything — only execute_plan() does,
     and only against a plan_id that actually exists.
  3. The high-risk plans require ack_high_risk=True; low/medium ones
     don't.

The LLM call itself is monkeypatched (agent._generate_local /
_generate_cloud) rather than hitting a real local llama-server or a
real cloud API, so this suite runs the same on a laptop/CI as it does
against the real backend.
"""

import os
import sys

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import agent  # noqa: E402
import config  # noqa: E402
import scripts_runner  # noqa: E402


def _init_all(tmp_path):
    """agent.execute_plan() delegates to scripts_runner.run_script(), which
    reads its timeout default from config — so, same as backend_app
    .create_app() does for the real server, tests that actually execute a
    plan need all three initialized against the same FILES_DIR, not just
    agent.init() on its own."""
    d = str(tmp_path)
    config.init(d)
    scripts_runner.init(d)
    agent.init(d)


def test_extract_code_pulls_fenced_python_block():
    resp = "Here's the script:\n```python\nprint('hi')\n```\nLet me know if you want changes."
    assert agent._extract_code(resp) == "print('hi')"


def test_extract_code_falls_back_to_raw_text_when_unfenced():
    assert agent._extract_code("print('hi')") == "print('hi')"


def test_analyze_safety_benign_code_is_low_risk():
    result = agent._analyze_safety("print('hello')\nx = 1 + 1\n")
    assert result["risk_level"] == "low"
    assert result["flags"] == []


def test_analyze_safety_flags_shell_and_delete_as_high_risk():
    code = "import os\nos.system('rm -rf /')\nos.remove('/tmp/x')\n"
    result = agent._analyze_safety(code)
    assert result["risk_level"] == "high"
    messages = " ".join(f["message"] for f in result["flags"])
    assert "shell command" in messages
    assert "Deletes" in messages


def test_analyze_safety_flags_sensitive_import_as_medium():
    code = "import contacts\ncontacts.list_contacts()\n"
    result = agent._analyze_safety(code)
    assert result["risk_level"] == "medium"


def test_analyze_safety_flags_network_call_as_low():
    code = "import requests\nrequests.get('http://example.com')\n"
    result = agent._analyze_safety(code)
    assert result["risk_level"] == "low"
    assert any("network" in f["message"] for f in result["flags"])


def test_analyze_safety_handles_syntax_error_as_high_risk():
    result = agent._analyze_safety("def broken(:\n")
    assert result["risk_level"] == "high"


def test_create_plan_does_not_execute_anything(tmp_path, monkeypatch):
    agent.init(str(tmp_path))
    monkeypatch.setattr(agent, "_generate_local", lambda task, timeout=120: "print('side effect')")

    plan = agent.create_plan("say hi", backend="local")

    assert plan["risk_level"] == "low"
    stored = agent.get_plan(plan["plan_id"])
    assert stored["status"] == "pending"
    assert stored["stdout"] is None  # never ran


def test_execute_plan_runs_the_exact_generated_code(tmp_path, monkeypatch):
    _init_all(tmp_path)
    monkeypatch.setattr(agent, "_generate_local", lambda task, timeout=120: "print('ran for real')")

    plan = agent.create_plan("say hi", backend="local")
    result = agent.execute_plan(plan["plan_id"])

    assert "ran for real" in result["stdout"]
    stored = agent.get_plan(plan["plan_id"])
    assert stored["status"] == "executed"


def test_execute_plan_rejects_unknown_plan_id(tmp_path):
    agent.init(str(tmp_path))
    try:
        agent.execute_plan("does-not-exist")
        assert False, "should have raised"
    except agent.AgentError:
        pass


def test_execute_plan_cannot_run_twice(tmp_path, monkeypatch):
    _init_all(tmp_path)
    monkeypatch.setattr(agent, "_generate_local", lambda task, timeout=120: "print('once')")
    plan = agent.create_plan("say hi", backend="local")
    agent.execute_plan(plan["plan_id"])
    try:
        agent.execute_plan(plan["plan_id"])
        assert False, "should have raised on second execution"
    except agent.AgentError as e:
        assert "already" in str(e)


def test_high_risk_plan_requires_explicit_ack(tmp_path, monkeypatch):
    _init_all(tmp_path)
    monkeypatch.setattr(
        agent, "_generate_local",
        lambda task, timeout=120: "import os\nos.system('echo hi')\nprint('dangerous ran')\n",
    )
    plan = agent.create_plan("do something risky", backend="local")
    assert plan["risk_level"] == "high"

    try:
        agent.execute_plan(plan["plan_id"])
        assert False, "should have refused without ack_high_risk"
    except agent.AgentError as e:
        assert "high-risk" in str(e)

    result = agent.execute_plan(plan["plan_id"], ack_high_risk=True)
    assert "dangerous ran" in result["stdout"]


def test_reject_plan_marks_rejected_and_blocks_execution(tmp_path, monkeypatch):
    agent.init(str(tmp_path))
    monkeypatch.setattr(agent, "_generate_local", lambda task, timeout=120: "print('x')")
    plan = agent.create_plan("say hi", backend="local")

    agent.reject_plan(plan["plan_id"])
    stored = agent.get_plan(plan["plan_id"])
    assert stored["status"] == "rejected"

    try:
        agent.execute_plan(plan["plan_id"])
        assert False, "should not be able to execute a rejected plan"
    except agent.AgentError:
        pass


def test_expired_plan_cannot_be_executed(tmp_path, monkeypatch):
    agent.init(str(tmp_path))
    monkeypatch.setattr(agent, "_generate_local", lambda task, timeout=120: "print('x')")
    monkeypatch.setattr(agent, "PLAN_EXPIRY_SECONDS", 0)
    plan = agent.create_plan("say hi", backend="local")

    import time
    time.sleep(0.05)
    try:
        agent.execute_plan(plan["plan_id"])
        assert False, "should have refused an expired plan"
    except agent.AgentError as e:
        assert "expired" in str(e)


def test_list_history_returns_most_recent_first(tmp_path, monkeypatch):
    agent.init(str(tmp_path))
    monkeypatch.setattr(agent, "_generate_local", lambda task, timeout=120: "print('x')")
    agent.create_plan("first task", backend="local")
    agent.create_plan("second task", backend="local")

    history = agent.list_history()
    assert history[0]["task"] == "second task"
    assert history[1]["task"] == "first task"


def test_create_plan_requires_nonempty_task(tmp_path):
    agent.init(str(tmp_path))
    try:
        agent.create_plan("   ", backend="local")
        assert False, "should have raised"
    except agent.AgentError:
        pass


def test_create_plan_cloud_requires_connector_name(tmp_path):
    agent.init(str(tmp_path))
    try:
        agent.create_plan("do a thing", backend="cloud")
        assert False, "should have raised"
    except agent.AgentError as e:
        assert "connector_name" in str(e)
