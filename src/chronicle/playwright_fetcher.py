"""Playwright-based fetcher for Chronicle.

For JS-rendered pages — YouTube, Twitter, most modern SPAs — the plain
httpx Fetcher returns a shell of HTML with no data. This fetcher launches
a headless Chromium browser, waits for the page to fully render, and
returns the post-JavaScript HTML.

Same `get(url) -> str` interface as `chronicle.fetch.Fetcher`, so it's
a drop-in alternative.

Requires:
    pip install "chronicle-ds[browser]"
    playwright install chromium

Usage:
    from chronicle import Scrape

    df = Scrape(
        url="https://www.youtube.com/feed/trending",
        selectors={
            "_container": "ytd-rich-item-renderer",
            "title": "a#video-title-link@title",
            "url":   "a#video-title-link@href",
        },
        use_playwright=True,
        wait_for="ytd-rich-item-renderer",
    ).to_dataframe()
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

from chronicle.exceptions import FetchError
from chronicle.fetch import DEFAULT_CACHE_DIR, DEFAULT_USER_AGENT, RateLimiter


class PlaywrightFetcher:
    """Fetches URLs with a headless Chromium browser.

    Renders JavaScript before returning HTML. Slower than the httpx
    Fetcher, but works on SPAs and any site that loads data client-side.
    """

    def __init__(
        self,
        rate_limit: float = 1.0,
        retries: int = 2,
        timeout: float = 30000.0,            # milliseconds (Playwright's unit)
        wait_for: Optional[str] = None,      # CSS selector to wait for
        wait_until: str = "domcontentloaded", # "load" | "domcontentloaded" | "networkidle"
        headless: bool = True,
        user_agent: str = DEFAULT_USER_AGENT,
        cache_dir: Optional[Path] = None,
        use_cache: bool = True,
        cookies: Optional[list[dict]] = None,  # pre-set cookies (e.g. consent bypass)
    ) -> None:
        self.rate_limiter = RateLimiter(rate_limit)
        self.retries = retries
        self.timeout = timeout
        self.wait_for = wait_for
        self.wait_until = wait_until
        self.headless = headless
        self.user_agent = user_agent
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.use_cache = use_cache
        self.cookies = cookies or []
        self._playwright = None
        self._browser = None
        self._context = None

    # ---------- lifecycle ----------

    def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise FetchError(
                "Playwright is not installed. Run:\n"
                '  pip install "chronicle-ds[browser]"\n'
                "  playwright install chromium"
            ) from e

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.launch(
                headless=self.headless
            )
        except Exception as e:
            self._playwright.stop()
            self._playwright = None
            raise FetchError(
                f"Failed to launch Chromium: {e}\n"
                "If the browser isn't installed yet, run:\n"
                "  playwright install chromium"
            ) from e

        self._context = self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 800},
        )

        # Apply pre-set cookies (e.g. YouTube consent bypass).
        # Must be applied to the context BEFORE any page loads.
        if self.cookies:
            try:
                self._context.add_cookies(self.cookies)
            except Exception as e:
                raise FetchError(
                    f"Failed to add cookies to browser context: {e}"
                ) from e

    def close(self) -> None:
        """Close the browser and stop Playwright."""
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def __enter__(self) -> "PlaywrightFetcher":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ---------- fetch ----------

    def get(self, url: str) -> str:
        """Fetch a URL, render its JavaScript, and return the HTML."""
        if self.use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        html = self._fetch_with_retries(url)

        if self.use_cache:
            self._write_cache(url, html)

        return html

    def _fetch_with_retries(self, url: str) -> str:
        self._ensure_browser()
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            self.rate_limiter.wait()
            page = None
            try:
                page = self._context.new_page()
                page.goto(url, timeout=self.timeout, wait_until=self.wait_until)
                if self.wait_for:
                    page.wait_for_selector(self.wait_for, timeout=self.timeout)
                return page.content()
            except Exception as e:
                last_error = e
                if attempt < self.retries:
                    time.sleep(1.0 * attempt)
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass

        raise FetchError(
            f"Failed to fetch {url} after {self.retries} attempts: {last_error}"
        )

    # ---------- cache ----------

    def _cache_path(self, url: str) -> Path:
        # `pw_` prefix so Playwright cache never collides with httpx cache
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"pw_{digest}.html"

    def _read_cache(self, url: str) -> Optional[str]:
        path = self._cache_path(url)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _write_cache(self, url: str, html: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_text(html, encoding="utf-8")

    def clear_cache(self) -> None:
        if not self.cache_dir.exists():
            return
        for path in self.cache_dir.glob("pw_*.html"):
            path.unlink()