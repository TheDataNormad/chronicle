"""HTTP fetching layer for Chronicle.

This module handles:
- HTTP requests with httpx
- Automatic retries with exponential backoff
- Per-host rate limiting
- File-based response caching (so we don't hammer sites)
- Clean error handling via FetchError
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx

from chronicle.exceptions import FetchError

DEFAULT_USER_AGENT = "chronicle/0.1.0 (+https://github.com/TheDataNormad/chronicle)"
DEFAULT_TIMEOUT = 15.0
DEFAULT_CACHE_DIR = Path.home() / ".chronicle" / "cache"


class RateLimiter:
    """Blocks requests to enforce a per-second limit.

    Example:
        limiter = RateLimiter(per_second=2.0)  # 2 requests per second
        limiter.wait()  # blocks until next slot is available
    """

    def __init__(self, per_second: float = 1.0) -> None:
        if per_second <= 0:
            raise ValueError("per_second must be > 0")
        self.interval = 1.0 / per_second
        self._last_call = 0.0

    def wait(self) -> None:
        """Block until the next request is allowed."""
        elapsed = time.monotonic() - self._last_call
        if elapsed < self.interval:
            time.sleep(self.interval - elapsed)
        self._last_call = time.monotonic()


class Fetcher:
    """Fetches URLs with retries, rate limiting, and caching.

    Usage:
        fetcher = Fetcher(rate_limit=2.0, retries=3)
        html = fetcher.get("https://example.com")
    """

    def __init__(
        self,
        rate_limit: float = 1.0,
        retries: int = 3,
        backoff: float = 0.5,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        cache_dir: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self.rate_limiter = RateLimiter(rate_limit)
        self.retries = retries
        self.backoff = backoff
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.use_cache = use_cache
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
        )

    def get(self, url: str) -> str:
        """Fetch a URL and return the response body as text."""
        if self.use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        html = self._request_with_retries(url)

        if self.use_cache:
            self._write_cache(url, html)

        return html

    def _request_with_retries(self, url: str) -> str:
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            self.rate_limiter.wait()
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as e:
                # Don't retry 4xx client errors (except 429 Too Many Requests)
                if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise FetchError(
                        f"Client error {e.response.status_code} for {url}"
                    ) from e
                last_error = e
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e

            if attempt < self.retries:
                sleep_time = self.backoff * (2 ** (attempt - 1))
                time.sleep(sleep_time)

        raise FetchError(
            f"Failed to fetch {url} after {self.retries} attempts: {last_error}"
        )

    # ---- cache helpers ----

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.html"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _write_cache(self, url: str, html: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_text(html, encoding="utf-8")

    def clear_cache(self) -> None:
        """Delete all cached responses."""
        if not self.cache_dir.exists():
            return
        for path in self.cache_dir.glob("*.html"):
            path.unlink()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *args) -> None:
        self.close()