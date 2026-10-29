# DataSentinel security model (v10)

## Controls that are now enforced
| Control | Where | Config |
|---|---|---|
| API-key authentication (`X-API-Key` or `Authorization: Bearer`) | `app/core/security.py::ApiKeyAuthMiddleware` | `DS_API_KEY`, `DS_READONLY_API_KEY` |
| Read-only role (GET/HEAD only, 403 otherwise) | same | `DS_READONLY_API_KEY` |
| Rate limits on uploads and analysis endpoints (429 + `Retry-After`) | `RateLimitMiddleware` | `DS_RATE_LIMIT_UPLOAD`, `DS_RATE_LIMIT_ANALYSIS`, `DS_TRUST_PROXY_HEADERS` |
| SSRF guard for alert webhooks (blocks loopback/private/link-local/reserved, no redirects, no embedded credentials) | `validate_webhook_url`, `alert_service._post` | `DS_ALLOW_PRIVATE_WEBHOOKS` (never in production) |
| Production start-up guard (missing/short API key, localhost CORS, private webhooks refuse to start) | `Settings.check_production_safety` | `DS_APP_ENV=production` |

`/health`, `/docs`, `/redoc` and `/openapi.json` stay public. Authentication is **off** when no key is set (development default); production mode refuses to start without one.

## Known limitations
* Rate-limit counters are per process. With several workers or replicas the effective limit is multiplied; use a gateway limit or a shared store for strict limits. The Dockerfile runs one worker for this reason.
* Only two roles exist (full / read-only). No users, no per-dataset permissions, no audit trail of who did what.
* Webhook validation resolves DNS once; a hostile DNS server could rebind afterwards (DNS-rebinding is not mitigated).
* Several endpoints still accept free-form `dict` bodies instead of typed models.
* The key is sent in a header: terminate TLS in front of the API.
* Not verified here: all of the above is unit-tested at ASGI level (`tests/test_security.py`) but the full application with the real FastAPI stack was not started in the build environment.
