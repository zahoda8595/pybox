"""
scripts_runner.py — backend for the /scripts page: a small in-app Python
IDE. Lets Khan write, save, and run one-off or reusable Python scripts
directly on the phone, no Termux/adb round trip needed.

STORAGE:
  Scripts are plain .py files under FILES_DIR/scripts/ (app-private
  storage - survives restarts, not visible to other apps).

EXECUTION:
  Chaquopy embeds CPython IN the app's own process - there's no separate
  `python` binary on the device to subprocess.run(), so scripts run via
  exec() inside a short-lived thread of this same interpreter, with:
    - stdout/stderr captured and returned to the page (not lost to logcat)
    - a wall-clock timeout (config: scripts_timeout_seconds, default 30s)
      enforced by joining the thread with a deadline - if it's still
      running past that, the run is reported as timed out. The thread
      itself is daemonized so a runaway script can't block the app.
    - the full exception + traceback captured and returned instead of
      crashing the backend, same "isolate, don't take the whole app
      down" philosophy as error_manager.safe_route elsewhere.
    - each run gets its own fresh globals dict (import fully allowed -
      this is Khan's own device, his own sandboxed app storage, same
      trust level as Termux or Pydroid) with __name__ set to "__main__"
      so `if __name__ == "__main__":` scripts behave normally.
  Output is capped (OUTPUT_CAP chars) so a runaway print loop can't blow
  up the response payload.
"""

import contextlib
import io
import os
import queue
import threading
import time
import traceback

import config
import error_manager
import executor

FILES_DIR = None
SCRIPTS_DIR = None
OUTPUT_CAP = 200_000


def init(files_dir):
    global FILES_DIR, SCRIPTS_DIR
    FILES_DIR = files_dir
    SCRIPTS_DIR = os.path.join(files_dir, "scripts")
    os.makedirs(SCRIPTS_DIR, exist_ok=True)


def _safe_name(name):
    """Rejects path traversal / non-.py names - same pattern used for
    plugin filenames in backend_app.py's admin_plugins_save."""
    if not name or not name.endswith(".py"):
        return None
    if "/" in name or "\\" in name or ".." in name:
        return None
    return name


def list_scripts():
    if not SCRIPTS_DIR or not os.path.isdir(SCRIPTS_DIR):
        return []
    out = []
    for fname in sorted(os.listdir(SCRIPTS_DIR)):
        if fname.endswith(".py"):
            path = os.path.join(SCRIPTS_DIR, fname)
            try:
                stat = os.stat(path)
                out.append({
                    "name": fname,
                    "size": stat.st_size,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                })
            except OSError:
                continue
    return out


def read_script(name):
    safe = _safe_name(name)
    if not safe:
        return None
    path = os.path.join(SCRIPTS_DIR, safe)
    if not os.path.isfile(path):
        return None
    with open(path, "r") as f:
        return f.read()


def write_script(name, code):
    safe = _safe_name(name)
    if not safe:
        raise ValueError("invalid script name - must end in .py with no slashes")
    path = os.path.join(SCRIPTS_DIR, safe)
    with open(path, "w") as f:
        f.write(code)
    return safe


def delete_script(name):
    safe = _safe_name(name)
    if not safe:
        raise ValueError("invalid script name")
    path = os.path.join(SCRIPTS_DIR, safe)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def search_scripts(query):
    """Filename + content substring search across saved scripts, used by
    the /search global-search page. Returns a snippet of the first
    matching line so results are scannable without opening the file."""
    if not query or not SCRIPTS_DIR or not os.path.isdir(SCRIPTS_DIR):
        return []
    q = query.lower()
    out = []
    for fname in sorted(os.listdir(SCRIPTS_DIR)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(SCRIPTS_DIR, fname)
        name_hit = q in fname.lower()
        snippet = None
        try:
            with open(path, "r", errors="ignore") as f:
                for line_no, line in enumerate(f, start=1):
                    if q in line.lower():
                        snippet = f"L{line_no}: {line.strip()[:120]}"
                        break
        except OSError:
            continue
        if name_hit or snippet:
            out.append({"name": fname, "snippet": snippet or "(name match)"})
    return out



def _exec_capturing(code, script_name):
    """Runs inside executor.run()'s worker thread. Returns (stdout, stderr)
    and lets any exception propagate - executor.run() turns that into
    result.error with the traceback, same as _Runner did before."""
    out, err = io.StringIO(), io.StringIO()
    g = {"__name__": "__main__", "__file__": script_name}
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            compiled = compile(code, script_name, "exec")
            exec(compiled, g)
    except BaseException as e:
        # Logged here (inside the worker thread, right at the exception)
        # so traceback.format_exc() inside error_manager.log_error still
        # has valid exception context - same reasoning as the original
        # _Runner had, just relocated into this closure.
        try:
            error_manager.log_error(f"script:{script_name}", e)
        except Exception:
            pass
        raise
    return out.getvalue(), err.getvalue()


def run_script(code, script_name="script.py", timeout=None):
    """Runs `code` in its own thread with a wall-clock timeout (via
    executor.run - the same primitive scheduler.py and watcher.py use).
    Returns a dict with stdout/stderr/error/timed_out/elapsed_seconds -
    never raises, so the route can always return a normal 200 with
    results."""
    if timeout is None:
        timeout = config.get("scripts_timeout_seconds", 30)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 30.0
    timeout = max(1.0, min(120.0, timeout))

    result = executor.run(
        _exec_capturing, args=(code, script_name or "<script>"),
        timeout=timeout, name=script_name or "<script>",
    )

    out, err = result.value if result.ok else ("", "")
    return {
        "stdout": (out or "")[:OUTPUT_CAP],
        "stderr": (err or "")[:OUTPUT_CAP],
        "error": result.error,
        "timed_out": result.timed_out,
        "elapsed_seconds": result.elapsed_seconds,
    }


class _QueueWriter(io.TextIOBase):
    """A file-like object that pushes every write() onto a queue instead
    of buffering it - this is what makes streaming possible. Used in
    place of io.StringIO for run_script_stream()."""

    def __init__(self, q, kind):
        self.q = q
        self.kind = kind

    def write(self, s):
        if s:
            self.q.put((self.kind, s))
        return len(s)

    def flush(self):
        pass


class _StreamRunner(threading.Thread):
    def __init__(self, code, script_name, q):
        super().__init__(daemon=True)
        self.code = code
        self.script_name = script_name or "<script>"
        self.q = q
        self.error = None

    def run(self):
        g = {"__name__": "__main__", "__file__": self.script_name}
        out_w = _QueueWriter(self.q, "stdout")
        err_w = _QueueWriter(self.q, "stderr")
        try:
            with contextlib.redirect_stdout(out_w), contextlib.redirect_stderr(err_w):
                compiled = compile(self.code, self.script_name, "exec")
                exec(compiled, g)
        except BaseException as e:
            self.error = traceback.format_exc()
            try:
                error_manager.log_error(f"script:{self.script_name}", e)
            except Exception:
                pass
        finally:
            self.q.put(("done", self.error))


def run_script_stream(code, script_name="script.py", timeout=None):
    """Generator version of run_script() for the /scripts/api/run_stream
    SSE-style route: yields (kind, text) tuples as the script produces
    them - "stdout"/"stderr" chunks live, then a final "done" event with
    the error text (or None) and a "timeout" event if the deadline hits
    before the script finished. The underlying thread is still a daemon,
    so a script that never returns just keeps running in the background
    after the timeout fires - same tradeoff as run_script(), just visible
    to the caller as it happens instead of only at the end."""
    if timeout is None:
        timeout = config.get("scripts_timeout_seconds", 30)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 30.0
    timeout = max(1.0, min(120.0, timeout))

    q = queue.Queue()
    runner = _StreamRunner(code, script_name, q)
    start = time.time()
    runner.start()

    sent_bytes = 0
    while True:
        remaining = timeout - (time.time() - start)
        if remaining <= 0:
            yield ("timeout", f"Timed out after {timeout:.0f}s (may still be running in background)")
            return
        try:
            kind, payload = q.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if kind == "done":
            yield ("done", payload)
            return
        sent_bytes += len(payload)
        if sent_bytes > OUTPUT_CAP:
            yield (kind, payload[:200])
            yield ("done", "(output cap reached - script may still be running, truncating stream)")
            return
        yield (kind, payload)
