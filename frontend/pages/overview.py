from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, state, ui  # noqa: E402

c = state.client()
health = state.api(c.health)
datasets = state.api(c.list_datasets)

ui.page_header("Overview", "Is my data healthy, what changed, and what should I investigate first?")

if not datasets:
    ui.callout("info", "No datasets yet. Open Datasets → Demo data to load the synthetic retail example, "
                       "or upload your own CSV/Excel file.")
    st.stop()

rows, versions_total, with_baseline = [], 0, 0
for d in datasets:
    detail = state.api(c.get_dataset, d["id"])
    lp = detail.get("latest_profile") or {}
    b = state.active_baseline_version(d["id"])
    versions_total += d["version_count"]
    with_baseline += 1 if b else 0
    rows.append({"Dataset": d["name"], "Project": d["project"] or "–", "Versions": d["version_count"],
                 "Latest rows": lp.get("row_count"), "Profile score": lp.get("quality_score"),
                 "Baseline": f"v{b}" if b else "not set", "Data": "synthetic" if d["is_synthetic"] else "uploaded"})

ui.kpi_cards([
    {"label": "Datasets", "value": len(datasets), "sub": f"{versions_total} versions in total"},
    {"label": "With baseline", "value": f"{with_baseline}/{len(datasets)}", "sub": "approved reference versions",
     "color": "#22c55e" if with_baseline == len(datasets) else "#eab308"},
    {"label": "API", "value": health["status"].upper(), "sub": f"v{health['version']} · database {health['database']}",
     "color": "#22c55e"},
])
st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
    "Profile score": st.column_config.ProgressColumn("Profile score", min_value=0, max_value=100, format="%.1f")})

st.divider()
ds = state.current_dataset()
st.subheader("Run an assessment")
st.caption("Runs the quality score, drift detection and root-cause analysis for the active dataset in one go.")
cur, ref = state.version_selectors(ds, "ov")
if cur == ref:
    ui.callout("warn", "Current and reference are the same version; drift needs two different versions.")

key = (ds["id"], cur, ref)
if st.button("Run assessment", type="primary", disabled=cur == ref):
    with st.spinner("Validating, measuring drift and analysing root causes…"):
        score = state.api(c.score, ds["id"], state.analysis_body(cur, ref))
        drift = state.api(c.drift, ds["id"], state.analysis_body(cur, ref)) if ref else None
        rca = state.api(c.root_cause, ds["id"], state.analysis_body(cur, ref))
    st.session_state["assessment"] = {"key": key, "score": score, "drift": drift, "rca": rca}

result = st.session_state.get("assessment")
if not result or result["key"] != key:
    st.stop()

score, drift, rca = result["score"]["score"], result["drift"], result["rca"]
sev = rca["summary"]["by_severity"]
schema = rca.get("schema_summary") or {}
ui.kpi_cards([
    {"label": "Quality score", "value": "–" if score["score"] is None else f"{score['score']:.1f}",
     "sub": f"status: {score['status']}", "color": {"healthy": "#22c55e", "degraded": "#eab308"}.get(score["status"], "#ef4444")},
    {"label": "Drifted features", "value": "n/a" if drift is None else drift["summary"]["features_drifted"],
     "sub": "no reference selected" if drift is None else f"of {drift['summary']['features_evaluated']} evaluated",
     "color": "#f97316" if drift and drift["summary"]["features_drifted"] else "#22c55e"},
    {"label": "Breaking schema changes", "value": "n/a" if not schema else schema["breaking_changes"],
     "sub": "" if not schema else f"{schema['total_changes']} changes in total",
     "color": "#ef4444" if schema and schema["breaking_changes"] else "#22c55e"},
    {"label": "Root-cause findings", "value": rca["summary"]["total_findings"],
     "sub": f"{sev['critical'] + sev['high']} high or critical", "color": "#f97316"},
])

left, right = st.columns([2, 3])
with left:
    st.markdown("**Quality dimensions**")
    ui.show_chart(charts.dimension_bars(score["dimensions"]), key="ov_dims")
    weakest = score.get("weakest_dimension")
    if weakest:
        st.caption(f"Weakest dimension: **{weakest['name']}** ({weakest['score']:.1f}).")
with right:
    st.markdown("**What to investigate first**")
    if not rca["findings"]:
        ui.callout("success", "No issues detected for this comparison.")
    for f in rca["findings"][:6]:
        top = f["hypotheses"][0]
        st.markdown(
            f'<div class="ds-card"><div class="ds-row">{ui.severity_badge(f["severity"])}{ui.badge(f["issue_type"], "#64748b")}'
            f'<b>{ui.esc(f["issue_name"])}</b></div><div class="ds-muted">Possible cause '
            f'({ui.esc(top["likelihood"])}): {ui.esc(top["cause"])}</div></div>', unsafe_allow_html=True)
    st.caption("Open **Root Cause Analysis** in the sidebar for evidence, investigation steps and corrective actions.")
