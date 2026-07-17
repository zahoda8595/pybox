"""
appstate.py - single source of truth for the small bits of process-wide
mutable state that used to live as bare module-level globals inside the
2,752-line backend_app.py monolith.

Every blueprint module in routes_*.py imports this module (never the bare
names) so a rebind like ``appstate.FILES_DIR = files_dir`` in create_app()
is visible everywhere immediately, exactly like the old ``global FILES_DIR``
pattern did within a single file - just spread across files now.

Mutable containers (PLUGIN_ROUTES, SEARCH_JOBS, the lock) are safe to import
by name with ``from appstate import PLUGIN_ROUTES`` since they are mutated
in place, never reassigned wholesale. FILES_DIR *is* reassigned wholesale
(once, in create_app), so every reader uses the qualified ``appstate.FILES_DIR``
form instead - grep for it if you're ever unsure which pattern a given name
needs.
"""

import threading

# Set once by create_app(files_dir, ...) in backend_app.py. Always read this
# as `appstate.FILES_DIR` from other modules - never `from appstate import
# FILES_DIR`, which would freeze a stale None at import time.
FILES_DIR = None

# Where LlamaEngineService.kt binds the compiled llama-server process.
LLM_BASE_URL = "http://127.0.0.1:8081"

# Registry plugins drop a route handler into by name; shared between
# plugin_loader.init() (writer) and routes_plugins.plugin_dispatch (reader).
PLUGIN_ROUTES = {}

# In-memory registry of background /search/deep jobs. Deliberately NOT
# persisted to SQLite - a restart clears any in-flight search, which is
# fine since it isn't durable automation like scheduler.py's jobs.
SEARCH_JOBS = {}
SEARCH_JOBS_LOCK = threading.Lock()
