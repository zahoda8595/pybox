"""
intelligence.py — PyBox's shared self-healing layer.

WHAT THIS IS, HONESTLY:
  Not a model, not "AI" in the learning sense - it's a retry/fallback
  engine with memory. It gives every risky operation (a web fetch, an
  external API call) a consistent way to: try, fail, remember *why* it
  failed, try again with backoff, and if a list of alternate strategies
  was given, fall over to the next one instead of just repeating the
  same failing thing. That's what "self-healing" means here: known
  failure patterns get worked around automatically instead of surfacing
  as a dead end every time.

  It's built this way on purpose: an actual learned model would need
  training data this device doesn't have and a footprint that doesn't
  fit "runs entirely offline on a phone". A deterministic, inspectable
  retry/health engine is something that can be reasoned about, tested,
  and — per its own name — upgraded later (swap in smarter strategy
  selection, add weighting, whatever) without changing how callers use
  it, since the public surface is just run() and health().

WHAT GETS TRACKED, PER "capability" (a free-form string you choose, e.g.
"scrape:example.com" or "connector:openai"):
  - attempts, successes, failures, consecutive_failures
  - last_error (type + message, not full traceback - kept short for the
    admin dashboard)
  - last_success_at / last_failure_at
  - a rolling health score (successes / attempts over the last N
    outcomes) used to flag a capability as "degraded" in the admin UI.

PERSISTENCE:
  In-memory during the process, plus every outcome appended to
  intelligence.jsonl (same append-only pattern as error_manager's
  errors.jsonl) so history survives a restart and is inspectable.

HOW TO USE:
    import intelligence

    result = intelligence.run("scrape:" + domain, fetch_fn,
                               fallbacks=[fetch_with_different_ua],
                               attempts=3)

  fetch_fn and each entry in fallbacks are zero-arg callables (use a
  lambda/functools.partial to bind arguments) - run() calls fetch_fn
  first; if it raises, retries it (with backoff) up to `attempts` times,
  then moves to the next fallback and repeats the same attempts budget
  for that one, until something succeeds or every option is exhausted.
  Raises the LAST exception seen if everything fails, so callers can
  still handle it normally with try/except.
"""

import functools
import json
import logging
import os
import time

_FILES_DIR = None
_HEALTH = {}  # capability -> {attempts, successes, failures, consecutive_failures, last_error, last_success_at, last_failure_at, recent}
_ROLLING_WINDOW = 20
_LOCK_FREE_NOTE = "single-threaded-per-capability access assumed; Flask dev server is single-process"


def init(files_dir):
    global _FILES_DIR
    _FILES_DIR = files_dir


def _log_path():
    return os.path.join(_FILES_DIR or ".", "intelligence.jsonl")


def _record(capability, outcome, detail=None):
    """Appends one line to intelligence.jsonl. Never raises - logging a
    retry attempt must not itself become a new failure to handle."""
    try:
        with open(_log_path(), "a") as f:
            f.write(json.dumps({
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "capability": capability,
                "outcome": outcome,
                "detail": detail,
            }) + "\n")
    except Exception:
        pass


def _bucket(capability):
    if capability not in _HEALTH:
        _HEALTH[capability] = {
            "attempts": 0, "successes": 0, "failures": 0,
            "consecutive_failures": 0, "last_error": None,
            "last_success_at": None, "last_failure_at": None,
            "recent": [],  # list of True/False, most recent last, capped at _ROLLING_WINDOW
        }
    return _HEALTH[capability]


def _note_success(capability):
    b = _bucket(capability)
    b["attempts"] += 1
    b["successes"] += 1
    b["consecutive_failures"] = 0
    b["last_success_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    b["recent"].append(True)
    b["recent"] = b["recent"][-_ROLLING_WINDOW:]
    _record(capability, "success")


def _note_failure(capability, exc):
    b = _bucket(capability)
    b["attempts"] += 1
    b["failures"] += 1
    b["consecutive_failures"] += 1
    b["last_error"] = f"{type(exc).__name__}: {exc}"
    b["last_failure_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    b["recent"].append(False)
    b["recent"] = b["recent"][-_ROLLING_WINDOW:]
    _record(capability, "failure", b["last_error"])


def run(capability, fn, fallbacks=None, attempts=3, backoff_seconds=0.5, backoff_multiplier=2.0):
    """Runs fn() with retry-with-backoff; on exhausting `attempts`, moves
    to the next callable in `fallbacks` (if any) and repeats. Every
    attempt (success or failure) updates health(capability). Re-raises
    the final exception if every option is exhausted, so this composes
    normally with the caller's own try/except."""
    options = [fn] + list(fallbacks or [])
    last_exc = None

    for idx, option in enumerate(options):
        delay = backoff_seconds
        for attempt_no in range(1, attempts + 1):
            try:
                result = option()
                _note_success(capability)
                return result
            except Exception as e:
                last_exc = e
                _note_failure(capability, e)
                is_last_attempt_for_option = attempt_no == attempts
                is_last_option = idx == len(options) - 1
                if is_last_attempt_for_option and is_last_option:
                    break  # nothing left to try - fall through to raise below
                if not is_last_attempt_for_option:
                    logging.info(
                        "intelligence: %s attempt %d/%d failed (%s), retrying in %.1fs",
                        capability, attempt_no, attempts, e, delay,
                    )
                    time.sleep(delay)
                    delay *= backoff_multiplier
                else:
                    logging.info(
                        "intelligence: %s exhausted %d attempts, falling back to option %d/%d",
                        capability, attempts, idx + 2, len(options),
                    )

    raise last_exc


def resilient(capability, fallbacks=None, attempts=3, backoff_seconds=0.5):
    """Decorator form of run() for a function that always represents the
    same capability - wraps the call so callers don't need to pass a
    zero-arg lambda themselves:

        @intelligence.resilient("connector:openai")
        def call_openai(prompt):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return run(
                capability,
                lambda: fn(*args, **kwargs),
                fallbacks=fallbacks,
                attempts=attempts,
                backoff_seconds=backoff_seconds,
            )
        return wrapper
    return decorator


def health(capability=None):
    """Returns the health bucket for one capability, or all of them if
    capability is None. Includes a 0-100 rolling health score."""
    def with_score(cap, b):
        recent = b["recent"]
        score = round(100 * sum(recent) / len(recent)) if recent else None
        return {**b, "capability": cap, "score": score}

    if capability is not None:
        return with_score(capability, _bucket(capability))
    return [with_score(cap, b) for cap, b in sorted(_HEALTH.items())]


def degraded_capabilities(threshold=50):
    """Capabilities whose rolling score has dropped below `threshold` -
    used by the admin dashboard to flag what needs attention."""
    return [h for h in health() if h["score"] is not None and h["score"] < threshold]


def reset(capability):
    """Clears tracked history for one capability - e.g. after you've
    fixed whatever was causing repeated failures and don't want stale
    numbers colouring the dashboard."""
    _HEALTH.pop(capability, None)
