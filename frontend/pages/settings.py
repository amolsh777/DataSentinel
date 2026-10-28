from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
from pages.settings_ext import render_alerting, render_scheduler  # noqa: E402
import streamlit as st  # noqa: E402

from components import insights, rules as rl, state, ui  # noqa: E402
from components.rules import RULE_TEMPLATES  # noqa: E402
from services.api_client import ApiError  # noqa: E402

SEVS = ["info", "low", "medium", "high", "critical"]

def render_rule_form(rtype: str, columns: list[str], dataset_names: list[str]) -> dict:
    """Guided parameter widgets for one rule type. Returns the (cleaned) params dict."""
    k = lambda f: f"nf_{rtype}_{f}"  # noqa: E731
    p: dict = {}
    if rtype == "missing_values":
        a, b = st.columns(2)
        p["max_missing_pct"] = a.number_input("Fail above (% missing)", 0.0, 100.0, 5.0, 0.5, key=k("max"))
        p["warn_missing_pct"] = b.number_input("Warn above (% missing)", 0.0, 100.0, 2.0, 0.5, key=k("warn"))
    elif rtype == "duplicate_rows":
        a, b = st.columns(2)
        p["max_duplicate_pct"] = a.number_input("Allowed duplicate rows (%)", 0.0, 100.0, 0.0, 0.1, key=k("max"))
        p["subset"] = b.multiselect("Only compare these columns (optional)", columns, key=k("subset"))
    elif rtype == "required_columns":
        p["columns"] = st.multiselect("Columns that must exist", columns, key=k("cols"))
    elif rtype == "unexpected_columns":
        p["allowed"] = st.multiselect("Allowed columns", columns, default=columns, key=k("allowed"),
                                      help="Any column not in this list raises a warning.")
    elif rtype == "data_type":
        a, b = st.columns(2)
        p["expected_type"] = a.selectbox("Expected type", rl.DATA_TYPES, key=k("type"))
        p["max_invalid_pct"] = b.number_input("Allowed invalid values (%)", 0.0, 100.0, 0.0, 0.1, key=k("inv"))
    elif rtype == "numeric_range":
        a, b, c3 = st.columns(3)
        use_min = a.checkbox("Set a minimum", value=True, key=k("use_min"))
        use_max = b.checkbox("Set a maximum", value=False, key=k("use_max"))
        p["min"] = a.number_input("Minimum", value=0.0, key=k("min")) if use_min else None
        p["max"] = b.number_input("Maximum", value=1000.0, key=k("max")) if use_max else None
        p["max_violation_pct"] = c3.number_input("Allowed violations (%)", 0.0, 100.0, 0.0, 0.1, key=k("viol"))
    elif rtype == "allowed_values":
        raw = st.text_area("Allowed values (one per line, or comma-separated)", key=k("vals"), height=110)
        a, b = st.columns(2)
        p["values"] = rl.split_values(raw)
        p["case_sensitive"] = a.checkbox("Case sensitive", value=True, key=k("case"))
        p["max_violation_pct"] = b.number_input("Allowed violations (%)", 0.0, 100.0, 0.0, 0.1, key=k("viol"))
    elif rtype == "date_range":
        a, b = st.columns(2)
        p["min"] = a.text_input("Earliest date (YYYY-MM-DD, optional)", key=k("min")).strip() or None
        p["max"] = b.text_input("Latest date (YYYY-MM-DD, optional)", key=k("max")).strip() or None
        a, b = st.columns(2)
        p["allow_future"] = a.checkbox("Allow dates in the future", value=False, key=k("future"))
        p["max_invalid_pct"] = b.number_input("Allowed unparseable dates (%)", 0.0, 100.0, 0.0, 0.1, key=k("inv"))
    elif rtype == "business_rule":
        p["expression"] = st.text_area("Condition every row must satisfy", RULE_TEMPLATES["business_rule"]["expression"], height=90, key=k("expr"),
                                       help="Safe expression language: arithmetic, comparisons, in / not in, and / or / not, abs(), isnull(), notnull().")
        a, b = st.columns([1, 2])
        p["max_violation_pct"] = a.number_input("Allowed violations (%)", 0.0, 100.0, 0.0, 0.1, key=k("viol"))
        p["description"] = b.text_input("Description (optional)", key=k("desc"))
    elif rtype == "referential_integrity":
        mode = st.radio("Valid values come from", ["Another dataset", "A fixed list"], horizontal=True, key=k("mode"))
        if mode == "Another dataset":
            a, b = st.columns(2)
            p["reference_dataset"] = a.selectbox("Reference dataset", dataset_names or ["(none registered)"], key=k("ds"))
            p["reference_column"] = b.text_input("Reference column", key=k("rc")).strip() or None
        else:
            p["reference_values"] = rl.split_values(st.text_area("Valid values (one per line, or comma-separated)", key=k("rv"), height=100))
    else:
        st.caption("This rule type has no parameters.")
    return rl.clean_params(p)


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
    st.caption(rl.RULE_HELP.get(rtype, ""))
    n1, n2, n3 = st.columns(3)
    name = n1.text_input("Name (optional)", key="new_name")
    mode = rl.column_mode(rtype)
    column = n2.selectbox(
        "Column" + {"required": " (required)", "optional": " (optional)", "none": " (not used)"}[mode], [None] + cols, key="new_col",
        format_func=lambda x: "(none)" if x is None else x, disabled=mode == "none",
        help="Column rules (unique, data_type, numeric_range, …) need one; table-level rules ignore it.")
    severity = n3.selectbox("Severity", SEVS, index=2, key="new_sev")
    scope = st.radio("Applies to", ["This dataset", "All datasets (global)"], horizontal=True, key="new_scope")
    guided = render_rule_form(rtype, cols, [d["name"] for d in state.api(c.list_datasets)])
    adv = st.checkbox("Edit parameters as JSON (advanced)", key="new_adv",
                      help="Starts from what the form above produces; use it for options the form does not cover.")
    if adv:
        new_params = st.text_area("Parameters (JSON)", json.dumps(guided or RULE_TEMPLATES[rtype], indent=2), height=150, key=f"new_params_{rtype}")
    if st.button("Create rule", type="primary"):
        try:
            params = json.loads(new_params) if adv else guided
            col_value = column if mode != "none" else None
            issues = rl.problems(rtype, col_value, params) if isinstance(params, dict) else ["Parameters must be a JSON object."]
            if issues:
                for msg in issues:
                    st.error(msg)
                st.stop()
            body = {"dataset_id": None if scope.startswith("All") else ds["id"], "rule_type": rtype, "severity": severity,
                    "column": col_value, "params": params}
            if name.strip():
                body["name"] = name.strip()
            c.create_rule(body)
        except json.JSONDecodeError as exc:
            st.error(f"Parameters are not valid JSON: {exc}")
        except ApiError as exc:
            ui.show_error(exc)
        else:
            ui.toast("Rule created", "✅")
            st.success("Rule created.")
            st.rerun()

    st.divider()
    st.markdown("**Export / import rules**")
    st.caption("Move a rule set between datasets or environments. Import skips rules whose name already exists here.")
    x1, x2 = st.columns(2)
    own = [r for r in rules if r["dataset_id"] is not None]
    x1.download_button(f"Export {len(own)} dataset rule(s) (JSON)", json.dumps(insights.rules_export(own), indent=2),
                       f"{ds['name'].replace(' ', '_').lower()}_rules.json", "application/json", disabled=not own, key="rules_export")
    up = x2.file_uploader("Import rules (JSON)", type=["json"], key="rules_import_file")
    if up is not None:
        try:
            payload = json.loads(up.getvalue().decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            st.error(f"That file is not valid JSON: {exc}")
        else:
            plan, notes = insights.rules_import_plan(payload, [r["name"] for r in rules])
            for note in notes:
                st.caption("• " + note)
            if plan and st.button(f"Import {len(plan)} rule(s) into {ds['name']}", type="primary", key="rules_import_go"):
                made, failed = 0, []
                for body in plan:
                    try:
                        c.create_rule({"dataset_id": ds["id"], **body})
                        made += 1
                    except ApiError as exc:
                        failed.append(f"{body.get('name') or body['rule_type']}: {exc}")
                ui.toast(f"Imported {made} rule(s)" + (f" · {len(failed)} failed" if failed else ""), "✅" if not failed else "⚠️")
                for f_ in failed:
                    st.error(f_)
                if made:
                    st.rerun()
    st.caption("Business-rule expressions use a safe subset of Python (see docs/RULES.md); text is never executed.")


with tab_alert:
    render_alerting(c, ds)

with tab_sched:
    render_scheduler(c, ds)
