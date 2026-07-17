"""Every page should return real HTML and carry the shared bottom nav +
theme override injected by theme.render() - this is the regression test
for 'did I forget to wrap a new page in theme.render()'."""

import pytest

PAGES = ["/", "/admin", "/contacts", "/scripts", "/settings", "/search"]


@pytest.mark.parametrize("route", PAGES)
def test_page_loads(client, route):
    r = client.get(route)
    assert r.status_code == 200
    assert b"<html" in r.data.lower()


@pytest.mark.parametrize("route", PAGES)
def test_page_has_shared_nav_bar(client, route):
    r = client.get(route)
    assert b'class="pybox-nav"' in r.data


@pytest.mark.parametrize("route", PAGES)
def test_page_has_theme_override(client, route):
    r = client.get(route)
    assert b'id="pybox-theme-override"' in r.data


def test_home_links_to_every_other_page(client):
    r = client.get("/")
    for href in [b'href="/contacts"', b'href="/admin"', b'href="/scripts"', b'href="/settings"']:
        assert href in r.data


def test_search_nav_tab_points_at_dedicated_page(client):
    r = client.get("/")
    assert b'href="/search"' in r.data
