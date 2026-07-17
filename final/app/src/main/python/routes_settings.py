"""Blueprint: routes_settings - split from the original monolithic backend_app.py."""


from flask import Blueprint, jsonify, request

import config
import theme
from auth import require_auth
from error_manager import safe_route

bp_settings = Blueprint("routes_settings", __name__)

@bp_settings.route("/settings")
@safe_route("settings-page")
def settings_page():
    return theme.render(_SETTINGS_HTML, active="settings")


@bp_settings.route("/settings/api/theme", methods=["GET"])
@require_auth
@safe_route("settings-theme-get")
def settings_theme_get():
    out = theme.current()
    out["presets"] = list(theme.PRESETS.keys())
    return jsonify(out)


@bp_settings.route("/settings/api/theme", methods=["POST"])
@require_auth
@safe_route("settings-theme-set")
def settings_theme_set():
    body = request.get_json(force=True)
    preset = body.get("preset")
    if preset and preset in theme.PRESETS:
        values = dict(theme.PRESETS[preset])
        values["theme_preset"] = preset
    else:
        values = {k: v for k, v in body.items() if k in theme.THEME_DEFAULTS}
        values["theme_preset"] = "custom"
    config.set_many(values)
    return jsonify(theme.current())


_SETTINGS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox Settings</title>
<style>
  body { margin:0; background:#0d0d0d; color:#e8e8e8; font-family:-apple-system,Roboto,sans-serif; padding:16px; }
  h1 { font-size:19px; margin:0 0 4px; }
  .sub { color:#888; font-size:12px; margin-bottom:16px; }
  .card { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:12px; padding:14px; margin-bottom:14px; }
  .card h2 { font-size:15px; margin:0 0 10px; }
  .preset-row { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:6px; }
  .preset-btn { padding:8px 12px; border-radius:8px; border:1px solid #333; background:#111; color:#e8e8e8; font-size:12px; }
  .preset-btn.active { border-color:#7ec8f2; color:#7ec8f2; }
  .field-row { display:flex; align-items:center; justify-content:space-between; padding:8px 0; border-bottom:1px solid #232323; gap:10px; }
  .field-row:last-child { border-bottom:none; }
  .field-row label { font-size:13px; color:#ccc; }
  input[type=color] { width:44px; height:32px; border:none; border-radius:6px; background:none; padding:0; }
  input[type=range] { width:140px; }
  .range-val { font-size:12px; color:#888; width:38px; text-align:right; }
  button { background:#2e7d4f; color:#fff; border:none; border-radius:8px; padding:10px 16px; font-size:13px; }
  button.secondary { background:#333; }
  .actions { display:flex; gap:8px; margin-top:6px; }
  .preview { border-radius:10px; padding:14px; border:1px solid; margin-top:4px; }
  .preview .p-tile { display:inline-block; padding:8px 12px; border-radius:8px; margin-right:8px; font-size:12px; }
  .status-msg { font-size:12px; color:#7ec8f2; min-height:16px; margin-top:8px; }
</style>
</head>
<body>
<h1>🎨 Theme &amp; UI Settings</h1>
<div class="sub">Changes apply immediately across every page — no rebuild, no GitHub push.</div>

<div class="card">
  <h2>Presets</h2>
  <div class="preset-row" id="presetRow"></div>
</div>

<div class="card">
  <h2>Custom colors</h2>
  <div class="field-row"><label>Background</label><input type="color" id="theme_bg"></div>
  <div class="field-row"><label>Card background</label><input type="color" id="theme_card_bg"></div>
  <div class="field-row"><label>Border</label><input type="color" id="theme_border"></div>
  <div class="field-row"><label>Accent (buttons)</label><input type="color" id="theme_accent"></div>
  <div class="field-row"><label>Links / active tab</label><input type="color" id="theme_link"></div>
  <div class="field-row"><label>Text</label><input type="color" id="theme_text"></div>
  <div class="field-row"><label>Muted text</label><input type="color" id="theme_muted"></div>
  <div class="field-row"><label>Corner radius</label>
    <div style="display:flex;align-items:center;gap:8px">
      <input type="range" id="theme_radius" min="0" max="28" step="1">
      <span class="range-val" id="theme_radius_val"></span>
    </div>
  </div>
  <div class="field-row"><label>Font scale (%)</label>
    <div style="display:flex;align-items:center;gap:8px">
      <input type="range" id="theme_font_scale" min="80" max="130" step="5">
      <span class="range-val" id="theme_font_scale_val"></span>
    </div>
  </div>

  <div class="preview" id="previewBox">
    <span class="p-tile">Sample tile</span>
    <button style="margin-top:6px">Sample button</button>
  </div>

  <div class="actions">
    <button id="applyBtn">Apply &amp; Save</button>
    <button class="secondary" id="reloadBtn">Reload page</button>
  </div>
  <div class="status-msg" id="statusMsg"></div>
</div>

<script>
const FIELDS = ["theme_bg", "theme_card_bg", "theme_border", "theme_accent", "theme_link", "theme_text", "theme_muted"];
let PRESETS = [];
let currentPreset = "";

function updatePreview() {
  const v = id => document.getElementById(id).value;
  const box = document.getElementById("previewBox");
  box.style.background = v("theme_bg");
  box.style.borderColor = v("theme_border");
  box.style.color = v("theme_text");
  const radius = document.getElementById("theme_radius").value;
  const tile = box.querySelector(".p-tile");
  tile.style.background = v("theme_card_bg");
  tile.style.borderRadius = radius + "px";
  tile.style.color = v("theme_muted");
  const btn = box.querySelector("button");
  btn.style.background = v("theme_accent");
  btn.style.borderRadius = Math.max(4, radius - 4) + "px";
  document.getElementById("theme_radius_val").textContent = radius;
  document.getElementById("theme_font_scale_val").textContent = document.getElementById("theme_font_scale").value;
}

function fillForm(t) {
  FIELDS.forEach(f => { document.getElementById(f).value = t[f]; });
  document.getElementById("theme_radius").value = t.theme_radius;
  document.getElementById("theme_font_scale").value = t.theme_font_scale;
  currentPreset = t.theme_preset || "";
  renderPresetButtons();
  updatePreview();
}

function renderPresetButtons() {
  const row = document.getElementById("presetRow");
  row.innerHTML = PRESETS.map(p => `
    <button type="button" class="preset-btn ${p === currentPreset ? 'active' : ''}" data-preset="${p}">${p.replace(/_/g, ' ')}</button>
  `).join("");
  row.querySelectorAll("button").forEach(btn => {
    btn.onclick = () => applyPreset(btn.dataset.preset);
  });
}

async function applyPreset(name) {
  const r = await fetch("/settings/api/theme", { method: "POST", headers: authHeaders(), body: JSON.stringify({ preset: name }) });
  const t = await r.json();
  fillForm(t);
  document.getElementById("statusMsg").textContent = `Applied "${name}" preset. Reload other open pages to see it there too.`;
}

async function loadTheme() {
  const r = await fetch("/settings/api/theme", { headers: authHeaders() });
  const t = await r.json();
  PRESETS = t.presets || [];
  fillForm(t);
}

document.getElementById("applyBtn").onclick = async () => {
  const body = {};
  FIELDS.forEach(f => { body[f] = document.getElementById(f).value; });
  body.theme_radius = document.getElementById("theme_radius").value;
  body.theme_font_scale = document.getElementById("theme_font_scale").value;
  const r = await fetch("/settings/api/theme", { method: "POST", headers: authHeaders(), body: JSON.stringify(body) });
  const t = await r.json();
  fillForm(t);
  document.getElementById("statusMsg").textContent = "Saved. Reloading…";
  setTimeout(() => location.reload(), 500);
};

document.getElementById("reloadBtn").onclick = () => location.reload();

[...FIELDS, "theme_radius", "theme_font_scale"].forEach(id => {
  document.getElementById(id).addEventListener("input", updatePreview);
});

loadTheme();
</script>
</body>
</html>"""
