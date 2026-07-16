"""
osint_report.py — PyBox plugin: one-call composite OSINT recon report.

Runs whois_lookup + dns_lookup + subdomain_search + http_fingerprint
(all from osint_tools.py, all passive/public-records-only - see that
file's docstring for what's deliberately excluded and why) against one
domain in a single call, and saves the combined result as a
timestamped JSON report you can pull up again later without re-running
everything.

Why this is hard to find elsewhere: most free OSINT tools make you run
each lookup separately and stitch results together yourself; paid
recon platforms (SpiderFoot HX, etc.) charge per-scan or per-seat.
This runs the same category of passive lookups, entirely on your own
phone, for free, with results kept locally instead of on someone
else's server.

REMINDER (same as osint_tools.py): only run this against domains you
own or have clear permission to research. Every lookup here is passive
(public records only) - nothing here scans or probes the target's own
infrastructure - but that doesn't mean point-and-shoot at anything.

SETUP:
  Copy to /sdcard/PyBox/plugins/osint_report.py, reload plugins.

USE:
  POST /plugins/osint_report/run   {"domain": "example.com"}
  GET  /plugins/osint_report/list  - past reports
  GET  /plugins/osint_report/<id>  - one full report
    (note: that last one isn't reachable through the dispatcher's flat
    namespace - use the list route's returned data instead, or extend
    this plugin yourself; kept simple here on purpose)
"""

import json
import logging
import os
import sqlite3
import time

from flask import request

_DB = None
_osint = None


def _conn():
    return sqlite3.connect(_DB)


def _init_db(files_dir):
    global _DB
    _DB = os.path.join(files_dir, "osint_report.db")
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            report_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def run_report():
    body = request.get_json(force=True)
    domain = body["domain"]

    report = {
        "domain": domain,
        "whois": _osint.whois_lookup(domain),
        "dns": _osint.dns_lookup(domain),
        "subdomains": _osint.subdomain_search(domain),
        "fingerprint": _osint.http_fingerprint(f"https://{domain}"),
        "generated_at": time.time(),
    }

    conn = _conn()
    conn.execute(
        "INSERT INTO reports (domain, report_json, created_at) VALUES (?, ?, ?)",
        (domain, json.dumps(report), report["generated_at"]),
    )
    conn.commit()
    conn.close()

    logging.info("osint_report: generated report for %s", domain)
    return report


def list_reports():
    conn = _conn()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, domain, created_at FROM reports ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return {"reports": [dict(r) for r in rows]}


def register(ctx):
    global _osint
    import osint_tools as _osint_module
    _osint = _osint_module

    _init_db(ctx["files_dir"])
    ctx["plugin_routes"]["osint_report/run"] = run_report
    ctx["plugin_routes"]["osint_report/list"] = list_reports
    logging.info("osint_report plugin loaded")
