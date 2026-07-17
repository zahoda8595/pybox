"""
theme.py — lets Khan restyle the whole app from inside the app itself
(the /settings page), with no Android rebuild and no GitHub push needed.

HOW IT WORKS:
  Every page in backend_app.py (_HOME_HTML, _ADMIN_HTML, _CONTACTS_HTML,
  and the new _SCRIPTS_HTML / _SETTINGS_HTML) is still a normal static
  HTML string with its own <style> block, same as before. This module
  does NOT touch those strings. Instead, chrome() builds two extra
  fragments and wrap() splices them in at render time, right before the
  page is returned to the WebView:

    1. A small <style> block that redefines a handful of high-level
       selectors (body, .card/.tile backgrounds, buttons, headings, the
       hint/sub text color) using colors read fresh from config.json on
       every request. Because it's inserted right before </head>, it
       comes AFTER each page's own <style> block in the cascade, so at
       equal specificity it simply wins - no page needs to be rewritten
       to use CSS variables.
    2. A fixed bottom navigation bar (Home / Search / Contacts / Scripts
       / Admin / Settings) inserted right before </body>, present on
       every page, with the current page highlighted.

  Changing a color in /settings just calls config.set_many(...) - same
  mechanism already used for every other setting - and the very next
  page load picks it up. Nothing to recompile, nothing to redeploy.
"""

import config

PRESETS = {
    "dark": {
        "theme_bg": "#0d0d0d", "theme_card_bg": "#1a1a1a", "theme_border": "#2a2a2a",
        "theme_accent": "#2e7d4f", "theme_link": "#7ec8f2", "theme_text": "#e8e8e8",
        "theme_muted": "#888888", "theme_radius": "12", "theme_font_scale": "100",
    },
    "midnight_blue": {
        "theme_bg": "#0a0e17", "theme_card_bg": "#131a29", "theme_border": "#22304a",
        "theme_accent": "#3b82f6", "theme_link": "#60a5fa", "theme_text": "#e5edf9",
        "theme_muted": "#7c8aa5", "theme_radius": "14", "theme_font_scale": "100",
    },
    "amoled": {
        "theme_bg": "#000000", "theme_card_bg": "#0c0c0c", "theme_border": "#1e1e1e",
        "theme_accent": "#00c853", "theme_link": "#29b6f6", "theme_text": "#f2f2f2",
        "theme_muted": "#777777", "theme_radius": "10", "theme_font_scale": "100",
    },
    "forest": {
        "theme_bg": "#0e1512", "theme_card_bg": "#182620", "theme_border": "#26392f",
        "theme_accent": "#4caf50", "theme_link": "#8bd4a0", "theme_text": "#e6f2ea",
        "theme_muted": "#7f9587", "theme_radius": "12", "theme_font_scale": "100",
    },
    "sunset": {
        "theme_bg": "#160f0e", "theme_card_bg": "#241713", "theme_border": "#3a2620",
        "theme_accent": "#ff7043", "theme_link": "#ffb199", "theme_text": "#f5e9e4",
        "theme_muted": "#a3897f", "theme_radius": "14", "theme_font_scale": "100",
    },
    "light": {
        "theme_bg": "#f4f5f7", "theme_card_bg": "#ffffff", "theme_border": "#e1e3e8",
        "theme_accent": "#2e7d4f", "theme_link": "#1565c0", "theme_text": "#1c1c1c",
        "theme_muted": "#666666", "theme_radius": "12", "theme_font_scale": "100",
    },
}

# Keys config.py should carry defaults for (merged into config._DEFAULTS at
# import time so /settings has something sane on first run).
THEME_DEFAULTS = dict(PRESETS["dark"])
THEME_DEFAULTS["theme_preset"] = "dark"

NAV_ITEMS = [
    ("home", "/", "🏠", "Home"),
    ("search", "/search", "🔍", "Search"),
    ("contacts", "/contacts", "👤", "Contacts"),
    ("scripts", "/scripts", "🐍", "Scripts"),
    ("agent", "/agent", "🤖", "Agent"),
    ("admin", "/admin", "🖥️", "Admin"),
    ("settings", "/settings", "⚙️", "Settings"),
]


def current():
    """Returns the active theme values, falling back to the dark preset
    for anything not yet present in config.json."""
    cfg = config.get_all()
    out = dict(THEME_DEFAULTS)
    for k in THEME_DEFAULTS:
        if k in cfg:
            out[k] = cfg[k]
    return out


def _style_block():
    t = current()
    try:
        scale = max(70, min(150, int(t.get("theme_font_scale", 100)))) / 100.0
    except (TypeError, ValueError):
        scale = 1.0
    try:
        radius = max(0, min(28, int(t.get("theme_radius", 12))))
    except (TypeError, ValueError):
        radius = 12
    return f"""
<style id="pybox-theme-override">
  html {{ font-size: {scale * 100:.0f}%; }}
  body {{ background:{t['theme_bg']} !important; color:{t['theme_text']} !important; padding-bottom:76px !important; }}
  h1, h2, h3 {{ color:{t['theme_text']}; }}
  .card, a.tile, .result-card, .link-row, .modal {{
    background:{t['theme_card_bg']} !important;
    border-color:{t['theme_border']} !important;
    border-radius:{radius}px !important;
  }}
  a.tile .desc, .sub, .hint, .card .sub, .meta {{ color:{t['theme_muted']} !important; }}
  a, .result-card a, .link-row a {{ color:{t['theme_link']} !important; }}
  button {{ background:{t['theme_accent']} !important; border-radius:{max(4, radius - 4)}px !important; }}
  button.danger {{ background:#8a3030 !important; }}
  button.secondary {{ background:#333 !important; }}
  input, select, textarea {{ border-radius:{max(4, radius - 4)}px !important; }}
  ::selection {{ background:{t['theme_accent']}; color:#fff; }}
</style>
"""


def _nav_bar_html(active):
    items_html = ""
    for key, href, icon, label in NAV_ITEMS:
        cls = "pybox-nav-item active" if key == active else "pybox-nav-item"
        items_html += (
            f'<a class="{cls}" href="{href}">'
            f'<span class="pybox-nav-icon">{icon}</span>'
            f'<span class="pybox-nav-label">{label}</span></a>'
        )
    t = current()
    return f"""
<style id="pybox-nav-style">
  .pybox-nav {{
    position:fixed; left:0; right:0; bottom:0; z-index:999;
    display:flex; justify-content:space-around; align-items:stretch;
    background:{t['theme_card_bg']}; border-top:1px solid {t['theme_border']};
    padding:6px 2px calc(6px + env(safe-area-inset-bottom, 0px));
    box-shadow:0 -2px 10px rgba(0,0,0,.35);
  }}
  .pybox-nav-item {{
    flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center;
    text-decoration:none; color:{t['theme_muted']}; font-size:9.5px; gap:2px; padding:4px 2px;
    -webkit-tap-highlight-color:transparent;
  }}
  .pybox-nav-icon {{ font-size:18px; line-height:1; }}
  .pybox-nav-item.active {{ color:{t['theme_link']}; font-weight:600; }}
  .pybox-nav-item:active {{ opacity:.6; }}
</style>
<nav class="pybox-nav">{items_html}</nav>
"""


_SHARED_ASSETS_HEAD = (
    '<link rel="stylesheet" href="/static/app.css">\n'
    '<script src="/static/app.js"></script>\n'
)


def render(html, active=""):
    """Splices the shared static assets, theme override CSS, and bottom
    nav bar into a page's HTML string. Call this on every HTML page
    right before returning it from a Flask route - see the routes_*.py
    blueprint files for usage.

    The <link>/<script> tags go in first (right after <head>, before
    each page's own <style>/<script>), so:
      - app.css's `*` reset is already active before the page's own
        <style> block runs (order doesn't matter for that one rule, but
        keeps the cascade predictable if app.css grows more rules).
      - app.js's authHeaders()/escapeHtml() are already defined by the
        time each page's own inline <script> near the bottom of <body>
        runs and calls them.
    Both files are served by Flask's default static handler and cached
    by the WebView across page navigations (see create_app() for the
    Cache-Control lifetime), instead of being re-sent as inline markup
    on every single nav.
    """
    out = html
    if "<head>" in out:
        out = out.replace("<head>", "<head>\n" + _SHARED_ASSETS_HEAD, 1)
    if "</head>" in out:
        out = out.replace("</head>", _style_block() + "</head>", 1)
    if "</body>" in out:
        out = out.replace("</body>", _nav_bar_html(active) + "</body>", 1)
    return out
