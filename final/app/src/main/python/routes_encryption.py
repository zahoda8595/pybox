"""Blueprint: routes_encryption - split from the original monolithic backend_app.py."""

import os

from flask import Blueprint, jsonify, request

import appstate
import encryption
from auth import require_auth
from error_manager import safe_route

bp_encryption = Blueprint("routes_encryption", __name__)

@bp_encryption.route("/encryption/status")
@require_auth
@safe_route("encryption-status")
def encryption_status():
    return jsonify({"available": encryption.available()})


@bp_encryption.route("/encryption/backup", methods=["POST"])
@require_auth
@safe_route("encryption-backup")
def encryption_backup():
    body = request.get_json(force=True)
    db_name = body.get("db_name", "contacts.db")
    result = encryption.encrypted_backup(
        os.path.join(appstate.FILES_DIR, db_name), os.path.join(appstate.FILES_DIR, "backups")
    )
    return jsonify(result)


@bp_encryption.route("/encryption/backups")
@require_auth
@safe_route("encryption-list-backups")
def encryption_list_backups():
    backups_dir = os.path.join(appstate.FILES_DIR, "backups")
    if not os.path.isdir(backups_dir):
        return jsonify({"backups": []})
    files = sorted(os.listdir(backups_dir), reverse=True)
    return jsonify({"backups": files})


@bp_encryption.route("/encryption/restore", methods=["POST"])
@require_auth
@safe_route("encryption-restore")
def encryption_restore():
    """Body: {"backup_name": "contacts.db.1234567.enc", "dest_name": "contacts_restored.db"}
    Decrypts a backup back into appstate.FILES_DIR under a NEW name - never
    silently overwrites the live DB."""
    body = request.get_json(force=True)
    src = os.path.join(appstate.FILES_DIR, "backups", body["backup_name"])
    dest = os.path.join(appstate.FILES_DIR, body.get("dest_name", "restored.db"))
    if not os.path.exists(src):
        return jsonify({"error": "no such backup"}), 404
    encryption.decrypt_file(src, dest)
    return jsonify({"restored_to": dest})


@bp_encryption.route("/encryption/full_backup", methods=["POST"])
@require_auth
@safe_route("encryption-full-backup")
def encryption_full_backup():
    """Bundles config.json + scripts/ into one encrypted backup - the
    piece the single-file /encryption/backup route above doesn't cover.
    See encryption.encrypted_full_backup() for exactly what's included."""
    result = encryption.encrypted_full_backup(appstate.FILES_DIR, os.path.join(appstate.FILES_DIR, "backups"))
    return jsonify(result)


@bp_encryption.route("/encryption/full_restore", methods=["POST"])
@require_auth
@safe_route("encryption-full-restore")
def encryption_full_restore():
    """Body: {"backup_name": "full_backup.1234567.zip.enc"}. Extracts
    into appstate.FILES_DIR/restored_backups/<ts>/ rather than overwriting your
    live config/scripts - same never-silently-overwrite rule as the
    single-file restore above."""
    body = request.get_json(force=True)
    src = os.path.join(appstate.FILES_DIR, "backups", body["backup_name"])
    result = encryption.restore_full_backup(src, appstate.FILES_DIR)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)
