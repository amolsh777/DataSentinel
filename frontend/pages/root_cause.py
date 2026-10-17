from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, state, ui  # noqa: E402

SEVS = ["critical", "high", "medium", "low", "info"]


def to_markdown(rca: dict, ds_name: str) -> str:
    lines = [f"# Root-cause report: {ds_name}",
             f"Version {rca['current_version_number']} vs reference {rca['reference_version_number']}", "",
             f"> {rca['disclaimer']}", ""]
    for f in rca["findings"]:
        lines += [f"## [{f['severity'].upper()}] {f['issue_name']}", f["description"], "", "**Possible causes (hypotheses)**"]
        for h in f["hypotheses"]:
            lines.append(f"- *{h['likelihood']}*: {h['cause']}")
            lines += [f"  - evidence: {e}" for e in h["supporting_evidence"]]
        lines += ["", "**Investigate**"] + [f"{i}. {s}" for i, s in enumerate(f["investigation_steps"], 1)]
        lines += ["", "**Corrective actions**"] + [f"- {a}" for a in f["corrective_actions"]] + [""]
    return "\n".join(lines)


c = state.client()
ds = state.current_dataset()
ui.page_header("Root Cause Analysis", "Evidence-backed hypotheses for why issues occurred, and how to investigate them.", ds)
cur, ref = state.version_selectors(ds, "rc")
if ref is None:
    st.caption("No reference selected: only quality-rule findings will be analysed (no schema or drift evidence).")
include_drift = st.checkbox("Include statistical drift evidence", value=True, key="rc_drift", disabled=ref is None)
key = (ds["id"], cur, ref, include_drift)

if st.button("Analyse root causes", type="primary", disabled=cur == ref):
    with st.spinner("Collecting evidence from rules, schema and drift…"):
        st.session_state["rca_page"] = {"key": key, "res": state.api(
            c.root_cause, ds["id"], state.analysis_body(cur, ref, include_drift=include_drift and ref is not None))}
r = st.session_state.get("rca_page")
if not r or r["key"] != key:
    st.info("Pick versions and run the analysis.")
    st.stop()

rca = r["res"]
ui.callout("warn", rca["disclaimer"])
sm = rca["summary"]
ui.kpi_cards([
    {"label": "Findings", "value": sm["total_findings"], "sub": f"max severity: {sm['max_severity']}"},
    {"label": "Quality", "value": sm["by_type"]["quality"], "color": "#38bdf8"},
    {"label": "Schema", "value": sm["by_type"]["schema"], "color": "#a78bfa"},
    {"label": "Drift", "value": sm["by_type"]["drift"], "color": "#f97316"}])
if not rca["findings"]:
    ui.callout("success", "No issues detected for this comparison.")
    st.stop()

top, donut = st.columns([3, 1])
with donut:
    ui.show_chart(charts.severity_donut(sm["by_severity"]), key="rc_donut")
with top:
    f1, f2, f3 = st.columns(3)
    types = f1.multiselect("Issue type", ["quality", "schema", "drift"], default=["quality", "schema", "drift"], key="rc_types")
    sevs = f2.multiselect("Severity", SEVS, default=SEVS, key="rc_sev")
    text = f3.text_input("Search column or issue", key="rc_q").strip().lower()
shown = [f for f in rca["findings"] if f["issue_type"] in types and f["severity"] in sevs
         and text in (f["issue_name"] + " " + " ".join(f["columns"])).lower()]
st.caption(f"{len(shown)} of {len(rca['findings'])} findings shown, most severe first.")

for i, f in enumerate(shown):
    with st.expander(f"[{f['severity'].upper()}] {f['issue_name']}", expanded=i < 2):
        st.markdown(f"{ui.severity_badge(f['severity'])} {ui.badge(f['issue_type'], '#64748b')} "
                    + " ".join(ui.badge(col, '#2dd4bf') for col in f["columns"][:8]), unsafe_allow_html=True)
        st.write(f["description"])
        t_ev, t_hyp, t_inv, t_fix = st.tabs(["Evidence", "Possible causes", "How to investigate", "Corrective actions"])
        with t_ev:
            ev = f["evidence"]
            cur_ev, base_ev = ev.get("current"), ev.get("baseline")
            a, b = st.columns(2)
            a.markdown("**Current run**"); a.json(cur_ev if cur_ev is not None else {}, expanded=False)
            b.markdown("**Baseline / previous run**")
            b.json(base_ev) if base_ev else b.caption("No baseline evidence available.")
            for label, k in (("Related schema changes", "related_schema_changes"), ("Related failed rules", "related_quality_issues")):
                if ev.get(k):
                    st.markdown(f"**{label}**")
                    ui.show_df(pd.DataFrame(ev[k]))
            if ev.get("related_drift"):
                st.markdown("**Related drift**"); st.json(ev["related_drift"], expanded=False)
        with t_hyp:
            st.caption("Hypotheses, not confirmed facts. Each is shown with the evidence that supports it.")
            for h in f["hypotheses"]:
                color = ui.LIKELIHOOD_COLORS[h["likelihood"]]
                sup = "".join(f"<div class='why'>• {ui.esc(e)}</div>" for e in h["supporting_evidence"])
                con = "".join(f"<div class='why'>✗ {ui.esc(e)}</div>" for e in h["contradicting_evidence"])
                st.markdown(f'<div class="ds-hyp" style="--c:{color}">{ui.likelihood_badge(h["likelihood"])} '
                            f'<b>{ui.esc(h["cause"])}</b>{sup}{con}</div>', unsafe_allow_html=True)
        with t_inv:
            for n, step in enumerate(f["investigation_steps"], 1):
                st.markdown(f"{n}. {step}")
        with t_fix:
            for act in f["corrective_actions"]:
                st.markdown(f"- {act}")

d1, d2 = st.columns(2)
d1.download_button("Download report (Markdown)", to_markdown(rca, ds["name"]), "root_cause_report.md", "text/markdown")
d2.download_button("Download report (JSON)", json.dumps(rca, indent=2, default=str), "root_cause_report.json", "application/json")
