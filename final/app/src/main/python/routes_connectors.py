"""Blueprint: routes_connectors - split from the original monolithic backend_app.py."""

from flask import Blueprint, jsonify, request

import connectors
import intelligence
from auth import require_auth
from error_manager import safe_route

bp_connectors = Blueprint("routes_connectors", __name__)

@bp_connectors.route("/admin/connectors", methods=["GET"])
@require_auth
@safe_route("connectors-list")
def connectors_list():
    return jsonify(connectors.list_connectors())


@bp_connectors.route("/admin/connectors", methods=["POST"])
@require_auth
@safe_route("connectors-add")
def connectors_add():
    body = request.get_json(force=True)
    try:
        name = connectors.add_connector(
            body.get("name", ""),
            body.get("base_url", ""),
            auth_header=body.get("auth_header", ""),
            auth_value=body.get("auth_value", ""),
            default_headers=body.get("default_headers") or {},
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"saved": name})


@bp_connectors.route("/admin/connectors/<name>", methods=["DELETE"])
@require_auth
@safe_route("connectors-delete")
def connectors_delete(name):
    ok = connectors.delete_connector(name)
    return jsonify({"deleted": ok})


@bp_connectors.route("/admin/connectors/<name>/test", methods=["POST"])
@require_auth
@safe_route("connectors-test")
def connectors_test(name):
    return jsonify(connectors.test_connector(name))


# ---------------------------------------------------------------------
# Intelligence (intelligence.py) — self-healing retry/fallback health
# dashboard: which capabilities (scrape:<domain>, connector:<name>) are
# succeeding, failing, or degraded right now.
# ---------------------------------------------------------------------

@bp_connectors.route("/admin/intelligence", methods=["GET"])
@require_auth
@safe_route("intelligence-health")
def intelligence_health():
    return jsonify({
        "capabilities": intelligence.health(),
        "degraded": intelligence.degraded_capabilities(),
    })


@bp_connectors.route("/admin/intelligence/<path:capability>/reset", methods=["POST"])
@require_auth
@safe_route("intelligence-reset")
def intelligence_reset(capability):
    intelligence.reset(capability)
    return jsonify({"reset": capability})
