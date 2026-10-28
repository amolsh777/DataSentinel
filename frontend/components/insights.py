"""Pure-Python analytics helpers for the frontend (no Streamlit / Plotly imports, so they are unit-testable).

Everything here works on the *real* payloads returned by the API: run-history rows, run detail
(`check_results`, `drift_results`) and incidents. Nothing is simulated or randomly filled.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY_ORDER)}


# --------------------------------------------------------------------------- time
def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-ish timestamp from the API ('2026-10-27T10:15:00', '2026-10-27 10:15:00', with or without tz)."""
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text[:32])
    except ValueError:
        try:
            dt = datetime.fromisoformat(text[:19])
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def time_ago(value: Any, now: datetime | None = None) -> str:
    dt = parse_ts(value)
    if dt is None:
        return "never"
    now = now or datetime.now(timezone.utc)
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    if secs < 86400 * 30:
        return f"{secs // 86400} d ago"
    return dt.strftime("%d %b %Y")


def age_days(value: Any, now: datetime | None = None) -> float | None:
    dt = parse_ts(value)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400)


# ------------------------------------------------------------------------- scores
def score_band(score: float | None) -> str:
    """healthy / degraded / failing / unknown from a 0-100 score (same cut-offs as the charts)."""
    if score is None:
        return "unknown"
    if score >= 90:
        return "healthy"
    if score >= 70:
        return "degraded"
    return "failing"


def score_color(score: float | None) -> str:
    return {"healthy": "#22c55e", "degraded": "#eab308", "failing": "#ef4444"}.get(score_band(score), "#64748b")


def compliance(scores: Iterable[float | None], target: float) -> dict[str, Any]:
    """Share of scored runs that met `target`, plus the current and best streaks (scores oldest -> newest)."""
    vals = [s for s in scores if s is not None]
    if not vals:
        return {"runs": 0, "met": 0, "pct": None, "current_streak": 0, "best_streak": 0}
    met = [s >= target for s in vals]
    best = cur = 0
    for ok in met:
        cur = cur + 1 if ok else 0
        best = max(best, cur)
    streak = 0
    for ok in reversed(met):
        if not ok:
            break
        streak += 1
    return {"runs": len(vals), "met": sum(met), "pct": round(100 * sum(met) / len(vals), 1),
            "current_streak": streak, "best_streak": best}


def detect_anomalies(scores: list[float | None], threshold: float = 3.5) -> list[int]:
    """Indices whose score is an outlier by the modified z-score (median / MAD). Needs >= 5 values.

    Robust to the outliers themselves, so one bad run does not hide itself by inflating the spread.
    """
    idx = [i for i, s in enumerate(scores) if s is not None]
    vals = [scores[i] for i in idx]
    if len(vals) < 5:
        return []
    med = statistics.median(vals)
    mad = statistics.median(abs(v - med) for v in vals)
    if mad == 0:
        # Flat history: any deviation of more than 5 points is notable.
        return [i for i, v in zip(idx, vals) if abs(v - med) > 5]
    return [i for i, v in zip(idx, vals) if abs(0.6745 * (v - med) / mad) > threshold]


def delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


# --------------------------------------------------------------------- run series
def runs_oldest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The API returns newest first; charts want oldest first. Stable for equal timestamps."""
    return sorted(rows, key=lambda r: str(r.get("started_at") or ""))


def filter_days(rows: list[dict[str, Any]], days: int | None, now: datetime | None = None) -> list[dict[str, Any]]:
    if not days:
        return list(rows)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    out = []
    for r in rows:
        dt = parse_ts(r.get("started_at"))
        if dt is None or dt >= cutoff:
            out.append(r)
    return out


def daily_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate real runs per calendar day: run count, mean score, total failures/warnings/drifted features.

    Days without runs are *absent* (never invented).
    """
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        day = str(r.get("started_at") or "")[:10]
        if day:
            buckets[day].append(r)
    out = []
    for day in sorted(buckets):
        rs = buckets[day]
        scores = [r["score"] for r in rs if r.get("score") is not None]
        out.append({
            "date": day, "runs": len(rs),
            "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
            "failures": sum(int(r.get("failures") or 0) for r in rs),
            "warnings": sum(int(r.get("warnings") or 0) for r in rs),
            "drifted": sum(int(r.get("drifted_features") or 0) for r in rs),
            "incidents_created": sum(int(r.get("incidents_created") or 0) for r in rs),
        })
    return out


# ------------------------------------------------------------ run details / checks
def issue_breakdown(check_results: list[dict[str, Any]], outcomes: tuple[str, ...] = ("fail", "warn")) -> dict[str, dict[str, Any]]:
    """Group failing/warning checks by rule type: number of checks and the largest affected share / row count."""
    out: dict[str, dict[str, Any]] = {}
    for r in check_results:
        if r.get("outcome") not in outcomes:
            continue
        slot = out.setdefault(r.get("rule_type") or "unknown",
                              {"checks": 0, "affected_rows": 0, "max_affected_pct": 0.0, "rules": []})
        slot["checks"] += 1
        slot["affected_rows"] += int(r.get("affected_count") or 0)
        slot["max_affected_pct"] = max(slot["max_affected_pct"], float(r.get("affected_pct") or 0))
        slot["rules"].append(r.get("rule_name"))
    return dict(sorted(out.items(), key=lambda kv: (-kv[1]["affected_rows"], -kv[1]["checks"], kv[0])))


def outcome_counts(check_results: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "skipped": 0}
    for r in check_results:
        counts[r.get("outcome")] = counts.get(r.get("outcome"), 0) + 1
    return counts


def rule_failure_table(runs_with_checks: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """For each rule: in how many of the given runs it failed/warned, and its latest affected %.

    `runs_with_checks` is a list of (run_history_row, check_results) ordered oldest -> newest.
    """
    stats: dict[str, dict[str, Any]] = {}
    for _, checks in runs_with_checks:
        for r in checks:
            name = r.get("rule_name") or "unnamed"
            s = stats.setdefault(name, {"rule": name, "type": r.get("rule_type"), "severity": r.get("severity"),
                                        "runs_evaluated": 0, "runs_failed": 0, "runs_warned": 0, "latest_pct": None,
                                        "latest_outcome": None, "columns": ", ".join(r.get("columns") or [])})
            s["runs_evaluated"] += 1
            if r.get("outcome") == "fail":
                s["runs_failed"] += 1
            elif r.get("outcome") == "warn":
                s["runs_warned"] += 1
            s["latest_pct"] = r.get("affected_pct")
            s["latest_outcome"] = r.get("outcome")
            s["severity"] = r.get("severity")
    rows = [s for s in stats.values() if s["runs_failed"] or s["runs_warned"]]
    rows.sort(key=lambda s: (-s["runs_failed"], -s["runs_warned"], SEVERITY_RANK.get(s["severity"], 9), s["rule"]))
    return rows


def heatmap_matrix(runs_with_checks: list[tuple[dict[str, Any], list[dict[str, Any]]]]) -> tuple[list[str], list[str], list[list[float | None]]]:
    """Rule-type x run matrix of the worst affected % (None when the type did not run). Returns (x_labels, y_labels, z)."""
    types = sorted({r.get("rule_type") or "unknown" for _, checks in runs_with_checks for r in checks})
    xs = [str(run.get("started_at") or "")[5:16].replace("T", " ") for run, _ in runs_with_checks]
    z: list[list[float | None]] = []
    for t in types:
        row: list[float | None] = []
        for _, checks in runs_with_checks:
            vals = [float(r.get("affected_pct") or 0) for r in checks if (r.get("rule_type") or "unknown") == t
                    and r.get("outcome") in ("pass", "warn", "fail")]
            row.append(max(vals) if vals else None)
        z.append(row)
    return xs, types, z


# ----------------------------------------------------------------- run comparison
_OUTCOME_RANK = {"pass": 0, "skipped": 0, "warn": 1, "fail": 2}


def run_diff(run_a: dict[str, Any], run_b: dict[str, Any]) -> dict[str, Any]:
    """Compare two run details (A = earlier, B = later) by rule name and drifted column.

    Returns lists: new_failures, resolved, worsened, improved, unchanged_failing; drift_new, drift_cleared.
    """
    def checks(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {r["rule_name"]: r for r in run.get("check_results", [])}

    a, b = checks(run_a), checks(run_b)
    new_failures, resolved, worsened, improved, still = [], [], [], [], []
    for name in sorted(set(a) | set(b)):
        ra, rb = a.get(name), b.get(name)
        oa = _OUTCOME_RANK.get((ra or {}).get("outcome"), 0)
        ob = _OUTCOME_RANK.get((rb or {}).get("outcome"), 0)
        pa = float((ra or {}).get("affected_pct") or 0)
        pb = float((rb or {}).get("affected_pct") or 0)
        entry = {"rule": name, "type": (rb or ra or {}).get("rule_type"), "severity": (rb or ra or {}).get("severity"),
                 "before": (ra or {}).get("outcome", "n/a"), "after": (rb or {}).get("outcome", "n/a"),
                 "before_pct": pa if ra else None, "after_pct": pb if rb else None, "delta_pct": round(pb - pa, 3)}
        if ob > oa and ob >= 1:
            new_failures.append(entry)
        elif oa >= 1 and ob == 0:
            resolved.append(entry)
        elif oa == ob and ob >= 1:
            (worsened if pb > pa + 1e-9 else improved if pb < pa - 1e-9 else still).append(entry)
        elif ob < oa:
            improved.append(entry)
    drift_a = {d["column"] for d in run_a.get("drift_results", []) if d.get("drift_detected")}
    drift_b = {d["column"] for d in run_b.get("drift_results", []) if d.get("drift_detected")}
    sa = (run_a.get("summary") or {}).get("score", {}).get("overall")
    sb = (run_b.get("summary") or {}).get("score", {}).get("overall")
    return {"score_before": sa, "score_after": sb, "score_delta": delta(sb, sa),
            "new_failures": new_failures, "resolved": resolved, "worsened": worsened, "improved": improved,
            "unchanged_failing": still, "drift_new": sorted(drift_b - drift_a), "drift_cleared": sorted(drift_a - drift_b)}


# ------------------------------------------------------------------------ incidents
def incident_priority(inc: dict[str, Any]) -> tuple:
    """Sort key: severity, then heuristic score (desc), then recurrence (desc)."""
    return (SEVERITY_RANK.get(inc.get("severity"), 9), -(inc.get("severity_score") or 0), -(inc.get("recurrence_count") or 0))


def incident_stats(incidents: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    open_like = [i for i in incidents if i.get("status") in ("open", "acknowledged")]
    ages = [a for a in (age_days(i.get("first_seen_at"), now) for i in open_like) if a is not None]
    by_sev = {s: sum(1 for i in incidents if i.get("severity") == s) for s in SEVERITY_ORDER}
    by_type: dict[str, int] = defaultdict(int)
    for i in incidents:
        by_type[i.get("incident_type") or "unknown"] += 1
    resolved = [i for i in incidents if i.get("status") == "resolved" and i.get("resolved_at") and i.get("first_seen_at")]
    ttr = []
    for i in resolved:
        a, b = parse_ts(i["first_seen_at"]), parse_ts(i["resolved_at"])
        if a and b and b >= a:
            ttr.append((b - a).total_seconds() / 86400)
    return {"open": len(open_like), "oldest_days": max(ages) if ages else None,
            "avg_age_days": sum(ages) / len(ages) if ages else None, "by_severity": by_sev, "by_type": dict(by_type),
            "mean_days_to_resolve": sum(ttr) / len(ttr) if ttr else None,
            "recurring": sum(1 for i in incidents if (i.get("recurrence_count") or 0) > 0)}


def allowed_transition(current: str, new: str) -> bool:
    return new in {"open": ["acknowledged", "resolved", "ignored"], "acknowledged": ["resolved", "open", "ignored"],
                   "resolved": ["open"], "ignored": ["open"]}.get(current, [])


# --------------------------------------------------------------------------- fleet
def fleet_row(dataset: dict[str, Any], runs: list[dict[str, Any]], incidents: list[dict[str, Any]]) -> dict[str, Any]:
    """One dataset's headline status for the Command Center. `runs` is newest-first (API order)."""
    ordered = runs_oldest_first(runs)
    latest = ordered[-1] if ordered else None
    prev = ordered[-2] if len(ordered) > 1 else None
    scores = [r.get("score") for r in ordered]
    open_inc = [i for i in incidents if i.get("status") in ("open", "acknowledged")]
    sev = {s: sum(1 for i in open_inc if i.get("severity") == s) for s in SEVERITY_ORDER}
    health = (latest or {}).get("health") or score_band((latest or {}).get("score"))
    if latest and latest.get("status") == "failed":
        health = "failing"
    if not latest:
        health = "unknown"
    return {
        "id": dataset["id"], "name": dataset["name"], "project": dataset.get("project"),
        "synthetic": bool(dataset.get("is_synthetic")), "versions": dataset.get("version_count"),
        "runs": len(ordered), "last_run": (latest or {}).get("started_at"), "run_status": (latest or {}).get("status"),
        "score": (latest or {}).get("score"), "score_delta": delta((latest or {}).get("score"), (prev or {}).get("score")),
        "health": health, "spark": [s for s in scores if s is not None][-20:],
        "failures": (latest or {}).get("failures", 0), "drifted": (latest or {}).get("drifted_features", 0),
        "breaking": (latest or {}).get("breaking_changes", 0),
        "open_incidents": len(open_inc), "critical_high": sev["critical"] + sev["high"], "by_severity": sev,
    }


def attention_score(row: dict[str, Any]) -> float:
    """Higher = look at it sooner. Combines health, open incidents, failures and staleness."""
    base = {"failing": 100, "degraded": 50, "unknown": 30, "healthy": 0}.get(row.get("health"), 20)
    sev = row.get("by_severity") or {}
    base += 15 * sev.get("critical", 0) + 8 * sev.get("high", 0) + 3 * sev.get("medium", 0)
    base += min(row.get("failures") or 0, 20) + 2 * min(row.get("drifted") or 0, 10) + 5 * min(row.get("breaking") or 0, 5)
    return float(base)


def fleet_kpis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r["score"] for r in rows if r.get("score") is not None]
    return {
        "datasets": len(rows), "monitored": sum(1 for r in rows if r["runs"]),
        "avg_score": round(sum(scored) / len(scored), 1) if scored else None,
        "failing": sum(1 for r in rows if r["health"] == "failing"),
        "degraded": sum(1 for r in rows if r["health"] == "degraded"),
        "healthy": sum(1 for r in rows if r["health"] == "healthy"),
        "unmonitored": sum(1 for r in rows if not r["runs"]),
        "open_incidents": sum(r["open_incidents"] for r in rows), "critical_high": sum(r["critical_high"] for r in rows),
    }


# --------------------------------------------------------------------- rules import
def rules_export(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Portable subset of rule fields (no ids / dataset ids), suitable for re-import into any dataset."""
    keep = ("name", "rule_type", "column", "severity", "enabled", "params", "description")
    return [{k: r.get(k) for k in keep if k in r} for r in rules]


def rules_import_plan(payload: Any, existing_names: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate an imported rules document. Returns (rules_to_create, problems). Same-name rules are skipped."""
    problems: list[str] = []
    if isinstance(payload, dict):
        payload = payload.get("rules", [])
    if not isinstance(payload, list):
        return [], ["Expected a JSON list of rules (or an object with a 'rules' list)."]
    have = set(existing_names)
    out = []
    for n, r in enumerate(payload, 1):
        if not isinstance(r, dict) or not r.get("rule_type"):
            problems.append(f"Entry {n}: missing 'rule_type'.")
            continue
        if r.get("name") in have:
            problems.append(f"Entry {n}: '{r.get('name')}' already exists, skipped.")
            continue
        body = {k: r[k] for k in ("name", "rule_type", "column", "severity", "params", "description") if r.get(k) is not None}
        body.setdefault("params", {})
        out.append(body)
        if r.get("name"):
            have.add(r["name"])
    return out, problems


# ------------------------------------------------------------------ v9 additions
def staleness(last_run: Any, max_hours: float, now: datetime | None = None) -> dict[str, Any]:
    """Is the newest monitoring run older than `max_hours`? Never-monitored datasets are stale with hours=None."""
    dt = parse_ts(last_run)
    if dt is None:
        return {"stale": True, "hours": None}
    hours = max(0.0, ((now or datetime.now(timezone.utc)) - dt).total_seconds() / 3600)
    return {"stale": hours > max_hours, "hours": round(hours, 1)}


def aging_buckets(incidents: list[dict[str, Any]], now: datetime | None = None) -> dict[str, int]:
    """Open/acknowledged incidents by age since first seen."""
    out = {"< 1 day": 0, "1–7 days": 0, "7–30 days": 0, "> 30 days": 0}
    for i in incidents:
        if i.get("status") not in ("open", "acknowledged"):
            continue
        a = age_days(i.get("first_seen_at"), now)
        if a is None:
            continue
        out["< 1 day" if a < 1 else "1–7 days" if a < 7 else "7–30 days" if a < 30 else "> 30 days"] += 1
    return out


def column_hotspots(check_results: list[dict[str, Any]], incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Columns ranked by how many failing/warning checks and open incidents mention them."""
    stats: dict[str, dict[str, Any]] = {}
    for r in check_results:
        if r.get("outcome") not in ("fail", "warn"):
            continue
        for col in r.get("columns") or []:
            s = stats.setdefault(col, {"column": col, "checks": 0, "incidents": 0, "max_affected_pct": 0.0, "worst": "low"})
            s["checks"] += 1
            s["max_affected_pct"] = max(s["max_affected_pct"], float(r.get("affected_pct") or 0))
    for i in incidents:
        if i.get("status") not in ("open", "acknowledged"):
            continue
        for col in i.get("columns") or []:
            s = stats.setdefault(col, {"column": col, "checks": 0, "incidents": 0, "max_affected_pct": 0.0, "worst": "low"})
            s["incidents"] += 1
            sev = i.get("severity") or "low"
            if SEVERITY_RANK.get(sev, 9) < SEVERITY_RANK.get(s["worst"], 9):
                s["worst"] = sev
    return sorted(stats.values(), key=lambda s: (-(s["checks"] + 2 * s["incidents"]), -s["max_affected_pct"], s["column"]))


def fleet_report_md(rows: list[dict[str, Any]], kpis: dict[str, Any], incidents: list[dict[str, Any]],
                    names: dict[str, str], generated_at: str) -> str:
    """Shareable Markdown status report for the whole fleet."""
    lines = ["# DataSentinel fleet report", f"_Generated {generated_at}_", "",
             f"- Datasets: **{kpis['datasets']}** ({kpis['healthy']} healthy, {kpis['degraded']} degraded, {kpis['failing']} failing, "
             f"{kpis['unmonitored']} not monitored)",
             f"- Average score: **{'–' if kpis['avg_score'] is None else kpis['avg_score']}**",
             f"- Open incidents: **{kpis['open_incidents']}** ({kpis['critical_high']} critical/high)", "",
             "## Datasets (most urgent first)", "", "| Dataset | Health | Score | Open incidents | Failed checks | Drifted | Last run |",
             "|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=attention_score, reverse=True):
        score_txt = "–" if r["score"] is None else f"{r['score']:.1f}"
        lines.append(f"| {r['name']} | {r['health']} | {score_txt} | {r['open_incidents']} | "
                     f"{r['failures']} | {r['drifted']} | {time_ago(r['last_run'])} |")
    top = sorted([i for i in incidents if i.get("status") in ("open", "acknowledged")], key=incident_priority)[:10]
    lines += ["", "## Top open incidents", ""]
    lines += [f"- **[{i['severity'].upper()}]** {names.get(i.get('dataset_id'), '?')} — {i['title']}" for i in top] or ["None."]
    return "\n".join(lines) + "\n"
