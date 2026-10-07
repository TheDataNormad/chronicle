"""End-to-end test of the scrape → store → drift pipeline.

This is the test that should have existed in v0.1.0. It spins up a real
local HTTP server, serves different HTML between runs, and asserts that
the full Chronicle stack (fetch → parse → normalize → profile → storage
→ drift) behaves correctly.

Specifically, it protects against regressions of:

- use_cache defaulting back to True (would return stale HTML and defeat
  the entire point of drift detection)
- Zero-row results being skipped in drift detection (a broken selector
  would silently produce no alert)
- Storage failing to persist a baseline across runs
"""

from __future__ import annotations

import http.server
import socketserver
import threading
from contextlib import contextmanager

from pydantic import BaseModel

from chronicle import Scrape

# ------------------------------------------------------------------ server

_SERVER_STATE: dict[str, object] = {"body": b"", "status": 200}


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves whatever body is currently in _SERVER_STATE."""

    def do_GET(self):
        body = _SERVER_STATE["body"]
        status = _SERVER_STATE["status"]
        assert isinstance(body, bytes)
        assert isinstance(status, int)

        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args, **kwargs):
        # Silence request logging during tests
        pass


@contextmanager
def http_server(initial_html: str = ""):
    """Run a local HTTP server on a random port. Yields its base URL."""
    _SERVER_STATE["body"] = initial_html.encode("utf-8")
    _SERVER_STATE["status"] = 200

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as httpd:
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}/"
        finally:
            httpd.shutdown()
            thread.join(timeout=2)


def set_html(html: str) -> None:
    """Change the HTML the server will return on the next request."""
    _SERVER_STATE["body"] = html.encode("utf-8")


# ------------------------------------------------------------------ fixtures

HTML_V1 = """
<html><body>
<div class="product"><span class="name">Widget</span><span class="price">$10.00</span></div>
<div class="product"><span class="name">Gadget</span><span class="price">$20.00</span></div>
<div class="product"><span class="name">Gizmo</span><span class="price">$30.00</span></div>
</body></html>
"""

HTML_V2_NEW_PRICES = """
<html><body>
<div class="product"><span class="name">Widget</span><span class="price">$1000.00</span></div>
<div class="product"><span class="name">Gadget</span><span class="price">$1000.00</span></div>
<div class="product"><span class="name">Gizmo</span><span class="price">$1000.00</span></div>
</body></html>
"""

# Structure is changed so the selector 'div.product' returns nothing.
HTML_V3_SELECTOR_BROKEN = """
<html><body>
<div class="totally-different-class">Widget</div>
<div class="totally-different-class">Gadget</div>
</body></html>
"""


class Product(BaseModel):
    name: str
    price: float


SELECTORS = {
    "_container": "div.product",
    "name": "span.name",
    "price": "span.price",
}


def _scrape(url: str, storage_root: str):
    return Scrape(
        url=url,
        selectors=SELECTORS,
        schema=Product,
        storage_root=storage_root,
        rate_limit=1000.0,  # local server; no need to throttle
    ).run()


# ------------------------------------------------------------------ tests


def test_first_run_establishes_baseline(tmp_path):
    storage_root = str(tmp_path / "chronicle")
    with http_server(HTML_V1) as url:
        result = _scrape(url, storage_root)

    assert result.is_first_run is True
    assert result.rows_scraped == 3
    assert result.drift_report is None
    assert not result.has_drift()


def test_unchanged_html_reports_no_drift(tmp_path):
    storage_root = str(tmp_path / "chronicle")
    with http_server(HTML_V1) as url:
        _scrape(url, storage_root)              # run 1: baseline
        result2 = _scrape(url, storage_root)    # run 2: same HTML

    assert result2.is_first_run is False
    assert result2.rows_scraped == 3
    assert not result2.has_drift(), (
        "Identical HTML across two runs must not trigger drift."
    )


def test_changed_prices_trigger_drift(tmp_path):
    """Regression test for the cache-on-by-default bug.

    If use_cache defaults back to True, run 2 sees run 1's cached HTML
    and drift reports NORMAL. This test would fail. That is the point.
    """
    storage_root = str(tmp_path / "chronicle")
    with http_server(HTML_V1) as url:
        _scrape(url, storage_root)              # run 1: mean price = $20

        set_html(HTML_V2_NEW_PRICES)            # site changes
        result2 = _scrape(url, storage_root)    # run 2: mean price = $1000

    assert result2.has_drift(), (
        "Drift must fire after prices change. If this fails, verify that "
        "use_cache=False is the default in Scrape.__init__ and that the "
        "fetcher actually reaches the local server on run 2."
    )
    assert result2.drift_report is not None
    assert result2.drift_report.severity in ("WARNING", "SEVERE")


def test_zero_rows_after_baseline_is_severe_drift(tmp_path):
    """Regression test for the missing zero-row check.

    If the current run returns 0 rows but the baseline had rows, that is
    SEVERE drift — likely a broken selector. It must not be silent.
    """
    storage_root = str(tmp_path / "chronicle")
    with http_server(HTML_V1) as url:
        _scrape(url, storage_root)              # run 1: 3 rows

        set_html(HTML_V3_SELECTOR_BROKEN)       # container class gone
        result2 = _scrape(url, storage_root)    # run 2: 0 rows

    assert result2.rows_scraped == 0
    assert result2.has_drift(), (
        "Zero rows after a nonzero baseline must trigger drift. If this "
        "fails, check _compute_drift in core.py."
    )
    assert result2.drift_report is not None
    assert result2.drift_report.severity == "SEVERE"


def test_zero_rows_on_first_run_is_not_drift(tmp_path):
    """An empty first run has no baseline to compare to — no drift."""
    storage_root = str(tmp_path / "chronicle")
    with http_server(HTML_V3_SELECTOR_BROKEN) as url:
        result = _scrape(url, storage_root)

    assert result.is_first_run is True
    assert result.rows_scraped == 0
    assert not result.has_drift()