"""
executor.py - Phase 3: one shared "run this in a thread, with a wall-clock
timeout, and never let it crash or hang the caller" primitive.

Before this file existed, three modules each reinvented the same pattern
with slightly different bugs:
  - scripts_runner.py  had a real thread+timeout runner (_Runner), but only
                        for agent/script code - the good version.
  - scheduler.py        called `handler(params)` directly in the tick
                         thread with NO timeout at all - a hanging job
                         handler stalls every future job forever.
  - watcher.py           called `handler(path)` directly in the scan loop,
                          also with NO timeout - a hanging watcher handler
                          stalls every future scan forever.

Both of those are real bugs (a slow plugin-registered handler freezes
background automation silently, with no error and no timeout to point at)
that plain code review over the split files wouldn't surface - they only
show up if you look at all three call sites side by side, which is the
actual point of Phase 3.

Usage:
    result = executor.run(fn, args=(...), kwargs={...}, timeout=10, name="thing")
    if result.timed_out:
        ...
    elif result.error:
        ...
    else:
        use result.value
"""

import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ExecResult:
    ok: bool
    value: Any = None
    error: Optional[str] = None
    timed_out: bool = False
    elapsed_seconds: float = 0.0
    name: str = ""


class _Worker(threading.Thread):
    def __init__(self, fn, args, kwargs):
        super().__init__(daemon=True)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.value = None
        self.error = None

    def run(self):
        try:
            self.value = self.fn(*self.args, **self.kwargs)
        except BaseException:
            self.error = traceback.format_exc()


def run(fn, args=(), kwargs=None, timeout=30, name="job"):
    """Runs fn(*args, **kwargs) in its own daemon thread with a wall-clock
    timeout. Never raises - always returns an ExecResult, so callers (the
    scheduler tick loop, the watcher scan loop, a route handler) can keep
    going even if the thing they ran hung or crashed.

    Note on timeouts: a timed-out worker thread is NOT killed (Python has
    no safe way to kill a thread) - it's abandoned as a daemon thread and
    will die with the process. This matches scripts_runner.py's original
    behavior; it's a real limitation, not silently swept under the rug.
    """
    try:
        timeout = max(1.0, min(300.0, float(timeout)))
    except (TypeError, ValueError):
        timeout = 30.0

    worker = _Worker(fn, args, kwargs or {})
    start = time.time()
    worker.start()
    worker.join(timeout)
    elapsed = round(time.time() - start, 3)

    if worker.is_alive():
        return ExecResult(ok=False, timed_out=True, elapsed_seconds=elapsed, name=name)
    if worker.error:
        return ExecResult(ok=False, error=worker.error, elapsed_seconds=elapsed, name=name)
    return ExecResult(ok=True, value=worker.value, elapsed_seconds=elapsed, name=name)
