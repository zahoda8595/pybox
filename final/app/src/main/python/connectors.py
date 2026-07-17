"""
connectors.py — a small registry for external API connections, so
scripts and plugins can call out to a REST API by name instead of every
script hardcoding its own base URL/auth header.

WHY THIS EXISTS:
  Without this, adding a new API integration means editing Python source
  and rebuilding. With this, adding one is: open /admin, add a
  connector (name, base URL, auth header/value), and any script can then
  call `connectors.call("openai", "POST", "/v1/chat/completions", json=...)`
  immediately - no rebuild.

STORAGE:
  Connector definitions live in config.json under the "connectors" key
  (a dict of name -> {base_url, auth_header, auth_value, default_headers}).
  If encryption.py has a key loaded, auth_value is stored encrypted at
  rest (same AES-GCM envelope used for backups) and only decrypted right
  before a request goes out; otherwise it's stored in plain config.json,
  same trust level as every other setting there.

CALLING OUT:
  call() goes through intelligence.run() for automatic retry-with-backoff
  on transient failures (timeouts, connection errors, 5xx) - a flaky API
  gets a couple of quick retries before the caller ever sees an
  exception. Non-transient failures (4xx) are NOT retried, since retrying
  a bad request just repeats the same mistake.
"""

import logging

import requests

import config
import encryption
import intelligence

TIMEOUT_SECONDS = 20
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ConnectorError(Exception):
    pass


def _all_connectors():
    return config.get("connectors", {}) or {}


def _save_all(connectors):
    config.set("connectors", connectors)


def list_connectors():
    """Returns connector definitions with secrets redacted - safe to
    send straight to the admin UI."""
    out = []
    for name, c in sorted(_all_connectors().items()):
        out.append({
            "name": name,
            "base_url": c.get("base_url", ""),
            "auth_header": c.get("auth_header", ""),
            "has_secret": bool(c.get("auth_value")),
            "default_headers": c.get("default_headers", {}),
        })
    return out


def add_connector(name, base_url, auth_header="", auth_value="", default_headers=None):
    if not name or "/" in name:
        raise ValueError("connector name must be non-empty and contain no '/'")
    if not base_url:
        raise ValueError("base_url is required")
    stored_value = auth_value
    encrypted = False
    if auth_value and encryption.available():
        stored_value = encryption.encrypt_bytes(auth_value.encode())
        encrypted = True
    connectors = _all_connectors()
    connectors[name] = {
        "base_url": base_url.rstrip("/"),
        "auth_header": auth_header,
        "auth_value": stored_value,
        "auth_value_encrypted": encrypted,
        "default_headers": default_headers or {},
    }
    _save_all(connectors)
    return name


def delete_connector(name):
    connectors = _all_connectors()
    if name in connectors:
        del connectors[name]
        _save_all(connectors)
        return True
    return False


def _resolve_auth_value(c):
    if not c.get("auth_value"):
        return ""
    if c.get("auth_value_encrypted"):
        if not encryption.available():
            raise ConnectorError(
                "this connector's secret was stored encrypted, but the "
                "encryption key isn't loaded right now - restart the app"
            )
        return encryption.decrypt_bytes(c["auth_value"]).decode()
    return c["auth_value"]


def _do_request(name, c, method, path, **kwargs):
    url = c["base_url"] + ("" if path.startswith("/") else "/") + path
    headers = dict(c.get("default_headers") or {})
    headers.update(kwargs.pop("headers", {}) or {})
    auth_header = c.get("auth_header")
    if auth_header:
        headers[auth_header] = _resolve_auth_value(c)
    kwargs.setdefault("timeout", TIMEOUT_SECONDS)
    resp = requests.request(method, url, headers=headers, **kwargs)
    if resp.status_code in _RETRYABLE_STATUS:
        # raising turns this into a normal transient failure that
        # intelligence.run() will retry, instead of silently returning a
        # 500-ish body to the caller as if it were a real answer.
        raise ConnectorError(f"{name}: {method} {path} -> HTTP {resp.status_code} (retryable)")
    return resp


def call(name, method="GET", path="", attempts=3, **kwargs):
    """Calls a saved connector by name with automatic retry on transient
    failures. Returns the requests.Response - callers do .json()/.text
    themselves, same as calling `requests` directly."""
    connectors = _all_connectors()
    c = connectors.get(name)
    if not c:
        raise ConnectorError(f"no connector named '{name}' - add one from /admin first")

    def attempt():
        return _do_request(name, c, method, path, **kwargs)

    try:
        return intelligence.run(f"connector:{name}", attempt, attempts=attempts)
    except ConnectorError:
        raise
    except requests.RequestException as e:
        logging.warning("connectors: %s call failed after retries: %s", name, e)
        raise ConnectorError(f"{name}: request failed after retries: {e}") from e


def test_connector(name):
    """A light GET against the base_url, mainly so /admin can show a
    green/red status without the user writing a script just to check
    whether a connector is configured correctly."""
    connectors = _all_connectors()
    c = connectors.get(name)
    if not c:
        return {"ok": False, "error": "no such connector"}
    try:
        resp = _do_request(name, c, "GET", "", timeout=8)
        return {"ok": resp.status_code < 500, "status_code": resp.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}
