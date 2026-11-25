from __future__ import annotations

import httpx
import pytest

from dealscreen.http import (
    DiskCache,
    FetchError,
    HttpClient,
    RateLimiter,
    UnsafeURLError,
    assert_safe_url,
    parse_networks,
)


def public(_: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/",
        "http://[::1]/",
        "http://localhost:8000/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "http://metadata.google.internal/",
    ],
)
def test_ssrf_guard_blocks_internal_targets(url: str) -> None:
    with pytest.raises(UnsafeURLError):
        assert_safe_url(url, resolver=public)


def test_ssrf_guard_blocks_hostnames_resolving_to_private_ips() -> None:
    with pytest.raises(UnsafeURLError):
        assert_safe_url("https://evil.example", resolver=lambda _: ["192.168.1.10"])


def test_ssrf_guard_allows_public_and_trusted_networks() -> None:
    assert_safe_url("https://example.com/page", resolver=public)
    assert_safe_url(
        "https://example.com",
        resolver=lambda _: ["198.18.0.1"],
        allowed=parse_networks("198.18.0.0/15"),
    )


def make_client(handler, **kw) -> HttpClient:  # type: ignore[no-untyped-def]
    return HttpClient(
        user_agent="test",
        transport=httpx.MockTransport(handler),
        resolver=public,
        rate_limiter=RateLimiter(default_interval=0),
        sleep=lambda _: None,
        **kw,
    )


def test_redirect_to_internal_address_is_blocked() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    with pytest.raises(UnsafeURLError):
        make_client(handler).get("https://example.com/")


def test_retries_on_503_then_succeeds() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, text="ok")

    assert make_client(handler, max_retries=2).get("https://example.com/").text == "ok"
    assert calls["n"] == 3


def test_gives_up_after_retries_and_reports_404_without_retry() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404 if "missing" in str(req.url) else 500)

    client = make_client(handler, max_retries=1)
    with pytest.raises(FetchError, match="after retries"):
        client.get("https://example.com/broken")
    calls["n"] = 0
    with pytest.raises(FetchError, match="404"):
        client.get("https://example.com/missing")
    assert calls["n"] == 1


def test_timeouts_are_retried_then_raise() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(FetchError):
        make_client(handler, max_retries=1).get("https://example.com/")


def test_cache_serves_repeat_requests(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="hello", headers={"content-type": "text/plain"})

    client = make_client(handler, cache=DiskCache(tmp_path / "c.sqlite3", ttl_seconds=60))
    first = client.get("https://example.com/a")
    second = client.get("https://example.com/a")
    assert (first.from_cache, second.from_cache) == (False, True)
    assert second.text == "hello" and calls["n"] == 1


def test_cache_ttl_expiry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    cache = DiskCache(tmp_path / "c.sqlite3", ttl_seconds=-1)
    cache.ttl = 1
    cache.set("k", "v")
    assert cache.get("k") == "v"
    cache._conn.execute("UPDATE cache SET t = t - 10")
    assert cache.get("k") is None


def test_rate_limiter_spaces_requests_per_host() -> None:
    slept: list[float] = []
    limiter = RateLimiter(default_interval=1.0)
    limiter.wait("a.com", slept.append)
    limiter.wait("a.com", slept.append)
    limiter.wait("b.com", slept.append)
    assert len(slept) == 1 and 0.9 < slept[0] <= 1.0
