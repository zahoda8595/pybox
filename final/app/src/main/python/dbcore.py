"""
dbcore.py — shared SQLite connection pooling + hardening (Phase 1).

THE PROBLEM THIS FIXES:
  Every db-backed module (contacts, scheduler, watcher, agent, browser,
  usage_stats) used to do `sqlite3.connect(path)` on every single call —
  every CRUD op, every 5-second scheduler tick, every 10-second watcher
  scan. That's an OS-level open/close paid dozens of times a minute,
  with no WAL, no busy timeout, and no reuse. Harmless when Flask's dev
  server was single-threaded (only one thing ever touched a DB at a
  time); a real bottleneck the moment `threaded=True` lets requests run
  concurrently — concurrent writers on a non-WAL SQLite file serialize
  hard and throw "database is locked" under load.

WHAT THIS MODULE GIVES EVERY OTHER MODULE, FOR FREE:
  - WAL mode + synchronous=NORMAL, set once per .db file the first time
    it's touched (readers no longer block writers).
  - A small real connection pool per .db file, so "get a connection" is
    borrowing an already-open handle instead of opening a new one.
  - busy_timeout=30s on every pooled connection, so a request that DOES
    collide with a writer waits briefly instead of throwing.
  - Existing call sites do not change at all. Every module still does
    `conn = _conn(); ...; conn.close()` — .close() now returns the
    connection to the pool instead of tearing it down. Every module
    that instead does `with _conn() as c: ...` (agent.py's pattern)
    also now gets the connection returned to the pool automatically on
    exit — sqlite3's own context manager only commits/rolls back, it
    never closes, which is what caused a real unbounded-connection leak
    in agent.py before this module existed. Both patterns now work
    correctly with zero call-site changes.
  - ensure_indexes(), so a module's init() can declare its indexes in
    one line instead of a wall of CREATE INDEX statements.

USAGE (per module):
    import dbcore

    def _conn():
        return dbcore.get_connection(_DB_PATH)

    def init(files_dir):
        global _DB_PATH
        _DB_PATH = os.path.join(files_dir, "whatever.db")
        conn = _conn()
        conn.execute("CREATE TABLE IF NOT EXISTS ...")
        dbcore.ensure_indexes(conn, "table_name", [
            ("idx_table_created_at", "created_at"),
        ])
        conn.commit()
        conn.close()
"""

import logging
import queue
import sqlite3
import threading

_pools = {}
_pools_lock = threading.Lock()
_wal_ready = set()
_wal_lock = threading.Lock()

POOL_SIZE = 4
BORROW_TIMEOUT_SECONDS = 30


class PooledConnection(sqlite3.Connection):
    """Drop-in sqlite3.Connection. The only behavior change: close()
    returns the connection to its pool instead of actually closing it,
    and using the connection as a context manager (`with conn as c:`)
    both commits/rolls back *and* returns it to the pool on exit."""

    _pool = None

    def close(self):
        pool = self._pool
        if pool is None:
            super().close()
            return
        try:
            if self.in_transaction:
                self.rollback()
        except Exception:
            # connection is in a bad state (e.g. underlying file issue) -
            # don't recycle something broken back into the pool
            try:
                super().close()
            except Exception:
                pass
            return
        pool.put(self)

    def __exit__(self, exc_type, exc_val, exc_tb):
        result = super().__exit__(exc_type, exc_val, exc_tb)
        self.close()
        return result

    def shutdown(self):
        """Actually close the underlying handle. Only used when tearing
        a pool down entirely (tests / process exit) — normal request
        handling never needs this."""
        super().close()


def _ensure_wal(db_path):
    if db_path in _wal_ready:
        return
    with _wal_lock:
        if db_path in _wal_ready:
            return
        conn = sqlite3.connect(db_path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        finally:
            conn.close()
        _wal_ready.add(db_path)


def _make_connection(db_path, pool):
    conn = sqlite3.connect(
        db_path, check_same_thread=False, timeout=30, factory=PooledConnection
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn._pool = pool
    return conn


def get_connection(db_path):
    """Borrow a pooled connection for db_path. Same surface as
    sqlite3.connect(db_path).row_factory=sqlite3.Row to every caller."""
    with _pools_lock:
        pool = _pools.get(db_path)
        if pool is None:
            _ensure_wal(db_path)
            pool = queue.Queue()
            for _ in range(POOL_SIZE):
                pool.put(_make_connection(db_path, pool))
            _pools[db_path] = pool

    try:
        return pool.get(timeout=BORROW_TIMEOUT_SECONDS)
    except queue.Empty:
        # Every pooled connection is checked out at once (unusually high
        # concurrency, or a leak somewhere upstream). Rather than block
        # the request indefinitely, open a one-off overflow connection —
        # WAL is already enabled at the file level, so this is still
        # correct, just not reused afterward.
        logging.warning("dbcore: pool exhausted for %s, opening overflow connection", db_path)
        return _make_connection(db_path, pool)


def ensure_indexes(conn, table, index_specs):
    """index_specs: iterable of (index_name, column_expression) tuples."""
    for name, cols in index_specs:
        conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({cols})")


def pool_stats():
    """Diagnostic snapshot for an admin/health endpoint: how many
    connections are currently idle in each pool."""
    with _pools_lock:
        return {path: pool.qsize() for path, pool in _pools.items()}


def shutdown_all():
    """Actually close every pooled connection. For clean process exit
    or test teardown — normal request handling never calls this."""
    with _pools_lock:
        for pool in _pools.values():
            while True:
                try:
                    pool.get_nowait().shutdown()
                except queue.Empty:
                    break
        _pools.clear()
    with _wal_lock:
        _wal_ready.clear()
