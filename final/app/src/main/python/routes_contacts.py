"""Blueprint: routes_contacts - split from the original monolithic backend_app.py."""

import os

from flask import Blueprint, jsonify, Response, request

import appstate
import contacts
import theme
from auth import require_auth
from error_manager import safe_route

bp_contacts = Blueprint("routes_contacts", __name__)

@bp_contacts.route("/contacts")
@safe_route("contacts-page")
def contacts_page():
    return theme.render(_CONTACTS_HTML, active="contacts")


@bp_contacts.route("/contacts/api/list")
@require_auth
@safe_route("contacts-list")
def contacts_list():
    return jsonify(contacts.list_contacts(search=request.args.get("q")))


@bp_contacts.route("/contacts/api/<contact_id>")
@require_auth
@safe_route("contacts-get")
def contacts_get(contact_id):
    c = contacts.get_contact(contact_id)
    if not c:
        return jsonify({"error": "not found"}), 404
    return jsonify(c)


@bp_contacts.route("/contacts/api", methods=["POST"])
@require_auth
@safe_route("contacts-create")
def contacts_create():
    body = request.get_json(force=True)
    contact_id = contacts.create_contact(
        name=body.get("name"), phone=body.get("phone"),
        email=body.get("email"), notes=body.get("notes"),
    )
    return jsonify(contacts.get_contact(contact_id))


@bp_contacts.route("/contacts/api/<contact_id>", methods=["POST"])
@require_auth
@safe_route("contacts-update")
def contacts_update(contact_id):
    body = request.get_json(force=True)
    updated = contacts.update_contact(contact_id, **body)
    if not updated:
        return jsonify({"error": "not found"}), 404
    return jsonify(updated)


@bp_contacts.route("/contacts/api/<contact_id>", methods=["DELETE"])
@require_auth
@safe_route("contacts-delete")
def contacts_delete(contact_id):
    contacts.delete_contact(contact_id)
    return jsonify({"deleted": contact_id})


@bp_contacts.route("/contacts/api/<contact_id>/photo", methods=["POST"])
@require_auth
@safe_route("contacts-set-photo")
def contacts_set_photo(contact_id):
    """Body: {"source_path": "/storage/emulated/0/Pictures/whatever.jpg"} —
    copies a LOCAL file already on the phone. No network fetch."""
    body = request.get_json(force=True)
    result = contacts.set_photo_from_path(contact_id, body["source_path"])
    return jsonify(result)


@bp_contacts.route("/contacts/api/<contact_id>/photo/file")
@require_auth
@safe_route("contacts-get-photo")
def contacts_get_photo(contact_id):
    path = os.path.join(appstate.FILES_DIR, "contacts", contact_id, "profile.jpg")
    if not os.path.exists(path):
        return jsonify({"error": "no photo set"}), 404
    with open(path, "rb") as f:
        return Response(f.read(), mimetype="image/jpeg")


@bp_contacts.route("/contacts/api/<contact_id>/links", methods=["POST"])
@require_auth
@safe_route("contacts-add-link")
def contacts_add_link(contact_id):
    """Body: {"url": "https://..."} — YOU supply the link; this fetches
    just that one page to pull title/photo/description and files it
    under the contact. Never searches for links on its own."""
    body = request.get_json(force=True)
    result = contacts.add_link(contact_id, body["url"])
    if isinstance(result, dict) and result.get("error"):
        return jsonify(result), 400
    return jsonify(result)


@bp_contacts.route("/contacts/api/links/<link_id>", methods=["DELETE"])
@require_auth
@safe_route("contacts-remove-link")
def contacts_remove_link(link_id):
    return jsonify(contacts.remove_link(link_id))


@bp_contacts.route("/contacts/api/links/<link_id>/refresh", methods=["POST"])
@require_auth
@safe_route("contacts-refresh-link")
def contacts_refresh_link(link_id):
    return jsonify(contacts.refresh_link(link_id))


@bp_contacts.route("/contacts/api/import/vcard", methods=["POST"])
@require_auth
@safe_route("contacts-import-vcard")
def contacts_import_vcard():
    """Body: {"path": "/storage/emulated/0/PyBox/import/contacts.vcf"} —
    a vCard file already on the phone (e.g. exported from your own
    contacts app)."""
    body = request.get_json(force=True)
    return jsonify(contacts.import_vcard(body["path"]))


@bp_contacts.route("/contacts/api/import/csv", methods=["POST"])
@require_auth
@safe_route("contacts-import-csv")
def contacts_import_csv():
    body = request.get_json(force=True)
    return jsonify(contacts.import_csv(body["path"]))


@bp_contacts.route("/contacts/api/dedup", methods=["POST"])
@require_auth
@safe_route("contacts-dedup")
def contacts_dedup():
    return jsonify(contacts.dedup_contacts())


_CONTACTS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PyBox Contacts</title>
<style>
  body { font-family: -apple-system, sans-serif; background:#0d0d0d; color:#e8e8e8; margin:0; padding:14px; line-height:1.4; }
  h1 { font-size:19px; margin:0 0 14px; display:flex; align-items:center; justify-content:space-between; }
  .toolbar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
  input,select,textarea { background:#141414; color:#eee; border:1px solid #333; border-radius:6px; padding:7px 9px; font-size:13px; }
  button { background:#2e7d4f; color:#fff; border:none; border-radius:6px; padding:7px 12px; font-size:13px; cursor:pointer; }
  button.danger { background:#8a3030; }
  button.secondary { background:#333; }
  #search { flex:1; min-width:140px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(150px,1fr)); gap:10px; }
  .card { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:10px; padding:10px; cursor:pointer; text-align:center; }
  .card img, .avatar { width:56px; height:56px; border-radius:50%; object-fit:cover; background:#333; margin:0 auto 6px; display:block; }
  .avatar { display:flex; align-items:center; justify-content:center; font-size:20px; color:#888; }
  .card .name { font-size:12.5px; font-weight:600; word-break:break-word; }
  .card .sub { font-size:10.5px; color:#888; margin-top:2px; }
  .badge { display:inline-block; background:#263238; color:#8bd; font-size:10px; padding:1px 6px; border-radius:8px; margin-top:4px; }
  .modal-bg { position:fixed; inset:0; background:rgba(0,0,0,.6); display:none; align-items:flex-start; justify-content:center; padding:16px; overflow-y:auto; z-index:10; }
  .modal { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:12px; padding:16px; max-width:480px; width:100%; margin-top:20px; }
  .modal h2 { font-size:15px; margin:0 0 10px; }
  .field { margin-bottom:8px; }
  .field label { display:block; font-size:11px; color:#999; margin-bottom:3px; text-transform:uppercase; letter-spacing:.03em; }
  .field input, .field textarea { width:100%; }
  .link-row { background:#141414; border:1px solid #262626; border-radius:8px; padding:8px; margin-bottom:8px; display:flex; gap:8px; align-items:flex-start; }
  .link-row img { width:36px; height:36px; border-radius:6px; object-fit:cover; background:#333; flex:none; }
  .link-row .info { flex:1; min-width:0; }
  .link-row a { color:#7ec8f2; text-decoration:none; font-size:12.5px; font-weight:600; word-break:break-word; }
  .link-row .platform { font-size:10px; color:#8bd; }
  .link-row .desc { font-size:11px; color:#999; margin-top:2px; }
  .link-row .actions { display:flex; flex-direction:column; gap:4px; }
  .link-row button { font-size:10.5px; padding:4px 7px; }
  .close-x { float:right; background:none; color:#999; padding:2px 8px; }
  .hint { color:#888; font-size:11.5px; margin:0 0 10px; }
  .empty { color:#666; font-style:italic; grid-column:1/-1; text-align:center; padding:30px 0; }
</style>
</head>
<body>
<h1>Contacts <button onclick="openNew()">+ New</button></h1>
<div class="toolbar">
  <input id="search" placeholder="Search name / phone / email..." oninput="loadList()">
  <button class="secondary" onclick="runDedup()">Dedup</button>
</div>
<p class="hint">Import a vCard/CSV or paste social links per contact from a contact's detail view. Each contact lives in its own folder on the device.</p>
<div class="grid" id="grid"></div>

<div class="modal-bg" id="detail-modal">
  <div class="modal" id="detail-content"></div>
</div>

<div class="modal-bg" id="new-modal">
  <div class="modal">
    <button class="close-x" onclick="closeNew()">x</button>
    <h2>New Contact</h2>
    <div class="field"><label>Name</label><input id="new-name"></div>
    <div class="field"><label>Phone</label><input id="new-phone"></div>
    <div class="field"><label>Email</label><input id="new-email"></div>
    <div class="field"><label>Notes</label><textarea id="new-notes" rows="2"></textarea></div>
    <button onclick="createContact()">Create</button>
  </div>
</div>

<script>
async function loadList() {
  const q = document.getElementById("search").value.trim();
  const r = await fetch("/contacts/api/list" + (q ? "?q=" + encodeURIComponent(q) : ""), { headers: authHeaders() });
  const list = await r.json();
  const grid = document.getElementById("grid");
  if (!list.length) { grid.innerHTML = '<div class="empty">No contacts yet. Import a vCard/CSV or add one.</div>'; return; }
  grid.innerHTML = list.map(c => `
    <div class="card" onclick="openDetail('${c.id}')">
      ${c.has_photo ? `<img src="/contacts/api/${c.id}/photo/file">` : `<div class="avatar">${(c.name||'?')[0].toUpperCase()}</div>`}
      <div class="name">${escapeHtml(c.name || '(no name)')}</div>
      <div class="sub">${escapeHtml(c.phone || c.email || '')}</div>
      <div class="badge">${c.link_count} link${c.link_count===1?'':'s'}</div>
    </div>
  `).join("");
}

async function runDedup() {
  const r = await fetch("/contacts/api/dedup", { method: "POST", headers: authHeaders() });
  const d = await r.json();
  alert(`Merged ${d.merged_groups} duplicate group(s).`);
  loadList();
}

function openNew() { document.getElementById("new-modal").style.display = "flex"; }
function closeNew() { document.getElementById("new-modal").style.display = "none"; }

async function createContact() {
  const body = {
    name: document.getElementById("new-name").value.trim(),
    phone: document.getElementById("new-phone").value.trim(),
    email: document.getElementById("new-email").value.trim(),
    notes: document.getElementById("new-notes").value.trim(),
  };
  await fetch("/contacts/api", { method: "POST", headers: authHeaders(true), body: JSON.stringify(body) });
  closeNew();
  ["new-name","new-phone","new-email","new-notes"].forEach(id => document.getElementById(id).value = "");
  loadList();
}

async function openDetail(id) {
  const r = await fetch(`/contacts/api/${id}`, { headers: authHeaders() });
  const c = await r.json();
  const links = (c.links||[]).map(l => `
    <div class="link-row">
      ${l.image_url ? `<img src="${escapeHtml(l.image_url)}">` : `<img>`}
      <div class="info">
        <div class="platform">${escapeHtml(l.platform||'')}</div>
        <a href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.title || l.url)}</a>
        <div class="desc">${escapeHtml((l.description||'').slice(0,120))}</div>
      </div>
      <div class="actions">
        <button class="secondary" onclick="refreshLink('${l.id}','${id}')">Refresh</button>
        <button class="danger" onclick="removeLink('${l.id}','${id}')">Remove</button>
      </div>
    </div>
  `).join("") || '<p class="hint">No links yet — paste one below.</p>';

  document.getElementById("detail-content").innerHTML = `
    <button class="close-x" onclick="closeDetail()">x</button>
    <h2>${escapeHtml(c.name || '(no name)')}</h2>
    <div class="field"><label>Name</label><input id="d-name" value="${escapeHtml(c.name||'')}"></div>
    <div class="field"><label>Phone</label><input id="d-phone" value="${escapeHtml(c.phone||'')}"></div>
    <div class="field"><label>Email</label><input id="d-email" value="${escapeHtml(c.email||'')}"></div>
    <div class="field"><label>Notes</label><textarea id="d-notes" rows="2">${escapeHtml(c.notes||'')}</textarea></div>
    <button onclick="saveDetail('${id}')">Save</button>
    <button class="danger" onclick="deleteContact('${id}')">Delete</button>
    <h2 style="margin-top:16px">Linked profiles</h2>
    ${links}
    <div class="field"><label>Add a link (you supply the URL)</label>
      <input id="new-link-url" placeholder="https://...">
      <button style="margin-top:6px" onclick="addLink('${id}')">Add & extract</button>
    </div>
  `;
  document.getElementById("detail-modal").style.display = "flex";
}
function closeDetail() { document.getElementById("detail-modal").style.display = "none"; loadList(); }

async function saveDetail(id) {
  const body = {
    name: document.getElementById("d-name").value.trim(),
    phone: document.getElementById("d-phone").value.trim(),
    email: document.getElementById("d-email").value.trim(),
    notes: document.getElementById("d-notes").value.trim(),
  };
  await fetch(`/contacts/api/${id}`, { method: "POST", headers: authHeaders(true), body: JSON.stringify(body) });
  openDetail(id);
}

async function deleteContact(id) {
  if (!confirm("Delete this contact and its folder?")) return;
  await fetch(`/contacts/api/${id}`, { method: "DELETE", headers: authHeaders() });
  closeDetail();
}

async function addLink(id) {
  const url = document.getElementById("new-link-url").value.trim();
  if (!url) return;
  await fetch(`/contacts/api/${id}/links`, { method: "POST", headers: authHeaders(true), body: JSON.stringify({ url }) });
  openDetail(id);
}

async function refreshLink(linkId, contactId) {
  await fetch(`/contacts/api/links/${linkId}/refresh`, { method: "POST", headers: authHeaders() });
  openDetail(contactId);
}

async function removeLink(linkId, contactId) {
  await fetch(`/contacts/api/links/${linkId}`, { method: "DELETE", headers: authHeaders() });
  openDetail(contactId);
}

loadList();
</script>
</body>
</html>"""
