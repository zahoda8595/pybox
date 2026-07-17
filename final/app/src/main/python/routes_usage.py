"""Blueprint: routes_usage - split from the original monolithic backend_app.py."""

from flask import Blueprint, jsonify, request

import usage_stats
from auth import require_auth
from error_manager import safe_route

bp_usage = Blueprint("routes_usage", __name__)

@bp_usage.route("/usage/report", methods=["POST"])
@require_auth
@safe_route("usage-report")
def usage_report():
    body = request.get_json(force=True)
    return jsonify(usage_stats.record_batch(body["entries"]))


@bp_usage.route("/usage/summary")
@require_auth
@safe_route("usage-summary")
def usage_summary():
    days = int(request.args.get("days", 7))
    return jsonify(usage_stats.summary(days=days))


@bp_usage.route("/usage/daily")
@require_auth
@safe_route("usage-daily")
def usage_daily():
    day = request.args.get("day")
    return jsonify(usage_stats.daily(day))
