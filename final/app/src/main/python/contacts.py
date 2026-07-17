"""
contacts.py — intelligent contact sync: one folder per contact, built from
data YOU already have (vCard/CSV exports, links you paste in yourself).

WHAT'S IN SCOPE:
  - Each contact gets its own folder under FILES_DIR/contacts/<id>/
    containing profile.json (name/phone/email/notes) and profile.jpg
    (if you set one).
  - Import from vCard (.vcf) or CSV files you provide (e.g. your phone's
    own contact export). No network calls involved.
  - "Link triage": you paste a URL (a social profile, a webpage) onto a
    contact, and this fetches THAT ONE PAGE (via scraper.py — same rules:
    public pages, no login bypass) to pull its title/description/photo
    and file it under that contact automatically. It never searches for
    URLs on your behalf — you provide the link, this organizes it.
  - Dedup: merges contacts that share a normalized phone number or email,
    combining their links/notes into one folder.
  - Hook points for full automation: register a watcher on an import
    folder (via existing /automation/watchers) to auto-ingest new .vcf/
    .csv files the moment they land, and a scheduler job to re-check
    saved links periodically or run dedup on a cadence — both using the
    automation infra already in this app (scheduler.py / watcher.py).

WHAT'S DELIBERATELY NOT HERE:
  Nothing here searches the open web, social platforms, or people-search
  sites for a phone number or name to auto-discover accounts. That
  crosses from "organize what you already have" into building a profile
  on someone without their involvement, which this app doesn't do
  regardless of how automated the rest of it is. If you have a link,
  paste it and this will happily extract and file it — it just won't go
  looking for links on its own.
"""

import csv
import json
import logging
import os
import re
import shutil
import time
import uuid
from urllib.parse import urlparse

import dbcore
import scraper

_DB = None
_CONTACTS_DIR = None

# domain -> friendly platform label, purely a lookup table for display —
# not used to go fetch anything beyond the single URL the user supplied.
_PLATFORM_MAP = {
    "facebook.com": "Facebook", "fb.com": "Facebook",
    "instagram.com": "Instagram",
    "twitter.com": "Twitter/X", "x.com": "Twitter/X",
    "linkedin.com": "LinkedIn",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube", "youtu.be": "YouTube",
    "t.me": "Telegram", "telegram.me": "Telegram",
    "wa.me": "WhatsApp",
    "github.com": "GitHub",
    "reddit.com": "Reddit",
    "snapchat.com": "Snapchat",
    "pinterest.com": "Pinterest",
    "threads.net": "Threads",
    "medium.com": "Medium",
}


def _conn():
    return dbcore.get_connection(_DB)


def init(files_dir):
    global _DB, _CONTACTS_DIR
    _DB = os.path.join(files_dir, "contacts.db")
    _CONTACTS_DIR = os.path.join(files_dir, "contacts")
    os.makedirs(_CONTACTS_DIR, exist_ok=True)

    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id TEXT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            phone_norm TEXT,
            email TEXT,
            notes TEXT,
            created_at REAL,
            updated_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS links (
            id TEXT PRIMARY KEY,
            contact_id TEXT NOT NULL,
            url TEXT NOT NULL,
            platform TEXT,
            title TEXT,
            description TEXT,
            image_url TEXT,
            added_at REAL,
            refreshed_at REAL,
            FOREIGN KEY(contact_id) REFERENCES contacts(id)
        )
    """)
    dbcore.ensure_indexes(conn, "links", [
        ("idx_links_contact", "contact_id"),
        ("idx_links_added_at", "added_at"),
    ])
    dbcore.ensure_indexes(conn, "contacts", [
        ("idx_contacts_phone", "phone_norm"),
        ("idx_contacts_email", "email"),
        ("idx_contacts_created_at", "created_at"),
    ])
    conn.commit()
    conn.close()
    logging.info("contacts: initialized at %s", _CONTACTS_DIR)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def normalize_phone(phone):
    if not phone:
        return None
    digits = re.sub(r"[^\d+]", "", phone)
    # keep a leading + if present, strip any interior ones
    if digits.startswith("+"):
        digits = "+" + digits[1:].replace("+", "")
    else:
        digits = digits.replace("+", "")
    return digits or None


def guess_platform(url):
    try:
        host = urlparse(url).netloc.lower()
        host = host[4:] if host.startswith("www.") else host
    except Exception:
        return "Unknown"
    for domain, label in _PLATFORM_MAP.items():
        if host == domain or host.endswith("." + domain):
            return label
    return host or "Unknown"


def _folder(contact_id):
    path = os.path.join(_CONTACTS_DIR, contact_id)
    os.makedirs(path, exist_ok=True)
    return path


def _write_profile_json(contact_id):
    """Keeps profile.json inside the contact's own folder in sync with
    the DB row — the folder is always a complete, self-contained record."""
    conn = _conn()
    row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    links = conn.execute(
        "SELECT * FROM links WHERE contact_id=? ORDER BY added_at", (contact_id,)
    ).fetchall()
    conn.close()
    if not row:
        return
    data = dict(row)
    data["links"] = [dict(l) for l in links]
    with open(os.path.join(_folder(contact_id), "profile.json"), "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------

def create_contact(name=None, phone=None, email=None, notes=None):
    contact_id = uuid.uuid4().hex[:12]
    now = time.time()
    conn = _conn()
    conn.execute(
        "INSERT INTO contacts (id, name, phone, phone_norm, email, notes, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (contact_id, name, phone, normalize_phone(phone), email, notes, now, now),
    )
    conn.commit()
    conn.close()
    _folder(contact_id)
    _write_profile_json(contact_id)
    logging.info("contacts: created %s (%s)", contact_id, name)
    return contact_id


def update_contact(contact_id, **fields):
    allowed = {"name", "phone", "email", "notes"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_contact(contact_id)
    if "phone" in updates:
        updates["phone_norm"] = normalize_phone(updates["phone"])
    updates["updated_at"] = time.time()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn = _conn()
    conn.execute(f"UPDATE contacts SET {set_clause} WHERE id=?", (*updates.values(), contact_id))
    conn.commit()
    conn.close()
    _write_profile_json(contact_id)
    return get_contact(contact_id)


def get_contact(contact_id):
    conn = _conn()
    row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if not row:
        conn.close()
        return None
    links = conn.execute(
        "SELECT * FROM links WHERE contact_id=? ORDER BY added_at", (contact_id,)
    ).fetchall()
    conn.close()
    data = dict(row)
    data["links"] = [dict(l) for l in links]
    data["folder"] = _folder(contact_id)
    data["has_photo"] = os.path.exists(os.path.join(_folder(contact_id), "profile.jpg"))
    return data


def list_contacts(search=None):
    conn = _conn()
    if search:
        like = f"%{search.lower()}%"
        rows = conn.execute(
            "SELECT * FROM contacts WHERE lower(name) LIKE ? OR lower(phone) LIKE ? "
            "OR lower(email) LIKE ? ORDER BY name COLLATE NOCASE",
            (like, like, like),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM contacts ORDER BY name COLLATE NOCASE").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        link_count = conn.execute(
            "SELECT COUNT(*) c FROM links WHERE contact_id=?", (d["id"],)
        ).fetchone()["c"]
        d["link_count"] = link_count
        d["has_photo"] = os.path.exists(os.path.join(_folder(d["id"]), "profile.jpg"))
        result.append(d)
    conn.close()
    return result


def delete_contact(contact_id):
    conn = _conn()
    conn.execute("DELETE FROM links WHERE contact_id=?", (contact_id,))
    conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
    conn.commit()
    conn.close()
    folder = os.path.join(_CONTACTS_DIR, contact_id)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    logging.info("contacts: deleted %s", contact_id)


def set_photo_from_path(contact_id, source_path):
    """Copies a LOCAL image file (e.g. one already on the phone) into the
    contact's own folder as its profile picture. Does not fetch anything
    from the network."""
    if not os.path.exists(source_path):
        return {"error": f"no such file: {source_path}"}
    dest = os.path.join(_folder(contact_id), "profile.jpg")
    shutil.copyfile(source_path, dest)
    conn = _conn()
    conn.execute("UPDATE contacts SET updated_at=? WHERE id=?", (time.time(), contact_id))
    conn.commit()
    conn.close()
    return {"ok": True, "path": dest}


# ---------------------------------------------------------------------
# Link triage — user supplies the URL, this files it under the contact
# ---------------------------------------------------------------------

def add_link(contact_id, url):
    if not get_contact(contact_id):
        return {"error": "no such contact"}
    meta = {}
    try:
        page = scraper.fetch_page(url)
        if page["status_code"] < 400:
            meta = scraper.extract_metadata(page["html"])
    except Exception as e:
        meta = {"error": str(e)}

    link_id = uuid.uuid4().hex[:12]
    now = time.time()
    conn = _conn()
    conn.execute(
        "INSERT INTO links (id, contact_id, url, platform, title, description, image_url, "
        "added_at, refreshed_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            link_id, contact_id, url, guess_platform(url),
            meta.get("title"), meta.get("description"),
            (meta.get("og") or {}).get("image"),
            now, now,
        ),
    )
    conn.commit()
    conn.close()
    _write_profile_json(contact_id)
    logging.info("contacts: added link %s to contact %s", url, contact_id)
    return get_contact(contact_id)


def refresh_link(link_id):
    """Re-fetches metadata for a link already saved on a contact — same
    single-page fetch as add_link, just re-run on demand or via a
    scheduled job."""
    conn = _conn()
    row = conn.execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()
    if not row:
        conn.close()
        return {"error": "no such link"}
    url = row["url"]
    contact_id = row["contact_id"]
    try:
        page = scraper.fetch_page(url)
        meta = scraper.extract_metadata(page["html"]) if page["status_code"] < 400 else {}
    except Exception as e:
        conn.close()
        return {"error": str(e)}
    conn.execute(
        "UPDATE links SET title=?, description=?, image_url=?, refreshed_at=? WHERE id=?",
        (meta.get("title"), meta.get("description"), (meta.get("og") or {}).get("image"),
         time.time(), link_id),
    )
    conn.commit()
    conn.close()
    _write_profile_json(contact_id)
    return {"ok": True}


def remove_link(link_id):
    conn = _conn()
    row = conn.execute("SELECT contact_id FROM links WHERE id=?", (link_id,)).fetchone()
    conn.execute("DELETE FROM links WHERE id=?", (link_id,))
    conn.commit()
    conn.close()
    if row:
        _write_profile_json(row["contact_id"])
    return {"ok": True}


# ---------------------------------------------------------------------
# Import: vCard / CSV — files YOU provide (e.g. your phone's own export)
# ---------------------------------------------------------------------

def _find_or_create(name, phone, email):
    phone_norm = normalize_phone(phone)
    conn = _conn()
    row = None
    if phone_norm:
        row = conn.execute("SELECT * FROM contacts WHERE phone_norm=?", (phone_norm,)).fetchone()
    if not row and email:
        row = conn.execute("SELECT * FROM contacts WHERE lower(email)=?", (email.lower(),)).fetchone()
    conn.close()
    if row:
        return row["id"]
    return create_contact(name=name, phone=phone, email=email)


def import_vcard(path):
    """Minimal vCard 2.1/3.0/4.0 parser — no external deps needed for the
    handful of fields we care about (FN, TEL, EMAIL)."""
    if not os.path.exists(path):
        return {"error": f"no such file: {path}"}

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()

    cards = re.findall(r"BEGIN:VCARD(.*?)END:VCARD", text, re.DOTALL | re.IGNORECASE)
    imported = []
    for card in cards:
        name = None
        phones = []
        emails = []
        for line in card.splitlines():
            line = line.strip()
            if not line:
                continue
            key, _, value = line.partition(":")
            key_upper = key.upper()
            if key_upper.startswith("FN"):
                name = value.strip()
            elif key_upper.startswith("TEL"):
                phones.append(value.strip())
            elif key_upper.startswith("EMAIL"):
                emails.append(value.strip())
        if not (name or phones or emails):
            continue
        contact_id = _find_or_create(
            name=name,
            phone=phones[0] if phones else None,
            email=emails[0] if emails else None,
        )
        imported.append({"id": contact_id, "name": name})

    logging.info("contacts: imported %d contact(s) from vCard %s", len(imported), path)
    return {"imported": imported, "count": len(imported)}


def import_csv(path):
    """Expects a header row with any of: name, phone, email, notes
    (case-insensitive, extra columns ignored)."""
    if not os.path.exists(path):
        return {"error": f"no such file: {path}"}

    imported = []
    with open(path, newline="", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        fieldmap = {(fn or "").strip().lower(): fn for fn in (reader.fieldnames or [])}
        for row in reader:
            def get(field):
                key = fieldmap.get(field)
                return (row.get(key) or "").strip() if key else ""

            name = get("name")
            phone = get("phone")
            email = get("email")
            notes = get("notes")
            if not (name or phone or email):
                continue
            contact_id = _find_or_create(name=name or None, phone=phone or None, email=email or None)
            if notes:
                update_contact(contact_id, notes=notes)
            imported.append({"id": contact_id, "name": name})

    logging.info("contacts: imported %d contact(s) from CSV %s", len(imported), path)
    return {"imported": imported, "count": len(imported)}


# ---------------------------------------------------------------------
# Automation hooks — designed to plug into the existing watcher.py /
# scheduler.py infra with zero new endpoints needed.
# ---------------------------------------------------------------------

def watch_handler(path):
    """Register via watcher.EVENT_HANDLERS.append(contacts.watch_handler),
    then point a watcher (POST /automation/watchers) at your import
    folder. Any .vcf or .csv dropped there gets auto-ingested."""
    lower = path.lower()
    try:
        if lower.endswith(".vcf"):
            import_vcard(path)
        elif lower.endswith(".csv"):
            import_csv(path)
    except Exception as e:
        logging.error("contacts.watch_handler failed on %s: %s", path, e)


def job_dedup(params=None):
    """Register via scheduler.JOB_HANDLERS['contacts_dedup'] = contacts.job_dedup,
    then create a recurring job through POST /automation/jobs to run merges
    on a cadence instead of by hand."""
    result = dedup_contacts()
    logging.info("contacts: scheduled dedup merged %d group(s)", result["merged_groups"])
    return result


def job_refresh_links(params=None):
    """Register via scheduler.JOB_HANDLERS['contacts_refresh_links']. Re-fetches
    metadata for every saved link on a cadence, so a contact's stored
    profile snapshot (photo/title/bio) stays current without you doing
    it by hand — still only touches URLs you already added."""
    conn = _conn()
    links = conn.execute("SELECT id FROM links").fetchall()
    conn.close()
    count = 0
    for row in links:
        res = refresh_link(row["id"])
        if res.get("ok"):
            count += 1
    logging.info("contacts: scheduled refresh updated %d link(s)", count)
    return {"refreshed": count}


# ---------------------------------------------------------------------
# Dedup / reconciliation
# ---------------------------------------------------------------------

def dedup_contacts():
    """Merges contacts that share a normalized phone number or a lower-
    cased email. The oldest contact in each group becomes primary; its
    folder absorbs the others' links and any missing fields, then the
    duplicate folders/rows are removed."""
    conn = _conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM contacts ORDER BY created_at").fetchall()]
    conn.close()

    groups = {}
    for row in rows:
        key = row["phone_norm"] or (row["email"].lower() if row["email"] else None)
        if not key:
            continue
        groups.setdefault(key, []).append(row)

    merged_groups = 0
    for key, group in groups.items():
        if len(group) < 2:
            continue
        primary, *dupes = group  # oldest first, already sorted
        conn = _conn()
        for field in ("name", "phone", "email", "notes"):
            if not primary.get(field):
                for d in dupes:
                    if d.get(field):
                        conn.execute(
                            f"UPDATE contacts SET {field}=? WHERE id=?", (d[field], primary["id"])
                        )
                        primary[field] = d[field]
                        break
        for d in dupes:
            conn.execute("UPDATE links SET contact_id=? WHERE contact_id=?", (primary["id"], d["id"]))
            conn.execute("DELETE FROM contacts WHERE id=?", (d["id"],))
            dupe_folder = os.path.join(_CONTACTS_DIR, d["id"])
            if os.path.isdir(dupe_folder):
                shutil.rmtree(dupe_folder, ignore_errors=True)
        conn.commit()
        conn.close()
        _write_profile_json(primary["id"])
        merged_groups += 1
        logging.info("contacts: merged %d duplicate(s) into %s", len(dupes), primary["id"])

    return {"merged_groups": merged_groups}
