def test_default_theme_is_dark_preset(client, auth_headers):
    r = client.get("/settings/api/theme", headers=auth_headers)
    d = r.get_json()
    assert d["theme_preset"] == "dark"
    assert d["theme_bg"] == "#0d0d0d"


def test_all_presets_are_listed(client, auth_headers):
    r = client.get("/settings/api/theme", headers=auth_headers)
    d = r.get_json()
    for expected in ["dark", "midnight_blue", "amoled", "forest", "sunset", "light"]:
        assert expected in d["presets"]


def test_applying_a_preset_changes_colors(client, auth_headers):
    r = client.post("/settings/api/theme", headers=auth_headers,
                     json={"preset": "midnight_blue"})
    d = r.get_json()
    assert d["theme_preset"] == "midnight_blue"
    assert d["theme_bg"] == "#0a0e17"

    # and it should persist to the next GET, not just the response
    r2 = client.get("/settings/api/theme", headers=auth_headers)
    assert r2.get_json()["theme_bg"] == "#0a0e17"


def test_custom_colors_are_saved_and_marked_custom(client, auth_headers):
    r = client.post("/settings/api/theme", headers=auth_headers,
                     json={"theme_bg": "#123456", "theme_accent": "#abcdef"})
    d = r.get_json()
    assert d["theme_preset"] == "custom"
    assert d["theme_bg"] == "#123456"
    assert d["theme_accent"] == "#abcdef"


def test_theme_colors_actually_render_into_pages(client, auth_headers):
    client.post("/settings/api/theme", headers=auth_headers, json={"preset": "sunset"})
    r = client.get("/")
    assert b"#160f0e" in r.data  # sunset's theme_bg


def test_unknown_preset_name_falls_back_to_custom_passthrough(client, auth_headers):
    r = client.post("/settings/api/theme", headers=auth_headers,
                     json={"preset": "not-a-real-preset", "theme_bg": "#222222"})
    d = r.get_json()
    assert d["theme_preset"] == "custom"
    assert d["theme_bg"] == "#222222"
