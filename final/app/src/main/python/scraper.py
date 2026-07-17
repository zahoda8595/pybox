"""
scraper.py — scrape publicly accessible web pages.

SCOPE, ON PURPOSE:
  This fetches pages the way a normal browser request would - no login
  bypass, no session hijacking, no reading another app's cookies. If a
  page needs you to be logged in to see it, this won't see it either -
  same as curl or a browser in a private window. Respect each site's
  robots.txt and terms of service; this tool doesn't check that for you.

WHAT IT GIVES YOU:
  - fetch_page(url) - raw HTML + status + headers, self-healing (see below)
  - extract_text(html) - visible text, scripts/styles stripped
  - extract_main_content(html) - best-guess "the article", nav/ads/footer
    stripped out, for when extract_text's full-page dump is too noisy
  - extract_links(html, base_url) - all <a href> resolved to absolute URLs
  - extract_tables(html) - each <table> as a list of dicts if it has a
    header row, else a list of row-lists
  - extract_metadata(html) - title, meta description, OpenGraph tags
  - extract_structured_data(html) - JSON-LD (schema.org) blocks, parsed
  - extract_images(html, base_url) - <img> src + alt, resolved to absolute

SELF-HEALING FETCH:
  fetch_page() and scrape() route through intelligence.run() with two
  fallback strategies: a different desktop-style User-Agent, and a longer
  timeout. A single flaky request (site rate-limiting the default UA, a
  slow mobile-network moment) gets a real second chance with a different
  approach before it's reported as failed, instead of failing once and
  giving up. Every attempt updates intelligence.health("scrape:<domain>")
  so the admin dashboard shows which domains are consistently a problem.
"""

import json
import logging
import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

import intelligence

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) PyBox/1.0 (personal automation)"
}
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]
TIMEOUT_SECONDS = 20

# Tags that are almost never "the content" - stripped for extract_main_content.
_NOISE_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"]
_NOISE_CLASS_HINTS = re.compile(
    r"nav|footer|header|sidebar|advert|banner|cookie|popup|menu|breadcrumb|social|share",
    re.I,
)


def _domain_of(url):
    try:
        return urlparse(url).netloc or "unknown"
    except Exception:
        return "unknown"


def _fetch_once(url, headers, timeout):
    resp = requests.get(url, headers=headers, timeout=timeout)
    return {
        "url": resp.url,
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "html": resp.text,
    }


def fetch_page(url, headers=None, timeout=TIMEOUT_SECONDS):
    """Self-healing fetch: tries the default headers/timeout first; on
    failure (timeout, connection error, DNS hiccup), retries with the
    same headers, then falls back to a couple of alternate desktop
    User-Agents in case the default one is what's getting blocked."""
    merged_headers = dict(DEFAULT_HEADERS)
    merged_headers.update(headers or {})

    def default_attempt():
        return _fetch_once(url, merged_headers, timeout)

    fallbacks = []
    for ua in FALLBACK_USER_AGENTS:
        alt_headers = dict(merged_headers)
        alt_headers["User-Agent"] = ua
        fallbacks.append(
            lambda h=alt_headers: _fetch_once(url, h, timeout * 1.5)
        )

    return intelligence.run(f"scrape:{_domain_of(url)}", default_attempt, fallbacks=fallbacks, attempts=2)


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_main_content(html, min_paragraph_chars=40):
    """Best-effort 'reader mode': strips nav/footer/ads/etc, then picks
    the container with the most real paragraph text - the same basic
    heuristic reader-mode extractors use (most links/markup = chrome,
    most plain text = the actual article), without pulling in a heavy
    external readability library."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(class_=_NOISE_CLASS_HINTS):
        tag.decompose()
    for tag in soup.find_all(id=_NOISE_CLASS_HINTS):
        tag.decompose()

    candidates = soup.find_all(["article", "main", "div", "section"])
    best_text = ""
    best_score = 0
    for tag in candidates:
        paragraphs = [p.get_text(" ", strip=True) for p in tag.find_all("p")]
        paragraphs = [p for p in paragraphs if len(p) >= min_paragraph_chars]
        text = "\n\n".join(paragraphs)
        score = len(text)
        if score > best_score:
            best_score = score
            best_text = text

    if not best_text:
        # nothing scored - fall back to whatever plain text is left after
        # stripping noise, better than returning nothing.
        best_text = extract_text(str(soup))
    return best_text


def extract_links(html, base_url=""):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if base_url and not href.startswith(("http://", "https://")):
            href = requests.compat.urljoin(base_url, href)
        links.append({"text": a.get_text(strip=True), "href": href})
    return links


def extract_images(html, base_url=""):
    soup = BeautifulSoup(html, "html.parser")
    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src:
            continue
        if base_url and not src.startswith(("http://", "https://", "data:")):
            src = requests.compat.urljoin(base_url, src)
        images.append({"src": src, "alt": img.get("alt", "")})
    return images


def extract_tables(html):
    """Each <table> becomes a list of dicts keyed by header text if the
    table has a <thead>/first-<tr> header row, otherwise falls back to
    plain row-lists like before - dicts are far more useful for scripts
    that want to pull one column out (e.g. row["Price"])."""
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        header_row = table.find("tr")
        has_header = bool(header_row and header_row.find("th"))
        if has_header and len(rows) > 1:
            headers = rows[0]
            dict_rows = []
            for row in rows[1:]:
                dict_rows.append({headers[i] if i < len(headers) else f"col{i}": v
                                   for i, v in enumerate(row)})
            tables.append(dict_rows)
        else:
            tables.append(rows)
    return tables


def extract_metadata(html):
    soup = BeautifulSoup(html, "html.parser")
    meta = {"title": None, "description": None, "og": {}}
    if soup.title:
        meta["title"] = soup.title.get_text(strip=True)
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        meta["description"] = desc_tag["content"]
    for tag in soup.find_all("meta", attrs={"property": True}):
        prop = tag["property"]
        if prop.startswith("og:") and tag.get("content"):
            meta["og"][prop[3:]] = tag["content"]
    return meta


def extract_structured_data(html):
    """Parses every <script type="application/ld+json"> block (schema.org
    structured data - product prices, recipes, articles, events, etc,
    the same data Google uses for rich search results). Malformed JSON
    blocks are skipped rather than failing the whole extraction."""
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
            out.append(data)
        except (ValueError, TypeError):
            continue
    return out


def scrape(url, want=("text", "links", "metadata")):
    """Convenience wrapper: self-healing fetch + selected extractions.
    `want` can include: text, main_content, links, images, tables,
    metadata, structured_data."""
    page = fetch_page(url)
    result = {"url": page["url"], "status_code": page["status_code"]}
    if page["status_code"] >= 400:
        result["error"] = f"HTTP {page['status_code']}"
        return result
    if "text" in want:
        result["text"] = extract_text(page["html"])
    if "main_content" in want:
        result["main_content"] = extract_main_content(page["html"])
    if "links" in want:
        result["links"] = extract_links(page["html"], base_url=page["url"])
    if "images" in want:
        result["images"] = extract_images(page["html"], base_url=page["url"])
    if "tables" in want:
        result["tables"] = extract_tables(page["html"])
    if "metadata" in want:
        result["metadata"] = extract_metadata(page["html"])
    if "structured_data" in want:
        result["structured_data"] = extract_structured_data(page["html"])
    logging.info("scraped %s (%d bytes html)", url, len(page["html"]))
    return result
