"""Blueprint: routes_scrape_osint - split from the original monolithic backend_app.py."""

from flask import Blueprint, jsonify, request

import osint_tools
import scraper
from auth import require_auth
from error_manager import safe_route

bp_scrape_osint = Blueprint("routes_scrape_osint", __name__)

@bp_scrape_osint.route("/scrape", methods=["POST"])
@require_auth
@safe_route("scrape")
def scrape_route():
    body = request.get_json(force=True)
    url = body["url"]
    want = body.get("want", ["text", "links", "metadata"])
    return jsonify(scraper.scrape(url, want=want))


# ---------------------------------------------------------------------
# OSINT (osint_tools.py) - passive, public-records lookups only.
# ---------------------------------------------------------------------

@bp_scrape_osint.route("/osint/whois")
@require_auth
@safe_route("osint-whois")
def osint_whois():
    domain = request.args.get("domain")
    return jsonify(osint_tools.whois_lookup(domain))


@bp_scrape_osint.route("/osint/dns")
@require_auth
@safe_route("osint-dns")
def osint_dns():
    domain = request.args.get("domain")
    return jsonify(osint_tools.dns_lookup(domain))


@bp_scrape_osint.route("/osint/fingerprint")
@require_auth
@safe_route("osint-fingerprint")
def osint_fingerprint():
    url = request.args.get("url")
    return jsonify(osint_tools.http_fingerprint(url))


@bp_scrape_osint.route("/osint/subdomains")
@require_auth
@safe_route("osint-subdomains")
def osint_subdomains():
    domain = request.args.get("domain")
    return jsonify(osint_tools.subdomain_search(domain))


@bp_scrape_osint.route("/osint/file-metadata")
@require_auth
@safe_route("osint-file-metadata")
def osint_file_metadata():
    path = request.args.get("path")
    return jsonify(osint_tools.file_metadata(path))
