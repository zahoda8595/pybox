import os
import sys
from unittest.mock import MagicMock, patch

PYTHON_SRC = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "app", "src", "main", "python")
)
if PYTHON_SRC not in sys.path:
    sys.path.insert(0, PYTHON_SRC)

import intelligence  # noqa: E402
import scraper  # noqa: E402


def setup_module():
    intelligence.init("/tmp")


SAMPLE_HTML = """
<html><head><title>Test Page</title>
<meta name="description" content="A test page">
<meta property="og:title" content="OG Title">
<script type="application/ld+json">{"@type": "Article", "headline": "Hi"}</script>
</head>
<body>
<nav><a href="/menu1">Menu</a></nav>
<article>
<p>This is the first real paragraph of the article, long enough to count as content.</p>
<p>This is the second real paragraph, also long enough to be picked up by the heuristic.</p>
</article>
<footer>Copyright footer text that should be stripped out entirely.</footer>
<table>
<tr><th>Name</th><th>Price</th></tr>
<tr><td>Widget</td><td>$5</td></tr>
<tr><td>Gadget</td><td>$10</td></tr>
</table>
<img src="/pic.jpg" alt="a picture">
<a href="/relative-link">Relative</a>
<a href="https://example.com/abs">Absolute</a>
</body></html>
"""


def test_extract_metadata():
    meta = scraper.extract_metadata(SAMPLE_HTML)
    assert meta["title"] == "Test Page"
    assert meta["description"] == "A test page"
    assert meta["og"]["title"] == "OG Title"


def test_extract_structured_data():
    data = scraper.extract_structured_data(SAMPLE_HTML)
    assert len(data) == 1
    assert data[0]["@type"] == "Article"


def test_extract_main_content_strips_nav_and_footer():
    content = scraper.extract_main_content(SAMPLE_HTML)
    assert "first real paragraph" in content
    assert "second real paragraph" in content
    assert "Copyright footer" not in content
    assert "Menu" not in content


def test_extract_tables_becomes_dicts_with_header_row():
    tables = scraper.extract_tables(SAMPLE_HTML)
    assert len(tables) == 1
    rows = tables[0]
    assert rows[0] == {"Name": "Widget", "Price": "$5"}
    assert rows[1] == {"Name": "Gadget", "Price": "$10"}


def test_extract_images_resolves_relative_src():
    images = scraper.extract_images(SAMPLE_HTML, base_url="https://example.com/page")
    assert images[0]["src"] == "https://example.com/pic.jpg"
    assert images[0]["alt"] == "a picture"


def test_extract_links_resolves_relative_and_keeps_absolute():
    links = scraper.extract_links(SAMPLE_HTML, base_url="https://example.com/page")
    hrefs = [l["href"] for l in links]
    assert "https://example.com/relative-link" in hrefs
    assert "https://example.com/abs" in hrefs


def test_fetch_page_retries_then_succeeds_with_fallback_ua():
    ok_response = MagicMock(url="https://x.com", status_code=200, headers={}, text="<html>ok</html>")
    with patch("scraper.requests.get", side_effect=[ConnectionError("blocked"), ok_response]):
        result = scraper.fetch_page("https://x.com")
    assert result["status_code"] == 200
    assert result["html"] == "<html>ok</html>"


def test_fetch_page_raises_after_all_options_exhausted():
    with patch("scraper.requests.get", side_effect=ConnectionError("always down")):
        try:
            scraper.fetch_page("https://always-down.example.com")
            assert False, "should have raised"
        except ConnectionError:
            pass


def test_scrape_reports_http_error_without_raising():
    err_response = MagicMock(url="https://x.com/404", status_code=404, headers={}, text="not found")
    with patch("scraper.requests.get", return_value=err_response):
        result = scraper.scrape("https://x.com/404")
    assert result["error"] == "HTTP 404"
