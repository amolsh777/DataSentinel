# DataSentinel

**AI-assisted data quality, drift detection & pipeline intelligence platform.**

DataSentinel catches *silent* data failures — schema changes, missing data,
duplicates, invalid values and distribution drift — before dashboards and ML
models quietly start using bad data, and traces which downstream assets are
affected.

> **Status: Phase 3 of 7 complete** — foundation, ingestion, profiling,
> synthetic demo data, the validation engine, schema drift, **baselines,
> statistical drift detection, a configurable quality score and root-cause
> analysis**. Monitoring runs, incidents, lineage and the dashboard come next. See [`docs/CHECKLIST.md`](docs/CHECKLIST.md)
> and the full plan in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## What works now

- Upload CSV or Excel (with sheet selection) → strict validation with clear error codes
- Every upload becomes an immutable, versioned dataset; raw files are stored on disk, metadata in SQLite/PostgreSQL
- Automatic profiling: semantic type inference, invalid values, missingness, duplicates,
  numeric/date/category statistics, constant and identifier detection, and an explainable quality score
- REST API with interactive docs at `/docs`
- Deterministic synthetic demo datasets with 9 documented, intentionally injected issues
- **Validation engine** with 12 configurable rule types, pass/warn/fail/skipped outcomes and
  evidence for every result (counts, percentages, example values and row positions)
- **Rule suggestions** learned from a clean version (types, keys, allowed categories,
  completeness, non-negativity) — see [`docs/RULES.md`](docs/RULES.md)
- **Safe business rules** such as `abs(revenue - quantity * unit_price) <= 0.01`, evaluated
  through a whitelisted AST walker (user text is never executed)
- **Schema drift**: added/removed columns, evidence-based rename *candidates*, type and storage
  changes, nullability/missingness, lost key uniqueness, new/vanished categories — each labelled
  breaking or compatible, with a side-by-side column comparison
- **Baselines** with an audit trail: every approval records a reason and who; replacing one
  needs explicit confirmation and the old one is kept as history
- **Statistical drift**: PSI, KS, normalised Wasserstein, Jensen-Shannon and chi-square, chosen per
  feature type, with minimum sample sizes, Benjamini-Hochberg correction and configurable
  thresholds. **Effect size decides the verdict; p-values never do.** See [`docs/DRIFT.md`](docs/DRIFT.md)
- **Transparent quality score**: completeness, validity, uniqueness, consistency and accuracy with
  configurable weights and thresholds; every dimension shows the rules behind it. Accuracy is
  `not_evaluated` unless trusted reference data is configured
- **Root-cause analysis**: for each issue, evidence from current and baseline data, ranked
  *hypotheses* (never stated as fact, each backed by observed evidence), investigation steps and
  corrective actions. Deterministic, no LLM needed. See [`docs/RCA.md`](docs/RCA.md)
- 227 automated tests

## Quick start (Windows PowerShell)

Requires **Python 3.11 or 3.12** (`py --list` shows installed versions).

```powershell
# 1. Unzip, then open PowerShell in the project folder
cd datasentinel

# 2. Create and activate a virtual environment
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# 3. Install dependencies
python -m pip install --upgrade pip
pip install -r backend\requirements-dev.txt

# 4. Configuration (optional — defaults work out of the box)
Copy-Item .env.example .env

# 5. Generate sample data and seed the demo database
python scripts\reset_demo.py --yes

# 6. Run the tests
cd backend
python -m pytest

# 7. Start the API (still inside backend\)
uvicorn app.asgi:app --reload --port 8000
```

Open **http://127.0.0.1:8000/docs** to try every endpoint from the browser.

### Try it from PowerShell

```powershell
# Health
Invoke-RestMethod http://127.0.0.1:8000/api/health

# List datasets (Retail Transactions + Customer Master after the reset)
Invoke-RestMethod http://127.0.0.1:8000/api/datasets | ConvertTo-Json -Depth 5

# Upload the modified v2 as a new version (use curl.exe, not the PowerShell alias)
$id = (Invoke-RestMethod "http://127.0.0.1:8000/api/datasets?search=Retail").items[0].id
curl.exe -F "file=@..\data\sample\retail_transactions_v2.csv" "http://127.0.0.1:8000/api/datasets/$id/versions"

# Compare profiles
Invoke-RestMethod "http://127.0.0.1:8000/api/datasets/$id/profile?version=2" | ConvertTo-Json -Depth 6

# Validate v2 with the rules the reset configured (v1 = reference for schema checks)
$r = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/datasets/$id/versions/2/validate?reference_version=1"
$r.summary.status
$r.summary.top_issues | Format-Table outcome, severity, message -Wrap

# Drift v2 vs the baseline the reset approved (v1)
$d = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/datasets/$id/drift" -ContentType "application/json" `
  -Body '{"current_version": 2, "exclude_columns": ["transaction_date"]}'
$d.features | Format-Table status, severity, name -AutoSize

# Quality score with its breakdown
$s = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/datasets/$id/score" -ContentType "application/json" -Body '{"current_version": 2}'
$s.score.score; $s.score.dimensions.PSObject.Properties | ForEach-Object { "{0,-13}{1}" -f $_.Name, $_.Value.score }

# Root-cause analysis
$r = Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/datasets/$id/root-cause" -ContentType "application/json" -Body '{"current_version": 2}'
$r.findings | Select-Object -First 3 | ForEach-Object { $_.issue_name; $_.hypotheses | ForEach-Object { "  [$($_.likelihood)] $($_.cause)" } }

# Schema comparison v1 -> v2
$d = Invoke-RestMethod "http://127.0.0.1:8000/api/datasets/$id/schema-diff?reference=1&current=2"
$d.changes | Format-Table severity, change_type, columns, breaking -AutoSize
```

Expected on the synthetic data: v1 is `healthy` (47 checks pass); v2 is `failing`
with 10 failures, 2 warnings and 4 checks *skipped with reasons* (they reference the
removed `discount_pct` column).

macOS/Linux: replace step 2 with `python3 -m venv .venv && source .venv/bin/activate`
and use forward slashes.

## Configuration

All settings come from environment variables prefixed `DS_` or a `.env` file
in the repository root. See [`.env.example`](.env.example).

| Variable | Default | Meaning |
|----------|---------|---------|
| `DS_DATABASE_URL` | `sqlite:///data/datasentinel.db` | SQLAlchemy URL; relative SQLite paths resolve from the repo root |
| `DS_STORAGE_DIR` | `data/storage` | Where uploaded files are kept |
| `DS_MAX_UPLOAD_MB` | `50` | Upload size limit |
| `DS_MAX_ROWS` / `DS_MAX_COLUMNS` | `1000000` / `500` | Parsing limits |
| `DS_PROFILE_SAMPLE_ROWS` | `200000` | Column statistics use a reproducible sample above this |
| `DS_CORS_ORIGINS` | `http://localhost:8501,...` | Allowed frontend origins |

## API

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/health` | Liveness + database check |
| POST | `/api/files/inspect` | Validate & preview a file without saving (sheet list for Excel) |
| POST | `/api/datasets` | Register a dataset; file becomes version 1 |
| GET | `/api/datasets` | List with `limit`, `offset`, `project`, `search` |
| GET | `/api/datasets/{id}` | Detail with versions and latest profile summary |
| GET / POST | `/api/datasets/{id}/versions` | List / upload versions |
| GET | `/api/datasets/{id}/profile?version=N` | Full profile (latest if omitted) |
| GET | `/api/datasets/{id}/versions/{n}/schema` | Schema snapshot |
| GET | `/api/datasets/{id}/versions/{n}/preview?rows=20` | First rows (max 100) |
| GET / POST | `/api/rules` | List (`dataset_id`, `include_global`) / create a validation rule |
| GET / PATCH / DELETE | `/api/rules/{rule_id}` | Read / partially update / delete a rule |
| POST | `/api/datasets/{id}/rules/suggest?version=N&save=true` | Suggest rules from a version's profile (optionally save) |
| POST | `/api/datasets/{id}/versions/{n}/validate?reference_version=M` | Run enabled rules now (not persisted until Phase 4) |
| GET | `/api/datasets/{id}/schema-diff?reference=M&current=N` | Schema comparison with breaking/compatible labels |
| GET | `/api/datasets/{id}/baselines` | Baseline history (active and replaced) |
| POST | `/api/datasets/{id}/baseline` | Approve a baseline (`reason` required; `confirm` to replace) |
| POST | `/api/datasets/{id}/drift` | Statistical drift vs baseline or an explicit reference |
| POST | `/api/datasets/{id}/score` | Configurable quality score with per-dimension breakdown |
| POST | `/api/datasets/{id}/score/compare` | Score two versions and show per-dimension change |
| POST | `/api/datasets/{id}/root-cause` | Evidence-backed hypotheses, investigation steps, corrective actions |

Errors always look like `{"error": {"code": "DUPLICATE_COLUMNS", "message": "...", "details": {...}}}`.

## Quality scores

DataSentinel has two scores, deliberately separate:

- **Profile score** (computed at upload, below): a quick intrinsic summary with no rules needed.
- **Rule-based quality score** (`/score`): driven by *your configured rules*, with adjustable
  weights and thresholds. Full methodology in [`docs/DRIFT.md`](docs/DRIFT.md).

### Profile score methodology

A transparent heuristic (0–100), always shown with its breakdown:

| Component | Weight | Measures |
|-----------|-------:|----------|
| Completeness | 40 | 1 − missing cells / total cells |
| Uniqueness | 20 | 1 − duplicate rows / rows |
| Validity | 25 | 1 − mean share of values that don't fit the column's inferred type |
| Consistency | 15 | 1 − constant or empty columns / columns |

It summarises evidence; it is **not** a calibrated probability. Schema changes,
drift and rule failures are reported separately (Phases 2–4), which is why the
synthetic v2 file still scores ~98.5 on intrinsic quality alone.

## Demo data (synthetic)

`python scripts\generate_sample_data.py` writes deterministic files to
`data/sample/`. `retail_transactions_v2.csv` contains nine documented issues
(new/removed columns, a type change, rising missingness, price and age shifts,
unseen categories, duplicates, negative revenue, unknown customer IDs) —
see `data/sample/manifest.json`.

`python scripts\reset_demo.py --yes` regenerates samples, wipes the local
database and stored files, registers *Retail Transactions* (v1) and
*Customer Master*, and configures 36 validation rules for Retail Transactions
(34 suggested from v1 plus a revenue-formula rule and a customer-exists check).
It also approves v1 as the Retail Transactions baseline, so drift, score and
root-cause calls work immediately. Add `--with-v2` to pre-upload the modified version.

## Project layout

```
backend/app/   api · core · database · models · schemas · services · analytics · demo · utils
backend/tests/ pytest suite
data/sample/   synthetic datasets + manifest
docs/          ARCHITECTURE.md, CHECKLIST.md, RULES.md, DRIFT.md, RCA.md
scripts/       generate_sample_data.py, reset_demo.py
frontend/      Streamlit app (Phase 6)
```

## Troubleshooting

- **`Activate.ps1 cannot be loaded`** — run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- **`ModuleNotFoundError: No module named 'app'`** — run `pytest`/`uvicorn` from inside `backend\`.
- **pandas fails to install** — use Python 3.11/3.12; very new Python versions may lack wheels.
- **`curl` behaves oddly in PowerShell** — call `curl.exe` explicitly.
- **Stale or broken local state** — `python scripts\reset_demo.py --yes`.

## Limitations (current phase)

- No persisted monitoring runs, incidents, lineage or UI yet (Phases 4–6).
- Validation, drift, score and root-cause results are computed on request and not stored yet
  (Phase 4 adds persisted runs, so score history across runs arrives then; today you can compare
  two versions with `/score/compare`).
- Drift re-reads the stored reference file rather than a stored summary.
- The rule-based score is driven by affected percentages, so a few severe failures cost little;
  read the dimension breakdown and the check results, not just the number.
- Root-cause hypotheses come from a fixed pattern catalogue and can only suggest causes the data
  hints at; a person must confirm them.
- Category changes are compared on the stored top-20 values per column; truncated lists are marked `certain: false`.
- Type inference is heuristic (e.g. zip codes are inferred as integers).
- Schema is created with `create_all`; Alembic migrations are deferred.
- `.xls` (legacy Excel) is not supported; save as `.xlsx`.
- SQL-database ingestion, authentication and PDF export are deferred features.

## Screenshots

*To be added in Phase 6.*
