"""
scraper.py — scrape publicly accessible web pages.

SCOPE, ON PURPOSE:
  This fetches pages the way a normal browser request would - no login
  bypass, no session hijacking, no reading another app's cookies. If a
  page needs you to be logged in to see it, this won't see it either -
  same as curl or a browser in a private window. Respect each site's
  robots.txt and terms of service; this tool doesn't check that for you.

WHAT IT GIVES YOU:
  - fetch_page(url) - raw HTML + status + headers
  - extract_text(html) - visible text, scripts/styles stripped
  - extract_links(html, base_url) - all <a href> resolved to absolute URLs
  - extract_tables(html) - each <table> as a list of row-lists
  - extract_metadata(html) - title, meta description, OpenGraph tags
"""

import logging

import requests
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) PyBox/1.0 (personal automation)"
}
TIMEOUT_SECONDS = 20


def fetch_page(url, headers=None, timeout=TIMEOUT_SECONDS):
    merged_headers = dict(DEFAULT_HEADERS)
    merged_headers.update(headers or {})
    resp = requests.get(url, headers=merged_headers, timeout=timeout)
    return {
        "url": resp.url,
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "html": resp.text,
    }


def extract_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_links(html, base_url=""):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if base_url and not href.startswith(("http://", "https://")):
            href = requests.compat.urljoin(base_url, href)
        links.append({"text": a.get_text(strip=True), "href": href})
    return links


def extract_tables(html):
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if rows:
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


def scrape(url, want=("text", "links", "metadata")):
    """Convenience wrapper: fetch + selected extractions in one call."""
    page = fetch_page(url)
    result = {"url": page["url"], "status_code": page["status_code"]}
    if page["status_code"] >= 400:
        result["error"] = f"HTTP {page['status_code']}"
        return result
    if "text" in want:
        result["text"] = extract_text(page["html"])
    if "links" in want:
        result["links"] = extract_links(page["html"], base_url=page["url"])
    if "tables" in want:
        result["tables"] = extract_tables(page["html"])
    if "metadata" in want:
        result["metadata"] = extract_metadata(page["html"])
    logging.info("scraped %s (%d bytes html)", url, len(page["html"]))
    return result
