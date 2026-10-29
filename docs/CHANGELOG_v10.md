# DataSentinel v10 - hardening release

## Added
* API-key auth + read-only key, enforced rate limits, SSRF guard (`backend/app/core/security.py`).
* Production start-up safety checks and warnings (`core/config.py`).
* Job reliability: duplicate-job detection and recovery of interrupted jobs at start-up (`services/job_service.py`, `api/jobs.py`).
* Frontend API-key field (Connection panel) and clear 401/403/429 messages.
* Tests: `test_security.py` (25), `test_analytics_edge_cases.py` (5).
* Docs: `SECURITY.md`, `AUDIT_v10.md`.

## Changed
* `request_id` is a `ContextVar` instead of thread-local (correct under async).
* Dead `slowapi` setup removed from `main.py` (it was never enforcing anything). The package is still listed in `requirements.txt`; remove it once confirmed unused.
* Dockerfile no longer forces `DS_APP_ENV=production`; one worker with explanatory comment.
* Alert errors no longer echo webhook URLs.

## Not verified in the build environment
The real FastAPI/SQLAlchemy runtime, Alembic migrations, PostgreSQL, the Streamlit AppTest suite and CI could not be run (PyPI is unreachable there). Run `pytest` in `backend/` and `frontend/` before deploying.
