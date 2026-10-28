"""Quality Trends — how quality, failures, drift and incidents evolve across *real* monitoring runs.

v8 note: earlier versions padded missing days with randomly generated numbers. Everything on this page now comes
from stored monitoring runs and their check results; days without a run simply have no data point.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, insights, prefs, state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

RANGES = {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30, "Last 90 days": 90, "All runs": None}

c = state.client()
ds = state.current_dataset()
ui.page_header("Quality Trends", "Quality score, failed checks, drift and incidents across monitoring runs — measured, never simulated.", ds)

all_runs = state.api(c.run_history, ds["id"], limit=100)
completed = [r for r in all_runs if r.get("status") == "completed"]
if not completed:
    ui.empty_state("📈", "No monitoring runs yet", "Trends are built from stored monitoring runs. Run a first check and this page fills in.")
    if st.button("Go to Run History", type="primary", key="tr_go_runs"):
        st.switch_page("pages/run_history.py")
    st.stop()

# ------------------------------------------------------------------ controls
f1, f2, f3 = st.columns([2, 1, 2])
range_label = f1.selectbox("Time range", list(RANGES), index=2, key="dq_range")
target = f2.number_input("Score target (SLA)", 0, 100, int(prefs.get("score_target", 90)), key="dq_target",
                         help="Used for the compliance KPI, the target line and the streak. Saved to data/ui_prefs.json.")
prefs.set("score_target", int(target))
rows = insights.filter_days(insights.runs_oldest_first(completed), RANGES[range_label])
if not rows:
    ui.callout("info", f"No completed runs in the {range_label.lower()}. Pick a wider range.")
    st.stop()
f3.caption(f"{len(rows)} completed run(s) in range · {len(all_runs) - len(completed)} failed run(s) excluded" if len(all_runs) != len(completed)
           else f"{len(rows)} completed run(s) in range")

scores = [r.get("score") for r in rows]
comp = insights.compliance(scores, target)
anomalies = insights.detect_anomalies(scores)
latest, prev = rows[-1], (rows[-2] if len(rows) > 1 else None)
try:
    inc_data = c.list_incidents(dataset_id=ds["id"], limit=200)
    incidents = inc_data["items"]
except ApiError:
    incidents = []
inc_stats = insights.incident_stats(incidents)

# ------------------------------------------------------------------ KPIs
ui.kpi_cards([
    {"label": "Latest score", "value": ui.fmt_score(latest.get("score")),
     "delta": ui.delta_html(insights.delta(latest.get("score"), (prev or {}).get("score")), good_when="up"),
     "sub": f"v{latest.get('current_version')} · {insights.time_ago(latest.get('started_at'))}",
     "color": insights.score_color(latest.get("score")), "spark": [s for s in scores if s is not None][-20:]},
    {"label": f"Runs ≥ {target}", "value": "–" if comp["pct"] is None else f"{comp['pct']:.0f}%",
     "sub": f"{comp['met']} of {comp['runs']} · current streak {comp['current_streak']} · best {comp['best_streak']}",
     "color": "#22c55e" if (comp["pct"] or 0) >= 90 else "#eab308" if (comp["pct"] or 0) >= 60 else "#ef4444"},
    {"label": "Failed checks", "value": latest.get("failures", 0),
     "delta": ui.delta_html(None if not prev else latest.get("failures", 0) - prev.get("failures", 0), good_when="down", digits=0),
     "sub": f"{latest.get('warnings', 0)} warnings", "color": "#ef4444" if latest.get("failures") else "#22c55e"},
    {"label": "Drifted features", "value": latest.get("drifted_features", 0),
     "sub": f"{latest.get('schema_changes', 0)} schema changes ({latest.get('breaking_changes', 0)} breaking)",
     "color": "#f97316" if latest.get("drifted_features") else "#22c55e"},
    {"label": "Open incidents", "value": inc_stats["open"],
     "sub": (f"oldest {inc_stats['oldest_days']:.0f} d" if inc_stats["oldest_days"] is not None else "none open"),
     "color": "#ef4444" if inc_stats["by_severity"]["critical"] + inc_stats["by_severity"]["high"] else "#eab308" if inc_stats["open"] else "#22c55e"},
])
if anomalies:
    when = ", ".join(charts.run_label(rows[i]) for i in anomalies[-3:])
    ui.callout("warn", f"{len(anomalies)} run(s) look unusual compared with this dataset's own history (most recent: {when}). "
                       "They are circled on the score chart.")

# ------------------------------------------------------------------ score trend
with ui.card("tr_score"):
    ui.card_title("Quality score per run", f"green band = at or above the {target} target · circled = unusual vs. history")
    ui.show_chart(charts.score_trend_with_target(rows, float(target), anomalies), key="tr_score_chart")

# run details (cached) feed the check-level charts
pairs = state.runs_with_checks(ds["id"], list(reversed(rows)), max_runs=20)
latest_checks = pairs[-1][1] if pairs else []

a, b = st.columns([3, 2])
with a:
    with ui.card("tr_outcomes"):
        ui.card_title("Failed checks, warnings and drift per run")
        ui.show_chart(charts.outcomes_per_run(rows), key="tr_outcomes_chart")
with b:
    with ui.card("tr_donut"):
        counts = insights.outcome_counts(latest_checks)
        ui.card_title("Latest run — check outcomes", f"{sum(counts.values())} checks evaluated")
        if latest_checks:
            total_eval = counts["pass"] + counts["warn"] + counts["fail"]
            centre = f"{100 * counts['pass'] / total_eval:.0f}%<br>passing" if total_eval else ""
            ui.show_chart(charts.outcome_donut(counts, centre), key="tr_donut_chart")
        else:
            st.caption("No stored check results for the latest run.")

# ------------------------------------------------------------------ where do the problems come from?
ui.section("Where the problems come from", "failed and warning checks grouped by rule type")
d1, d2 = st.columns([2, 3])
with d1:
    with ui.card("tr_types"):
        breakdown = insights.issue_breakdown(latest_checks)
        ui.card_title("Latest run — rows affected by rule type", "checks can touch the same rows, so this is an upper bound")
        if breakdown:
            ui.show_chart(charts.issue_type_bars(breakdown), key="tr_types_chart")
        else:
            ui.callout("success", "No failed or warning checks in the latest run.")
with d2:
    with ui.card("tr_heat"):
        ui.card_title("Worst affected % by rule type, per run", "blank = that rule type did not run")
        if len(pairs) >= 2:
            xs, ys, z = insights.heatmap_matrix(pairs)
            ui.show_chart(charts.rule_type_heatmap(xs, ys, z), key="tr_heat_chart")
        else:
            st.caption("Needs at least two runs with stored check results.")

e1, e2 = st.columns([3, 2])
with e1:
    with ui.card("tr_rules"):
        ui.card_title("Most persistent failing rules", f"over the last {len(pairs)} run(s) with stored results")
        table = insights.rule_failure_table(pairs)
        if table:
            top = table[:8]
            ui.bar_rows([(f"{t['rule']}", t["runs_failed"] + t["runs_warned"],
                          ui.SEVERITY_COLORS.get(t["severity"], "#64748b")) for t in top], max_value=max(1, len(pairs)), fmt="{:.0f} runs")
            with st.expander("Full table"):
                ui.show_df(pd.DataFrame([{
                    "rule": t["rule"], "type": t["type"], "severity": t["severity"], "failed in": t["runs_failed"],
                    "warned in": t["runs_warned"], "evaluated": t["runs_evaluated"], "latest outcome": t["latest_outcome"],
                    "latest affected %": t["latest_pct"], "columns": t["columns"]} for t in table]))
        else:
            ui.callout("success", "No rule failed or warned in these runs.")
with e2:
    with ui.card("tr_sev"):
        ui.card_title("Open incidents by severity")
        ui.show_chart(charts.severity_bars(inc_stats["by_severity"]), key="tr_sev_chart")

# ------------------------------------------------------------------ hotspots + aging
h1, h2 = st.columns([3, 2])
with h1:
    with ui.card("tr_hot"):
        ui.card_title("Column hotspots", "columns named by the most failing checks (latest run) and open incidents")
        hot = insights.column_hotspots(latest_checks, incidents)[:8]
        if hot:
            ui.bar_rows([(h["column"], h["checks"] + 2 * h["incidents"], ui.SEVERITY_COLORS.get(h["worst"], "#64748b")) for h in hot],
                        fmt="{:.0f} pts")
            st.caption("Score = failing checks + 2 × open incidents. Colour = worst open-incident severity.")
        else:
            st.caption("No column is currently implicated.")
with h2:
    with ui.card("tr_aging"):
        ui.card_title("Open incident age")
        ag = insights.aging_buckets(incidents)
        ui.bar_rows([(k, v, c_) for (k, v), c_ in zip(ag.items(), ("#38bdf8", "#eab308", "#f97316", "#ef4444"))], max_value=max(1, *ag.values()),
                    fmt="{:.0f}")

# ------------------------------------------------------------------ incident timeline + data table
if incidents:
    with ui.card("tr_timeline"):
        ui.card_title("Incident timeline", "when each open incident first appeared · bubble size = times seen")
        ui.show_chart(charts.incident_timeline(incidents), key="tr_timeline_chart")

ui.section("Daily summary", "days with no monitoring run are not shown")
daily = insights.daily_series(rows)
ui.show_df(pd.DataFrame([{
    "date": d["date"], "runs": d["runs"], "avg score": d["avg_score"], "failed checks": d["failures"], "warnings": d["warnings"],
    "drifted features": d["drifted"], "new incidents": d["incidents_created"]} for d in daily]))
runs_df = pd.DataFrame([{k: r.get(k) for k in ("started_at", "current_version", "reference_version", "health", "score", "failures",
                                                 "warnings", "drifted_features", "schema_changes", "breaking_changes",
                                                 "incidents_created", "incidents_updated", "run_id")} for r in rows])
st.download_button("Download runs (CSV)", runs_df.to_csv(index=False), f"{ds['name'].replace(' ', '_').lower()}_runs.csv", "text/csv", key="tr_dl")
st.caption(f"Dataset: {ds['name']} · range: {range_label.lower()} · source: stored monitoring runs and their check results.")
