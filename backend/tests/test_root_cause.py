import pandas as pd
import pytest

from app.analytics.data_drift import detect_drift
from app.analytics.profiling import profile_dataframe
from app.analytics.quality_checks import run_quality_checks, suggest_rules_from_profile, validate_rule
from app.analytics.root_cause import LIKELIHOOD_ORDER, analyze
from app.analytics.schema_drift import compare_schemas

NOW = pd.Timestamp("2026-10-17")


@pytest.fixture
def demo(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    v2 = pd.read_csv(sample_dir / "retail_transactions_v2.csv")
    cust = pd.read_csv(sample_dir / "customers_clean.csv")
    p1 = profile_dataframe(v1)
    p2 = profile_dataframe(v2)
    rules = suggest_rules_from_profile(p1["columns"], p1["dataset"], now=NOW) + [
        validate_rule({"name": "Revenue formula", "rule_type": "business_rule", "severity": "high",
                       "params": {"expression": "abs(revenue - quantity * unit_price * (1 - discount_pct / 100)) <= 0.01"}}),
        validate_rule({"name": "Customer exists", "rule_type": "referential_integrity",
                       "column": "customer_id", "severity": "high",
                       "params": {"reference_dataset": "c", "reference_column": "customer_id"}}),
    ]
    qres = run_quality_checks(v2, rules, {"baseline_columns": p1["columns"], "now": NOW,
                                          "reference_frames": {"c": cust}})
    sdiff = compare_schemas(p1["columns"], p2["columns"])
    drift = detect_drift(v1, v2, reference_profiles=p1["columns"],
                        current_profiles=p2["columns"], exclude_columns=["transaction_date"])
    return {"v1": v1, "v2": v2, "p1": p1, "p2": p2,
            "quality": qres, "schema": sdiff, "drift": drift}


def _by_column(report, column):
    return [f for f in report["findings"] if column in f["columns"]]


def _by_type(report, issue_type):
    return [f for f in report["findings"] if f["issue_type"] == issue_type]


# ---------------------------------------------------------------- shape rules


def test_report_has_summary_and_disclaimer(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    assert "findings" in r and "summary" in r and "disclaimer" in r
    assert r["summary"]["total_findings"] >= 5
    assert all("disclaimer" in f for f in r["findings"])


def test_every_hypothesis_has_supporting_evidence_or_is_absent(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    for f in r["findings"]:
        for h in f["hypotheses"]:
            assert h["supporting_evidence"], f"empty support in {f['id']}: {h['cause']}"
            assert h["likelihood"] in LIKELIHOOD_ORDER


def test_hypotheses_ordered_by_likelihood(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    for f in r["findings"]:
        ordered = [LIKELIHOOD_ORDER.index(h["likelihood"]) for h in f["hypotheses"]]
        assert ordered == sorted(ordered, reverse=True)


def test_every_finding_has_actions_and_steps(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    for f in r["findings"]:
        assert len(f["investigation_steps"]) >= 2
        assert len(f["corrective_actions"]) >= 2


def test_findings_sorted_by_severity(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    sev_order = ["critical", "high", "medium", "low", "info"]
    severities = [f["severity"] for f in r["findings"]]
    assert severities == sorted(severities, key=sev_order.index)


# ------------------------------------------------------------ domain-specific


def test_missing_values_spike_hypothesises_upstream_source_change(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    f = next(f for f in _by_column(r, "customer_age")
             if f["issue_type"] == "quality" and "missing" in f["issue_name"].lower())
    causes = " | ".join(h["cause"].lower() for h in f["hypotheses"])
    assert "upstream" in causes or "pipeline" in causes
    assert f["evidence"]["baseline"]["missing_pct"] is not None
    assert f["evidence"]["delta_pp"] > 10


def test_type_change_hypothesises_null_sentinel(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    f = next(f for f in _by_column(r, "quantity")
             if f["issue_type"] == "schema" and "type" in f["issue_name"].lower())
    joined = " | ".join(h["cause"].lower() for h in f["hypotheses"])
    assert "sentinel" in joined or "null-handling" in joined
    assert "unknown" in str(f["evidence"]["current"]["invalid_examples"]).lower()


def test_negative_revenue_hypothesises_refunds_and_links_to_drift(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    f = next(f for f in _by_column(r, "revenue") if f["issue_type"] == "quality")
    joined = " | ".join(h["cause"].lower() for h in f["hypotheses"])
    assert "refund" in joined or "sign-flip" in joined or "sentinel" in joined
    # Related drift is attached to the same finding, not a separate drift one.
    assert f["evidence"]["related_drift"] is not None
    assert not any(x["issue_type"] == "drift" and "revenue" in x["columns"] for x in r["findings"])


def test_duplicate_key_hypothesises_counter_reuse(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    f = next(f for f in _by_column(r, "transaction_id") if "key" in f["issue_name"].lower())
    assert any("reusing" in h["cause"].lower() or "concat" in h["cause"].lower()
               for h in f["hypotheses"])
    assert f["evidence"]["current"]["duplicate_key_count"] > 0


def test_referential_integrity_hypothesises_lagging_reference(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    f = next(f for f in _by_column(r, "customer_id"))
    assert any("out of sync" in h["cause"].lower() or "reference table" in h["cause"].lower()
               for h in f["hypotheses"])
    assert f["evidence"]["current"]["orphan_rows"] > 0


def test_unexpected_categories_links_to_drift_evidence(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    f = next(f for f in _by_column(r, "product_category") if "categor" in f["issue_name"].lower())
    unseen = f["evidence"]["related_drift"]["unseen_categories"]
    assert {u["value"] for u in unseen} >= {"Toys", "Grocery"}


def test_removed_column_mentions_deliberate_vs_accidental(demo):
    r = analyze(current_profile=demo["p2"], quality_results=demo["quality"],
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    f = next(f for f in r["findings"] if f["id"] == "RCA-removed-discount_pct")
    joined = " | ".join(h["cause"].lower() for h in f["hypotheses"])
    assert "deliberately removed" in joined or "accident" in joined or "renamed" in joined
    assert f["severity"] == "high"


def test_drift_only_findings_appear_for_pure_drift(demo):
    # Build a situation where unit_price drift is NOT also a quality failure.
    v1 = demo["v1"]
    rules = []  # no rules → no quality findings for unit_price
    qres = run_quality_checks(demo["v2"], rules, {})
    r = analyze(current_profile=demo["p2"], quality_results=qres,
                drift_report=demo["drift"], schema_diff=demo["schema"], reference_profile=demo["p1"])
    drift_findings = _by_type(r, "drift")
    assert any("unit_price" in f["columns"] for f in drift_findings)


def test_no_findings_when_everything_is_fine(demo):
    v1 = demo["v1"]
    p1 = demo["p1"]
    rules = suggest_rules_from_profile(p1["columns"], p1["dataset"], now=NOW)
    qres = run_quality_checks(v1, rules, {"baseline_columns": p1["columns"], "now": NOW})
    drift = detect_drift(v1, v1, reference_profiles=p1["columns"], current_profiles=p1["columns"])
    sdiff = compare_schemas(p1["columns"], p1["columns"])
    r = analyze(current_profile=p1, quality_results=qres, drift_report=drift,
                schema_diff=sdiff, reference_profile=p1)
    assert r["summary"]["total_findings"] == 0


def test_works_without_baseline_or_drift():
    # Only a current profile + quality results — must still produce a finding without crashing.
    cur = pd.DataFrame({"email": [None] * 90 + [f"u{i}@x" for i in range(10)]})
    prof = profile_dataframe(cur)
    results = run_quality_checks(cur, [{"rule_type": "missing_values", "column": "email",
                                        "params": {"max_missing_pct": 10}}])
    r = analyze(current_profile=prof, quality_results=results)
    assert r["summary"]["total_findings"] == 1
    f = r["findings"][0]
    assert f["evidence"]["baseline"] is None
    assert "disclaimer" in f and f["hypotheses"]
