"""
config.py — simple persisted key-value settings, editable from the admin panel.

Stored as plain JSON in FILES_DIR/config.json (app-private storage). Not a
database, not for large data - just small settings/customization values
(feature toggles, display preferences, plugin config) that should survive
an app restart and be editable without touching code.

CACHING (Phase 1 hardening):
  theme.current() (and therefore _read()) used to hit disk on literally
  every page load, every scheduler tick's config.get("automation_enabled"),
  and every other config.get() call anywhere in the app - a stat+open+
  json.load paid dozens of times a minute for a file that almost never
  changes. _read() now keeps an in-memory copy and only re-reads the
  file when its mtime moves (i.e. something actually called set()/
  set_many(), whether from this process or - if ever inspected/edited by
  hand - another one), so repeat calls in between are pure memory reads.
"""

import json
import os
import threading

_PATH = None
_LOCK = threading.Lock()
_CACHE = None
_CACHE_MTIME = None
_DEFAULTS = {
    "theme": "dark",
    "automation_enabled": True,
    "admin_panel_title": "PyBox Admin",
    # --- UI theme (see theme.py - editable live from /settings, no rebuild) ---
    "theme_preset": "dark",
    "theme_bg": "#0d0d0d",
    "theme_card_bg": "#1a1a1a",
    "theme_border": "#2a2a2a",
    "theme_accent": "#2e7d4f",
    "theme_link": "#7ec8f2",
    "theme_text": "#e8e8e8",
    "theme_muted": "#888888",
    "theme_radius": "12",
    "theme_font_scale": "100",
    # --- Python script runner (see scripts_runner.py) ---
    "scripts_timeout_seconds": 30,
}


def init(files_dir):
    global _PATH
    _PATH = os.path.join(files_dir, "config.json")
    if not os.path.exists(_PATH):
        _write(dict(_DEFAULTS))


def _read():
    global _CACHE, _CACHE_MTIME
    with _LOCK:
        if not os.path.exists(_PATH):
            return dict(_DEFAULTS)
        try:
            mtime = os.path.getmtime(_PATH)
        except OSError:
            mtime = None
        if _CACHE is not None and mtime == _CACHE_MTIME:
            return dict(_CACHE)
        with open(_PATH) as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
        _CACHE = merged
        _CACHE_MTIME = mtime
        return dict(merged)


def _write(data):
    global _CACHE, _CACHE_MTIME
    with _LOCK:
        with open(_PATH, "w") as f:
            json.dump(data, f, indent=2)
        # invalidate immediately rather than waiting for the next _read()
        # to notice an mtime change - closes a race where a fast
        # read-after-write on the same second could otherwise see a stale
        # cache if the filesystem's mtime resolution is coarse
        _CACHE = None
        _CACHE_MTIME = None


def get_all():
    return _read()


def get(key, default=None):
    return _read().get(key, default)


def set(key, value):
    data = _read()
    data[key] = value
    _write(data)


def set_many(updates: dict):
    data = _read()
    data.update(updates)
    _write(data)
