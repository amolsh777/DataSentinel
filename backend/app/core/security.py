"""Enforced API security controls (pure ASGI, no third-party dependencies).

* :class:`ApiKeyAuthMiddleware` - opt-in API-key authentication with a read-only role.
* :class:`RateLimitMiddleware` - fixed-window, in-process rate limits for uploads and expensive analysis.
* :func:`validate_webhook_url` - SSRF guard for outbound alert webhooks.

Both middlewares answer with the standard error envelope ``{"error": {"code", "message", "details"}}``
so the frontend's error handling keeps working. Limits are per process: with several workers each keeps
its own counters (see docs/SECURITY.md).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import socket
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_API_PREFIX = re.compile(r"^/api(?:/v1)?(?=/|$)")


def _strip_prefix(path: str) -> str:
    return _API_PREFIX.sub("", path, count=1) or "/"


async def _reply(send, status: int, code: str, message: str, headers: dict[str, str] | None = None,
                 details: dict[str, Any] | None = None) -> None:
    body = json.dumps({"error": {"code": code, "message": message, "details": details or {}}}).encode()
    raw = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": raw})
    await send({"type": "http.response.body", "body": body})


def _header(scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return ""


def extract_key(scope) -> str:
    """API key from ``X-API-Key`` or ``Authorization: Bearer <key>``."""
    key = _header(scope, b"x-api-key").strip()
    if key:
        return key
    auth = _header(scope, b"authorization").strip()
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


# ----------------------------------------------------------------------------- authentication
class ApiKeyAuthMiddleware:
    """Require a key on every API call when one is configured.

    * ``api_key``          - full access.
    * ``readonly_key``     - GET/HEAD only (403 for anything that changes state).
    * Health endpoints and the interactive docs stay public so orchestrators can probe the service.
    * ``OPTIONS`` is allowed through so CORS pre-flight works (CORS middleware must sit outside this one).
    """

    PUBLIC = ("/health", "/docs", "/redoc", "/openapi.json")

    def __init__(self, app, *, api_key: str | None, readonly_key: str | None = None) -> None:
        self.app, self.api_key, self.readonly_key = app, api_key or "", readonly_key or ""

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.readonly_key)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self.enabled or scope["method"] == "OPTIONS":
            return await self.app(scope, receive, send)
        path = scope["path"]
        bare = _strip_prefix(path)
        if bare in self.PUBLIC or path in self.PUBLIC:
            return await self.app(scope, receive, send)
        key = extract_key(scope)
        if _same(key, self.api_key):
            return await self.app(scope, receive, send)
        if _same(key, self.readonly_key):
            if scope["method"] in READ_METHODS:
                return await self.app(scope, receive, send)
            return await _reply(send, 403, "FORBIDDEN", "This API key is read-only.")
        return await _reply(send, 401, "UNAUTHORIZED", "A valid API key is required (X-API-Key header).",
                            headers={"WWW-Authenticate": 'Bearer realm="datasentinel"'})


# --------------------------------------------------------------------------------- rate limiting
_UNITS = {"second": 1, "minute": 60, "hour": 3600}


def parse_limit(spec: str) -> tuple[int, int]:
    """'10/minute' -> (10, 60). Raises ValueError for anything else."""
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(second|minute|hour)s?\s*", spec or "")
    if not m or int(m.group(1)) < 1:
        raise ValueError(f"Invalid rate limit '{spec}'. Use e.g. '10/minute'.")
    return int(m.group(1)), _UNITS[m.group(2)]


_ANALYSIS = re.compile(r"/(monitor|drift|score|root-cause|validate|rules/suggest|jobs/(monitor|drift))(/|$)")


def classify(method: str, path: str) -> str | None:
    """Which limit bucket a request belongs to: 'upload', 'analysis' or None (unlimited)."""
    if method != "POST":
        return None
    p = _strip_prefix(path).rstrip("/") or "/"
    if p in ("/files/inspect", "/datasets") or re.fullmatch(r"/datasets/[^/]+/versions", p):
        return "upload"
    return "analysis" if _ANALYSIS.search(p) else None


class RateLimitMiddleware:
    """Fixed-window limiter keyed by API key (hashed) or client IP.

    Only the buckets returned by :func:`classify` are limited. ``X-Forwarded-For`` is ignored unless
    ``trust_proxy`` is True - otherwise any client could pick its own identity.
    """

    def __init__(self, app, *, upload: str = "10/minute", analysis: str = "30/minute", trust_proxy: bool = False,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.app, self.trust_proxy, self.clock = app, trust_proxy, clock
        self.limits = {"upload": parse_limit(upload), "analysis": parse_limit(analysis)}
        self._hits: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = threading.Lock()

    def _identity(self, scope) -> str:
        key = extract_key(scope)
        if key:
            return "k:" + hashlib.sha256(key.encode()).hexdigest()[:16]
        if self.trust_proxy:
            fwd = _header(scope, b"x-forwarded-for").split(",")[0].strip()
            if fwd:
                return "ip:" + fwd
        client = scope.get("client")
        return "ip:" + (client[0] if client else "unknown")

    def check(self, bucket: str, identity: str) -> tuple[bool, int]:
        """Count one hit. Returns (allowed, retry_after_seconds)."""
        limit, window = self.limits[bucket]
        now = self.clock()
        with self._lock:
            if len(self._hits) > 10_000:  # bounded memory: drop expired windows
                self._hits = {k: v for k, v in self._hits.items() if now - v[0] < self.limits[k[0]][1]}
            start, count = self._hits.get((bucket, identity), (now, 0))
            if now - start >= window:
                start, count = now, 0
            if count >= limit:
                return False, max(1, int(window - (now - start)) + 1)
            self._hits[(bucket, identity)] = (start, count + 1)
            return True, 0

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            bucket = classify(scope["method"], scope["path"])
            if bucket:
                ok, retry = self.check(bucket, self._identity(scope))
                if not ok:
                    return await _reply(send, 429, "RATE_LIMITED", f"Too many {bucket} requests. Retry in {retry}s.",
                                        headers={"Retry-After": str(retry)}, details={"retry_after_seconds": retry})
        await self.app(scope, receive, send)


# ----------------------------------------------------------------------------------- SSRF guard
def validate_webhook_url(url: str, *, allow_private: bool = False, resolver=socket.getaddrinfo) -> str:
    """Return the URL if it is safe to POST to, else raise ``ValueError``.

    Rejects non-http(s) schemes, embedded credentials and hosts that resolve to loopback, private,
    link-local (cloud metadata), multicast or reserved addresses unless ``allow_private``. DNS is resolved
    at validation time; a hostile DNS server could still rebind later (documented limitation).
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("Webhook URL must start with http:// or https:// and include a host.")
    if parts.username or parts.password:
        raise ValueError("Webhook URL must not contain credentials.")
    if allow_private:
        return url
    try:
        infos = resolver(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        raise ValueError(f"Cannot resolve webhook host '{parts.hostname}'.") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            raise ValueError("Webhook URL points to a private or internal address, which is not allowed "
                             "(set DS_ALLOW_PRIVATE_WEBHOOKS=true for local testing).")
    return url
