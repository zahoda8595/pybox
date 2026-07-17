"""
conftest.py — shared fixtures for the PyBox backend test suite.

This runs on a desktop/CI Python interpreter, NOT inside Chaquopy/Android
- backend_app.py and its subsystem modules have no Android-specific
  imports, so they work unmodified on a normal CPython install as long
  as the same pip packages Chaquopy installs (see app/build.gradle's
  `pip { install ... }` block) are present. requirements-test.txt in
  this folder mirrors that list exactly, so what passes here is what
  will run on-device.

Every test gets its own tmp_path as FILES_DIR (via the `app` fixture),
so tests never touch your real phone data and never interfere with each
other - config.json, contacts.db, scripts/, etc. all start fresh per test.
"""

import os
import sys

import pytest

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import backend_app  # noqa: E402
import auth  # noqa: E402


@pytest.fixture
def app(tmp_path):
    """A fully-initialized Flask app wired against a throwaway FILES_DIR.
    Uses create_app() (see backend_app.py) specifically so app.run()
    never gets called - no real socket, no port 5000 needed.

    Passes a fixed test encryption key, matching production: MainActivity
    .kt always hands start_server() a real Keystore-derived key via
    SecureKeyManager.kt, so encryption.available() is always True on a
    real device. Leaving this out in tests would silently test a
    no-encryption code path that can't actually happen in production -
    exactly the kind of gap that hid a real bug during development here."""
    files_dir = str(tmp_path)
    flask_app = backend_app.create_app(files_dir, encryption_key_hex="ab" * 32)
    flask_app.config.update(TESTING=True)
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers():
    """A valid X-PyBox-Token header, using the token create_app() just
    generated for this test's tmp_path FILES_DIR."""
    token = auth.get_token()
    return {"X-PyBox-Token": token, "Content-Type": "application/json"}
