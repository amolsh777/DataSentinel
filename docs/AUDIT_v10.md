# v10 audit (prioritised)

| # | Finding (evidence) | Severity | Status |
|---|---|---|---|
| 1 | Rate limiting configured via `slowapi` but never applied (`main.py`) | High | Fixed: enforced middleware, unit-tested |
| 2 | No authentication on any endpoint | High | Fixed (opt-in; mandatory in production) |
| 3 | Webhook URLs POSTed without validation (SSRF) | High | Fixed, DNS-rebinding remains |
| 4 | Background job endpoints created duplicate jobs and used a private session factory (`api/jobs.py`) | Medium | Fixed (unrun against a real DB) |
| 5 | Interrupted jobs stuck as running after restart | Medium | Mitigated: marked failed at start-up |
| 6 | Thread-local request id unsafe under async | Low | Fixed |
| 7 | `init_db` uses `create_all` while Alembic migrations exist | Medium | **Open** - choose one strategy; do not change destructively |
| 8 | Scheduler state stored in `dataset.notes`, runs per worker | Medium | **Open** |
| 9 | Free-form dict request bodies on several endpoints | Medium | **Open** |
| 10 | No RBAC beyond two keys, no audit log | Medium | **Open** |
| 11 | Analytics edge cases (empty/all-null/single-row) | Low | Tested, pass |

Future work: durable job queue (RQ/Celery), shared rate-limit store, OIDC login, typed request models, Alembic-only schema management, CI `pip-audit`.

Additional fix: `api/datasets.py` annotated the download endpoint with an undefined name (`Response`, flagged by ruff F821). It is now imported. Remaining ruff F-level findings are unused imports/variables, mostly in tests (not auto-fixed to avoid noise).
