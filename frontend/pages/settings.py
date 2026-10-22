from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from pages.settings_ext import render_alerting, render_scheduler  # noqa: E402
import streamlit as st  # noqa: E402

from components import state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

RULE_TEMPLATES = {
    "missing_values": {"max_missing_pct": 5, "warn_missing_pct": 2},
    "duplicate_rows": {"max_duplicate_pct": 0},
    "unique": {},
    "required_columns": {"columns": ["column_a", "column_b"]},
    "unexpected_columns": {"allowed": ["column_a", "column_b"]},
    "data_type": {"expected_type": "numeric", "max_invalid_pct": 0},
    "numeric_range": {"min": 0, "max": 1000, "max_violation_pct": 0},
    "allowed_values": {"values": ["A", "B"], "case_sensitive": True, "max_violation_pct": 0},
    "date_range": {"min": "2025-01-01", "allow_future": False, "max_invalid_pct": 0},
    "constant_column": {},
    "schema_match": {},
    "business_rule": {"expression": "abs(revenue - quantity * unit_price) <= 0.01", "max_violation_pct": 0},
    "referential_integrity": {"reference_dataset": "Customer Master", "reference_column": "customer_id"},
}
SEVS = ["info", "low", "medium", "high", "critical"]

c = state.client()
ds = state.current_dataset()
ui.page_header("Rules & Baselines", "Approve reference versions and configure validation rules.", ds)
tab_base, tab_rules, tab_alert, tab_sched = st.tabs(["Baselines", "Validation rules", "Alerting", "Scheduler"])

# --------------------------------------------------------------- baselines
with tab_base:
    history = state.api(c.baselines, ds["id"])
    active = next((b for b in history if b["is_active"]), None)
    ui.kpi_cards([{"label": "Active baseline", "value": f"v{active['version_number']}" if active else "not set",
                   "sub": active["reason"] if active else "drift and schema checks need a reference",
                   "color": "#22c55e" if active else "#eab308"}])
    if history:
        ui.show_df(pd.DataFrame([{
            "version": f"v{b['version_number']}", "active": b["is_active"], "reason": b["reason"], "set by": b["set_by"],
            "approved": b["created_at"][:19].replace("T", " "),
            "replaced": (b["deactivated_at"] or "")[:19].replace("T", " ")} for b in history]))
    st.markdown("**Approve a baseline**")
    vers = state.versions_of(ds)
    with st.form("baseline_form"):
        version = st.selectbox("Version to approve", vers, index=len(vers) - 1, format_func=lambda n: state.version_label(ds, n))
        reason = st.text_input("Reason (required, recorded in the audit trail)")
        set_by = st.text_input("Approved by (optional)")
        confirm = st.checkbox("I confirm replacing the current baseline", disabled=active is None,
                              help="Required only when a baseline is already active; the previous one is kept as history.")
        submitted = st.form_submit_button("Set baseline", type="primary")
    if submitted:
        try:
            out = c.set_baseline(ds["id"], version, reason, set_by, confirm)
        except ApiError as exc:
            ui.show_error(exc)
        else:
            st.success(out["note"])
            st.rerun()

# ------------------------------------------------------------------- rules
with tab_rules:
    rules = state.api(c.list_rules, ds["id"])
    cols = [x["name"] for x in state.api(c.schema, ds["id"], state.versions_of(ds)[-1])["columns"]]
    ui.kpi_cards([{"label": "Rules", "value": len(rules), "sub": f"{sum(r['enabled'] for r in rules)} enabled"},
                  {"label": "Global rules", "value": sum(r["dataset_id"] is None for r in rules)}])
    if rules:
        ui.show_df(pd.DataFrame([{
            "enabled": r["enabled"], "severity": r["severity"], "name": r["name"], "type": r["rule_type"],
            "column": r["column"] or "—", "scope": "global" if r["dataset_id"] is None else "dataset",
            "params": json.dumps(r["params"])} for r in rules]))

    st.markdown("**Suggest rules from a version**")
    s1, s2, s3 = st.columns([2, 1, 1])
    sv = s1.selectbox("Learn from version", state.versions_of(ds), key="sg_ver", format_func=lambda n: state.version_label(ds, n))
    if s2.button("Preview suggestions"):
        st.session_state["suggest"] = state.api(c.suggest_rules, ds["id"], sv, False)
    if s3.button("Save as rules"):
        out = state.api(c.suggest_rules, ds["id"], sv, True)
        st.session_state.pop("suggest", None)
        st.success(f"Saved {len(out['saved_rules'])} new rules (existing names were kept).")
        st.rerun()
    sg = st.session_state.get("suggest")
    if sg:
        st.caption(f"{len(sg['suggestions'])} suggested rules learned from v{sg['version_number']}. Learn from a clean version.")
        ui.show_df(pd.DataFrame([{"name": r["name"], "type": r["rule_type"], "column": r["column"] or "—",
                                  "severity": r["severity"], "why": r["rationale"]} for r in sg["suggestions"]]))

    st.divider()
    st.markdown("**Edit or delete a rule**")
    if rules:
        by = {f"{r['name']} ({r['rule_type']})": r for r in rules}
        chosen = by[st.selectbox("Rule", list(by), key="rule_pick")]
        rid = chosen["id"]
        e1, e2 = st.columns(2)
        sev = e1.selectbox("Severity", SEVS, index=SEVS.index(chosen["severity"]), key=f"sev_{rid}")
        enabled = e2.checkbox("Enabled", value=chosen["enabled"], key=f"en_{rid}")
        params_text = st.text_area("Parameters (JSON)", json.dumps(chosen["params"], indent=2), height=160, key=f"par_{rid}")
        b1, b2 = st.columns(2)
        if b1.button("Save changes", key=f"save_{rid}"):
            try:
                c.update_rule(rid, {"severity": sev, "enabled": enabled, "params": json.loads(params_text)})
            except json.JSONDecodeError as exc:
                st.error(f"Parameters are not valid JSON: {exc}")
            except ApiError as exc:
                ui.show_error(exc)
            else:
                st.success("Rule updated.")
                st.rerun()
        if b2.button("Delete rule", key=f"del_{rid}"):
            try:
                c.delete_rule(rid)
            except ApiError as exc:
                ui.show_error(exc)
            else:
                st.rerun()
    else:
        st.caption("No rules to edit yet.")

    st.divider()
    st.markdown("**Create a rule**")
    rtype = st.selectbox("Rule type", list(RULE_TEMPLATES), key="new_type")
    n1, n2, n3 = st.columns(3)
    name = n1.text_input("Name (optional)", key="new_name")
    column = n2.selectbox("Column", [None] + cols, key="new_col", format_func=lambda x: "(none)" if x is None else x,
                          help="Required for column rules such as unique, data_type, numeric_range.")
    severity = n3.selectbox("Severity", SEVS, index=2, key="new_sev")
    scope = st.radio("Applies to", ["This dataset", "All datasets (global)"], horizontal=True, key="new_scope")
    new_params = st.text_area("Parameters (JSON, template for the chosen type)", json.dumps(RULE_TEMPLATES[rtype], indent=2),
                              height=150, key=f"new_params_{rtype}")
    if st.button("Create rule", type="primary"):
        try:
            body = {"dataset_id": None if scope.startswith("All") else ds["id"], "rule_type": rtype, "severity": severity,
                    "column": column, "params": json.loads(new_params)}
            if name.strip():
                body["name"] = name.strip()
            c.create_rule(body)
        except json.JSONDecodeError as exc:
            st.error(f"Parameters are not valid JSON: {exc}")
        except ApiError as exc:
            ui.show_error(exc)
        else:
            st.success("Rule created.")
            st.rerun()
    st.caption("Business-rule expressions use a safe subset of Python (see docs/RULES.md); text is never executed.")


with tab_alert:
    render_alerting(c, ds)

with tab_sched:
    render_scheduler(c, ds)
