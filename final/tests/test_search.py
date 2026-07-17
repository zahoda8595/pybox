import os
import sys

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import contacts  # noqa: E402
import global_search  # noqa: E402
import plugin_loader  # noqa: E402
import scripts_runner  # noqa: E402


def test_empty_query_returns_empty_everything(client, auth_headers):
    r = client.get("/search/global?q=", headers=auth_headers)
    d = r.get_json()
    assert d["contacts"] == []
    assert d["scripts"] == []
    assert d["plugins"] == []
    assert d["files"] == []


def test_finds_a_contact_by_name(client, auth_headers, tmp_path):
    contacts.create_contact(name="Zarghuna Khan", phone="0700000000")
    r = client.get("/search/global?q=zarghuna", headers=auth_headers)
    d = r.get_json()
    assert any("Zarghuna" in c["title"] for c in d["contacts"])


def test_finds_a_script_by_content(client, auth_headers, tmp_path):
    scripts_runner.write_script("weather.py", "# fetches the weather for Kabul\nprint('ok')")
    r = client.get("/search/global?q=kabul", headers=auth_headers)
    d = r.get_json()
    assert any(s["title"] == "weather.py" for s in d["scripts"])


def test_finds_a_plugin_by_filename(client, auth_headers, tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_loader.init(str(plugin_dir), {
        "app": None, "plugin_routes": {}, "scheduler": None,
        "watcher": None, "config": None, "require_auth": None,
        "files_dir": str(tmp_path),
    })
    (plugin_dir / "osint_report.py").write_text("def register(ctx):\n    pass\n")
    plugin_loader.load_all()

    r = client.get("/search/global?q=osint", headers=auth_headers)
    d = r.get_json()
    assert any(p["title"] == "osint_report.py" for p in d["plugins"])


def test_finds_a_file_by_content(client, auth_headers, tmp_path):
    (tmp_path / "notes.txt").write_text("remember to buy naan\n")
    r = client.get("/search/global?q=naan", headers=auth_headers)
    d = r.get_json()
    assert any(f["title"] == "notes.txt" for f in d["files"])
    # files have no direct web route to open them
    assert all(f["url"] is None for f in d["files"])


def test_file_search_skips_backups_directory(client, auth_headers, tmp_path):
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "secret_backup_findme.enc").write_bytes(b"\x00\x01")
    r = client.get("/search/global?q=findme", headers=auth_headers)
    d = r.get_json()
    assert d["files"] == []


def test_no_matches_returns_empty_lists_not_error(client, auth_headers):
    r = client.get("/search/global?q=zzzznonexistentzzzz", headers=auth_headers)
    assert r.status_code == 200
    d = r.get_json()
    assert d["contacts"] == [] and d["files"] == []


def test_global_search_requires_auth(client):
    r = client.get("/search/global?q=test")
    assert r.status_code == 401
