from unittest.mock import patch


def test_connectors_routes_require_auth(client):
    assert client.get("/admin/connectors").status_code == 401
    assert client.post("/admin/connectors", json={}).status_code == 401


def test_connectors_add_list_delete_via_routes(client, auth_headers):
    r = client.post("/admin/connectors", headers=auth_headers,
                     json={"name": "weather", "base_url": "https://api.weather.com",
                           "auth_header": "X-Key", "auth_value": "abc123"})
    assert r.status_code == 200
    assert r.get_json()["saved"] == "weather"

    r = client.get("/admin/connectors", headers=auth_headers)
    names = [c["name"] for c in r.get_json()]
    assert "weather" in names

    r = client.delete("/admin/connectors/weather", headers=auth_headers)
    assert r.get_json()["deleted"] is True


def test_connectors_add_route_rejects_missing_url(client, auth_headers):
    r = client.post("/admin/connectors", headers=auth_headers,
                     json={"name": "bad"})
    assert r.status_code == 400


def test_connector_test_route(client, auth_headers):
    client.post("/admin/connectors", headers=auth_headers,
                json={"name": "pingme", "base_url": "https://example.com"})
    fake_resp = type("R", (), {"status_code": 200})()
    with patch("connectors.requests.request", return_value=fake_resp):
        r = client.post("/admin/connectors/pingme/test", headers=auth_headers)
    assert r.get_json()["ok"] is True


def test_intelligence_dashboard_route(client, auth_headers):
    r = client.get("/admin/intelligence", headers=auth_headers)
    assert r.status_code == 200
    d = r.get_json()
    assert "capabilities" in d and "degraded" in d


def test_intelligence_reset_route(client, auth_headers):
    r = client.post("/admin/intelligence/scrape%3Aexample.com/reset", headers=auth_headers)
    assert r.status_code == 200


def test_full_backup_route_requires_auth(client):
    assert client.post("/encryption/full_backup", json={}).status_code == 401


def test_full_backup_route_succeeds_with_just_default_config(client, auth_headers):
    # config.json always exists once the app has started (config.init()
    # writes defaults on first run - see config.py) - so "nothing to
    # back up" isn't actually reachable through the running app, only
    # via encryption.encrypted_full_backup() called directly against an
    # empty directory (covered in test_backup.py).
    r = client.post("/encryption/full_backup", headers=auth_headers, json={})
    d = r.get_json()
    assert "error" not in d
    assert "config.json" in d["included"]


def test_full_backup_and_restore_round_trip(client, auth_headers):
    client.post("/scripts/api/file", headers=auth_headers,
                json={"name": "roundtrip.py", "code": "print('x')"})
    r = client.post("/encryption/full_backup", headers=auth_headers, json={})
    d = r.get_json()
    assert "error" not in d
    backup_name = d["backup"].split("/")[-1]

    r = client.post("/encryption/full_restore", headers=auth_headers,
                     json={"backup_name": backup_name})
    assert r.status_code == 200
    assert "restored_to" in r.get_json()


def test_full_restore_missing_backup_404s(client, auth_headers):
    r = client.post("/encryption/full_restore", headers=auth_headers,
                     json={"backup_name": "does_not_exist.zip.enc"})
    assert r.status_code == 404


def test_script_exception_is_logged_to_error_manager(client, auth_headers):
    client.post("/scripts/api/run", headers=auth_headers,
                json={"name": "crasher.py", "code": "raise RuntimeError('kaboom')"})
    # error_manager has no dedicated Flask route in this codebase yet, so
    # check the underlying log file directly via the same module the
    # admin panel's error view reads from.
    import error_manager
    recent = error_manager.get_recent_errors(limit=5)
    assert any("crasher.py" in e.get("route", "") for e in recent)
    assert any("kaboom" in e.get("message", "") for e in recent)
