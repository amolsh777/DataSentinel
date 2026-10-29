"""Security controls: API-key auth, rate limiting, SSRF guard. Pure ASGI tests - no server, no network."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core import security as sec


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def call(app, method="GET", path="/api/datasets", headers=None, client=("1.2.3.4", 5000)):
    sent = []

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": method, "path": path, "client": client,
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}
    asyncio.run(app(scope, None, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body


# ---- authentication --------------------------------------------------------
def test_auth_disabled_when_no_key_configured():
    assert call(sec.ApiKeyAuthMiddleware(_ok_app, api_key=None))[0] == 200


def test_auth_rejects_missing_and_wrong_keys_with_error_envelope():
    app = sec.ApiKeyAuthMiddleware(_ok_app, api_key="s3cret")
    status, headers, body = call(app)
    assert status == 401 and headers[b"www-authenticate"].startswith(b"Bearer")
    assert json.loads(body)["error"]["code"] == "UNAUTHORIZED"
    assert call(app, headers={"X-API-Key": "nope"})[0] == 401
    assert call(app, headers={"X-API-Key": "s3cre"})[0] == 401  # prefix is not enough


def test_auth_accepts_header_or_bearer_and_leaves_health_public():
    app = sec.ApiKeyAuthMiddleware(_ok_app, api_key="s3cret")
    assert call(app, headers={"X-API-Key": "s3cret"})[0] == 200
    assert call(app, headers={"Authorization": "Bearer s3cret"})[0] == 200
    assert call(app, path="/api/health")[0] == 200 and call(app, path="/api/v1/health")[0] == 200
    assert call(app, path="/api/healthcheck-evil")[0] == 401
    assert call(app, method="OPTIONS")[0] == 200  # CORS pre-flight


def test_readonly_key_cannot_change_state():
    app = sec.ApiKeyAuthMiddleware(_ok_app, api_key="admin", readonly_key="viewer")
    assert call(app, headers={"X-API-Key": "viewer"})[0] == 200
    status, _, body = call(app, method="DELETE", path="/api/datasets/1", headers={"X-API-Key": "viewer"})
    assert status == 403 and json.loads(body)["error"]["code"] == "FORBIDDEN"
    assert call(app, method="POST", headers={"X-API-Key": "admin"})[0] == 200


def test_non_http_scopes_pass_through():
    hit = []

    async def inner(scope, receive, send):
        hit.append(scope["type"])

    asyncio.run(sec.ApiKeyAuthMiddleware(inner, api_key="k")({"type": "lifespan"}, None, None))
    assert hit == ["lifespan"]


# ---- rate limiting ---------------------------------------------------------
def test_parse_limit():
    assert sec.parse_limit("10/minute") == (10, 60) and sec.parse_limit("5 / second") == (5, 1)
    for bad in ("", "x/minute", "0/minute", "10/day"):
        with pytest.raises(ValueError):
            sec.parse_limit(bad)


def test_classify_routes():
    assert sec.classify("POST", "/api/files/inspect") == "upload"
    assert sec.classify("POST", "/api/v1/datasets") == "upload"
    assert sec.classify("POST", "/api/datasets/abc/versions") == "upload"
    for p in ("/api/datasets/a/monitor", "/api/datasets/a/drift", "/api/datasets/a/score", "/api/datasets/a/score/compare",
              "/api/datasets/a/root-cause", "/api/datasets/a/versions/2/validate", "/api/datasets/a/jobs/monitor"):
        assert sec.classify("POST", p) == "analysis", p
    assert sec.classify("GET", "/api/datasets") is None and sec.classify("POST", "/api/rules") is None
    assert sec.classify("GET", "/api/datasets/a/monitor") is None


def test_rate_limit_blocks_then_recovers_and_isolates_clients():
    now = [0.0]
    app = sec.RateLimitMiddleware(_ok_app, upload="2/minute", clock=lambda: now[0])
    for _ in range(2):
        assert call(app, "POST", "/api/files/inspect")[0] == 200
    status, headers, body = call(app, "POST", "/api/files/inspect")
    assert status == 429 and int(headers[b"retry-after"]) >= 1 and json.loads(body)["error"]["code"] == "RATE_LIMITED"
    assert call(app, "POST", "/api/files/inspect", client=("9.9.9.9", 1))[0] == 200  # other IP unaffected
    assert call(app, "GET", "/api/datasets")[0] == 200  # unlimited route
    now[0] = 61
    assert call(app, "POST", "/api/files/inspect")[0] == 200  # new window


def test_forwarded_for_is_ignored_unless_trusted():
    app = sec.RateLimitMiddleware(_ok_app, upload="1/minute")
    assert call(app, "POST", "/api/files/inspect", headers={"X-Forwarded-For": "5.5.5.5"})[0] == 200
    assert call(app, "POST", "/api/files/inspect", headers={"X-Forwarded-For": "6.6.6.6"})[0] == 429  # spoofing does not help
    trusted = sec.RateLimitMiddleware(_ok_app, upload="1/minute", trust_proxy=True)
    assert call(trusted, "POST", "/api/files/inspect", headers={"X-Forwarded-For": "5.5.5.5"})[0] == 200
    assert call(trusted, "POST", "/api/files/inspect", headers={"X-Forwarded-For": "6.6.6.6"})[0] == 200


# ---- SSRF guard ------------------------------------------------------------
def fake_dns(ip):
    return lambda host, port, proto=0: [(2, 1, 6, "", (ip, port))]


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.0.9", "169.254.169.254", "0.0.0.0", "::1", "224.0.0.1"])
def test_internal_addresses_are_blocked(ip):
    with pytest.raises(ValueError):
        sec.validate_webhook_url("https://hooks.example.com/x", resolver=fake_dns(ip))


def test_public_address_and_overrides():
    url = "https://hooks.slack.com/services/T/B/x"
    assert sec.validate_webhook_url(url, resolver=fake_dns("52.1.2.3")) == url
    assert sec.validate_webhook_url("http://localhost:9000/hook", allow_private=True) == "http://localhost:9000/hook"


@pytest.mark.parametrize("url", ["", "ftp://x.com/a", "file:///etc/passwd", "https://", "https://user:pw@example.com/x", "javascript:alert(1)"])
def test_bad_schemes_and_credentials(url):
    with pytest.raises(ValueError):
        sec.validate_webhook_url(url, resolver=fake_dns("52.1.2.3"))


def test_unresolvable_host_is_rejected():
    def boom(*a, **k):
        raise OSError("nxdomain")

    with pytest.raises(ValueError):
        sec.validate_webhook_url("https://nope.invalid/x", resolver=boom)
