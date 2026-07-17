"""
osint_tools.py — passive OSINT: public-records lookups only.

WHAT'S IN SCOPE (and why each is legitimate):
  - whois_lookup(domain)     - domain registration data is intentionally
                                public (that's what WHOIS is for).
  - dns_lookup(domain)       - DNS records are public by design; any
                                resolver on earth can query them.
  - http_fingerprint(url)    - reads response headers from a normal GET
                                request (server software, security
                                headers) - the same thing your browser
                                already receives on every page load.
  - subdomain_search(domain) - queries crt.sh, a public Certificate
                                Transparency log search engine. Doesn't
                                touch the target's infrastructure at all -
                                it's a public log of who requested a
                                certificate, not a scan of anything.
  - file_metadata(path)      - EXIF/metadata from a LOCAL file already on
                                your device (photos, PDFs) - useful for
                                understanding what metadata your own
                                files expose before you share them.

WHAT'S DELIBERATELY NOT HERE:
  Port scanning, vulnerability scanning, credential testing, directory
  brute-forcing, or anything that sends non-standard/probing traffic at
  a target - those cross from "look up public information" into
  "actively test a system," which needs explicit authorization from
  whoever owns that system, every time, regardless of intent. If you
  have a specific authorized pentest target in mind, that's a different,
  narrower conversation - tell me the target and your authorization and
  we can scope tooling for that specifically.

USE RESPONSIBLY: query domains/systems you own or have clear permission
to research. WHOIS/DNS/crt.sh lookups are passive and low-impact, but
running them against someone else's infrastructure without reason is
still worth thinking about before you do it.
"""

import logging
import socket

import dns.resolver
import requests
import whois as whois_lib


def whois_lookup(domain):
    try:
        w = whois_lib.whois(domain)
        return {
            "domain": domain,
            "registrar": w.registrar,
            "creation_date": str(w.creation_date),
            "expiration_date": str(w.expiration_date),
            "name_servers": w.name_servers,
            "status": w.status,
            "raw": str(w.text) if hasattr(w, "text") else None,
        }
    except Exception as e:
        return {"domain": domain, "error": str(e)}


def dns_lookup(domain, record_types=("A", "AAAA", "MX", "TXT", "NS")):
    results = {}
    resolver = dns.resolver.Resolver()
    for rtype in record_types:
        try:
            answers = resolver.resolve(domain, rtype)
            results[rtype] = [str(r) for r in answers]
        except Exception as e:
            results[rtype] = f"error: {e}"
    return {"domain": domain, "records": results}


def http_fingerprint(url):
    try:
        resp = requests.get(url, timeout=15, allow_redirects=True)
        headers = dict(resp.headers)
        security_headers = {
            k: headers.get(k)
            for k in [
                "Strict-Transport-Security", "Content-Security-Policy",
                "X-Frame-Options", "X-Content-Type-Options",
                "Referrer-Policy", "Permissions-Policy",
            ]
            if k in headers
        }
        return {
            "url": resp.url,
            "status_code": resp.status_code,
            "server": headers.get("Server"),
            "powered_by": headers.get("X-Powered-By"),
            "security_headers_present": security_headers,
            "all_headers": headers,
        }
    except Exception as e:
        return {"url": url, "error": str(e)}


def subdomain_search(domain, limit=100):
    """Public Certificate Transparency log search via crt.sh - passive,
    doesn't touch the target's own infrastructure at all."""
    try:
        resp = requests.get(
            f"https://crt.sh/?q=%25.{domain}&output=json", timeout=25
        )
        resp.raise_for_status()
        entries = resp.json()
        subdomains = set()
        for entry in entries:
            for name in entry.get("name_value", "").split("\n"):
                name = name.strip().lower()
                if name.endswith(domain):
                    subdomains.add(name)
        return {"domain": domain, "subdomains": sorted(subdomains)[:limit]}
    except Exception as e:
        return {"domain": domain, "error": str(e)}


def file_metadata(path):
    """EXIF/metadata from a local file already on your device."""
    result = {"path": path}
    try:
        if path.lower().endswith((".jpg", ".jpeg", ".tiff", ".tif")):
            import exifread
            with open(path, "rb") as f:
                tags = exifread.process_file(f, details=False)
            result["exif"] = {k: str(v) for k, v in tags.items()}
        else:
            result["note"] = "No metadata extractor wired up for this file type yet."
    except Exception as e:
        result["error"] = str(e)
    return result
