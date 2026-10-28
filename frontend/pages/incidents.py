"""Incident Center — triage incidents (bulk actions), inspect evidence, impact and history."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, insights, state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

SEVERITIES = ["critical", "high", "medium", "low", "info"]
STATUSES = ["open", "acknowledged", "resolved", "ignored"]
TYPES = ["quality", "schema", "drift"]
SORTS = {  # label -> (key function, reverse)
    "Priority (severity, score)": (insights.incident_priority, False),
    "Most recently seen": (lambda i: str(i.get("last_seen_at") or ""), True),
    "Oldest first": (lambda i: str(i.get("first_seen_at") or ""), False),
    "Most frequent": (lambda i: i.get("occurrence_count") or 0, True),
}
TRANSITIONS = {"open": ["acknowledged", "resolved", "ignored"], "acknowledged": ["resolved", "open", "ignored"],
               "resolved": ["open"], "ignored": ["open"]}

c = state.client()
ds = state.current_dataset()
all_datasets = state.api(c.list_datasets)
names = {d["id"]: d["name"] for d in all_datasets}
ui.page_header("Incident Center", "Triage data-quality, schema and drift incidents — with evidence, downstream impact and recommended actions.", ds)

# ── filters ──────────────────────────────────────────────────────────────────
scope = st.radio("Scope", ["This dataset", "All datasets"], horizontal=True, key="inc_scope", label_visibility="collapsed")
f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 3, 2])
sel_status = f1.multiselect("Status", STATUSES, default=["open", "acknowledged"], key="inc_status")
sel_sev = f2.multiselect("Severity", SEVERITIES, default=SEVERITIES, key="inc_sev")
sel_type = f3.multiselect("Type", TYPES, default=TYPES, key="inc_type")
search = f4.text_input("Search title or column", key="inc_q")
sort_by = f5.selectbox("Sort by", list(SORTS), key="inc_sort")

params = dict(dataset_id=ds["id"] if scope == "This dataset" else None, status=sel_status or None, severity=sel_sev or None,
              incident_type=None, search=search or None, limit=200)
data = state.api(c.list_incidents, **params)
_key, _rev = SORTS[sort_by]
incidents = sorted([i for i in data["items"] if not sel_type or i["incident_type"] in sel_type], key=_key, reverse=_rev)
total = data["total"]
stats = insights.incident_stats(incidents)

# ── KPIs & charts ────────────────────────────────────────────────────────────
by_sev = stats["by_severity"]
ch = by_sev["critical"] + by_sev["high"]
ui.kpi_cards([
    {"label": "Showing", "value": len(incidents), "sub": f"of {total} matching the filters"},
    {"label": "Critical / High", "value": ch, "color": "#ef4444" if ch else "#22c55e", "sub": "act on these first"},
    {"label": "Open now", "value": stats["open"], "color": "#f97316" if stats["open"] else "#22c55e", "sub": "open + acknowledged"},
    {"label": "Oldest open", "value": "–" if stats["oldest_days"] is None else f"{stats['oldest_days']:.0f} d",
     "sub": "" if stats["avg_age_days"] is None else f"average {stats['avg_age_days']:.1f} d", "color": "#eab308"},
    {"label": "Recurring", "value": stats["recurring"], "sub": "came back after being resolved",
     "color": "#a78bfa" if stats["recurring"] else "#22c55e"},
    {"label": "Mean time to resolve", "value": "–" if stats["mean_days_to_resolve"] is None else f"{stats['mean_days_to_resolve']:.1f} d",
     "sub": "for resolved incidents in view", "color": "#38bdf8"},
])

if not incidents:
    ui.empty_state("🔎" if search else "✅", "No incidents match",
                   "Nothing found for the current filters." if search else "No incidents in this view. Run monitoring to check again.")
    st.stop()

with st.expander("Charts", expanded=len(incidents) > 3):
    g1, g2 = st.columns([1, 2])
    with g1:
        ui.show_chart(charts.severity_donut({k: v for k, v in by_sev.items() if v}), key="inc_donut")
    with g2:
        ui.show_chart(charts.incident_timeline(incidents), key="inc_timeline")
    st.caption("Severity scores are a **heuristic** on a 0–100 scale, not probabilities. They combine rule severity, affected share, "
               "incident type, downstream assets and recurrence.")

# ── table with multi-select ──────────────────────────────────────────────────
ui.section("Incidents", "tick rows to triage several at once, or pick one below to inspect it")
table = pd.DataFrame([{
    "severity": i["severity"], "status": i["status"], "type": i["incident_type"],
    **({"dataset": names.get(i.get("dataset_id"), "?")} if scope == "All datasets" else {}),
    "title": i["title"][:90], "columns": ", ".join(i.get("columns") or [])[:40], "score": i.get("severity_score"),
    "seen ×": i.get("occurrence_count"), "age (d)": round(insights.age_days(i.get("first_seen_at")) or 0, 1),
    "last seen": insights.time_ago(i.get("last_seen_at"))} for i in incidents])
selected_rows: list[int] = []
try:
    event = st.dataframe(table, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="multi-row", key="inc_table",
                         column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%d")})
    selected_rows = list(event.selection.rows)
except (TypeError, AttributeError):  # Streamlit without dataframe selection support
    ui.show_df(table)
picked = [incidents[n] for n in selected_rows if n < len(incidents)]

if picked:
    with ui.card("inc_bulk"):
        ui.card_title(f"Triage {len(picked)} selected incident(s)", "status changes that are not allowed from an incident's current state are skipped")
        b0, b1 = st.columns([3, 2])
        note = b0.text_input("Note (recorded in each incident's history)", key="inc_bulk_note")
        who = b1.text_input("Your name (optional)", key="inc_bulk_by")
        a1, a2, a3, a4 = st.columns(4)
        action = None
        for col, (label, new, kind) in zip((a1, a2, a3, a4), (("Acknowledge", "acknowledged", "secondary"), ("Resolve", "resolved", "primary"),
                                                              ("Ignore", "ignored", "secondary"), ("Reopen", "open", "secondary"))):
            if col.button(label, key=f"inc_bulk_{new}", type=kind, use_container_width=True):
                action = new
        if action:
            done = skipped = failed = 0
            for inc_ in picked:
                if not insights.allowed_transition(inc_["status"], action):
                    skipped += 1
                    continue
                try:
                    c.update_incident(inc_["id"], action, note, who)
                    done += 1
                except ApiError:
                    failed += 1
            ui.toast(f"{done} incident(s) set to {action}" + (f" · {skipped} skipped" if skipped else "") + (f" · {failed} failed" if failed else ""),
                     "✅" if done and not failed else "⚠️")
            st.session_state.pop("_fleet_snap", None)
            st.rerun()

# ── detail ────────────────────────────────────────────────────────────────────
labels = [f"[{i['severity'].upper()}] {i['incident_type']} — {i['title'][:80]} ({i['status']})" for i in incidents]
default_idx = selected_rows[0] if selected_rows and selected_rows[0] < len(labels) else 0
pick = st.selectbox("Inspect incident", range(len(labels)), index=default_idx, format_func=lambda i: labels[i], key="inc_pick")
inc = state.api(c.get_incident, incidents[pick]["id"])

st.markdown("---")
st.markdown(
    f'{ui.severity_badge(inc["severity"])} {ui.badge(inc["incident_type"], "#64748b")} {ui.status_badge(inc["status"])} '
    + " ".join(ui.badge(col, "#2dd4bf") for col in (inc.get("columns") or [])[:8]), unsafe_allow_html=True)
st.markdown(f"### {ui.esc(inc['title'][:200])}", unsafe_allow_html=True)
sco = inc.get("severity_score")
if sco is not None:
    st.caption(f"Severity score {sco}/100 · seen {inc['occurrence_count']}× · recurred {inc['recurrence_count']}× · "
               f"first seen {inc['first_seen_at'][:10]} · last {inc['last_seen_at'][:10]}"
               + (f" · dataset **{names.get(inc.get('dataset_id'), '?')}**" if scope == "All datasets" else ""))

tab_ev, tab_recs, tab_lin, tab_hist, tab_act = st.tabs(["Evidence", "Recommendations", "Downstream impact", "Status history", "Update status"])

with tab_ev:
    a, b = st.columns(2)
    a.markdown("**Observed**")
    a.json(inc["observed"] or {}, expanded=False)
    b.markdown("**Expected**")
    b.code(str(inc["expected"]) if inc.get("expected") else "–")
    if inc.get("evidence"):
        st.markdown("**Evidence**")
        st.json(inc["evidence"], expanded=False)
    if inc.get("affected_pct") is not None:
        st.metric("Rows affected", ui.fmt_pct(inc["affected_pct"]), f"{ui.fmt_int(inc['affected_count'])} rows")
    bd = inc.get("severity_breakdown") or {}
    if bd:
        with st.expander("Severity score breakdown (heuristic, not a probability)"):
            ui.show_df(pd.DataFrame([{k: v for k, v in bd.items() if k != "note"}]))
            st.caption(bd.get("note", ""))

with tab_recs:
    recs = inc.get("recommendations") or []
    if not recs:
        st.info("No recommendations available.")
    else:
        for r in recs:
            st.markdown(f"**{r['priority']}.** {r['action']}")
        md = f"# {inc['title']}\n\n" + "\n".join(f"{r['priority']}. {r['action']}" for r in recs)
        st.download_button("Download checklist (Markdown)", md, "incident_actions.md", "text/markdown", key="inc_dl_recs")

with tab_lin:
    assets = inc.get("lineage_impact") or []
    if not assets:
        st.info("No downstream assets found. Add lineage on the Data Lineage page to see impact here.")
    else:
        ui.kpi_cards([{"label": "Affected assets", "value": len(assets), "sub": ", ".join(sorted({a["asset_type"] for a in assets}))}])
        ui.show_df(pd.DataFrame([{"asset": a["name"], "type": a["asset_type"], "dependency": a["dependency_kind"], "via": a.get("via", "–")}
                                 for a in assets]))

with tab_hist:
    hist = state.api(c.incident_history, inc["id"])
    if not hist:
        st.info("No status changes yet.")
    else:
        ui.show_df(pd.DataFrame(hist))

with tab_act:
    allowed = TRANSITIONS.get(inc["status"], [])
    if not allowed:
        st.info(f"No transitions available from '{inc['status']}'.")
    else:
        new_status = st.selectbox("New status", allowed, key="inc_new_status")
        note1 = st.text_area("Note", key="inc_note")
        by1 = st.text_input("Your name (optional)", key="inc_by")
        if st.button("Update status", type="primary", key="inc_update"):
            try:
                updated = c.update_incident(inc["id"], new_status, note1, by1)
                ui.toast(f"Status updated to {updated['status']}", "✅")
                st.session_state.pop("_fleet_snap", None)
                st.rerun()
            except ApiError as exc:
                ui.show_error(exc)

# ── export ───────────────────────────────────────────────────────────────────
st.download_button("Download visible incidents (CSV)", table.to_csv(index=False), "incidents.csv", "text/csv", key="inc_dl_all")
