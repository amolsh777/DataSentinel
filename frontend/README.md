# DataSentinel frontend (Streamlit)

A dark-first dashboard that talks to the backend **only over REST** (`DS_API_URL`, default
`http://127.0.0.1:8000/api`). Every button, filter and form on every page is wired to a working
backend call; there are no placeholder controls. 

## Run it (Windows PowerShell, from the repository root)

```powershell
# one-time setup
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r frontend\requirements.txt -r backend\requirements-dev.txt
python scripts\reset_demo.py --yes --with-v2        # optional: seeds demo data, rules and a baseline

# terminal 1 - backend
cd backend
uvicorn app.asgi:app --port 8000

# terminal 2 - frontend (repository root, same virtual environment)
streamlit run frontend\app.py


Open **http://localhost:8501**. Starting from an empty database also works: **Datasets → Demo data**
has three buttons that load the synthetic datasets, configure rules and a baseline, and add the
modified v2 file.

## Pages

| Page | What you can do |
|------|-----------------|
| **Command Center** | Fleet health across all datasets: score, ranked attention list, dataset tiles, run monitoring everywhere, CSV export |
| **Quality Trends** | Score vs. SLA target, failures/drift per run, rule-type heatmap, persistent failing rules, incident timeline (real runs only) |
| **Run History** | Timeline, run inspector, compare two runs, launch runs with live job status, report downloads |
| **Incident Center** | Filter, sort, bulk triage, evidence, impact, history |
| **Guide** | Quick start, how numbers are produced, glossary |
| **Assessment** (formerly Overview) | Dataset table with profile scores and baselines; one-click assessment (score + drift + root cause) with a prioritised list of what to investigate first |
| **Datasets** | Browse versions: preview, schema, per-column profiles and charts, profile-score breakdown. Upload CSV/Excel (sheet selection, preview, inferred types) as a new dataset or a new version. Demo-data loader |
| **Data Quality** | Run the validation rules (filter by outcome, severity, text; inspect expected/observed/evidence; export JSON/CSV). Configurable quality score: weights and minimums per dimension, gauge, radar, per-rule breakdown, version comparison and score history |
| **Drift Monitoring** | Pick current and reference versions (baseline by default), tune every threshold, exclude columns, see effect sizes across features, per-feature distribution charts, method tables (verdict-deciding vs context-only), new categories, missingness changes |
| **Schema Comparison** | Side-by-side column table, breaking/compatible changes with downstream risk, rename *candidates* (never merged automatically) |
| **Root Cause Analysis** | Findings with evidence from current and baseline data, ranked hypotheses (labelled as hypotheses, each with its supporting evidence), investigation steps, corrective actions; export as Markdown or JSON |
| **Rules & Baselines** | Baseline history and approval (reason required, explicit confirmation to replace); create, edit, enable/disable, delete rules; preview or save suggested rules |

The sidebar holds the active dataset, which all pages use, and the API URL.

## Tests

```powershell
python -m pytest frontend\tests
```

The suite starts a real backend on a free port with the demo data and drives each page with Streamlit's
headless `AppTest`, clicking the real buttons (validation, score, drift, root cause, rule create/edit/delete,
baseline confirmation, the from-scratch demo workflow, empty and unreachable backends).
It checks that pages run and behave correctly; it does not check pixel-level appearance.

## Layout

```
frontend/
  app.py                 entry point: theme, sidebar, navigation
  pages/                 one script per page
  components/ui.py       CSS design system, badges, KPI cards (all user text HTML-escaped)
  components/charts.py   Plotly figures (dark theme)
  components/state.py    session helpers, version pickers, error handling
  services/api_client.py REST client (the only module that touches HTTP)
  tests/                 AppTest suite
.streamlit/config.toml   theme (repository root)
```

## Notes

- `frontend/app.py` and the backend package are both named `app`. They run in separate processes, so this
  only matters for tests, which import the backend first (see `tests/conftest.py`).
- Run Streamlit from the repository root so `.streamlit/config.toml` (the theme) is picked up.
