from __future__ import annotations
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd, streamlit as st
from components import charts, state, ui

c = state.client()
ds = state.current_dataset()
ui.page_header("Run History", "Every monitoring run for this dataset — score trend, drift and incident count.", ds)

if not state.versions_of(ds):
    ui.callout("info", "No versions yet."); st.stop()

rows = state.api(c.run_history, ds["id"], limit=30)
if not rows:
    ui.callout("info", "No monitoring runs yet. Use the Monitor button on the Overview page or the Run Monitor section below.")
else:
    healthy   = sum(1 for r in rows if r.get("health") == "healthy")
    degraded  = sum(1 for r in rows if r.get("health") == "degraded")
    failing   = sum(1 for r in rows if r.get("health") == "failing")
    avg_score = sum(r["score"] for r in rows if r.get("score") is not None) / max(sum(1 for r in rows if r.get("score") is not None), 1)
    ui.kpi_cards([
        {"label": "Total runs", "value": len(rows), "sub": "shown: last 30"},
        {"label": "Avg score", "value": f"{avg_score:.1f}", "sub": "over runs with a score", "color": "#22c55e" if avg_score >= 90 else "#eab308" if avg_score >= 70 else "#ef4444"},
        {"label": "Healthy", "value": healthy, "color": "#22c55e"},
        {"label": "Degraded", "value": degraded, "color": "#eab308"},
        {"label": "Failing", "value": failing, "color": "#ef4444"},
    ])
    scored = [r for r in rows if r.get("score") is not None]
    if scored:
        ui.show_chart(charts.run_history_chart(list(reversed(scored))), key="rh_trend")

    st.subheader("Run log")
    df = pd.DataFrame([{
        "started": r.get("started_at", "")[:16], "status": r.get("status"),
        "health": r.get("health", "–"), "score": r.get("score"),
        "v.cur": r.get("current_version"), "v.ref": r.get("reference_version"),
        "failures": r.get("failures", 0), "warnings": r.get("warnings", 0),
        "drifted": r.get("drifted_features", 0), "schema Δ": r.get("schema_changes", 0),
        "incidents +": r.get("incidents_created", 0), "incidents ~": r.get("incidents_updated", 0),
        "run_id": r["run_id"][:8],
    } for r in rows])
    ui.show_df(df)

    st.subheader("Inspect a run")
    run_options = [f"{r['started_at'][:16]}  v{r['current_version']}  [{r['status']}]" for r in rows]
    pick_idx = st.selectbox("Select run", range(len(run_options)), format_func=lambda i: run_options[i], key="rh_pick")
    selected_run = rows[pick_idx]
    run_id = selected_run["run_id"]
    detail = state.api(c.get_run, ds["id"], run_id)
    checks = detail.get("check_results", [])
    drift  = detail.get("drift_results", [])
    t_sum, t_chk, t_drft, t_rprt = st.tabs(["Summary", "Check results", "Drift results", "Download report"])
    with t_sum:
        st.json(detail.get("summary") or {}, expanded=True)
    with t_chk:
        if checks:
            ui.show_df(pd.DataFrame([{k: r[k] for k in ("rule_name","outcome","severity","columns","affected_pct")} for r in checks]))
        else:
            st.info("No check results stored for this run.")
    with t_drft:
        if drift:
            ui.show_df(pd.DataFrame([{"column": r["column"], "kind": r["kind"], "status": r["status"],
                                       "drift_detected": r["drift_detected"], "effect_size": r.get("effect_size")} for r in drift]))
        else:
            st.info("No drift results stored (run had no reference version or drift was not run).")
    with t_rprt:
        c1, c2, c3 = st.columns(3)
        for label, fmt, mime in (("HTML", "html", "text/html"), ("JSON", "json", "application/json"), ("CSV", "csv", "text/csv")):
            if c1.button(f"Download {label}", key=f"rh_dl_{fmt}"):
                try:
                    data = c.get_report(run_id, fmt)
                    st.download_button(f"Save {label}", data, f"report_{run_id[:8]}.{fmt}", mime)
                except Exception as exc:
                    ui.show_error(exc)
            c1, c2, c3 = c2, c3, st.columns(3)[2]  # rotate columns

st.divider()
st.subheader("Run a new monitoring check")
cur, ref = state.version_selectors(ds, "rh_new")
excl_cols = [x["name"] for x in state.api(c.schema, ds["id"], cur)["columns"]
             if x["inferred_type"] == "datetime"]
exclude = st.multiselect("Exclude from drift", [x["name"] for x in state.api(c.schema, ds["id"], cur)["columns"]],
                         default=excl_cols, key="rh_excl")
col_sync, col_async = st.columns(2)
if col_async.button("Submit as background job (recommended)", type="primary",
                    help="Returns immediately; poll status from the jobs list above."):
    try:
        job = c.submit_monitor_job(ds["id"], {"current_version": cur, "reference_version": ref,
                                               "exclude_drift_columns": exclude})
        st.success(f"Job submitted: `{job['job_id'][:12]}…` — poll `GET /api/v1/jobs/{job['job_id']}`")
        st.rerun()
    except Exception as exc:
        ui.show_error(exc)

if col_sync.button("Run now (synchronous)", help="Waits in the browser. Use for small datasets."):
    with st.spinner("Running — this may take a moment…"):
        try:
            run = c.monitor(ds["id"], {"current_version": cur, "reference_version": ref,
                                       "exclude_drift_columns": exclude})
            st.success(f"Run completed: {run['status']} · {run['summary'].get('incidents',{}).get('created',0)} incidents created.")
            st.rerun()
        except Exception as exc:
            ui.show_error(exc)

# Job list for this dataset
with st.expander("Recent background jobs for this dataset", expanded=False):
    try:
        jobs = c.list_jobs(ds["id"], limit=10)
        if jobs:
            ui.show_df(pd.DataFrame([{
                "job_id": j["job_id"][:12], "type": j["job_type"], "status": j["status"],
                "queued": j.get("queued_at","")[:16], "finished": (j.get("finished_at") or "")[:16],
                "error": j.get("error_message","")[:60],
            } for j in jobs]))
        else:
            st.caption("No jobs yet.")
    except Exception as exc:
        ui.show_error(exc)

