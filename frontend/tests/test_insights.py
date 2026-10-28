"""Unit tests for the pure analytics helpers (no Streamlit, no backend needed)."""

from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from components import insights as ins  # noqa: E402

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


def run(ts, score=95, failures=0, warnings=0, drifted=0, status="completed", health="healthy"):
    return {"run_id": ts, "started_at": ts, "score": score, "failures": failures, "warnings": warnings,
            "drifted_features": drifted, "status": status, "health": health, "incidents_created": 0}


def chk(name, outcome, pct=0.0, rtype="missing_values", sev="high", count=0, cols=None):
    return {"rule_name": name, "rule_type": rtype, "outcome": outcome, "severity": sev, "affected_pct": pct,
            "affected_count": count, "columns": cols or []}


# ---- time -----------------------------------------------------------------
def test_parse_and_time_ago():
    assert ins.parse_ts("2026-10-27T11:30:00").hour == 11
    assert ins.parse_ts("2026-10-27 11:30:00Z").tzinfo is not None
    assert ins.parse_ts("garbage") is None and ins.parse_ts(None) is None
    assert ins.time_ago("2026-10-27T11:30:00", NOW) == "30 min ago"
    assert ins.time_ago("2026-10-27T09:00:00", NOW) == "3 h ago"
    assert ins.time_ago("2026-10-24T12:00:00", NOW) == "3 d ago"
    assert ins.time_ago(None) == "never"
    assert abs(ins.age_days("2026-10-25T12:00:00", NOW) - 2) < 1e-6


# ---- scores ---------------------------------------------------------------
def test_score_band_and_compliance():
    assert ins.score_band(95) == "healthy" and ins.score_band(75) == "degraded"
    assert ins.score_band(10) == "failing" and ins.score_band(None) == "unknown"
    c = ins.compliance([80, 92, 95, 60, 91, 93, None], 90)
    assert c == {"runs": 6, "met": 4, "pct": 66.7, "current_streak": 2, "best_streak": 2}
    assert ins.compliance([], 90)["pct"] is None


def test_detect_anomalies_is_robust_and_needs_history():
    assert ins.detect_anomalies([90, 91, 92]) == []
    scores = [92, 93, 91, 92, 94, 30, 92, 93]
    assert ins.detect_anomalies(scores) == [5]
    assert ins.detect_anomalies([90, 90, 90, 90, 90, 70]) == [5]  # flat history: MAD == 0
    assert ins.detect_anomalies([90, 90, 90, 90, 90, 92]) == []


# ---- series ---------------------------------------------------------------
def test_daily_series_never_invents_days():
    rows = [run("2026-10-20T08:00:00", 90, 2), run("2026-10-20T18:00:00", 80, 4), run("2026-10-22T09:00:00", 99)]
    out = ins.daily_series(rows)
    assert [d["date"] for d in out] == ["2026-10-20", "2026-10-22"]  # 21st absent, not filled
    assert out[0]["runs"] == 2 and out[0]["failures"] == 6 and out[0]["avg_score"] == 85


def test_filter_days_and_ordering():
    rows = [run("2026-10-27T10:00:00"), run("2026-09-01T10:00:00"), run("2026-10-20T10:00:00")]
    assert len(ins.filter_days(rows, 14, NOW)) == 2
    assert len(ins.filter_days(rows, None, NOW)) == 3
    assert [r["started_at"][:10] for r in ins.runs_oldest_first(rows)] == ["2026-09-01", "2026-10-20", "2026-10-27"]


# ---- checks ---------------------------------------------------------------
def test_issue_breakdown_groups_and_sorts():
    checks = [chk("a", "fail", 4.0, "missing_values", count=40), chk("b", "warn", 1.0, "missing_values", count=10),
              chk("c", "fail", 9.0, "unique", count=90), chk("d", "pass", 0, "unique")]
    out = ins.issue_breakdown(checks)
    assert list(out) == ["unique", "missing_values"]
    assert out["missing_values"]["checks"] == 2 and out["missing_values"]["affected_rows"] == 50
    assert out["missing_values"]["max_affected_pct"] == 4.0
    assert ins.outcome_counts(checks) == {"pass": 1, "warn": 1, "fail": 2, "skipped": 0}


def test_rule_failure_table_and_heatmap():
    r1 = (run("2026-10-01T00:00:00"), [chk("x", "fail", 5), chk("y", "pass")])
    r2 = (run("2026-10-02T00:00:00"), [chk("x", "fail", 7), chk("y", "warn", 1, sev="low")])
    table = ins.rule_failure_table([r1, r2])
    assert [t["rule"] for t in table] == ["x", "y"]
    assert table[0]["runs_failed"] == 2 and table[0]["latest_pct"] == 7 and table[1]["runs_warned"] == 1
    xs, ys, z = ins.heatmap_matrix([r1, r2])
    assert ys == ["missing_values"] and z == [[5.0, 7.0]] and len(xs) == 2


# ---- run diff -------------------------------------------------------------
def test_run_diff_classifies_changes():
    a = {"summary": {"score": {"overall": 90}}, "drift_results": [{"column": "price", "drift_detected": True}],
         "check_results": [chk("fixed", "fail", 5), chk("worse", "fail", 2), chk("better", "fail", 8),
                           chk("same", "fail", 3), chk("regress", "pass"), chk("gone", "fail", 1)]}
    b = {"summary": {"score": {"overall": 85.5}}, "drift_results": [{"column": "qty", "drift_detected": True}],
         "check_results": [chk("fixed", "pass"), chk("worse", "fail", 6), chk("better", "fail", 4),
                           chk("same", "fail", 3), chk("regress", "fail", 12), chk("new", "warn", 2)]}
    d = ins.run_diff(a, b)
    assert d["score_delta"] == -4.5
    assert {e["rule"] for e in d["new_failures"]} == {"regress", "new"}
    assert {e["rule"] for e in d["resolved"]} == {"fixed", "gone"}  # 'gone' no longer evaluated => no longer failing
    assert [e["rule"] for e in d["worsened"]] == ["worse"]
    assert [e["rule"] for e in d["improved"]] == ["better"]
    assert [e["rule"] for e in d["unchanged_failing"]] == ["same"]
    assert d["drift_new"] == ["qty"] and d["drift_cleared"] == ["price"]


def test_run_diff_rule_missing_in_later_run_counts_as_resolved():
    a = {"check_results": [chk("gone", "fail", 1)]}
    b = {"check_results": []}
    assert [e["rule"] for e in ins.run_diff(a, b)["resolved"]] == ["gone"]


# ---- incidents ------------------------------------------------------------
def test_incident_priority_and_stats():
    incs = [
        {"severity": "medium", "severity_score": 50, "status": "open", "first_seen_at": "2026-10-25T12:00:00",
         "incident_type": "quality"},
        {"severity": "critical", "severity_score": 70, "status": "open", "first_seen_at": "2026-10-20T12:00:00",
         "incident_type": "drift", "recurrence_count": 2},
        {"severity": "critical", "severity_score": 90, "status": "resolved", "first_seen_at": "2026-10-01T00:00:00",
         "resolved_at": "2026-10-03T00:00:00", "incident_type": "schema"},
    ]
    ordered = sorted(incs, key=ins.incident_priority)
    assert [i["severity_score"] for i in ordered] == [90, 70, 50]
    st = ins.incident_stats(incs, NOW)
    assert st["open"] == 2 and round(st["oldest_days"]) == 7 and st["by_severity"]["critical"] == 2
    assert round(st["mean_days_to_resolve"]) == 2 and st["recurring"] == 1
    assert ins.allowed_transition("open", "resolved") and not ins.allowed_transition("resolved", "ignored")


# ---- fleet ----------------------------------------------------------------
def test_fleet_row_attention_and_kpis():
    ds_a = {"id": "a", "name": "A", "version_count": 2}
    ds_b = {"id": "b", "name": "B", "version_count": 1}
    runs_a = [run("2026-10-27T10:00:00", 60, 5, health="failing"), run("2026-10-26T10:00:00", 90)]  # newest first
    incs_a = [{"status": "open", "severity": "critical"}, {"status": "resolved", "severity": "high"}]
    a = ins.fleet_row(ds_a, runs_a, incs_a)
    b = ins.fleet_row(ds_b, [], [])
    assert a["score"] == 60 and a["score_delta"] == -30 and a["health"] == "failing" and a["critical_high"] == 1
    assert b["health"] == "unknown" and b["runs"] == 0
    assert ins.attention_score(a) > ins.attention_score(b) > 0
    k = ins.fleet_kpis([a, b])
    assert k["failing"] == 1 and k["unmonitored"] == 1 and k["avg_score"] == 60 and k["critical_high"] == 1
    failed = ins.fleet_row(ds_a, [run("2026-10-27T10:00:00", None, status="failed", health=None)], [])
    assert failed["health"] == "failing"


# ---- rules import / export -----------------------------------------------
def test_rules_export_and_import_plan():
    exported = ins.rules_export([{"id": "1", "dataset_id": "d", "name": "n", "rule_type": "unique", "column": "id",
                                  "severity": "high", "enabled": True, "params": {}}])
    assert "id" not in exported[0] and exported[0]["rule_type"] == "unique"
    plan, problems = ins.rules_import_plan(exported + [{"name": "n", "rule_type": "unique"}, {"nope": 1}], [])
    assert len(plan) == 1 and len(problems) == 2  # duplicate name + missing rule_type
    plan2, problems2 = ins.rules_import_plan({"rules": exported}, ["n"])
    assert plan2 == [] and "already exists" in problems2[0]
    assert ins.rules_import_plan("oops", [])[0] == []


# ---- v9 -------------------------------------------------------------------
def test_staleness_and_aging():
    assert ins.staleness("2026-10-27T11:00:00", 48, NOW) == {"stale": False, "hours": 1.0}
    assert ins.staleness("2026-10-20T11:00:00", 48, NOW)["stale"] is True
    assert ins.staleness(None, 48, NOW) == {"stale": True, "hours": None}
    incs = [{"status": "open", "first_seen_at": "2026-10-27T06:00:00"}, {"status": "acknowledged", "first_seen_at": "2026-10-24T00:00:00"},
            {"status": "open", "first_seen_at": "2026-09-01T00:00:00"}, {"status": "resolved", "first_seen_at": "2026-10-27T06:00:00"}]
    assert ins.aging_buckets(incs, NOW) == {"< 1 day": 1, "1–7 days": 1, "7–30 days": 0, "> 30 days": 1}


def test_column_hotspots_rank_by_checks_and_incidents():
    checks = [chk("a", "fail", 5, cols=["price"]), chk("b", "warn", 1, cols=["price", "qty"]), chk("c", "pass", cols=["id"])]
    incs = [{"status": "open", "columns": ["qty"], "severity": "critical"}, {"status": "resolved", "columns": ["id"], "severity": "high"}]
    out = ins.column_hotspots(checks, incs)
    assert [h["column"] for h in out] == ["qty", "price"]  # qty: 1 check + 1 incident(x2) = 3 > price 2
    assert out[0]["worst"] == "critical" and out[1]["max_affected_pct"] == 5


def test_fleet_report_markdown():
    a = ins.fleet_row({"id": "a", "name": "A", "version_count": 1}, [run("2026-10-27T10:00:00", 60, 5, health="failing")],
                      [{"status": "open", "severity": "critical", "title": "Boom", "dataset_id": "a"}])
    md = ins.fleet_report_md([a], ins.fleet_kpis([a]), [{"status": "open", "severity": "critical", "title": "Boom", "dataset_id": "a"}],
                             {"a": "A"}, "now")
    assert "| A | failing | 60.0 |" in md and "**[CRITICAL]** A — Boom" in md
