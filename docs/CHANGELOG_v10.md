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

## Theme update
* Fixed overlapping sidebar labels: a global font rule also hit Streamlit's icon font, so icon names printed over the page titles.
* Dark grey (replaces dark blue) and white themes with a "Light theme" switch in the sidebar; it restyles the page, sidebar, inputs, menus, tabs, buttons, charts and tables. The choice is saved in `data/ui_prefs.json` (shared by all users of this server).
* New `frontend/components/theme.py`, `tests/test_theme.py`. Not verified in a real browser (Streamlit could not be installed here): check dataframes, code blocks and the JSON viewer in light mode, which are colour-inverted rather than restyled.

## Demo portfolio
* 10 synthetic datasets (orders, banking, healthcare, IoT, HR, telecom, logistics, clickstream, insurance, energy), 5 versions each: v1 clean reference, v2 mild drift, v3 degraded, v4 severe (schema change), v5 recovering. Generators: `backend/app/demo/portfolio.py` (deterministic). Files shipped at 25% size in `data/sample/portfolio/` (~860k rows); `--scale 1` writes ~3.4M rows.
* `python scripts/seed_portfolio.py --yes [--quick]` wipes the database and loads everything: rules (auto-suggested + hand-written), baseline v1, lineage, and 8-12 real monitoring runs per dataset with timestamps moved back over 30 days, incidents resolved / acknowledged / ignored / recurring.
* Datasets page -> Demo data tab: button that uploads all portfolio versions through the API (no rules/history; use the script for those).
* Verified here: generators, rule definitions, scores that decrease with severity (`tests/test_portfolio.py`, 5 tests pass). **Not verified: the database seeding (`portfolio_seed.py`) and the upload button**, because SQLAlchemy/FastAPI/Streamlit could not be installed. If the script errors, send me the traceback.
