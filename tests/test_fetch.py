"""Tests for the fetch layer: retries, Retry-After, robots.txt, per-host
limits and the opt-in cache (CORE principle 7)."""

from __future__ import annotations

import pytest

from chronicle import fetch as fetch_mod
from chronicle.exceptions import FetchError
from chronicle.fetch import (
    Fetcher,
    HostRateLimiter,
    RateLimiter,
    parse_retry_after,
)
from tests._server import Route, json_route, serve


@pytest.fixture()
def sleeps(monkeypatch):
    """Capture (and skip) every sleep the fetcher asks for."""
    calls: list[float] = []
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: calls.append(s))
    return calls


def _fetcher(tmp_path, **kw):
    kw.setdefault("rate_limit", 1000.0)
    kw.setdefault("backoff", 0.01)
    kw.setdefault("cache_dir", tmp_path / "cache")
    return Fetcher(**kw)


# ------------------------------------------------------------ rate limiting


def test_rate_limiter_rejects_nonpositive():
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        HostRateLimiter(-1)


def test_rate_limiter_slow_to_only_slows_down():
    rl = RateLimiter(10.0)  # 0.1s interval
    rl.slow_to(0.5)
    assert rl.interval == 0.5
    rl.slow_to(0.2)
    assert rl.interval == 0.5


def test_rate_limits_are_per_host(sleeps):
    hl = HostRateLimiter(1.0)
    hl.wait("http://a.example/x")
    hl.wait("http://b.example/x")        # different host: must not wait
    assert sleeps == []
    hl.wait("http://a.example/y")        # same host again: must wait
    assert len(sleeps) == 1


# ------------------------------------------------------------ Retry-After


def test_parse_retry_after():
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(" 2.5 ") == 2.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0  # in the past


def test_retry_after_is_honoured(tmp_path, sleeps):
    def flaky(hit):
        if hit == 1:
            return Route("slow down", status=429, headers={"Retry-After": "7"})
        return Route("ok")

    with serve({"/robots.txt": Route("", status=404), "/p": flaky}) as (base, hits):
        resp = _fetcher(tmp_path).fetch(f"{base}/p")
    assert resp.text == "ok"
    assert hits["/p"] == 2
    assert 7.0 in sleeps                       # waited the full Retry-After


def test_retry_after_503_is_honoured(tmp_path, sleeps):
    def flaky(hit):
        return Route("x", status=503, headers={"Retry-After": "3"}) if hit == 1 else Route("ok")

    with serve({"/robots.txt": Route("", status=404), "/p": flaky}) as (base, _):
        assert _fetcher(tmp_path).get(f"{base}/p") == "ok"
    assert 3.0 in sleeps


def test_absurd_retry_after_is_an_error_not_a_freeze(tmp_path, sleeps):
    r = Route("no", status=429, headers={"Retry-After": "86400"})
    with serve({"/robots.txt": Route("", status=404), "/p": r}) as (base, hits):
        with pytest.raises(FetchError, match="Retry-After"):
            _fetcher(tmp_path).get(f"{base}/p")
    assert hits["/p"] == 1
    assert sleeps == []


# ------------------------------------------------------------ retries


def test_4xx_is_not_retried(tmp_path, sleeps):
    with serve({"/robots.txt": Route("", status=404)}) as (base, hits):
        with pytest.raises(FetchError, match="404"):
            _fetcher(tmp_path).get(f"{base}/missing")
    assert hits["/missing"] == 1


def test_5xx_is_retried_then_fails(tmp_path, sleeps):
    with serve({"/robots.txt": Route("", status=404),
                "/p": Route("err", status=500)}) as (base, hits):
        with pytest.raises(FetchError, match="after 3 attempts"):
            _fetcher(tmp_path).get(f"{base}/p")
    assert hits["/p"] == 3


def test_invalid_url_is_not_retried(tmp_path, sleeps):
    with pytest.raises(FetchError, match="Invalid URL"):
        _fetcher(tmp_path, respect_robots=False).get("notaurl://x")
    assert sleeps == []


def test_content_type_is_returned(tmp_path):
    routes = {"/robots.txt": Route("", status=404), "/j": json_route({"a": 1})}
    with serve(routes) as (base, _):
        resp = _fetcher(tmp_path).fetch(f"{base}/j")
    assert resp.is_json and resp.status == 200


def test_client_reopens_after_close(tmp_path):
    with serve({"/robots.txt": Route("", status=404), "/p": Route("ok")}) as (base, _):
        f = _fetcher(tmp_path)
        assert f.get(f"{base}/p") == "ok"
        f.close()
        assert f.get(f"{base}/p") == "ok"       # a Scrape can run twice


# ------------------------------------------------------------ cache


def test_cache_is_off_by_default(tmp_path):
    with serve({"/robots.txt": Route("", status=404), "/p": Route("ok")}) as (base, hits):
        f = _fetcher(tmp_path)
        assert f.use_cache is False
        f.get(f"{base}/p")
        f.get(f"{base}/p")
    assert hits["/p"] == 2                      # fresh data every time


def test_cache_opt_in_serves_second_call_from_disk(tmp_path):
    with serve({"/robots.txt": Route("", status=404), "/j": json_route({"a": 1})}) as (base, hits):
        f = _fetcher(tmp_path, use_cache=True)
        f.get(f"{base}/j")
        again = f.fetch(f"{base}/j")
    assert hits["/j"] == 1
    assert again.is_json                        # content type survives the cache


def test_clear_cache(tmp_path):
    with serve({"/robots.txt": Route("", status=404), "/p": Route("ok")}) as (base, hits):
        f = _fetcher(tmp_path, use_cache=True)
        f.get(f"{base}/p")
        f.clear_cache()
        f.get(f"{base}/p")
    assert hits["/p"] == 2


# ------------------------------------------------------------ robots.txt


def test_robots_disallow_blocks_fetch(tmp_path):
    robots = Route("User-agent: *\nDisallow: /private\n", content_type="text/plain")
    with serve({"/robots.txt": robots, "/private/x": Route("secret"),
                "/public": Route("hello")}) as (base, hits):
        f = _fetcher(tmp_path)
        assert f.get(f"{base}/public") == "hello"
        with pytest.raises(FetchError, match="robots.txt"):
            f.get(f"{base}/private/x")
    assert hits["/private/x"] == 0              # never even requested


def test_robots_is_fetched_once_per_host(tmp_path):
    with serve({"/robots.txt": Route("", status=404), "/a": Route("1"),
                "/b": Route("2")}) as (base, hits):
        f = _fetcher(tmp_path)
        f.get(f"{base}/a")
        f.get(f"{base}/b")
    assert hits["/robots.txt"] == 1


def test_missing_robots_allows_everything(tmp_path):
    with serve({"/robots.txt": Route("nope", status=404), "/p": Route("ok")}) as (base, _):
        assert _fetcher(tmp_path).get(f"{base}/p") == "ok"


def test_unreachable_robots_refuses_per_rfc9309(tmp_path, sleeps):
    with serve({"/robots.txt": Route("boom", status=500), "/p": Route("ok")}) as (base, hits):
        with pytest.raises(FetchError, match="Refusing"):
            _fetcher(tmp_path).get(f"{base}/p")
    assert hits["/p"] == 0


def test_respect_robots_false_skips_the_check(tmp_path):
    robots = Route("User-agent: *\nDisallow: /\n", content_type="text/plain")
    with serve({"/robots.txt": robots, "/p": Route("ok")}) as (base, hits):
        assert _fetcher(tmp_path, respect_robots=False).get(f"{base}/p") == "ok"
    assert hits["/robots.txt"] == 0


def test_crawl_delay_slows_the_host(tmp_path, sleeps):
    robots = Route("User-agent: *\nCrawl-delay: 4\n", content_type="text/plain")
    with serve({"/robots.txt": robots, "/a": Route("1"), "/b": Route("2")}) as (base, _):
        f = _fetcher(tmp_path)
        f.get(f"{base}/a")
        f.get(f"{base}/b")
    assert any(s > 3 for s in sleeps)           # waited ~4s between requests


# ------------------------------------------------------------ playwright


def test_playwright_fetcher_checks_robots_before_launching_a_browser(tmp_path):
    from chronicle.playwright_fetcher import PlaywrightFetcher

    robots = Route("User-agent: *\nDisallow: /\n", content_type="text/plain")
    with serve({"/robots.txt": robots, "/p": Route("x")}) as (base, hits):
        pw = PlaywrightFetcher(cache_dir=tmp_path / "pwcache")
        with pytest.raises(FetchError, match="robots.txt"):
            pw.get(f"{base}/p")                 # raises before any browser starts
        pw.close()
    assert hits["/p"] == 0
    assert pw.use_cache is False
