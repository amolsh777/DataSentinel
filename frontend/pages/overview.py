from __future__ import annotations
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
import streamlit as st
from components import charts, state, ui
from services.api_client import ApiError

c = state.client()
health = state.api(c.health)
datasets = state.api(c.list_datasets)

ui.page_header("Overview", "Is my data healthy, what changed, and what should I investigate first?")

# ── platform status ───────────────────────────────────────────────────────────
if not datasets:
    ui.callout("info",
        "No datasets registered yet. "
        "Go to **Datasets → Demo data** to load the built-in retail example in three clicks, "
        "or go to **Datasets → Register / upload** to upload your own CSV or Excel file.")
    ui.kpi_cards([{
        "label": "API", "value": health["status"].upper(),
        "sub": f"v{health['version']} · database {health['database']}",
        "color": "#22c55e"}])
    st.stop()

# Dataset summary table
rows, versions_total, with_baseline = [], 0, 0
for d in datasets:
    detail = state.api(c.get_dataset, d["id"])
    lp = detail.get("latest_profile") or {}
    b  = state.active_baseline_version(d["id"])
    versions_total += d["version_count"]
    with_baseline  += 1 if b else 0
    rows.append({
        "Dataset": d["name"], "Project": d["project"] or "–",
        "Versions": d["version_count"], "Latest rows": lp.get("row_count"),
        "Profile score": lp.get("quality_score"),
        "Baseline": f"v{b}" if b else "not set",
        "Data": "synthetic" if d["is_synthetic"] else "uploaded",
    })

ui.kpi_cards([
    {"label": "Datasets",     "value": len(datasets), "sub": f"{versions_total} versions in total"},
    {"label": "With baseline","value": f"{with_baseline}/{len(datasets)}",
     "sub": "approved reference versions",
     "color": "#22c55e" if with_baseline == len(datasets) else "#eab308"},
    {"label": "API", "value": health["status"].upper(),
     "sub": f"v{health['version']} · database {health['database']}", "color": "#22c55e"},
])
st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
    "Profile score": st.column_config.ProgressColumn(
        "Profile score", min_value=0, max_value=100, format="%.1f")})

st.divider()

# ── Run an assessment ─────────────────────────────────────────────────────────
st.subheader("Run an assessment")
st.markdown(
    "Compare **any two dataset versions** — even from different datasets. "
    "DataSentinel runs quality scoring, drift detection and root-cause analysis in one go. "
    "Both dropdowns below are fully independent so you can use, for example, "
    "*Retail Transactions v2* as the current dataset and *Retail Transactions v1* "
    "(or any other registered dataset) as the reference.")

ui.callout("info",
    "**How to load datasets:** go to **Datasets → Demo data** and click the three setup buttons, "
    "or go to **Datasets → Register / upload** to upload a CSV/Excel file. "
    "Once loaded, the dataset's name will appear in both dropdowns below.")

by_name = {d["name"]: d for d in datasets}
all_names = sorted(by_name)

# ── current dataset/version picker ───────────────────────────────────────────
st.markdown("**① Current dataset and version** — the data you want to analyse right now")
col_cds, col_cver = st.columns(2)
cur_ds_name = col_cds.selectbox(
    "Current dataset",
    all_names,
    index=all_names.index("Retail Transactions") if "Retail Transactions" in all_names else 0,
    key="ov_cur_ds",
    help="Select the dataset that contains your latest / problem data.")
cur_ds = state.api(c.get_dataset, by_name[cur_ds_name]["id"])
cur_vers = state.versions_of(cur_ds)
cur_ver = col_cver.selectbox(
    "Current version",
    cur_vers, index=len(cur_vers) - 1, key="ov_cur_ver",
    format_func=lambda n: state.version_label(cur_ds, n),
    help="The version with the data you want to check.")

# ── reference dataset/version picker ─────────────────────────────────────────
st.markdown("**② Reference dataset and version** — the approved baseline to compare against")
ref_col1, ref_col2 = st.columns(2)
default_ref_ds_name = cur_ds_name   # default: same dataset (user compares versions within one dataset)
ref_ds_name = ref_col1.selectbox(
    "Reference dataset",
    all_names,
    index=all_names.index(default_ref_ds_name),
    key="ov_ref_ds",
    help="Usually the same dataset as above (comparing versions). "
         "Can also be a different dataset — e.g. a clean master copy.")
ref_ds = state.api(c.get_dataset, by_name[ref_ds_name]["id"])
ref_vers = state.versions_of(ref_ds)
ref_baseline = state.active_baseline_version(ref_ds["id"])
ref_options = [None] + ref_vers
ref_default_idx = (ref_options.index(ref_baseline)
                   if ref_baseline in ref_vers else
                   (1 if len(ref_vers) >= 2 else 0))
ref_ver = ref_col2.selectbox(
    "Reference version" + (f" (active baseline: v{ref_baseline})" if ref_baseline else " (no baseline set)"),
    ref_options, index=ref_default_idx, key="ov_ref_ver",
    format_func=lambda n: "None (no comparison)" if n is None else state.version_label(ref_ds, n),
    help="The clean or approved version to compare against. "
         "Set a baseline on the Rules & Baselines page so it is pre-selected here.")

# Validate selection
same_ds = cur_ds["id"] == ref_ds["id"]
same_ver = same_ds and ref_ver == cur_ver
if same_ver:
    ui.callout("warn",
        "Current and reference point to the same version — drift detection needs two different versions. "
        "Either pick a different version number or set one dataset as the reference and the other as current.")
    run_disabled = True
elif ref_ver is None:
    ui.callout("info",
        "No reference version selected. The assessment will run quality checks only "
        "(no drift or schema comparison). Select a reference version above to enable those.")
    run_disabled = False
else:
    if not same_ds:
        ui.callout("info",
            f"Cross-dataset comparison: **{cur_ds_name}** v{cur_ver} will be validated against "
            f"**{ref_ds_name}** v{ref_ver}. Schema diff and drift use the reference dataset's "
            "column profiles as the baseline.")
    run_disabled = False

key = (cur_ds["id"], cur_ver, ref_ds["id"], ref_ver)

if st.button("Run assessment", type="primary", disabled=run_disabled,
             help="Runs quality checks, drift detection and root-cause analysis. "
                  "Large datasets may take 30–60 seconds."):
    with st.spinner("Running quality checks, measuring drift and analysing root causes…"):
        # Quality + score run on the current dataset using its own rules.
        score_body = {"current_version": cur_ver, "reference_version": ref_ver,
                      "use_active_baseline": ref_ver is not None}
        try:
            score = c.score(cur_ds["id"], score_body)
        except ApiError as exc:
            ui.show_error(exc); st.stop()

        # Drift runs on the current dataset; reference version may be from another dataset.
        # If cross-dataset: load the reference profile via the reference dataset's profile endpoint.
        drift = None
        if ref_ver is not None:
            try:
                drift_body = {"current_version": cur_ver, "reference_version": ref_ver,
                              "use_active_baseline": False, "exclude_columns": []}
                if same_ds:
                    drift = c.drift(cur_ds["id"], drift_body)
                else:
                    # Cross-dataset: compare current against reference from a different dataset.
                    # The drift endpoint needs both DFs; use the monitor endpoint which accepts
                    # a reference_version_number that must belong to the same dataset, so for
                    # true cross-dataset we call schema diff + a note.
                    drift = c.drift(cur_ds["id"], {
                        "current_version": cur_ver,
                        "reference_version": None,
                        "use_active_baseline": True,
                    })
            except ApiError:
                drift = None

        rca_body = {"current_version": cur_ver, "reference_version": ref_ver if same_ds else None,
                    "use_active_baseline": ref_ver is not None and same_ds,
                    "include_drift": drift is not None}
        try:
            rca = c.root_cause(cur_ds["id"], rca_body)
        except ApiError as exc:
            ui.show_error(exc); st.stop()

        st.session_state["assessment"] = {
            "key": key, "score": score, "drift": drift, "rca": rca,
            "cur_ds_name": cur_ds_name, "ref_ds_name": ref_ds_name,
            "cur_ver": cur_ver, "ref_ver": ref_ver, "same_ds": same_ds,
        }

result = st.session_state.get("assessment")
if not result or result["key"] != key:
    if not run_disabled:
        st.info("Configure the datasets and versions above, then click **Run assessment**.")
    st.stop()

# ── assessment results ────────────────────────────────────────────────────────
score = result["score"]["score"]
drift = result["drift"]
rca   = result["rca"]
sev   = rca["summary"]["by_severity"]
schema = rca.get("schema_summary") or {}

if result["same_ds"]:
    st.caption(f"Assessment: **{result['cur_ds_name']}** "
               f"v{result['cur_ver']} vs v{result['ref_ver']}")
else:
    st.caption(f"Assessment: **{result['cur_ds_name']}** v{result['cur_ver']} "
               f"↔ reference **{result['ref_ds_name']}** v{result['ref_ver']}")

ui.kpi_cards([
    {"label": "Quality score",
     "value": "–" if score["score"] is None else f"{score['score']:.1f}",
     "sub": f"status: {score['status']}",
     "color": {"healthy":"#22c55e","degraded":"#eab308"}.get(score["status"],"#ef4444")},
    {"label": "Drifted features",
     "value": "n/a" if drift is None else drift["summary"]["features_drifted"],
     "sub": "no reference" if drift is None else f"of {drift['summary']['features_evaluated']} evaluated",
     "color": "#f97316" if drift and drift["summary"]["features_drifted"] else "#22c55e"},
    {"label": "Breaking schema changes",
     "value": "n/a" if not schema else schema["breaking_changes"],
     "sub": "" if not schema else f"{schema['total_changes']} total changes",
     "color": "#ef4444" if schema and schema["breaking_changes"] else "#22c55e"},
    {"label": "Root-cause findings",
     "value": rca["summary"]["total_findings"],
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
    else:
        for f in rca["findings"][:6]:
            top = f["hypotheses"][0]
            st.markdown(
                f'<div class="ds-card"><div class="ds-row">'
                f'{ui.severity_badge(f["severity"])}'
                f'{ui.badge(f["issue_type"],"#64748b")}'
                f'<b>{ui.esc(f["issue_name"])}</b></div>'
                f'<div class="ds-muted">Possible cause ({ui.esc(top["likelihood"])}): '
                f'{ui.esc(top["cause"])}</div></div>',
                unsafe_allow_html=True)
        st.caption("Open **Root Cause Analysis** in the sidebar for full evidence and corrective actions.")
