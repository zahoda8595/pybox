"""
search_engine.py — multi-engine web search with two speed/depth modes.

WHY MULTIPLE ENGINES:
  Querying more than one public search engine and keeping URLs that show
  up across several of them (consensus ranking) is a cheap, honest way
  to surface results that are more likely to actually be relevant,
  instead of trusting any single engine's ranking blindly.

FAST MODE (search_fast):
  Hits each engine's public HTML results page in parallel, parses
  title/url/snippet, dedupes by normalized URL, ranks by how many
  engines agreed + position. Returns in a couple seconds. No page
  bodies are fetched - just what the search engines themselves show.

DEEP MODE (search_deep):
  Runs search_fast first, then actually visits the top N result pages
  (parallel, bounded concurrency) via scraper.py, pulls out real body
  text, and - if the local LLM engine (llama.cpp, via backend_app's
  /llm/generate proxy) is running - asks it to synthesize a short,
  source-grounded answer from what was actually fetched. If the LLM
  isn't running, you still get the full per-source extracted text,
  just without the synthesis step. This is slower and network-heavier
  on purpose: it trades speed for actually reading the pages instead
  of trusting snippets.

WHAT THIS DOESN'T DO:
  No JS rendering (that's what BrowserActivity.kt + browser.py are for,
  on pages you explicitly open). No bypassing paywalls/logins. No
  hammering a single engine hard enough to get rate-limited/blocked -
  concurrency is bounded and each engine is only hit once per query.
"""

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

import scraper

TIMEOUT_SECONDS = 12
MAX_DEEP_FETCH_CONCURRENCY = 4
MAX_PAGE_CHARS = 4000
LLM_BASE_URL = "http://127.0.0.1:8081"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14) PyBox/1.0 (personal automation)"
}


# ---------------------------------------------------------------------
# Per-engine result fetchers. Each returns a list of
# {"title", "url", "snippet"} dicts, best-effort - a failing engine
# just contributes an empty list, it never blocks the others.
# ---------------------------------------------------------------------

def _get(url, params):
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers=DEFAULT_HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
        return r.read().decode("utf-8", errors="replace")


def _engine_duckduckgo(query, max_results):
    try:
        html = _get("https://html.duckduckgo.com/html/", {"q": query})
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for res in soup.select("div.result")[:max_results]:
            a = res.select_one("a.result__a")
            snippet_tag = res.select_one(".result__snippet")
            if not a or not a.get("href"):
                continue
            out.append({
                "title": a.get_text(strip=True),
                "url": a["href"],
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
            })
        return out
    except Exception as e:
        logging.info("search_engine: duckduckgo failed: %s", e)
        return []


def _engine_bing(query, max_results):
    try:
        html = _get("https://www.bing.com/search", {"q": query})
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for li in soup.select("li.b_algo")[:max_results]:
            a = li.select_one("h2 a")
            snippet_tag = li.select_one(".b_caption p")
            if not a or not a.get("href"):
                continue
            out.append({
                "title": a.get_text(strip=True),
                "url": a["href"],
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
            })
        return out
    except Exception as e:
        logging.info("search_engine: bing failed: %s", e)
        return []


def _engine_startpage(query, max_results):
    try:
        html = _get("https://www.startpage.com/sp/search", {"query": query})
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for res in soup.select("div.result")[:max_results]:
            a = res.select_one("a.result-title") or res.select_one("a")
            snippet_tag = res.select_one("p.description")
            if not a or not a.get("href"):
                continue
            out.append({
                "title": a.get_text(strip=True),
                "url": a["href"],
                "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
            })
        return out
    except Exception as e:
        logging.info("search_engine: startpage failed: %s", e)
        return []


ENGINES = {
    "duckduckgo": _engine_duckduckgo,
    "bing": _engine_bing,
    "startpage": _engine_startpage,
}


# ---------------------------------------------------------------------
# Merge + consensus ranking
# ---------------------------------------------------------------------

def _normalize_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower().removeprefix("www.")
        path = parsed.path.rstrip("/")
        return f"{netloc}{path}"
    except Exception:
        return url


def _merge_and_rank(results_by_engine, max_results):
    """Dedupe by normalized URL. Score = sum of (engines seen) with a
    per-engine position bonus (earlier position = higher weight), so a
    result appearing near the top on multiple engines wins clearly over
    one appearing once, and a #1-on-one-engine result still beats a
    #20-on-one-engine result."""
    merged = {}
    for engine_name, results in results_by_engine.items():
        for position, r in enumerate(results):
            key = _normalize_url(r["url"])
            position_score = max(0.0, 1.0 - position * 0.08)
            if key not in merged:
                merged[key] = {
                    "title": r["title"],
                    "url": r["url"],
                    "snippet": r["snippet"],
                    "score": 0.0,
                    "seen_on": [],
                }
            merged[key]["score"] += 1.0 + position_score
            merged[key]["seen_on"].append(engine_name)
            # prefer the longest snippet seen for this URL
            if len(r["snippet"]) > len(merged[key]["snippet"]):
                merged[key]["snippet"] = r["snippet"]

    ranked = sorted(merged.values(), key=lambda r: r["score"], reverse=True)
    return ranked[:max_results]


# ---------------------------------------------------------------------
# FAST MODE
# ---------------------------------------------------------------------

def search_fast(query, max_results=10, engines=("duckduckgo", "bing", "startpage")):
    started = time.time()
    results_by_engine = {}
    with ThreadPoolExecutor(max_workers=len(engines) or 1) as pool:
        futures = {
            pool.submit(ENGINES[name], query, max_results * 2): name
            for name in engines if name in ENGINES
        }
        for fut in as_completed(futures):
            name = futures[fut]
            results_by_engine[name] = fut.result()

    ranked = _merge_and_rank(results_by_engine, max_results)
    return {
        "mode": "fast",
        "query": query,
        "engines_used": list(results_by_engine.keys()),
        "engines_with_results": {k: len(v) for k, v in results_by_engine.items()},
        "results": ranked,
        "elapsed_seconds": round(time.time() - started, 2),
    }


# ---------------------------------------------------------------------
# DEEP MODE
# ---------------------------------------------------------------------

def _score_relevance(text, query_terms):
    if not text:
        return 0
    lower = text.lower()
    return sum(lower.count(term) for term in query_terms)


def _fetch_one_source(result, query_terms):
    url = result["url"]
    try:
        page = scraper.fetch_page(url)
        if page["status_code"] >= 400:
            return {**result, "error": f"HTTP {page['status_code']}", "fetched": False}
        text = scraper.extract_text(page["html"])[:MAX_PAGE_CHARS]
        return {
            **result,
            "fetched": True,
            "full_text": text,
            "relevance_score": _score_relevance(text, query_terms),
        }
    except Exception as e:
        return {**result, "error": str(e), "fetched": False}


def _llm_running():
    try:
        req = urllib.request.Request(f"{LLM_BASE_URL}/health")
        with urllib.request.urlopen(req, timeout=1.5):
            return True
    except Exception:
        return False


def _synthesize_with_llm(query, sources):
    """Best-effort synthesis via the local llama.cpp server. Only ever
    runs against text actually fetched in this request - never invents
    sources, and the prompt tells the model to say so if the sources
    don't answer the question."""
    context_blocks = []
    for i, s in enumerate(sources, 1):
        if not s.get("fetched"):
            continue
        snippet = s["full_text"][:1200]
        context_blocks.append(f"[Source {i}: {s['title']} ({s['url']})]\n{snippet}")
    if not context_blocks:
        return None

    prompt = (
        "You are answering a question using ONLY the sources below. "
        "Cite sources inline like [1], [2]. If the sources don't fully "
        f"answer it, say what's missing.\n\nQuestion: {query}\n\n"
        + "\n\n".join(context_blocks)
        + "\n\nAnswer:"
    )
    payload = json.dumps({
        "prompt": prompt,
        "n_predict": 400,
        "temperature": 0.3,
        "stop": ["\nQuestion:"],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LLM_BASE_URL}/completion",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
            return data.get("content", "").strip()
    except Exception as e:
        logging.info("search_engine: LLM synthesis skipped: %s", e)
        return None


def search_deep(query, max_results=6, synthesize=True,
                 engines=("duckduckgo", "bing", "startpage")):
    started = time.time()
    fast = search_fast(query, max_results=max(max_results, 8), engines=engines)
    candidates = fast["results"][:max_results]

    query_terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 2]

    fetched = []
    with ThreadPoolExecutor(max_workers=MAX_DEEP_FETCH_CONCURRENCY) as pool:
        futures = [pool.submit(_fetch_one_source, r, query_terms) for r in candidates]
        for fut in as_completed(futures):
            fetched.append(fut.result())

    # re-rank by actual page relevance now that we've read the pages,
    # not just by search-engine consensus score
    fetched.sort(key=lambda r: (r.get("fetched", False), r.get("relevance_score", 0)),
                 reverse=True)

    synthesis = None
    llm_available = False
    if synthesize:
        llm_available = _llm_running()
        if llm_available:
            synthesis = _synthesize_with_llm(query, fetched)

    return {
        "mode": "deep",
        "query": query,
        "engines_used": fast["engines_used"],
        "sources": fetched,
        "synthesis": synthesis,
        "llm_available": llm_available,
        "elapsed_seconds": round(time.time() - started, 2),
    }
