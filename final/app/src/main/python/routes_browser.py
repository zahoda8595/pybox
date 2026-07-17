"""Blueprint: routes_browser - split from the original monolithic backend_app.py."""

from flask import Blueprint, jsonify, request

import browser
from auth import require_auth
from error_manager import safe_route

bp_browser = Blueprint("routes_browser", __name__)

@bp_browser.route("/browser/extract", methods=["POST"])
@require_auth
@safe_route("browser-extract")
def browser_extract():
    body = request.get_json(force=True)
    return jsonify(browser.extract(body["url"], body["html"]))


@bp_browser.route("/browser/rules", methods=["GET"])
@require_auth
@safe_route("browser-rules-get")
def browser_rules_get():
    domain = request.args.get("domain", "")
    return jsonify(browser.get_rules(domain))


@bp_browser.route("/browser/rules", methods=["POST"])
@require_auth
@safe_route("browser-rules-set")
def browser_rules_set():
    body = request.get_json(force=True)
    browser.set_rule(body["domain"], body["field_name"], body["css_selector"])
    return jsonify(browser.get_rules(body["domain"]))


@bp_browser.route("/browser/rules", methods=["DELETE"])
@require_auth
@safe_route("browser-rules-delete")
def browser_rules_delete():
    body = request.get_json(force=True)
    browser.delete_rule(body["domain"], body["field_name"])
    return jsonify(browser.get_rules(body["domain"]))
