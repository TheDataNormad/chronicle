"""HTTP fetching layer for Chronicle.

This module handles:
- HTTP requests with httpx
- Automatic retries with exponential backoff
- ``Retry-After`` on 429 / 503 (CORE principle 7)
- Per-host rate limiting, including ``Crawl-delay`` from robots.txt
- ``robots.txt`` is respected by default (CORE principle 7)
- Opt-in file-based response caching (off by default: drift detection
  needs fresh data)
- Clean error handling via FetchError
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from chronicle._version import __version__
from chronicle.exceptions import FetchError

DEFAULT_USER_AGENT = f"chronicle/{__version__} (+https://github.com/TheDataNormad/chronicle)"
DEFAULT_TIMEOUT = 15.0
DEFAULT_CACHE_DIR = Path.home() / ".chronicle" / "cache"

# A server asking us to wait longer than this is treated as an error rather
# than silently freezing the caller.
MAX_RETRY_AFTER_SECONDS = 120.0
_RETRY_AFTER_STATUSES = {429, 503}
_ROBOTS_PRODUCT_TOKEN = "chronicle"


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (seconds or HTTP-date) into seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def _host_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


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

    def slow_to(self, min_interval: float) -> None:
        """Never go faster than one request per ``min_interval`` seconds."""
        self.interval = max(self.interval, min_interval)


class HostRateLimiter:
    """One independent RateLimiter per host (CORE principle 7)."""

    def __init__(self, per_second: float = 1.0) -> None:
        if per_second <= 0:
            raise ValueError("per_second must be > 0")
        self.per_second = per_second
        self._limiters: dict[str, RateLimiter] = {}

    def for_host(self, host: str) -> RateLimiter:
        if host not in self._limiters:
            self._limiters[host] = RateLimiter(self.per_second)
        return self._limiters[host]

    def wait(self, url_or_host: str = "") -> None:
        host = _host_of(url_or_host) if "//" in url_or_host else url_or_host.lower()
        self.for_host(host).wait()


class RobotsChecker:
    """Answers "may Chronicle fetch this URL?" from the host's robots.txt.

    Follows RFC 9309: a missing robots.txt (4xx) allows everything; a
    robots.txt that cannot be reached (5xx / network error) means "do not
    crawl". Parsed rules are cached per host for the life of the checker.
    """

    def __init__(self, client: httpx.Client, user_agent: str = _ROBOTS_PRODUCT_TOKEN) -> None:
        self._client = client
        self.user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}
        self._errors: dict[str, str] = {}

    def crawl_delay(self, url: str) -> float | None:
        parser = self._parsers.get(_host_of(url))
        if parser is None:
            return None
        delay = parser.crawl_delay(_ROBOTS_PRODUCT_TOKEN)
        return float(delay) if delay is not None else None

    def check(self, url: str) -> None:
        """Raise FetchError if robots.txt forbids fetching ``url``."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return
        host = parts.netloc.lower()
        if host not in self._parsers:
            self._load(parts.scheme, host)
        if host in self._errors:
            raise FetchError(
                f"Refusing to fetch {url}: {self._errors[host]} "
                "(pass respect_robots=False only if you are allowed to)"
            )
        parser = self._parsers[host]
        if parser is not None and not parser.can_fetch(_ROBOTS_PRODUCT_TOKEN, url):
            raise FetchError(
                f"robots.txt on {host} disallows fetching {url}. Chronicle "
                "respects robots.txt; use the site's official API or data "
                "download instead."
            )

    def _load(self, scheme: str, host: str) -> None:
        robots_url = f"{scheme}://{host}/robots.txt"
        last: str = ""
        for _ in range(2):
            try:
                response = self._client.get(robots_url)
            except httpx.HTTPError as e:
                last = f"robots.txt could not be reached ({type(e).__name__})"
                continue
            if 200 <= response.status_code < 300:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                self._parsers[host] = parser
                return
            if 400 <= response.status_code < 500:
                self._parsers[host] = None  # no robots.txt: everything allowed
                return
            last = f"robots.txt returned HTTP {response.status_code}"
        self._parsers[host] = None
        self._errors[host] = last


@dataclass
class FetchResponse:
    """What came back: the body plus what Chronicle needs to interpret it."""

    text: str
    content_type: str = "text/html"
    status: int = 200
    url: str = ""

    @property
    def is_json(self) -> bool:
        ct = self.content_type.lower()
        return "json" in ct


class Fetcher:
    """Fetches URLs with retries, rate limiting, robots.txt and caching.

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
        use_cache: bool = False,
        respect_robots: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.rate_limiter = HostRateLimiter(rate_limit)
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.user_agent = user_agent
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.use_cache = use_cache
        self.respect_robots = respect_robots
        self._headers = {"User-Agent": user_agent, **(headers or {})}
        self._client_obj: httpx.Client | None = None
        self._robots: RobotsChecker | None = None

    # The client is created on first use and re-created after close(), so a
    # Scrape object can safely be run more than once.
    @property
    def _client(self) -> httpx.Client:
        if self._client_obj is None:
            self._client_obj = httpx.Client(
                headers=self._headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client_obj

    def get(self, url: str) -> str:
        """Fetch a URL and return the response body as text."""
        return self.fetch(url).text

    def fetch(self, url: str) -> FetchResponse:
        """Fetch a URL and return body + content type."""
        if self.use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        if self.respect_robots:
            if self._robots is None:
                self._robots = RobotsChecker(self._client, self.user_agent)
            self._robots.check(url)
            delay = self._robots.crawl_delay(url)
            if delay:
                self.rate_limiter.for_host(_host_of(url)).slow_to(delay)

        response = self._request_with_retries(url)

        if self.use_cache:
            self._write_cache(url, response)
        return response

    def _request_with_retries(self, url: str) -> FetchResponse:
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            self.rate_limiter.wait(url)
            wait_extra: float | None = None
            try:
                response = self._client.get(url)
                response.raise_for_status()
                return FetchResponse(
                    text=response.text,
                    content_type=response.headers.get("content-type", "text/html"),
                    status=response.status_code,
                    url=str(response.url),
                )
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                # Don't retry 4xx client errors (except 429 Too Many Requests)
                if 400 <= status < 500 and status != 429:
                    raise FetchError(f"Client error {status} for {url}") from e
                last_error = e
                if status in _RETRY_AFTER_STATUSES:
                    wait_extra = parse_retry_after(e.response.headers.get("retry-after"))
                    if wait_extra is not None and wait_extra > MAX_RETRY_AFTER_SECONDS:
                        raise FetchError(
                            f"{url} asked us to wait {wait_extra:.0f}s "
                            f"(Retry-After); longer than the "
                            f"{MAX_RETRY_AFTER_SECONDS:.0f}s limit. Try again later."
                        ) from e
            except (httpx.UnsupportedProtocol, httpx.InvalidURL) as e:
                # Retrying a malformed URL can never help.
                raise FetchError(f"Invalid URL {url!r}: {e}") from e
            except (httpx.RequestError, httpx.TimeoutException) as e:
                last_error = e

            if attempt < self.retries:
                sleep_time = self.backoff * (2 ** (attempt - 1))
                if wait_extra is not None:
                    sleep_time = max(sleep_time, wait_extra)  # honour Retry-After
                time.sleep(sleep_time)

        raise FetchError(
            f"Failed to fetch {url} after {self.retries} attempts: {last_error}"
        )

    # ---- cache helpers (opt-in) ----

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return self.cache_dir / f"{digest}.html"

    def _meta_path(self, url: str) -> Path:
        return self._cache_path(url).with_suffix(".meta.json")

    def _read_cache(self, url: str) -> FetchResponse | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        meta = {}
        meta_path = self._meta_path(url)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        return FetchResponse(
            text=path.read_text(encoding="utf-8"),
            content_type=meta.get("content_type", "text/html"),
            status=meta.get("status", 200),
            url=url,
        )

    def _write_cache(self, url: str, response: FetchResponse) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path(url).write_text(response.text, encoding="utf-8")
        self._meta_path(url).write_text(
            json.dumps({"content_type": response.content_type, "status": response.status}),
            encoding="utf-8",
        )

    def clear_cache(self) -> None:
        """Delete all cached responses."""
        if not self.cache_dir.exists():
            return
        for pattern in ("*.html", "*.meta.json"):
            for path in self.cache_dir.glob(pattern):
                if not path.name.startswith("pw_"):
                    path.unlink()

    def close(self) -> None:
        """Close the underlying HTTP client (it reopens on next use)."""
        if self._client_obj is not None:
            self._client_obj.close()
            self._client_obj = None
        self._robots = None

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *args) -> None:
        self.close()
