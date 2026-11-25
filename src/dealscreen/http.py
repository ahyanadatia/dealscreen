"""Shared HTTP layer: timeouts, retries, per-host rate limiting, SSRF guard and a disk cache.

Every outbound request in dealscreen goes through `HttpClient`, so politeness and safety rules
live in one place rather than being re-implemented by each data source.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_BODY_BYTES = 2_000_000
RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised when a request fails permanently (blocked, non-retryable status, or exhausted)."""


class UnsafeURLError(FetchError):
    """Raised when a URL points at a non-public address or uses a disallowed scheme."""


Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _is_public_ip(ip: str, allowed: tuple[Network, ...] = ()) -> bool:
    addr = ipaddress.ip_address(ip)
    if any(addr in net for net in allowed):
        return True
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


Resolver = Callable[[str], list[str]]


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return list({str(info[4][0]) for info in infos})


def parse_networks(spec: str) -> tuple[Network, ...]:
    """Parse a comma-separated CIDR list, e.g. for fake-IP egress proxies (198.18.0.0/15)."""
    return tuple(
        ipaddress.ip_network(p.strip(), strict=False) for p in spec.split(",") if p.strip()
    )


def assert_safe_url(
    url: str, resolver: Resolver = _default_resolver, allowed: tuple[Network, ...] = ()
) -> None:
    """Reject URLs that could be used to reach internal services (SSRF).

    Agent tools accept URLs chosen by an LLM that has read untrusted web content, so this check
    runs on every fetch, including each redirect hop.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURLError(f"scheme not allowed: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    if host.lower() in {"localhost", "metadata.google.internal"} or host.endswith(".internal"):
        raise UnsafeURLError(f"host not allowed: {host}")
    try:
        ipaddress.ip_address(host)
        candidates = [host]
    except ValueError:
        try:
            candidates = resolver(host)
        except OSError as exc:
            raise FetchError(f"DNS resolution failed for {host}: {exc}") from exc
    if not candidates or not all(_is_public_ip(ip, allowed) for ip in candidates):
        raise UnsafeURLError(f"host resolves to a non-public address: {host}")


class RateLimiter:
    """Minimum interval between requests to the same host (thread-safe)."""

    def __init__(self, default_interval: float = 0.5, per_host: Mapping[str, float] | None = None):
        self.default_interval = default_interval
        self.per_host = dict(per_host or {})
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str, sleep: Callable[[float], None] = time.sleep) -> float:
        interval = self.per_host.get(host, self.default_interval)
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next.get(host, 0.0))
            self._next[host] = start + interval
        delay = start - now
        if delay > 0:
            sleep(delay)
        return delay


class DiskCache:
    """Tiny SQLite key/value cache with TTL. Safe to share between threads."""

    def __init__(self, path: Path, ttl_seconds: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT NOT NULL, t REAL NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT v, t FROM cache WHERE k = ?", (key,)).fetchone()
        if row is None or (self.ttl > 0 and time.time() - row[1] > self.ttl):
            return None
        return str(row[0])

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (k, v, t) VALUES (?, ?, ?)", (key, value, time.time())
            )
            self._conn.commit()


@dataclass
class Response:
    url: str
    status: int
    text: str
    content_type: str
    from_cache: bool = False

    def json(self) -> object:
        return json.loads(self.text)


class HttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 15.0,
        cache: DiskCache | None = None,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = 2,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = _default_resolver,
        check_ssrf: bool = True,
        allowed_networks: tuple[Network, ...] = (),
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cache = cache
        self.rate_limiter = rate_limiter or RateLimiter()
        self.max_retries = max_retries
        self.resolver = resolver
        self.check_ssrf = check_ssrf
        self.allowed_networks = allowed_networks
        self.sleep = sleep
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            headers={"User-Agent": user_agent, "Accept-Language": "en-GB,en;q=0.8"},
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _key(url: str, headers: Mapping[str, str] | None) -> str:
        raw = url + "|" + json.dumps(sorted((headers or {}).items()))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        use_cache: bool = True,
        max_redirects: int = 5,
    ) -> Response:
        key = self._key(url, headers)
        if use_cache and self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                data = json.loads(hit)
                return Response(**data, from_cache=True)

        current = url
        for _ in range(max_redirects + 1):
            if self.check_ssrf:
                assert_safe_url(current, self.resolver, self.allowed_networks)
            resp = self._request(current, headers=headers, auth=auth)
            if resp.is_redirect and resp.headers.get("location"):
                current = str(resp.next_request.url) if resp.next_request else current
                continue
            break
        else:
            raise FetchError(f"too many redirects for {url}")

        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} for {current}")
        body = resp.content[:MAX_BODY_BYTES].decode(resp.encoding or "utf-8", errors="replace")
        out = Response(
            url=current,
            status=resp.status_code,
            text=body,
            content_type=resp.headers.get("content-type", ""),
        )
        if use_cache and self.cache is not None:
            payload = {k: v for k, v in out.__dict__.items() if k != "from_cache"}
            self.cache.set(key, json.dumps(payload))
        return out

    def _request(
        self, url: str, *, headers: Mapping[str, str] | None, auth: tuple[str, str] | None
    ) -> httpx.Response:
        host = urlparse(url).hostname or ""
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.rate_limiter.wait(host, self.sleep)
            try:
                resp = self._client.get(url, headers=dict(headers or {}), auth=auth)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
            else:
                if resp.status_code not in RETRY_STATUSES:
                    return resp
                last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                retry_after = resp.headers.get("retry-after", "")
                if retry_after.isdigit() and attempt < self.max_retries:
                    self.sleep(min(float(retry_after), 30.0))
                    continue
            if attempt < self.max_retries:
                self.sleep(0.5 * 2**attempt)
        raise FetchError(f"request failed after retries: {url}: {last_exc}")
