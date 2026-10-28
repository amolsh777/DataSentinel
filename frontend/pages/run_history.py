"""Run History — timeline, run inspector, run-vs-run comparison, and a monitoring launcher with live job status."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, insights, state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

c = state.client()
ds = state.current_dataset()
ui.page_header("Run History", "Every monitoring run for this dataset: score trend, what failed, what changed between runs, and a launcher for new checks.", ds)

if not state.versions_of(ds):
    ui.callout("info", "No versions yet.")
    st.stop()

rows = state.api(c.run_history, ds["id"], limit=50)  # newest first


def run_label(r: dict) -> str:
    return f"{r['started_at'][:16].replace('T', ' ')} · v{r['current_version']} vs {('v' + str(r['reference_version'])) if r['reference_version'] else 'no ref'} · {r['status']}"


def seconds_between(a, b):
    da, db = insights.parse_ts(a), insights.parse_ts(b)
    return None if not (da and db) else max(0.0, (db - da).total_seconds())


# ── live job status (polls while a background job is running) ──────────────────
def _job_watcher() -> None:
    jid = st.session_state.get("rh_job")
    if not jid:
        return
    try:
        job = c.get_job(jid)
    except ApiError as exc:
        st.session_state.pop("rh_job", None)
        ui.show_error(exc)
        return
    st.markdown(f'<div class="ds-callout" style="--c:#38bdf8">Background job <code>{ui.esc(jid[:12])}</code> {ui.status_badge(job["status"])} '
                f'· queued {ui.esc(insights.time_ago(job.get("queued_at")))}</div>', unsafe_allow_html=True)
    if job["status"] in ("completed", "failed"):
        st.session_state.pop("rh_job", None)
        st.session_state["rh_job_done"] = {"status": job["status"], "error": job.get("error_message"), "run_id": job.get("run_id")}
        st.cache_data.clear()
        st.session_state.pop("_fleet_snap", None)
        st.rerun()


if st.session_state.get("rh_job"):
    st.fragment(run_every=2)(_job_watcher)()
done_job = st.session_state.pop("rh_job_done", None)
if done_job:
    if done_job["status"] == "completed":
        ui.callout("success", "Background monitoring job finished. The run is in the timeline below.")
        ui.toast("Monitoring job finished", "✅")
    else:
        ui.callout("danger", f"Background job failed: {done_job.get('error') or 'unknown error'}")

tab_time, tab_inspect, tab_compare, tab_new = st.tabs(["Timeline", "Inspect a run", "Compare runs", "New run"])

# ── timeline ───────────────────────────────────────────────────────────────────
with tab_time:
    if not rows:
        ui.empty_state("🕑", "No monitoring runs yet", "Open the “New run” tab to run a first check against your baseline.")
    else:
        scored = [r for r in rows if r.get("score") is not None]
        healthy = sum(1 for r in rows if r.get("health") == "healthy")
        degraded = sum(1 for r in rows if r.get("health") == "degraded")
        failing = sum(1 for r in rows if r.get("health") == "failing") + sum(1 for r in rows if r.get("status") == "failed")
        avg_score = sum(r["score"] for r in scored) / len(scored) if scored else None
        newest = rows[0]
        ui.kpi_cards([
            {"label": "Runs shown", "value": len(rows), "sub": "most recent 50", "spark": [r["score"] for r in reversed(scored)][-20:]},
            {"label": "Average score", "value": ui.fmt_score(avg_score), "sub": "over runs with a score", "color": insights.score_color(avg_score)},
            {"label": "Healthy", "value": healthy, "color": "#22c55e"},
            {"label": "Degraded", "value": degraded, "color": "#eab308"},
            {"label": "Failing / errored", "value": failing, "color": "#ef4444" if failing else "#22c55e"},
            {"label": "Latest run", "value": insights.time_ago(newest.get("started_at")), "sub": newest.get("status", ""), "color": "#38bdf8"},
        ])
        if scored:
            with ui.card("rh_trend"):
                ui.show_chart(charts.run_history_chart(list(reversed(scored))), key="rh_trend_chart")
        f1, f2 = st.columns([2, 3])
        status_filter = f1.multiselect("Show", ["completed", "failed"], default=["completed", "failed"], key="rh_status_filter")
        health_filter = f2.multiselect("Health", ["healthy", "degraded", "failing"], default=["healthy", "degraded", "failing"], key="rh_health_filter",
                                       help="Runs without a health value (e.g. failed runs) are always shown if their status is selected.")
        shown = [r for r in rows if r.get("status") in status_filter and (r.get("health") is None or r.get("health") in health_filter)]
        log = pd.DataFrame([{
            "started": r.get("started_at", "")[:16].replace("T", " "), "status": r.get("status"), "health": r.get("health", "–"),
            "score": r.get("score"), "v.cur": r.get("current_version"), "v.ref": r.get("reference_version"),
            "failures": r.get("failures", 0), "warnings": r.get("warnings", 0), "drifted": r.get("drifted_features", 0),
            "schema Δ": r.get("schema_changes", 0), "breaking": r.get("breaking_changes", 0),
            "incidents +": r.get("incidents_created", 0), "incidents ~": r.get("incidents_updated", 0),
            "duration": (lambda s: "–" if s is None else f"{s:.0f}s")(seconds_between(r.get("started_at"), r.get("finished_at"))),
            "run_id": r["run_id"][:8]} for r in shown])
        ui.show_df(log, column_config={"score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%.1f")})
        st.download_button("Download run log (CSV)", log.to_csv(index=False), "run_history.csv", "text/csv", key="rh_dl_log")

# ── inspect ────────────────────────────────────────────────────────────────────
with tab_inspect:
    if not rows:
        st.info("No runs to inspect yet.")
    else:
        idx = st.selectbox("Run", range(len(rows)), format_func=lambda i: run_label(rows[i]), key="rh_pick")
        selected = rows[idx]
        run_id = selected["run_id"]
        detail = state.api(c.get_run, ds["id"], run_id)
        checks, drift = detail.get("check_results", []), detail.get("drift_results", [])
        summ = detail.get("summary") or {}
        quality = summ.get("quality") or {}
        counts = insights.outcome_counts(checks)
        if detail.get("status") == "failed":
            ui.callout("danger", f"This run failed: {detail.get('error_message') or 'no error message stored'}")
        dur = seconds_between(detail.get("started_at"), detail.get("finished_at"))
        ui.kpi_cards([
            {"label": "Score", "value": ui.fmt_score(selected.get("score")), "color": insights.score_color(selected.get("score")),
             "sub": f"health: {selected.get('health') or '–'}"},
            {"label": "Checks", "value": sum(counts.values()), "sub": f"{counts['pass']} pass · {counts['warn']} warn · {counts['fail']} fail",
             "color": "#ef4444" if counts["fail"] else "#22c55e"},
            {"label": "Drifted features", "value": sum(1 for d in drift if d.get("drift_detected")), "sub": f"{len(drift)} evaluated",
             "color": "#f97316" if any(d.get("drift_detected") for d in drift) else "#22c55e"},
            {"label": "Incidents", "value": f"+{selected.get('incidents_created', 0)} / ~{selected.get('incidents_updated', 0)}", "sub": "created / updated",
             "color": "#eab308"},
            {"label": "Duration", "value": "–" if dur is None else f"{dur:.0f}s", "sub": f"{summ.get('rules_evaluated', '–')} rules evaluated", "color": "#38bdf8"},
        ])
        t_chk, t_drft, t_sum, t_rprt = st.tabs(["Check results", "Drift results", "Raw summary", "Download report"])
        with t_chk:
            if checks:
                o1, o2 = st.columns([2, 3])
                outs = o1.multiselect("Outcome", ["fail", "warn", "pass", "skipped"], default=["fail", "warn"], key="rh_chk_out")
                q = o2.text_input("Search rule or column", key="rh_chk_q").strip().lower()
                shown_checks = [r for r in checks if r["outcome"] in outs and q in (r["rule_name"] + " " + " ".join(r["columns"])).lower()]
                st.caption(f"{len(shown_checks)} of {len(checks)} checks shown")
                if shown_checks:
                    ui.show_df(pd.DataFrame([{k: r[k] for k in ("rule_name", "outcome", "severity", "columns", "affected_pct", "affected_count")}
                                             for r in shown_checks]))
                if quality.get("status"):
                    st.caption(f"Run health: {quality['status']} · pass rate {ui.fmt_pct(quality.get('pass_rate_pct'), 1)}")
            else:
                st.info("No check results stored for this run.")
        with t_drft:
            if drift:
                ui.show_df(pd.DataFrame([{"column": r["column"], "kind": r["kind"], "status": r["status"], "drift_detected": r["drift_detected"],
                                          "effect_size": r.get("effect_size")} for r in drift]))
            else:
                st.info("No drift results stored (the run had no reference version or drift was not run).")
        with t_sum:
            st.json(summ, expanded=False)
        with t_rprt:
            st.caption("Reports are generated on demand by the backend.")
            store = st.session_state.setdefault("rh_reports", {})
            if st.button("Prepare report files", key=f"rh_prep_{run_id}"):
                try:
                    store[run_id] = {fmt: c.get_report(run_id, fmt) for fmt in ("html", "json", "csv")}
                except ApiError as exc:
                    ui.show_error(exc)
            if run_id in store:
                d1, d2, d3 = st.columns(3)
                for col, (label, fmt, mime) in zip((d1, d2, d3), (("HTML", "html", "text/html"), ("JSON", "json", "application/json"),
                                                                  ("CSV", "csv", "text/csv"))):
                    col.download_button(f"Download {label}", store[run_id][fmt], f"report_{run_id[:8]}.{fmt}", mime, key=f"rh_dl_{fmt}_{run_id}",
                                        use_container_width=True)

# ── compare ────────────────────────────────────────────────────────────────────
with tab_compare:
    done_rows = [r for r in rows if r.get("status") == "completed"]
    if len(done_rows) < 2:
        st.info("Comparing needs at least two completed runs. Run monitoring again after a change to see what moved.")
    else:
        st.caption("Pick two runs to see exactly which rules got worse, which were fixed and which drift changed.")
        k1, k2 = st.columns(2)
        ia = k1.selectbox("Earlier run (A)", range(len(done_rows)), index=1, format_func=lambda i: run_label(done_rows[i]), key="rh_cmp_a")
        ib = k2.selectbox("Later run (B)", range(len(done_rows)), index=0, format_func=lambda i: run_label(done_rows[i]), key="rh_cmp_b")
        if ia == ib:
            ui.callout("warn", "Pick two different runs.")
        else:
            ra, rb = sorted((done_rows[ia], done_rows[ib]), key=lambda r: r["started_at"])
            diff = insights.run_diff(state.api(c.get_run, ds["id"], ra["run_id"]), state.api(c.get_run, ds["id"], rb["run_id"]))
            sd = diff["score_delta"]
            ui.kpi_cards([
                {"label": "Score change", "value": ui.fmt_score(diff["score_after"]), "delta": ui.delta_html(sd, good_when="up"),
                 "sub": f"was {ui.fmt_score(diff['score_before'])}", "color": "#22c55e" if (sd or 0) >= 0 else "#ef4444"},
                {"label": "New problems", "value": len(diff["new_failures"]), "color": "#ef4444" if diff["new_failures"] else "#22c55e"},
                {"label": "Worsened", "value": len(diff["worsened"]), "color": "#f97316" if diff["worsened"] else "#22c55e"},
                {"label": "Resolved", "value": len(diff["resolved"]), "color": "#22c55e"},
                {"label": "Drift", "value": f"+{len(diff['drift_new'])} / −{len(diff['drift_cleared'])}", "sub": "newly drifted / cleared",
                 "color": "#f97316" if diff["drift_new"] else "#22c55e"},
            ])
            g1, g2 = st.columns([2, 3])
            with g1:
                with ui.card("rh_cmp_chart"):
                    ui.card_title("What changed")
                    ui.show_chart(charts.run_compare_bars(diff), key="rh_cmp_bars")
            with g2:
                sections = (("New problems", "new_failures", "danger"), ("Worsened", "worsened", "warn"), ("Still failing", "unchanged_failing", "info"),
                            ("Improved", "improved", "info"), ("Resolved", "resolved", "success"))
                for title, key, _ in sections:
                    items = diff[key]
                    with st.expander(f"{title} ({len(items)})", expanded=key in ("new_failures", "worsened") and bool(items)):
                        if not items:
                            st.caption("None.")
                        else:
                            ui.show_df(pd.DataFrame([{"rule": e["rule"], "type": e["type"], "severity": e["severity"], "before": e["before"],
                                                      "after": e["after"], "affected % before": e["before_pct"], "affected % after": e["after_pct"],
                                                      "Δ pp": e["delta_pct"]} for e in items]))
            if diff["drift_new"] or diff["drift_cleared"]:
                st.markdown("**Drift changes** — newly drifted: " + (", ".join(f"`{x}`" for x in diff["drift_new"]) or "none")
                            + " · cleared: " + (", ".join(f"`{x}`" for x in diff["drift_cleared"]) or "none"))

# ── new run ────────────────────────────────────────────────────────────────────
with tab_new:
    st.caption("Runs rules, schema comparison, drift, root cause and incident tracking for one version against a reference.")
    cur, ref = state.version_selectors(ds, "rh_new")
    schema_cols = state.api(c.schema, ds["id"], cur)["columns"]
    exclude = st.multiselect("Exclude from drift", [x["name"] for x in schema_cols],
                             default=[x["name"] for x in schema_cols if x["inferred_type"] == "datetime"], key="rh_excl",
                             help="Date columns differ by construction between periods, so they are excluded by default.")
    body = {"current_version": cur, "reference_version": ref, "exclude_drift_columns": exclude}
    col_sync, col_async = st.columns(2)
    if col_async.button("Submit as background job (recommended)", type="primary", key="rh_submit_job", use_container_width=True,
                        help="Returns immediately; the status updates live at the top of this page."):
        try:
            job = c.submit_monitor_job(ds["id"], body)
            st.session_state["rh_job"] = job["job_id"]
            ui.toast("Job submitted", "🚀")
            st.rerun()
        except ApiError as exc:
            ui.show_error(exc)
    if col_sync.button("Run now (synchronous)", key="rh_run_sync", use_container_width=True, help="Waits in the browser. Use for small datasets."):
        with st.spinner("Running — this may take a moment…"):
            try:
                run = c.monitor(ds["id"], body)
            except ApiError as exc:
                ui.show_error(exc)
            else:
                st.cache_data.clear()
                st.session_state.pop("_fleet_snap", None)
                ui.toast(f"Run {run['status']} · {run['summary'].get('incidents', {}).get('created', 0)} incident(s) created", "✅")
                st.rerun()

    with st.expander("Recent background jobs for this dataset", expanded=False):
        try:
            jobs = c.list_jobs(ds["id"], limit=10)
        except ApiError as exc:
            ui.show_error(exc)
            jobs = []
        if jobs:
            ui.show_df(pd.DataFrame([{
                "job": j["job_id"][:12], "type": j["job_type"], "status": j["status"], "queued": (j.get("queued_at") or "")[:16],
                "finished": (j.get("finished_at") or "")[:16], "error": (j.get("error_message") or "")[:60]} for j in jobs]))
        else:
            st.caption("No jobs yet.")
