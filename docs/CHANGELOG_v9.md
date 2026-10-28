# DataSentinel v9 — what changed (frontend only; backend unchanged)

## New
- **Staleness alerts** (Command Center): datasets whose latest run is older than a threshold get a "stale" badge, appear in "Needs attention"
  and in a new KPI. Threshold is a setting (default 48 h).
- **Persistent preferences**: the score target (Quality Trends) and stale threshold are saved to `data/ui_prefs.json`
  (override the path with `DS_UI_PREFS`). Previously the target reset every session.
- **Column hotspots** (Quality Trends): columns ranked by failing checks in the latest run plus open incidents.
- **Open-incident age** buckets (Quality Trends).
- **Fleet report** (Command Center): shareable Markdown status report with datasets ranked by urgency and top open incidents.

## Fixes
- Assessment: the cross-dataset message claimed drift used the reference dataset's profiles. It does not (the backend compares versions within
  one dataset), so the message now says exactly what is compared.
- `quality.py` used an f-string form that only works on Python 3.12+, breaking the documented Python 3.11 support. Fixed.

## Tests
- New: `tests/test_prefs.py`; `tests/test_insights.py` extended (staleness, aging, hotspots, fleet report).
- Pure tests pass here; the AppTest suite needs a real Streamlit install and was not run in the build sandbox.
