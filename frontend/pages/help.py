"""Guide — quick start, how the numbers are produced, glossary and system status."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import streamlit as st  # noqa: E402

from components import state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

c = state.client()
ui.page_header("Guide", "How DataSentinel works, where each number comes from, and a few habits that make monitoring reliable.")

tab_start, tab_numbers, tab_glossary, tab_system = st.tabs(["Quick start", "How numbers are produced", "Glossary", "System"])

with tab_start:
    try:
        datasets = c.list_datasets()
        has_ds = bool(datasets)
        has_base = any(state.active_baseline_version(d["id"]) for d in datasets)
        has_runs = any(c.run_history(d["id"], limit=1) for d in datasets)
    except ApiError:
        has_ds = has_base = has_runs = False
    st.markdown("Your progress:")
    ui.steps([
        ("1 · Register data", "Datasets → upload CSV/Excel, or load the synthetic retail demo.", has_ds),
        ("2 · Approve a baseline", "Rules & Baselines → learn rules from a clean version and approve it as the reference.", has_base),
        ("3 · Monitor", "Run History → New run. Use a background job for big files.", has_runs),
        ("4 · Triage", "Incident Center → acknowledge, resolve or ignore; Root Cause explains why.", False),
    ])
    st.markdown("""
**A good weekly rhythm**

1. Open **Command Center** — anything red or amber is ranked at the top.
2. Open **Incident Center**, filter to *critical + high*, tick rows and acknowledge or resolve them in one go.
3. For a surprising score, open **Run History → Compare runs** to see exactly which rules got worse or were fixed.
4. After a deliberate change you accept (new product codes, a new column), **approve a new baseline** so the next run compares against it.
5. Use **Quality Trends** to check that quality meets your target over time. Set the target on that page.
""")

with tab_numbers:
    st.markdown("""
| Number | Where it comes from |
|---|---|
| **Quality score (0–100)** | Weighted average of five dimensions (completeness, validity, uniqueness, consistency, accuracy). Each dimension is derived from the share of rows affected by its rules. Weights and minimums are configurable on **Data Quality**. |
| **Profile score** | A rule-free heuristic from the file itself (missing cells, duplicates, invalid values). Useful before any rules exist. |
| **Drift** | PSI, Wasserstein (IQR-normalised), Jensen–Shannon, chi-square and KS. The *verdict* uses effect sizes; p-values are shown for context only and corrected for multiple testing. |
| **Severity score (incidents)** | A heuristic on a 0–100 scale — **not** a probability. Combines rule severity, affected share, incident type, downstream assets and recurrence. |
| **Health** | `failing` if any high/critical check fails, `degraded` for lower-severity failures or warnings, otherwise `healthy`. |
| **Trends & compliance** | Computed only from stored monitoring runs. Days without a run have no data point — nothing is estimated or filled in. |
| **“Unusual run” circles** | Runs whose score is an outlier against the dataset's own history (robust median/MAD test, needs ≥ 5 runs). |
| **Rows affected by rule type** | Sum over checks of rows affected. Checks can hit the same rows, so treat it as an upper bound. |
""")

with tab_glossary:
    for term, text in (
        ("Baseline", "The approved reference version. Drift, schema comparison and `schema_match` compare new data against it."),
        ("Monitoring run", "One execution of the full pipeline: rules → schema diff → drift → root cause → incidents."),
        ("Incident", "A de-duplicated finding (by fingerprint). It gains occurrences when seen again and *recurs* if it returns after being resolved."),
        ("Lineage", "Which reports, metrics and models depend on which datasets and columns — used to show downstream impact."),
        ("PSI", "Population Stability Index. Rule of thumb: < 0.10 stable, 0.10–0.25 moderate shift, > 0.25 major shift."),
        ("SLA target", "The score you want every run to reach. Compliance = share of runs at or above it."),
    ):
        st.markdown(f"**{term}** — {text}")

with tab_system:
    try:
        h = c.health()
    except ApiError as exc:
        ui.show_error(exc)
    else:
        ui.kpi_cards([
            {"label": "API", "value": h["status"].upper(), "sub": f"v{h['version']}", "color": "#22c55e" if h["status"] == "ok" else "#eab308"},
            {"label": "Database", "value": h["database"], "sub": h.get("app", ""), "color": "#22c55e" if h["database"] == "ok" else "#ef4444"},
            {"label": "API URL", "value": c.base_url.replace("http://", "").replace("https://", ""), "sub": "change in the sidebar → Connection", "color": "#38bdf8"},
        ])
        metrics = c.metrics()
        if metrics:
            with st.expander("Prometheus metrics (what a scraper sees)"):
                st.code(metrics, language="text")
    st.caption("Interactive API docs are served by the backend at `/docs` (Swagger UI). More documentation lives in the repository's `docs/` folder.")
