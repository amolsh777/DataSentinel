# DataSentinel v8 — what changed

v8 is a **frontend release**. The backend, database schema and API are unchanged from v7.

## Design
- New design system (`frontend/components/ui.py`): CSS tokens, gradient page headers, KPI cards with delta pills and sparklines,
  score rings, bordered chart cards, empty states and onboarding steps, toasts, consistent badges.
- Sidebar: brand, dataset picker and a live "open incidents" alert sit above the page list; "Refresh data" button.
- New chart set (`components/charts.py`): score trend with SLA target and outlier markers, outcomes per run, rows-affected by rule type,
  rule-type heatmap, incident timeline, run-comparison and fleet-score charts.

## New pages
- **Command Center** (default): fleet score, ranked "Needs attention" list, per-dataset tiles with score ring and trend, one-click
  "run monitoring on every dataset", recent incident activity, CSV export.
- **Guide**: quick-start progress, how every number is produced, glossary, system status.

## Upgraded pages
- **Quality Trends** (was *Quality Dashboard*): now built **only from stored monitoring runs**. v7 padded missing days with random numbers;
  that is gone. Adds an SLA target, compliance %, streaks, unusual-run detection, persistent failing rules, heatmap.
- **Incident Center**: all-datasets scope, sorting, multi-row selection with bulk acknowledge/resolve/ignore/reopen, age, recurrence and
  time-to-resolve KPIs, severity/timeline charts, Markdown checklist export.
- **Run History**: tabs for Timeline / Inspect / **Compare runs** (new problems, worsened, resolved, drift changes) / New run;
  live background-job status; one-click report preparation (fixes the v7 download-button column bug).
- **Rules & Baselines**: guided rule builder per rule type (JSON still available under "advanced"), friendly validation,
  rule export / import as JSON.
- **Datasets**: KPI header, archive dataset with undo, download via the API client.
- **Assessment** (was *Overview*): Markdown export of the result.

## Fixes
- Dashboard cards no longer render white on dark theme; chart "cards" actually wrap their charts.
- Incident severity-breakdown table dropped keys by substring match (`k not in ("note")`); now an exact match.
- Job-submit message pointed at a non-existent `/api/v1/jobs/...` URL.

## Tests
- New pure unit tests: `tests/test_insights.py`, `tests/test_rules.py` (run without Streamlit or a backend).
- `tests/test_pages.py`: new AppTest cases for the new pages and the guided rule builder; the JSON-parameter test now
  ticks "Edit parameters as JSON (advanced)" first.
