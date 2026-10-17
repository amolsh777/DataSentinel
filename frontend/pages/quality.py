from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, state, ui  # noqa: E402

DIMS = ["completeness", "validity", "uniqueness", "consistency", "accuracy"]
DEFAULT_WEIGHTS = {"completeness": 25, "validity": 25, "uniqueness": 15, "consistency": 15, "accuracy": 20}
DEFAULT_MINS = {"completeness": 70, "validity": 70, "uniqueness": 80, "consistency": 80, "accuracy": 80}

c = state.client()
ds = state.current_dataset()
ui.page_header("Data Quality", "Rule-based validation and a transparent, configurable quality score.", ds)
cur, ref = state.version_selectors(ds, "q")
rules = state.api(c.list_rules, ds["id"])
if not [r for r in rules if r["enabled"]]:
    ui.callout("warn", "No enabled validation rules for this dataset. Open Rules & Baselines to suggest or create rules.")
else:
    st.caption(f"{len([r for r in rules if r['enabled']])} enabled rules apply "
               f"({len([r for r in rules if r['dataset_id'] is None])} global).")
if ref is None:
    st.caption("No reference version selected: schema_match checks will be skipped.")
key = (ds["id"], cur, ref)

tab_val, tab_score = st.tabs(["Validation results", "Quality score"])

# -------------------------------------------------------------- validation
with tab_val:
    if st.button("Run validation", type="primary", key="run_val"):
        with st.spinner("Running rules…"):
            st.session_state["val"] = {"key": key, "res": state.api(c.validate, ds["id"], cur, ref)}
    v = st.session_state.get("val")
    if not v or v["key"] != key:
        st.info("Choose versions above and run the validation.")
    else:
        res = v["res"]
        s = res["summary"]
        o = s["outcomes"]
        color = {"healthy": "#22c55e", "degraded": "#eab308", "failing": "#ef4444"}[s["status"]]
        ui.kpi_cards([
            {"label": "Health", "value": s["status"].title(), "sub": "failing = a high/critical hard failure", "color": color},
            {"label": "Checks", "value": s["total_checks"], "sub": f"{ui.fmt_pct(s['pass_rate_pct'], 1)} of evaluated pass"},
            {"label": "Failed", "value": o["fail"], "color": "#ef4444" if o["fail"] else "#22c55e"},
            {"label": "Warnings", "value": o["warn"], "color": "#eab308" if o["warn"] else "#22c55e"},
            {"label": "Skipped", "value": o["skipped"], "sub": "could not be evaluated", "color": "#64748b"}])
        f1, f2, f3 = st.columns([2, 2, 3])
        outs = f1.multiselect("Outcome", ["fail", "warn", "pass", "skipped"], default=["fail", "warn"], key="val_out")
        sevs = f2.multiselect("Severity", ["critical", "high", "medium", "low", "info"],
                              default=["critical", "high", "medium", "low", "info"], key="val_sev")
        text = f3.text_input("Search rule or column", key="val_q").strip().lower()
        shown = [r for r in res["results"] if r["outcome"] in outs and r["severity"] in sevs
                 and text in (r["rule_name"] + " " + " ".join(r["columns"])).lower()]
        st.caption(f"{len(shown)} of {len(res['results'])} results shown")
        if shown:
            ui.show_df(pd.DataFrame([{
                "outcome": r["outcome"], "severity": r["severity"], "rule": r["rule_name"], "type": r["rule_type"],
                "columns": ", ".join(r["columns"]), "affected %": r["affected_pct"], "message": r["message"]} for r in shown]))
            labels = [f"[{r['outcome']}] {r['rule_name']}" for r in shown]
            idx = labels.index(st.selectbox("Inspect evidence", labels, key="val_pick"))
            r = shown[idx]
            st.markdown(f"{ui.outcome_badge(r['outcome'])} {ui.severity_badge(r['severity'])} "
                        f"<b>{ui.esc(r['rule_name'])}</b>", unsafe_allow_html=True)
            st.write(r["message"])
            a, b = st.columns(2)
            a.markdown("**Expected**"); a.code(str(r["expected"]))
            b.markdown("**Observed**"); b.json(r["observed"] if r["observed"] is not None else {})
            st.markdown("**Evidence**"); st.json(r["evidence"], expanded=False)
        if s["skipped"]:
            with st.expander(f"{len(s['skipped'])} skipped checks (and why)"):
                ui.show_df(pd.DataFrame(s["skipped"]))
        d1, d2 = st.columns(2)
        d1.download_button("Download results (JSON)", json.dumps(res, indent=2, default=str),
                           "validation_results.json", "application/json")
        d2.download_button("Download issues (CSV)", pd.DataFrame([{
            "outcome": r["outcome"], "severity": r["severity"], "rule": r["rule_name"], "columns": ", ".join(r["columns"]),
            "affected_pct": r["affected_pct"], "message": r["message"]} for r in res["results"]
            if r["outcome"] in ("fail", "warn")]).to_csv(index=False), "validation_issues.csv", "text/csv")

# ------------------------------------------------------------------- score
with tab_score:
    with st.expander("Weights & thresholds (configurable)"):
        st.caption("Weights set how much each dimension counts toward the overall score. Minimums define acceptable quality.")
        wc = st.columns(5)
        weights = {d: wc[i].number_input(f"{d.title()} weight", 0, 100, DEFAULT_WEIGHTS[d], key=f"w_{d}")
                   for i, d in enumerate(DIMS)}
        mc = st.columns(6)
        overall_min = mc[0].number_input("Overall min", 0, 100, 70, key="min_overall")
        dim_min = {d: mc[i + 1].number_input(f"{d.title()} min", 0, 100, DEFAULT_MINS[d], key=f"min_{d}")
                   for i, d in enumerate(DIMS)}
    cfg = {"weights": weights, "thresholds": {"overall_min": overall_min, "dimension_min": dim_min}}
    if sum(weights.values()) == 0:
        ui.callout("danger", "At least one dimension weight must be above zero.")

    if st.button("Calculate score", type="primary", key="run_score", disabled=sum(weights.values()) == 0):
        with st.spinner("Scoring…"):
            st.session_state["score"] = {"key": key, "res": state.api(c.score, ds["id"], state.analysis_body(cur, ref, **cfg))}
    sc = st.session_state.get("score")
    if sc and sc["key"] == key:
        score = sc["res"]["score"]
        status_color = {"healthy": "#22c55e", "degraded": "#eab308", "failing": "#ef4444"}.get(score["status"], "#64748b")
        g, k = st.columns([1, 2])
        with g:
            ui.show_chart(charts.score_gauge(score["score"], score["overall_threshold"]), key="sc_gauge")
        with k:
            ui.kpi_cards([
                {"label": "Overall score", "value": "–" if score["score"] is None else f"{score['score']:.1f}",
                 "sub": f"minimum {score['overall_threshold']:.0f}", "color": status_color},
                {"label": "Status", "value": score["status"].title(), "color": status_color},
                {"label": "Rules evaluated", "value": sc["res"]["rules_evaluated"]}])
            w = score.get("weakest_dimension")
            if w:
                st.markdown(f"**Why this score?** The weakest dimension is **{w['name']}** at **{w['score']:.1f}**. "
                            f"{score['methodology']}")
        a, b = st.columns(2)
        with a:
            ui.show_chart(charts.dimension_bars(score["dimensions"]), key="sc_dims")
        with b:
            ui.show_chart(charts.radar(score["dimensions"]), key="sc_radar")
        for vio in score["threshold_violations"]:
            ui.callout("danger" if vio["severity"] == "hard" else "warn",
                       f"{vio['dimension'].title()} is {vio['score']:.1f}, below its minimum of {vio['threshold']:.0f} "
                       f"({vio['severity']} violation).")
        for un in score["unevaluated_dimensions"]:
            ui.callout("info", f"{un['dimension'].title()} not evaluated — {un['reason']}")
        st.markdown("**Dimension breakdown**")
        for name, d in score["dimensions"].items():
            head = (f"{name.title()} — {'not evaluated' if d['score'] is None else f'{d['score']:.1f}'} "
                    f"(weight {d['weight']:.0f})")
            with st.expander(head):
                st.markdown(ui.status_badge(d["status"]), unsafe_allow_html=True)
                if d["score"] is None:
                    st.write(d["reason"])
                else:
                    st.caption(f"Outcomes: {d['outcomes']}")
                    ui.show_df(pd.DataFrame([{**r, "columns": ", ".join(r["columns"])} for r in d["rule_breakdown"]]))
        with st.expander("Notes on interpretation"):
            for n in score["notes"]:
                st.write("• " + n)
            st.write("• The score is driven by the share of rows affected, so a few severe failures cost little. "
                     "Always read the dimension breakdown and the validation results.")
        st.download_button("Download score (JSON)", json.dumps(sc["res"], indent=2, default=str), "quality_score.json",
                           "application/json")

    st.divider()
    st.subheader("Compare quality across versions")
    vers = state.versions_of(ds)
    if len(vers) < 2:
        st.info("Upload a second version to compare quality over time.")
    else:
        others = [n for n in vers if n != cur]
        prev = st.selectbox("Compare the current version with", others, index=len(others) - 1, key="cmp_prev",
                            format_func=lambda n: state.version_label(ds, n))
        c1, c2 = st.columns(2)
        if c1.button("Compare two versions", key="run_cmp"):
            with st.spinner("Scoring both versions…"):
                st.session_state["cmp"] = {"key": (ds["id"], cur, prev), "res": state.api(
                    c.score_compare, ds["id"], {"current_version": cur, "previous_version": prev, **cfg})}
        if c2.button("Score every version", key="run_hist"):
            rows = []
            with st.spinner("Scoring all versions…"):
                for n in vers:
                    r = state.api(c.score, ds["id"], state.analysis_body(n, ref, **cfg))["score"]
                    if r["score"] is not None:
                        rows.append({"label": f"v{n}", "score": r["score"], "dims": r["dimensions"]})
            st.session_state["hist"] = {"ds": ds["id"], "rows": rows}
        cm = st.session_state.get("cmp")
        if cm and cm["key"] == (ds["id"], cur, prev):
            comp = cm["res"]["comparison"]
            delta = comp["delta"]
            ui.kpi_cards([
                {"label": f"v{prev} score", "value": comp["previous_score"]},
                {"label": f"v{cur} score", "value": comp["current_score"]},
                {"label": "Change", "value": "–" if delta is None else f"{delta:+.1f}",
                 "color": "#ef4444" if delta and delta < 0 else "#22c55e"}])
            ui.show_df(pd.DataFrame([{"dimension": d, "change": v} for d, v in comp["dimension_deltas"].items()]))
            if comp["regressed_dimensions"]:
                ui.callout("warn", "Regressed: " + ", ".join(comp["regressed_dimensions"]))
        h = st.session_state.get("hist")
        if h and h["ds"] == ds["id"] and h["rows"]:
            ui.show_chart(charts.score_trend(h["rows"]), key="hist_chart")
