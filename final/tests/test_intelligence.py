import os
import sys

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import pytest  # noqa: E402

import intelligence  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_health(tmp_path):
    intelligence.init(str(tmp_path))
    intelligence._HEALTH.clear()
    yield
    intelligence._HEALTH.clear()


def test_run_returns_result_on_first_success():
    result = intelligence.run("test:ok", lambda: 42)
    assert result == 42
    h = intelligence.health("test:ok")
    assert h["successes"] == 1
    assert h["failures"] == 0
    assert h["score"] == 100


def test_run_retries_then_succeeds():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("temporary")
        return "recovered"

    result = intelligence.run("test:flaky", flaky, attempts=3, backoff_seconds=0.01)
    assert result == "recovered"
    assert calls["n"] == 2
    h = intelligence.health("test:flaky")
    assert h["failures"] == 1
    assert h["successes"] == 1


def test_run_falls_back_to_second_option():
    def always_fails():
        raise RuntimeError("primary broken")

    def fallback_works():
        return "fallback result"

    result = intelligence.run(
        "test:fallback", always_fails, fallbacks=[fallback_works], attempts=1, backoff_seconds=0.01
    )
    assert result == "fallback result"


def test_run_raises_last_exception_when_everything_fails():
    def fails_a():
        raise ValueError("a broken")

    def fails_b():
        raise ValueError("b broken")

    with pytest.raises(ValueError, match="b broken"):
        intelligence.run("test:all-fail", fails_a, fallbacks=[fails_b], attempts=1, backoff_seconds=0.01)

    h = intelligence.health("test:all-fail")
    assert h["failures"] == 2
    assert h["score"] == 0


def test_resilient_decorator():
    calls = {"n": 0}

    @intelligence.resilient("test:decorated", attempts=2, backoff_seconds=0.01)
    def sometimes_fails(x):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first try fails")
        return x * 2

    assert sometimes_fails(5) == 10
    assert calls["n"] == 2


def test_degraded_capabilities_flags_low_score():
    for _ in range(3):
        try:
            intelligence.run("test:bad", lambda: (_ for _ in ()).throw(RuntimeError("x")), attempts=1, backoff_seconds=0.01)
        except RuntimeError:
            pass
    degraded = intelligence.degraded_capabilities(threshold=50)
    names = [d["capability"] for d in degraded]
    assert "test:bad" in names


def test_reset_clears_history():
    intelligence.run("test:to-reset", lambda: 1)
    assert intelligence.health("test:to-reset")["attempts"] == 1
    intelligence.reset("test:to-reset")
    assert intelligence.health("test:to-reset")["attempts"] == 0


def test_health_all_returns_sorted_list():
    intelligence.run("test:b", lambda: 1)
    intelligence.run("test:a", lambda: 1)
    caps = [h["capability"] for h in intelligence.health() if h["capability"].startswith("test:")]
    assert caps.index("test:a") < caps.index("test:b")
