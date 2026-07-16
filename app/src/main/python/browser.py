"""
browser.py — backend support for BrowserActivity.kt (PyBox's in-app browser).

WHY THIS IS SEPARATE FROM scraper.py:
  scraper.py fetches raw HTML via HTTP - it never sees anything a page's
  JavaScript renders afterward. BrowserActivity.kt sends this module the
  ACTUAL RENDERED DOM after the WebView has run the page's JS, so this
  can extract data from JS-heavy pages scraper.py fundamentally can't
  see. Both are legitimate, complementary tools depending on the page.

RULES:
  A "rule" is just a named CSS selector, saved per-domain, so you don't
  have to reinvent extraction logic every time you visit the same site.
  Example: for a price-tracking site, save a rule {"price": ".product-price"}
  once, and every future extraction on that domain pulls that field
  automatically alongside the generic text/links/metadata extraction.
"""

import json
import logging
import os
import sqlite3

from bs4 import BeautifulSoup

_DB_PATH = None


def _conn():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init(files_dir):
    global _DB_PATH
    _DB_PATH = os.path.join(files_dir, "browser.db")
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            field_name TEXT NOT NULL,
            css_selector TEXT NOT NULL,
            UNIQUE(domain, field_name)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS extractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            domain TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _domain_of(url):
    from urllib.parse import urlparse
    return urlparse(url).netloc


def get_rules(domain):
    conn = _conn()
    rows = conn.execute(
        "SELECT field_name, css_selector FROM rules WHERE domain = ?", (domain,)
    ).fetchall()
    conn.close()
    return {r["field_name"]: r["css_selector"] for r in rows}


def set_rule(domain, field_name, css_selector):
    conn = _conn()
    conn.execute(
        "INSERT INTO rules (domain, field_name, css_selector) VALUES (?, ?, ?) "
        "ON CONFLICT(domain, field_name) DO UPDATE SET css_selector = excluded.css_selector",
        (domain, field_name, css_selector),
    )
    conn.commit()
    conn.close()


def delete_rule(domain, field_name):
    conn = _conn()
    conn.execute(
        "DELETE FROM rules WHERE domain = ? AND field_name = ?", (domain, field_name)
    )
    conn.commit()
    conn.close()


def extract(url, html):
    """Runs generic extraction (text/links/metadata) plus any saved
    per-domain rules against already-rendered HTML."""
    import time
    domain = _domain_of(url)
    soup = BeautifulSoup(html, "html.parser")

    result = {"url": url, "domain": domain}

    if soup.title:
        result["title"] = soup.title.get_text(strip=True)

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    result["text"] = "\n".join(line.strip() for line in text.splitlines() if line.strip())[:5000]

    rules = get_rules(domain)
    if rules:
        custom = {}
        # re-parse since the text-extraction pass above stripped scripts/
        # styles in place - fine for text, but keep custom-field selectors
        # working against a fresh, untouched parse.
        soup2 = BeautifulSoup(html, "html.parser")
        for field_name, selector in rules.items():
            matches = soup2.select(selector)
            if len(matches) == 1:
                custom[field_name] = matches[0].get_text(strip=True)
            elif len(matches) > 1:
                custom[field_name] = [m.get_text(strip=True) for m in matches]
            else:
                custom[field_name] = None
        result["custom_fields"] = custom

    conn = _conn()
    conn.execute(
        "INSERT INTO extractions (url, domain, result_json, created_at) VALUES (?, ?, ?, ?)",
        (url, domain, json.dumps(result), time.time()),
    )
    conn.commit()
    conn.close()

    logging.info("browser: extracted data from %s (%d rule fields)", url, len(rules))
    return result
