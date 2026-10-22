from __future__ import annotations
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd, streamlit as st
from components import charts, state, ui
from services.api_client import ApiError

SEVERITIES = ["critical", "high", "medium", "low", "info"]
STATUSES   = ["open", "acknowledged", "resolved", "ignored"]
TYPES      = ["quality", "schema", "drift"]
TRANSITIONS = {
    "open":         ["acknowledged", "resolved", "ignored"],
    "acknowledged": ["resolved", "open",         "ignored"],
    "resolved":     ["open"],
    "ignored":      ["open"],
}

c = state.client()
ds = state.current_dataset()
ui.page_header("Incident Center", "All data-quality and drift incidents, with evidence, impact and recommended actions.", ds)

# ── filters ──────────────────────────────────────────────────────────────────
f1, f2, f3, f4 = st.columns([2, 2, 2, 3])
sel_status = f1.multiselect("Status", STATUSES, default=["open", "acknowledged"], key="inc_status")
sel_sev    = f2.multiselect("Severity", SEVERITIES, default=SEVERITIES, key="inc_sev")
sel_type   = f3.multiselect("Type", TYPES, default=TYPES, key="inc_type")
search     = f4.text_input("Search title or column", key="inc_q")

params = dict(dataset_id=ds["id"], status=sel_status or None, severity=sel_sev or None,
              incident_type=None, search=search or None, limit=100)
data = state.api(c.list_incidents, **params)
incidents = [i for i in data["items"] if not sel_type or i["incident_type"] in sel_type]
total = data["total"]

# ── KPIs ─────────────────────────────────────────────────────────────────────
by_sev = {s: sum(1 for i in data["items"] if i["severity"] == s) for s in SEVERITIES}
ui.kpi_cards([
    {"label": "Showing", "value": len(incidents), "sub": f"of {total} in filter"},
    {"label": "Critical/High", "value": by_sev["critical"] + by_sev["high"], "color": "#ef4444" if by_sev["critical"] + by_sev["high"] else "#22c55e"},
    {"label": "Open", "value": sum(1 for i in data["items"] if i["status"] == "open"), "color": "#f97316"},
    {"label": "Acknowledged", "value": sum(1 for i in data["items"] if i["status"] == "acknowledged"), "color": "#eab308"},
    {"label": "Resolved", "value": sum(1 for i in data["items"] if i["status"] == "resolved"), "color": "#22c55e"},
])
if by_sev:
    left, right = st.columns([1, 3])
    with left:
        ui.show_chart(charts.severity_donut(by_sev), key="inc_donut")
    with right:
        st.markdown("**Severity heuristic** — scores are shown as [0-100], not probabilities. "
                    "They combine rule severity, affected percentage, incident type, downstream assets and recurrence.")

if not incidents:
    ui.callout("success" if not search else "info", "No incidents match the current filters.")
    st.stop()

# ── incident list ─────────────────────────────────────────────────────────────
st.markdown(f"**{len(incidents)} incidents** — click one to inspect evidence and take action.")
labels = [f"[{i['severity'].upper()}] {i['incident_type']} — {i['title'][:80]} ({i['status']})" for i in incidents]
pick = st.selectbox("Select incident", range(len(labels)), format_func=lambda i: labels[i], key="inc_pick")
inc = state.api(c.get_incident, incidents[pick]["id"])

# ── detail ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(
    f'{ui.severity_badge(inc["severity"])} {ui.badge(inc["incident_type"],"#64748b")} '
    + ui.status_badge(inc["status"])
    + " ".join(ui.badge(col,"#2dd4bf") for col in (inc.get("columns") or [])[:8]),
    unsafe_allow_html=True)
st.markdown(f"**{ui.esc(inc['title'][:200])}**", unsafe_allow_html=True)
sco = inc.get("severity_score")
if sco is not None:
    st.caption(f"Severity score: {sco}/100 · Occurred {inc['occurrence_count']}× · "
               f"Recurrences {inc['recurrence_count']} · First seen {inc['first_seen_at'][:10]} · Last {inc['last_seen_at'][:10]}")

tab_ev, tab_recs, tab_lin, tab_hist, tab_act = st.tabs([
    "Evidence", "Recommendations", "Downstream impact", "Status history", "Update status"])

with tab_ev:
    a, b = st.columns(2)
    a.markdown("**Observed**"); a.json(inc["observed"] or {}, expanded=False)
    b.markdown("**Expected**"); b.code(str(inc["expected"]) if inc.get("expected") else "–")
    if inc.get("evidence"):
        st.markdown("**Evidence**"); st.json(inc["evidence"], expanded=False)
    if inc.get("affected_pct") is not None:
        st.metric("Rows affected", ui.fmt_pct(inc["affected_pct"]),
                  f"{ui.fmt_int(inc['affected_count'])} rows")
    bd = inc.get("severity_breakdown") or {}
    if bd:
        with st.expander("Severity score breakdown (heuristic, not a probability)"):
            rows_bd = [{k: v for k, v in bd.items() if k not in ("note")}]
            ui.show_df(pd.DataFrame([rows_bd[0]]))
            st.caption(bd.get("note",""))

with tab_recs:
    recs = inc.get("recommendations") or []
    if not recs:
        st.info("No recommendations available.")
    else:
        for r in recs:
            st.markdown(f"**{r['priority']}.** {r['action']}")

with tab_lin:
    assets = inc.get("lineage_impact") or []
    if not assets:
        st.info("No downstream assets found. Add lineage on the Lineage page to see impact here.")
    else:
        ui.kpi_cards([{"label": "Affected assets", "value": len(assets),
                       "sub": ", ".join({a["asset_type"] for a in assets})}])
        ui.show_df(pd.DataFrame([{"asset": a["name"], "type": a["asset_type"],
                                  "dependency": a["dependency_kind"], "via": a.get("via","–")} for a in assets]))

with tab_hist:
    hist = state.api(c.incident_history, inc["id"])
    if not hist:
        st.info("No history yet.")
    else:
        ui.show_df(pd.DataFrame(hist))

with tab_act:
    current_status = inc["status"]
    allowed = TRANSITIONS.get(current_status, [])
    if not allowed:
        st.info(f"No transitions available from '{current_status}'.")
    else:
        new_status = st.selectbox("New status", allowed, key="inc_new_status")
        note = st.text_area("Note", key="inc_note")
        changed_by = st.text_input("Your name (optional)", key="inc_by")
        if st.button("Update status", type="primary"):
            try:
                updated = c.update_incident(inc["id"], new_status, note, changed_by)
                st.success(f"Status updated to {updated['status']}.")
                st.rerun()
            except ApiError as exc:
                ui.show_error(exc)

# ── compact table ─────────────────────────────────────────────────────────────
with st.expander("All incidents table (CSV export)"):
    df_all = pd.DataFrame([{
        "severity": i["severity"], "type": i["incident_type"], "status": i["status"],
        "columns": ",".join(i.get("columns",[]))[:60], "occurred": i["occurrence_count"],
        "score": i.get("severity_score"), "downstream": len(i.get("downstream_assets",[])),
        "title": i["title"][:80]} for i in incidents])
    ui.show_df(df_all)
    st.download_button("Download CSV", df_all.to_csv(index=False), "incidents.csv", "text/csv")
