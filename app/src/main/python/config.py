"""
config.py — simple persisted key-value settings, editable from the admin panel.

Stored as plain JSON in FILES_DIR/config.json (app-private storage). Not a
database, not for large data - just small settings/customization values
(feature toggles, display preferences, plugin config) that should survive
an app restart and be editable without touching code.
"""

import json
import os
import threading

_PATH = None
_LOCK = threading.Lock()
_DEFAULTS = {
    "theme": "dark",
    "automation_enabled": True,
    "admin_panel_title": "PyBox Admin",
}


def init(files_dir):
    global _PATH
    _PATH = os.path.join(files_dir, "config.json")
    if not os.path.exists(_PATH):
        _write(dict(_DEFAULTS))


def _read():
    with _LOCK:
        if not os.path.exists(_PATH):
            return dict(_DEFAULTS)
        with open(_PATH) as f:
            data = json.load(f)
        merged = dict(_DEFAULTS)
        merged.update(data)
        return merged


def _write(data):
    with _LOCK:
        with open(_PATH, "w") as f:
            json.dump(data, f, indent=2)


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
