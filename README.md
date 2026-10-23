# DataSentinel

**AI-assisted data quality, drift detection and pipeline intelligence platform.**

DataSentinel catches silent data failures — schema changes, missing data,
duplicates, invalid values and distribution drift — before dashboards and ML
models quietly start using bad data. It traces which downstream assets are
affected, explains why the failure happened, and tells you exactly what to do.

---

## Prerequisites — read this before anything else

| Requirement | Version | Why |
|-------------|---------|-----|
| **Python** | **3.11 or 3.12 only** | `pandas`, `numpy`, `scipy` and `pyarrow` do not yet have pre-built Windows wheels for Python 3.13 or 3.14. Using a newer Python version will cause compile errors with Visual Studio not found. |
| pip | any recent | comes with Python |
| Git (optional) | any | only needed if you cloned instead of unzipping |

**Check which Python versions you have installed:**

```powershell
py --list
```

If you see only 3.13 or 3.14, download Python 3.12 from
https://www.python.org/downloads/release/python-3128/ (Windows installer 64-bit).
You do not need to uninstall your existing Python — both can coexist.

---

## First-time setup (Windows PowerShell)

Run every command from the **repository root** (`C:\...\datasentinel`) unless told otherwise.

### Step 1 — Create a virtual environment with Python 3.12

```powershell
py -3.12 -m venv .venv
```

If you get `Access is denied` deleting a previous `.venv`, close all terminals
first, then run the command again.

### Step 2 — Activate the virtual environment

```powershell
.\.venv\Scripts\Activate.ps1
```

If you see `cannot be loaded because running scripts is disabled`, run this
once and then try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Your prompt should now start with `(.venv)`. Every command below assumes the
venv is active.

### Step 3 — Install all dependencies

```powershell
pip install --upgrade pip
pip install -r backend\requirements.txt
pip install -r backend\requirements-dev.txt
pip install -r frontend\requirements.txt
```

This installs everything in one go. It should complete without any compile
errors on Python 3.12. If you see `Microsoft Visual Studio not found` or
`meson setup failed`, you are on the wrong Python version — check with
`python --version` and restart from Step 1 using `py -3.12`.

### Step 4 — Run the database migrations

The project uses Alembic to manage database schema changes. You must run this
before starting the server. Run it from inside the `backend\` folder:

```powershell
cd backend
alembic upgrade head
cd ..
```

**If you see `table datasets already exists`**, your database was created by
an older version of the code. Run these two commands instead:

```powershell
cd backend
alembic stamp 403b0276a9fa
alembic upgrade head
cd ..
```

You should see output ending with:
```
Running upgrade 403b0276a9fa -> 11de94d5e42b, parquet_cache_jobs_softdelete_type_overrides
```

### Step 5 — Load the demo data

```powershell
python scripts\reset_demo.py --yes --with-v2
```

This creates the database, generates synthetic datasets, registers them,
configures 36 validation rules, approves a baseline, and loads the
intentionally broken v2 file so every feature works immediately.

### Step 6 — Verify with tests (optional but recommended)

```powershell
cd backend
python -m pytest
cd ..
```

All tests should pass. If you see import errors, re-check Step 3.

---

## Running the application

You need **two terminals**, both with the venv active.

### Terminal 1 — Backend API

```powershell
cd C:\...\datasentinel\backend
.\.venv\Scripts\Activate.ps1
uvicorn app.asgi:app --port 8000 --reload
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

Leave this terminal open. The API is now available at http://127.0.0.1:8000.
Interactive API docs: http://127.0.0.1:8000/docs

### Terminal 2 — Frontend dashboard

Open a new PowerShell window:

```powershell
cd C:\...\datasentinel
.\.venv\Scripts\Activate.ps1
streamlit run frontend\app.py
```

You should see:
```
You can now view your Streamlit app in your browser.
Local URL: http://localhost:8501
```

Open http://localhost:8501 in your browser. The backend (Terminal 1) must be
running before the frontend can do anything useful.

---

## First time in the dashboard

1. Open http://localhost:8501
2. The sidebar shows **Retail Transactions** as the active dataset (loaded by the demo reset)
3. Go to **Overview** and click **Run assessment** — pick v2 as current and v1 as reference
4. Go to **Run History** and click **Run now (synchronous)** to run a full monitoring check
5. Go to **Incident Center** to see the issues detected
6. Go to **Data Lineage** to see the pre-seeded dependency graph
7. Go to **Rules & Baselines** to see the 36 configured rules

The full user guide for every page and every option is in [`docs/RUNit.md`](docs/RUNit.md).

---

## Starting again from scratch

If anything goes wrong and you want a completely clean state:

```powershell
cd C:\...\datasentinel\backend
alembic downgrade base
cd ..
python scripts\reset_demo.py --yes --with-v2
```

Or to also wipe stored files:

```powershell
Remove-Item -Recurse -Force data\storage\*
Remove-Item -Force data\datasentinel.db
cd backend
alembic upgrade head
cd ..
python scripts\reset_demo.py --yes --with-v2
```

---

## Troubleshooting

### `no such column: datasets.is_deleted`
The database is missing columns added by a later migration. Fix:
```powershell
cd backend
alembic stamp 403b0276a9fa
alembic upgrade head
```

### `table datasets already exists`
Same root cause. Run the `stamp` command above and then `alembic upgrade head`.

### `ModuleNotFoundError: No module named 'pandas'` (or numpy, scipy, etc.)
Either the venv is not active or you installed into the wrong Python. Check:
```powershell
python --version        # must say 3.11 or 3.12
where python            # must point inside your .venv folder
```
Then re-run Step 3.

### `pandas fails to install` / `meson setup failed` / `vswhere.exe not found`
You are on Python 3.13 or 3.14. Install Python 3.12 from python.org and
restart from Step 1 using `py -3.12 -m venv .venv`.

### `ModuleNotFoundError: No module named 'requests'` (or jinja2, pyarrow, apscheduler)
These are in `frontend\requirements.txt`. Run:
```powershell
pip install -r frontend\requirements.txt
```

### `Activate.ps1 cannot be loaded`
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### `ModuleNotFoundError: No module named 'app'` in pytest
You must run pytest from inside the `backend\` folder:
```powershell
cd backend
python -m pytest
```

### `Cannot reach the DataSentinel API` in the browser
The backend is not running. Start it in Terminal 1 as described above.

### `Cannot remove item python.exe: Access is denied`
Close all terminals that have the venv active, then delete the `.venv` folder.

### `curl` behaves oddly in PowerShell
Use `curl.exe` (with the `.exe`) to call the real curl, not the PowerShell alias.

---

## Project layout

```
datasentinel\
  backend\
    app\
      api\          REST endpoints
      analytics\    profiling, drift, quality checks, RCA, severity
      core\         config, logging, middleware, errors
      database\     SQLAlchemy session, base model
      models\       ORM models (dataset, monitoring, lineage)
      schemas\      Pydantic request/response models
      services\     business logic (dataset, monitoring, lineage, alerts, scheduler...)
      demo\         sample data generator and seed script
    alembic\        database migrations
    tests\          318 pytest tests
    requirements.txt
    requirements-dev.txt
  frontend\
    app.py          Streamlit entry point and navigation
    pages\          one file per page (overview, datasets, quality, drift, schema,
                    root_cause, incidents, lineage, run_history, settings)
    components\     ui.py, charts.py, state.py
    services\       api_client.py
    tests\          28 AppTest tests
    requirements.txt
  data\
    sample\         synthetic demo datasets and manifest.json
    storage\        uploaded files (not committed)
  docs\
    RUNit.md        complete user guide — start here
    RULES.md        validation rule types and parameters
    DRIFT.md        drift methods, baselines and quality score
    RCA.md          root-cause analysis reference
    ARCHITECTURE.md system design
    CHECKLIST.md    phase-by-phase build tracker
  scripts\
    reset_demo.py   wipe and reseed the demo database
    generate_sample_data.py  regenerate synthetic CSV files
  Dockerfile              backend container
  Dockerfile.frontend     frontend container
  docker-compose.yml      full stack (backend + frontend + optional PostgreSQL)
  .env.example            all environment variables with defaults
  .github\workflows\ci.yml  GitHub Actions CI
```

---

## Configuration

Copy `.env.example` to `.env` and edit as needed. All variables are optional —
the defaults work for local development.

| Variable | Default | Meaning |
|----------|---------|---------|
| `DS_DATABASE_URL` | `sqlite:///data/datasentinel.db` | Any SQLAlchemy URL. Use `postgresql+psycopg://user:pass@host/db` for production |
| `DS_STORAGE_DIR` | `data/storage` | Where uploaded files are stored on disk |
| `DS_MAX_UPLOAD_MB` | `50` | Maximum upload size |
| `DS_MAX_ROWS` | `1000000` | Maximum rows parsed per file |
| `DS_PROFILE_SAMPLE_ROWS` | `200000` | Rows used for column statistics on large files |
| `DS_CORS_ORIGINS` | `http://localhost:8501,...` | Comma-separated allowed frontend origins |
| `DS_LOG_LEVEL` | `INFO` | Log verbosity (DEBUG, INFO, WARNING, ERROR) |
| `DS_APP_ENV` | `development` | Set to `production` to enable the startup safety guard |
| `DS_SMTP_HOST` | *(unset)* | SMTP server for email alerts |

---

## Docker (alternative to the manual setup above)

If you have Docker Desktop installed you can skip all the Python setup:

```powershell
docker compose up
```

Then open http://localhost:8501. The backend runs on port 8000.

To also start a PostgreSQL database instead of SQLite:

```powershell
docker compose --profile postgres up
```

---

## API quick reference

Interactive docs with request/response examples: http://127.0.0.1:8000/docs

| Method | Path | What it does |
|--------|------|-------------|
| GET | `/api/health` | Liveness and database check |
| POST | `/api/datasets` | Upload a new dataset (CSV or Excel) |
| GET | `/api/datasets` | List datasets (`search`, `project`, `limit`, `offset`) |
| GET | `/api/datasets/{id}` | Full detail with versions and profile summary |
| POST | `/api/datasets/{id}/versions` | Upload a new version of an existing dataset |
| GET | `/api/datasets/{id}/versions/{n}/download` | Download the original uploaded file |
| GET | `/api/datasets/{id}/profile` | Full column profiles (latest version or `?version=N`) |
| GET | `/api/datasets/{id}/versions/{n}/schema` | Schema snapshot |
| GET | `/api/datasets/{id}/versions/{n}/preview` | First rows (`?rows=25`, max 100) |
| PATCH | `/api/datasets/{id}/versions/{n}/type-overrides` | Override inferred column types |
| DELETE | `/api/datasets/{id}?confirm=true` | Soft-delete a dataset |
| PATCH | `/api/datasets/{id}/restore` | Restore a soft-deleted dataset |
| POST | `/api/datasets/{id}/baseline` | Approve a version as the baseline |
| GET | `/api/datasets/{id}/baselines` | Baseline history |
| GET/POST | `/api/rules` | List or create validation rules |
| GET/PATCH/DELETE | `/api/rules/{id}` | Read, update or delete a rule |
| POST | `/api/datasets/{id}/rules/suggest` | Learn rules from a version (`?save=true` to persist) |
| POST | `/api/datasets/{id}/versions/{n}/validate` | Run validation rules (not persisted) |
| GET | `/api/datasets/{id}/schema-diff` | Schema comparison (`?reference=1&current=2`) |
| POST | `/api/datasets/{id}/drift` | Statistical drift vs baseline or explicit reference |
| POST | `/api/datasets/{id}/score` | Configurable quality score |
| POST | `/api/datasets/{id}/score/compare` | Compare scores across two versions |
| POST | `/api/datasets/{id}/root-cause` | Root-cause analysis |
| POST | `/api/datasets/{id}/monitor` | Full monitoring run (persists results + incidents) |
| GET | `/api/datasets/{id}/runs` | Run history |
| GET | `/api/datasets/{id}/runs/history` | Condensed run timeline for the dashboard |
| GET | `/api/datasets/{id}/runs/{run_id}` | Run detail with check and drift results |
| GET | `/api/runs/{run_id}/report` | Download report (`?format=html`, `json` or `csv`) |
| GET | `/api/incidents` | All incidents (filter by dataset, status, severity, type) |
| GET | `/api/incidents/{id}` | Incident detail with lineage impact |
| PATCH | `/api/incidents/{id}` | Update incident status |
| GET | `/api/incidents/{id}/history` | Status change audit trail |
| GET | `/api/lineage` | Full lineage graph |
| POST | `/api/lineage` | Create an asset |
| POST | `/api/lineage/edges` | Create a dependency edge |
| GET | `/api/lineage/impact/{dataset_id}` | Affected downstream assets for given columns |
| POST | `/api/datasets/{id}/alerts/test` | Send a test webhook alert |
| POST | `/api/datasets/{id}/schedule` | Set automatic monitoring schedule |
| GET | `/api/datasets/{id}/schedule` | Get current schedule |
| POST | `/api/v1/datasets/{id}/jobs/monitor` | Submit monitoring as a background job |
| GET | `/api/v1/jobs/{job_id}` | Poll a background job |
| GET | `/api/v1/jobs` | List recent jobs |
| GET | `/api/metrics` | Prometheus metrics |

All errors return `{"error": {"code": "ERROR_CODE", "message": "...", "details": {...}}}`.
Both `/api/` and `/api/v1/` prefixes work on every endpoint.

---

## Known limitations

- Python 3.13 and 3.14 are not supported (no pre-built wheels for the scientific stack).
- `.xls` (old Excel format) is not supported — save as `.xlsx`.
- SQL database ingestion (PostgreSQL/MySQL as a data source) is not yet implemented.
- Authentication and user accounts are not implemented — `set_by` fields are free text.
- PDF report export is not yet implemented (HTML, JSON and CSV work).
- The Streamlit dashboard has been tested headlessly; visual appearance depends on your browser.
