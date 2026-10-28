"""Command Center — fleet-wide health: every dataset, its latest run, open incidents and what needs attention first."""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, insights, prefs, state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

HEALTH_COLORS = {"healthy": "#22c55e", "degraded": "#eab308", "failing": "#ef4444", "unknown": "#64748b"}
CACHE_SECONDS = 30

c = state.client()
datasets = state.api(c.list_datasets)

ui.page_header("Command Center", "Every dataset at a glance: how healthy it is, what changed, and what to look at first.")

# ------------------------------------------------------------------ empty / onboarding
if not datasets:
    ui.empty_state("🛡️", "Welcome to DataSentinel", "Register a dataset, approve a clean baseline and run a first monitoring check. "
                   "Your fleet health will appear here.")
    ui.steps([("Load data", "Upload a CSV/Excel file or load the built-in synthetic retail demo on the Datasets page.", False),
              ("Set rules & a baseline", "Learn starter rules from a clean version and approve it as the reference.", False),
              ("Run monitoring", "Compare a new version against the baseline to score quality, find drift and open incidents.", False)])
    if st.button("Open Datasets", type="primary", key="cc_open_datasets"):
        st.switch_page("pages/datasets.py")
    st.stop()

# ------------------------------------------------------------------ fleet data (short cache, explicit refresh)
snap = st.session_state.get("_fleet_snap")
ids = tuple(sorted(d["id"] for d in datasets))
stale = (not snap or snap["ids"] != ids or time.time() - snap["at"] > CACHE_SECONDS)
top_l, top_r = st.columns([5, 1])
if top_r.button("Refresh", key="cc_refresh", use_container_width=True, help="Reload runs and incidents for every dataset."):
    stale = True
if stale:
    with st.spinner("Collecting fleet status…"):
        rows, incidents = state.fleet_snapshot(datasets)
    snap = st.session_state["_fleet_snap"] = {"ids": ids, "at": time.time(), "rows": rows, "incidents": incidents}
rows, incidents = snap["rows"], snap["incidents"]
stale_h = top_r.number_input("Stale after (h)", 1, 720, int(prefs.get("stale_hours", 48)), key="cc_stale",
                             help="A dataset whose latest run is older than this is flagged stale. Saved to data/ui_prefs.json.")
prefs.set("stale_hours", int(stale_h))
for r in rows:
    r["stale"] = insights.staleness(r["last_run"], stale_h)["stale"] and bool(r["runs"])
k = insights.fleet_kpis(rows)
age_s = int(time.time() - snap["at"])
top_l.caption(f"Updated {'just now' if age_s < 5 else f'{age_s}s ago'} · {k['datasets']} dataset(s), "
              f"{sum(r['runs'] for r in rows)} recent run(s) analysed")

# ------------------------------------------------------------------ KPIs
sparks = [r["spark"] for r in rows if len(r["spark"]) >= 2]
n_pts = min((len(x) for x in sparks), default=0)
avg_spark = [sum(x[-n_pts:][i] for x in sparks) / len(sparks) for i in range(n_pts)] if n_pts >= 2 else []
ui.kpi_cards([
    {"label": "Fleet score", "value": ui.fmt_score(k["avg_score"]), "sub": "average of latest runs",
     "color": insights.score_color(k["avg_score"]), "spark": avg_spark},
    {"label": "Healthy", "value": k["healthy"], "sub": f"of {k['datasets']} datasets", "color": "#22c55e"},
    {"label": "Degraded / failing", "value": f"{k['degraded']} / {k['failing']}", "sub": "need a look" if k["degraded"] + k["failing"] else "none",
     "color": "#ef4444" if k["failing"] else "#eab308" if k["degraded"] else "#22c55e"},
    {"label": "Open incidents", "value": k["open_incidents"], "sub": f"{k['critical_high']} critical / high",
     "color": "#ef4444" if k["critical_high"] else "#eab308" if k["open_incidents"] else "#22c55e"},
    {"label": "Not monitored / stale", "value": f"{k['unmonitored']} / {sum(1 for r in rows if r.get('stale'))}",
     "sub": f"no run yet / older than {stale_h} h",
     "color": "#eab308" if k["unmonitored"] or any(r.get("stale") for r in rows) else "#22c55e"},
])

# ------------------------------------------------------------------ run monitoring everywhere
with st.expander("Run monitoring on every dataset", expanded=False):
    st.caption("Runs the full pipeline (rules, schema diff, drift, root cause, incidents) on the latest version of each dataset, "
               "against its approved baseline when one exists.")
    pick = st.multiselect("Datasets", [d["name"] for d in datasets], default=[d["name"] for d in datasets], key="cc_run_pick")
    if st.button("Run monitoring now", type="primary", disabled=not pick, key="cc_run_all"):
        by_name = {d["name"]: d for d in datasets}
        bar, results = st.progress(0.0, text="Starting…"), []
        for n, name in enumerate(pick, 1):
            bar.progress((n - 1) / len(pick), text=f"Monitoring {name} ({n}/{len(pick)})…")
            try:
                run = c.monitor(by_name[name]["id"], {})
                s = run.get("summary") or {}
                results.append({"dataset": name, "status": run.get("status"), "score": (s.get("score") or {}).get("overall"),
                                "new incidents": (s.get("incidents") or {}).get("created", 0), "note": ""})
            except ApiError as exc:
                results.append({"dataset": name, "status": "failed", "score": None, "new incidents": 0, "note": str(exc)[:120]})
        bar.progress(1.0, text="Done")
        st.session_state.pop("_fleet_snap", None)
        failed = sum(1 for r in results if r["status"] == "failed")
        ui.toast(f"Monitoring finished for {len(results)} dataset(s)" + (f" · {failed} failed" if failed else ""),
                 "⚠️" if failed else "✅")
        ui.show_df(pd.DataFrame(results))
        st.button("Reload fleet status", key="cc_reload")

# ------------------------------------------------------------------ attention + chart
ui.section("Needs attention", "ranked by health, open incidents, failed checks and drift")
ranked = sorted((r for r in rows if insights.attention_score(r) > 0), key=insights.attention_score, reverse=True)
left, right = st.columns([3, 2])
with left:
    if not ranked:
        ui.callout("success", "Everything is healthy and no incidents are open. Nice.")
    for r in ranked[:5]:
        col = HEALTH_COLORS.get(r["health"], "#64748b")
        reasons = []
        if r["critical_high"]:
            reasons.append(f"{r['critical_high']} critical/high incident(s)")
        elif r["open_incidents"]:
            reasons.append(f"{r['open_incidents']} open incident(s)")
        if r["failures"]:
            reasons.append(f"{r['failures']} failed check(s)")
        if r["drifted"]:
            reasons.append(f"{r['drifted']} drifted feature(s)")
        if r["breaking"]:
            reasons.append(f"{r['breaking']} breaking schema change(s)")
        if not r["runs"]:
            reasons.append("never monitored")
        elif r.get("stale"):
            reasons.append(f"stale — last run {insights.time_ago(r['last_run'])}")
        st.markdown(
            f'<div class="ds-card" style="border-left:4px solid {col}"><div class="ds-row"><b style="font-size:1.02rem">{ui.esc(r["name"])}</b>'
            f'{ui.status_badge(r["health"])}<span class="ds-muted">last run {ui.esc(insights.time_ago(r["last_run"]))}</span></div>'
            f'<div class="ds-muted">{ui.esc(" · ".join(reasons) or "no specific issue")}</div></div>', unsafe_allow_html=True)
with right:
    with ui.card("cc_scores"):
        ui.card_title("Latest score by dataset", "dotted lines: 90 healthy · 70 degraded")
        if any(r["score"] is not None for r in rows):
            ui.show_chart(charts.fleet_score_bars(rows), key="cc_fleet_scores")
        else:
            st.caption("No scored runs yet. Run monitoring to see scores here.")

# ------------------------------------------------------------------ dataset tiles
ui.section("Datasets", "open one to dig in")
cols = st.columns(3)
for n, r in enumerate(sorted(rows, key=lambda x: (-insights.attention_score(x), x["name"]))):
    col = HEALTH_COLORS.get(r["health"], "#64748b")
    with cols[n % 3]:
        ring = ui.score_ring(r["score"], 76)
        spark = ui.sparkline(r["spark"], col, 110, 28) if len(r["spark"]) > 1 else ""
        sev = r["by_severity"]
        st.markdown(
            f'<div class="ds-tile" style="--c:{col}">{ring}<div style="min-width:0;flex:1">'
            f'<h4>{ui.esc(r["name"])}</h4>'
            f'<div class="meta">{ui.status_badge(r["health"])}{ui.badge("stale", "#eab308") if r.get("stale") else ""} &nbsp;{ui.esc(r["project"] or "no project")} · {r["versions"] or 0} version(s)</div>'
            f'<div class="stats"><span><b>{ui.esc(insights.time_ago(r["last_run"]))}</b>last run</span>'
            f'<span><b style="color:{"#ef4444" if sev["critical"] + sev["high"] else "#f1f5f9"}">{r["open_incidents"]}</b>open incidents</span>'
            f'<span><b>{r["failures"]}</b>failed</span><span><b>{r["drifted"]}</b>drifted</span></div>'
            f'<div style="margin-top:.35rem">{spark}</div></div></div>', unsafe_allow_html=True)
        b1, b2, b3 = st.columns(3)
        if b1.button("Trends", key=f"cc_tr_{r['id']}", use_container_width=True):
            state.open_page("pages/dashboard.py", r["name"])
        if b2.button("Incidents", key=f"cc_in_{r['id']}", use_container_width=True):
            state.open_page("pages/incidents.py", r["name"])
        if b3.button("Runs", key=f"cc_ru_{r['id']}", use_container_width=True):
            state.open_page("pages/run_history.py", r["name"])
        st.markdown("<div style='height:.5rem'></div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ recent incident activity + export
ui.section("Recent incident activity", "latest first, across all datasets")
names = {d["id"]: d["name"] for d in datasets}
recent = sorted(incidents, key=lambda i: str(i.get("last_seen_at") or ""), reverse=True)[:10]
if recent:
    ui.show_df(pd.DataFrame([{
        "when": insights.time_ago(i.get("last_seen_at")), "dataset": names.get(i.get("dataset_id"), "?"),
        "severity": i["severity"], "type": i["incident_type"], "status": i["status"], "title": i["title"][:90],
        "seen ×": i.get("occurrence_count")} for i in recent]))
else:
    st.caption("No open incidents across the fleet.")

fleet_df = pd.DataFrame([{
    "dataset": r["name"], "project": r["project"], "health": r["health"], "score": r["score"], "score_change": r["score_delta"],
    "last_run": r["last_run"], "failed_checks": r["failures"], "drifted_features": r["drifted"],
    "breaking_schema_changes": r["breaking"], "open_incidents": r["open_incidents"], "critical_high": r["critical_high"]} for r in rows])
import datetime as _dt  # noqa: E402
st.download_button("Download fleet report (Markdown)", insights.fleet_report_md(rows, k, incidents, names, _dt.datetime.now().strftime("%Y-%m-%d %H:%M")),
                   "datasentinel_fleet_report.md", "text/markdown", key="cc_dl_md")
st.download_button("Download fleet status (CSV)", fleet_df.to_csv(index=False), "datasentinel_fleet_status.csv", "text/csv", key="cc_dl")
