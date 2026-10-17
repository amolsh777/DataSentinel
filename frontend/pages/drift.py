from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, state, ui  # noqa: E402

c = state.client()
ds = state.current_dataset()
ui.page_header("Drift Monitoring", "Has the distribution of important features changed compared with the baseline?", ds)

if len(state.versions_of(ds)) < 2:
    ui.callout("info", "Drift needs two versions. Upload a second version on the Datasets page.")
    st.stop()
cur, ref = state.version_selectors(ds, "dr")
if ref is None or ref == cur:
    ui.callout("warn", "Select a reference version different from the current one (the active baseline is the default).")
    st.stop()

schema_cols = state.api(c.schema, ds["id"], cur)["columns"]
default_ex = [x["name"] for x in schema_cols if x["inferred_type"] == "datetime"]
with st.expander("Thresholds & options", expanded=False):
    st.caption("A feature is flagged when an effect size reaches its warn threshold. p-values are shown but never decide the verdict.")
    r1 = st.columns(4)
    psi_w = r1[0].number_input("PSI warn", 0.0, 5.0, 0.10, 0.01, key="t_psi_w")
    psi_h = r1[1].number_input("PSI high", 0.0, 5.0, 0.25, 0.01, key="t_psi_h")
    wd_w = r1[2].number_input("Wasserstein/IQR warn", 0.0, 5.0, 0.10, 0.01, key="t_wd_w")
    wd_h = r1[3].number_input("Wasserstein/IQR high", 0.0, 5.0, 0.30, 0.01, key="t_wd_h")
    r2 = st.columns(4)
    js_w = r2[0].number_input("JS distance warn", 0.0, 1.0, 0.10, 0.01, key="t_js_w")
    js_h = r2[1].number_input("JS distance high", 0.0, 1.0, 0.20, 0.01, key="t_js_h")
    un_w = r2[2].number_input("New categories % warn", 0.0, 100.0, 1.0, 0.5, key="t_un_w")
    un_h = r2[3].number_input("New categories % high", 0.0, 100.0, 5.0, 0.5, key="t_un_h")
    r3 = st.columns(4)
    ms_w = r3[0].number_input("Missingness change warn (pp)", 0.0, 100.0, 5.0, 1.0, key="t_ms_w")
    ms_h = r3[1].number_input("Missingness change high (pp)", 0.0, 100.0, 20.0, 1.0, key="t_ms_h")
    bins = r3[2].number_input("PSI bins", 3, 50, 10, 1, key="t_bins")
    exclude = st.multiselect("Exclude columns from drift", [x["name"] for x in schema_cols], default=default_ex,
                             key=f"dr_ex_{cur}", help="Date columns differ by construction between time periods, so they are excluded by default.")
bad = [n for n, (w, h) in {"PSI": (psi_w, psi_h), "Wasserstein": (wd_w, wd_h), "JS": (js_w, js_h),
                           "New categories": (un_w, un_h), "Missingness": (ms_w, ms_h)}.items() if w > h]
if bad:
    ui.callout("danger", "Warn threshold must not exceed high threshold for: " + ", ".join(bad))

body = state.analysis_body(cur, ref, exclude_columns=exclude, thresholds={
    "psi": {"warn": psi_w, "high": psi_h}, "wasserstein": {"warn": wd_w, "high": wd_h}, "js": {"warn": js_w, "high": js_h},
    "unseen_share_pct": {"warn": un_w, "high": un_h}, "missingness_change_pp": {"warn": ms_w, "high": ms_h},
    "psi_bins": int(bins)})
key = (ds["id"], cur, ref)
if st.button("Detect drift", type="primary", disabled=bool(bad)):
    with st.spinner("Comparing distributions…"):
        st.session_state["drift"] = {"key": key, "res": state.api(c.drift, ds["id"], body)}
d = st.session_state.get("drift")
if not d or d["key"] != key:
    st.info("Set thresholds if needed, then run drift detection.")
    st.stop()

res = d["res"]
s = res["summary"]
src = res.get("reference_source") or {}
color = {"stable": "#22c55e", "warn": "#eab308", "drifted": "#f97316"}[s["status"]]
ui.kpi_cards([
    {"label": "Dataset drift", "value": s["status"].title(), "sub": f"v{res['current_version_number']} vs v{res['reference_version_number']}", "color": color},
    {"label": "Drifted features", "value": s["features_drifted"], "sub": f"of {s['features_evaluated']} evaluated", "color": color},
    {"label": "Missingness changes", "value": s["missingness_changes"], "color": "#eab308" if s["missingness_changes"] else "#22c55e"},
    {"label": "Insufficient data", "value": s["features_insufficient_data"], "color": "#64748b"},
    {"label": "Skipped", "value": s["features_skipped"], "color": "#64748b"}])
st.caption(f"Reference: version {res['reference_version_number']} ({src.get('source', 'explicit')}). "
           f"Dataset status: drifted = any high-severity drifted feature; warn = any drifted feature; stable = none.")

feats = res["features"]
evaluated = [f for f in feats if f["status"] in ("drifted", "stable")]
ui.show_chart(charts.effect_size_chart(feats), key="dr_effect")
ui.show_df(pd.DataFrame([{
    "feature": f["name"], "kind": f["kind"], "status": f["status"], "severity": f["severity"],
    "ref n": f["reference_n"], "cur n": f["current_n"],
    "effect": (charts.primary_effect(f) or (0, "–"))[1] if f["status"] != "insufficient_data" else "–",
    "missing Δ (pp)": (f.get("missingness") or {}).get("delta_pp"),
    "KS sig. (BH)": (f.get("ks") or {}).get("significant_after_bh"),
    "explanation": f.get("explanation") or f.get("reason", "")} for f in feats]))

st.subheader("Feature detail")
names = [f["name"] for f in feats]
pick = st.selectbox("Feature", names, key="dr_feat",
                    index=next((i for i, f in enumerate(feats) if f["status"] == "drifted"), 0))
f = next(x for x in feats if x["name"] == pick)
st.markdown(f"{ui.status_badge(f['status'])} {ui.severity_badge(f['severity'])} "
            f"{ui.badge(f['kind'], '#64748b')}", unsafe_allow_html=True)
if f["status"] == "insufficient_data":
    ui.callout("warn", f["reason"])
else:
    st.write(f["explanation"])
    left, right = st.columns([3, 2])
    with left:
        if f["kind"] in ("numeric", "datetime") and f["psi"]["bins"]:
            ui.show_chart(charts.psi_bins_chart(f), key="dr_bins")
        elif f["kind"] == "categorical":
            ref_cols = {x["column_name"]: x for x in state.api(c.profile, ds["id"], ref)["columns"]}
            cur_cols = {x["column_name"]: x for x in state.api(c.profile, ds["id"], cur)["columns"]}
            ui.show_chart(charts.category_chart(
                pick, (ref_cols.get(pick) or {}).get("stats", {}).get("top_values", []),
                (cur_cols.get(pick) or {}).get("stats", {}).get("top_values", [])), key="dr_cat")
        else:
            st.info("No chart available for this feature.")
    with right:
        th = f["thresholds"]
        rows = []
        if f["kind"] == "categorical":
            rows += [("Jensen–Shannon distance", f["js"]["distance"], th["js"]["warn"], th["js"]["high"], "decides verdict"),
                     ("New categories (% of rows)", f["effect_sizes"]["unseen_share_pct"], th["unseen_share_pct"]["warn"],
                      th["unseen_share_pct"]["high"], "decides verdict"),
                     ("Chi-square p-value", f["chi_square"]["p_value"], None, None,
                      "context only" if f["chi_square"]["p_value"] is not None else f["chi_square"]["note"])]
        else:
            rows += [("PSI", f["psi"]["psi"], th["psi"]["warn"], th["psi"]["high"], "decides verdict"),
                     ("Wasserstein ÷ IQR", f["wasserstein"].get("normalized"), th["wasserstein"]["warn"],
                      th["wasserstein"]["high"], "decides verdict"),
                     ("KS statistic", f["ks"].get("statistic"), None, None, "context only"),
                     ("KS p-value", f["ks"].get("p_value"), None, None,
                      "context only · significant after BH" if f["ks"].get("significant_after_bh") else "context only")]
        ui.show_df(pd.DataFrame(rows, columns=["method", "value", "warn", "high", "role"]))
        if f.get("stats"):
            sdf = pd.DataFrame(f["stats"]).reset_index(names="statistic")
            for col_name in ("reference", "current"):
                sdf[col_name] = sdf[col_name].map(lambda v: f"{v:.4g}")
            ui.show_df(sdf)
        m = f["missingness"]
        st.metric("Missing values", ui.fmt_pct(m["current_pct"]), f"{m['delta_pp']:+.2f} pp vs reference", delta_color="inverse")
    if f["kind"] == "categorical" and f.get("unseen_categories"):
        st.markdown("**New category values (not in the reference)**")
        ui.show_df(pd.DataFrame(f["unseen_categories"]))
    if f["kind"] == "categorical" and f.get("vanished_categories"):
        st.markdown("**Reference values absent now:** " + ", ".join(f["vanished_categories"]))

fig = charts.missingness_chart(feats)
if fig is not None:
    ui.show_chart(fig, key="dr_miss")
if res["skipped"]:
    with st.expander(f"{len(res['skipped'])} skipped columns"):
        ui.show_df(pd.DataFrame(res["skipped"]))
mt = res["multiple_testing"]
with st.expander("Method notes & limitations"):
    st.write(f"p-values are corrected across features with {mt['method']} (KS rejected {mt['ks_rejected']}, "
             f"chi-square rejected {mt['chi_square_rejected']}).")
    for lim in res["limitations"]:
        st.write("• " + lim)
st.download_button("Download drift report (JSON)", json.dumps(res, indent=2, default=str), "drift_report.json", "application/json")
