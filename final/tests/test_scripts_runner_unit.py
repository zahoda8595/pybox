"""Unit tests directly against scripts_runner.py - these call the module
functions straight, not through Flask, specifically so timeout values
can be set low (e.g. 0.5s) without slowing down the whole test suite the
way testing the real default (30s) through the API would."""

import os
import sys

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import scripts_runner  # noqa: E402


def test_run_script_short_timeout_reports_timed_out(tmp_path):
    scripts_runner.init(str(tmp_path))
    result = scripts_runner.run_script(
        "import time; time.sleep(3)", script_name="slow.py", timeout=0.5
    )
    assert result["timed_out"] is True
    assert result["elapsed_seconds"] < 2  # joined at ~0.5s, didn't wait for the sleep


def test_run_script_stream_short_timeout(tmp_path):
    scripts_runner.init(str(tmp_path))
    events = list(scripts_runner.run_script_stream(
        "import time; time.sleep(3)", script_name="slow.py", timeout=0.5
    ))
    kinds = [k for k, _ in events]
    assert "timeout" in kinds


def test_search_scripts_matches_filename_and_content(tmp_path):
    scripts_runner.init(str(tmp_path))
    scripts_runner.write_script("finder.py", "# looks for treasure\nprint('x')")
    hits = scripts_runner.search_scripts("treasure")
    assert any(h["name"] == "finder.py" for h in hits)

    hits_by_name = scripts_runner.search_scripts("finder")
    assert any(h["name"] == "finder.py" for h in hits_by_name)

    hits_none = scripts_runner.search_scripts("nonexistent-term-xyz")
    assert hits_none == []


def test_output_is_capped(tmp_path):
    scripts_runner.init(str(tmp_path))
    scripts_runner.OUTPUT_CAP = 100
    try:
        result = scripts_runner.run_script("print('x' * 10000)", timeout=5)
        assert len(result["stdout"]) <= 100
    finally:
        scripts_runner.OUTPUT_CAP = 200_000
