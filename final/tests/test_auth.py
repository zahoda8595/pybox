"""Every mutating/data-reading route should be behind @require_auth.
GET-only *page* routes (the HTML shells) are intentionally public, same
reasoning documented in backend_app.py next to each route."""

PROTECTED_GET_ROUTES = [
    "/scripts/api/list",
    "/settings/api/theme",
    "/search/global?q=test",
]

PROTECTED_POST_ROUTES = [
    "/scripts/api/run",
    "/scripts/api/file",
    "/settings/api/theme",
]

PUBLIC_PAGE_ROUTES = ["/", "/admin", "/contacts", "/scripts", "/settings", "/search"]


def test_protected_get_routes_reject_missing_token(client):
    for route in PROTECTED_GET_ROUTES:
        r = client.get(route)
        assert r.status_code == 401, f"{route} should require auth"


def test_protected_post_routes_reject_missing_token(client):
    for route in PROTECTED_POST_ROUTES:
        r = client.post(route, json={})
        assert r.status_code == 401, f"{route} should require auth"


def test_protected_routes_accept_valid_token(client, auth_headers):
    r = client.get("/scripts/api/list", headers=auth_headers)
    assert r.status_code == 200


def test_wrong_token_is_rejected(client):
    r = client.get("/scripts/api/list", headers={"X-PyBox-Token": "not-the-real-token"})
    assert r.status_code == 401


def test_public_page_routes_do_not_require_auth(client):
    for route in PUBLIC_PAGE_ROUTES:
        r = client.get(route)
        assert r.status_code == 200, f"{route} should be publicly viewable"
