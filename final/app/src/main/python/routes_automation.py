"""Blueprint: routes_automation - split from the original monolithic backend_app.py."""

import logging
import os

from flask import Blueprint, jsonify, request

import appstate
import auth
import contacts
import encryption
import scheduler
import watcher
from auth import require_auth
from error_manager import safe_route

bp_automation = Blueprint("routes_automation", __name__)

def _log_event_job(params):
    """Default job handler, registered below - proves the scheduler works
    without needing you to wire anything else up first. Safe to delete
    once you've registered your own handlers."""
    logging.info("scheduler heartbeat job ran. params=%s", params)


def _log_watch_event(path):
    """Default watch handler, registered below - logs any file change in
    a watched folder. Safe to delete/replace once you have real handlers."""
    logging.info("watcher detected file: %s", path)


scheduler.JOB_HANDLERS["log_event"] = _log_event_job
watcher.EVENT_HANDLERS.append(_log_watch_event)

# Contacts automation: point a watcher (POST /automation/watchers) at your
# vCard/CSV import folder to auto-ingest new drops, and create scheduled
# jobs (POST /automation/jobs) using these handler names to run dedup or
# link-refresh on a cadence — no new endpoints needed, reuses the
# scheduler/watcher infra already in this app.
watcher.EVENT_HANDLERS.append(contacts.watch_handler)
scheduler.JOB_HANDLERS["contacts_dedup"] = contacts.job_dedup
scheduler.JOB_HANDLERS["contacts_refresh_links"] = contacts.job_refresh_links


def _encrypted_backup_job(params):
    """Register a job with handler='encrypted_backup', params={'db_name': 'contacts.db'}
    to run periodic encrypted snapshots of a local DB via WorkManager/scheduler."""
    db_name = (params or {}).get("db_name", "contacts.db")
    result = encryption.encrypted_backup(
        os.path.join(appstate.FILES_DIR, db_name),
        os.path.join(appstate.FILES_DIR, "backups"),
    )
    logging.info("encrypted_backup job: %s", result)


scheduler.JOB_HANDLERS["encrypted_backup"] = _encrypted_backup_job


@bp_automation.route("/automation/jobs", methods=["GET"])
@require_auth
@safe_route("automation-list-jobs")
def list_jobs():
    return jsonify(scheduler.list_jobs())


@bp_automation.route("/automation/jobs", methods=["POST"])
@require_auth
@safe_route("automation-create-job")
def create_job():
    body = request.get_json(force=True)
    job_id = scheduler.create_job(
        name=body["name"],
        handler=body["handler"],
        interval_seconds=int(body["interval_seconds"]),
        params=body.get("params", {}),
        enabled=body.get("enabled", True),
    )
    return jsonify({"id": job_id})


@bp_automation.route("/automation/jobs/<int:job_id>", methods=["DELETE"])
@require_auth
@safe_route("automation-delete-job")
def delete_job(job_id):
    scheduler.delete_job(job_id)
    return jsonify({"deleted": job_id})


@bp_automation.route("/automation/jobs/<int:job_id>/runs", methods=["GET"])
@require_auth
@safe_route("automation-job-runs")
def job_runs(job_id):
    return jsonify(scheduler.recent_runs(job_id))


@bp_automation.route("/automation/watchers", methods=["GET"])
@require_auth
@safe_route("automation-list-watchers")
def list_watchers():
    return jsonify(watcher.list_watches())


@bp_automation.route("/automation/watchers", methods=["POST"])
@require_auth
@safe_route("automation-create-watcher")
def create_watcher():
    body = request.get_json(force=True)
    watcher.add_watch(
        path=body["path"],
        extensions=body.get("extensions", []),
        recursive=body.get("recursive", False),
    )
    return jsonify({"ok": True})


@bp_automation.route("/automation/watchers/<int:watch_id>", methods=["DELETE"])
@require_auth
@safe_route("automation-delete-watcher")
def delete_watcher(watch_id):
    watcher.remove_watch(watch_id)
    return jsonify({"deleted": watch_id})


@bp_automation.route("/automation/events", methods=["GET"])
@require_auth
@safe_route("automation-events")
def automation_events():
    return jsonify(watcher.recent_events())


@bp_automation.route("/automation/token", methods=["GET"])
@safe_route("automation-token")
def automation_token():
    """
    Deliberately NOT behind @require_auth - it's how the WebView (running
    inside this same app) discovers the token in the first place. It's
    reachable only via loopback by definition of how Flask is bound, and
    the value itself doesn't grant anything beyond what this app can
    already do to itself. Prefer the JS interface (window.PyBoxAuth) that
    MainActivity.kt injects when possible; this route exists as a fallback.
    """
    return jsonify({"token": auth.get_token()})
