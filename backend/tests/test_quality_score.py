import pandas as pd
import pytest

from app.analytics.profiling import profile_dataframe
from app.analytics.quality_checks import run_quality_checks, suggest_rules_from_profile
from app.analytics.quality_score import (
    DEFAULT_WEIGHTS,
    compare_scores,
    compute_quality_score,
)

NOW = pd.Timestamp("2026-10-17")


def _result(**kwargs):
    base = {"rule_name": "r", "rule_type": "missing_values", "rule_severity": "medium",
            "columns": ["c"], "outcome": "pass", "severity": "info",
            "observed": None, "expected": None, "affected_count": 0, "affected_pct": 0.0,
            "evidence": {}, "message": "", "rule_id": None}
    base.update(kwargs)
    return base


def test_all_passes_scores_100():
    report = compute_quality_score([
        _result(rule_type=rt, outcome="pass")
        for rt in ("missing_values", "data_type", "unique", "schema_match", "referential_integrity")
    ])
    assert report["score"] == 100.0 and report["status"] == "healthy"
    assert all(d["score"] == 100.0 for d in report["dimensions"].values())


def test_dimension_with_no_rules_is_not_evaluated_including_accuracy():
    report = compute_quality_score([_result(rule_type="missing_values", outcome="pass")])
    assert report["dimensions"]["accuracy"]["status"] == "not_evaluated"
    assert "trusted reference" in report["dimensions"]["accuracy"]["reason"]
    assert report["dimensions"]["validity"]["status"] == "not_evaluated"


def test_fail_lowers_score_proportionally_to_affected_pct():
    report = compute_quality_score([
        _result(rule_type="missing_values", outcome="fail", affected_pct=40.0, rule_severity="high"),
    ])
    assert report["dimensions"]["completeness"]["score"] == pytest.approx(60.0, abs=0.01)


def test_higher_severity_rule_hurts_more():
    low = compute_quality_score([
        _result(rule_type="data_type", outcome="pass", rule_severity="low"),
        _result(rule_type="numeric_range", outcome="fail", affected_pct=50.0, rule_severity="low"),
    ])["dimensions"]["validity"]["score"]
    high = compute_quality_score([
        _result(rule_type="data_type", outcome="pass", rule_severity="low"),
        _result(rule_type="numeric_range", outcome="fail", affected_pct=50.0, rule_severity="critical"),
    ])["dimensions"]["validity"]["score"]
    assert high < low


def test_skipped_results_do_not_count_as_passes():
    all_skipped = compute_quality_score([_result(rule_type="data_type", outcome="skipped")])
    assert all_skipped["dimensions"]["validity"]["status"] == "not_evaluated"
    passing_dim = compute_quality_score([
        _result(rule_type="data_type", outcome="pass"),
        _result(rule_type="numeric_range", outcome="skipped"),
    ])["dimensions"]["validity"]
    assert passing_dim["weighted_rules"] == 1 and passing_dim["score"] == 100.0


def test_custom_weights_change_overall_score():
    results = [
        _result(rule_type="missing_values", outcome="fail", affected_pct=50),
        _result(rule_type="data_type", outcome="pass"),
    ]
    default = compute_quality_score(results)["score"]
    heavy_completeness = compute_quality_score(results, weights={"completeness": 90, "validity": 10})["score"]
    assert heavy_completeness < default


def test_zero_weight_sum_rejected():
    with pytest.raises(ValueError):
        compute_quality_score([], weights={k: 0 for k in DEFAULT_WEIGHTS})


def test_negative_weight_rejected():
    with pytest.raises(ValueError):
        compute_quality_score([], weights={"completeness": -1})


def test_threshold_hard_vs_soft_violation():
    strict = compute_quality_score(
        [_result(rule_type="missing_values", outcome="fail", affected_pct=10)],
        thresholds={"overall_min": 90, "dimension_min": {"completeness": 95}},
    )
    assert strict["threshold_violations"][0]["severity"] == "hard"
    assert strict["status"] == "failing"
    pass_only = compute_quality_score(
        [_result(rule_type="data_type", outcome="pass")],
        thresholds={"overall_min": 50, "dimension_min": {"validity": 50}},
    )
    assert pass_only["status"] == "healthy"


def test_breakdown_lists_contributing_rules():
    report = compute_quality_score([
        _result(rule_type="unique", outcome="fail", affected_pct=20, rule_name="r1"),
        _result(rule_type="duplicate_rows", outcome="pass", rule_name="r2"),
    ])
    rules = report["dimensions"]["uniqueness"]["rule_breakdown"]
    assert {r["rule_name"] for r in rules} == {"r1", "r2"}
    assert report["dimensions"]["uniqueness"]["worst_rules"][0]["rule_name"] == "r1"


def test_weakest_dimension_identified():
    report = compute_quality_score([
        _result(rule_type="missing_values", outcome="fail", affected_pct=40),
        _result(rule_type="unique", outcome="pass"),
    ])
    assert report["weakest_dimension"]["name"] == "completeness"


def test_compare_scores_delta():
    prev = compute_quality_score([_result(rule_type="missing_values", outcome="pass")])
    curr = compute_quality_score([_result(rule_type="missing_values", outcome="fail", affected_pct=20)])
    cmp = compare_scores(prev, curr)
    assert cmp["delta"] < 0 and "completeness" in cmp["regressed_dimensions"]


def test_demo_v1_scores_higher_than_v2(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    v2 = pd.read_csv(sample_dir / "retail_transactions_v2.csv")
    cust = pd.read_csv(sample_dir / "customers_clean.csv")
    prof = profile_dataframe(v1)
    rules = suggest_rules_from_profile(prof["columns"], prof["dataset"], now=NOW) + [
        {"name": "Customer exists", "rule_type": "referential_integrity", "column": "customer_id",
         "severity": "high", "params": {"reference_dataset": "c", "reference_column": "customer_id"}},
    ]
    ctx = {"baseline_columns": prof["columns"], "now": NOW,
           "reference_frames": {"c": cust}}
    s1 = compute_quality_score(run_quality_checks(v1, rules, ctx))
    s2 = compute_quality_score(run_quality_checks(v2, rules, ctx))
    assert s1["score"] == 100.0
    assert s2["score"] < s1["score"]
    assert s2["dimensions"]["completeness"]["score"] < 100.0
    assert s2["dimensions"]["uniqueness"]["score"] < 100.0
    assert s2["dimensions"]["accuracy"]["score"] < 100.0


def test_accuracy_requires_referential_rule(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    prof = profile_dataframe(v1)
    rules = suggest_rules_from_profile(prof["columns"], prof["dataset"], now=NOW)
    s = compute_quality_score(run_quality_checks(v1, rules, {"baseline_columns": prof["columns"], "now": NOW}))
    assert s["dimensions"]["accuracy"]["status"] == "not_evaluated"
    assert "trusted reference" in s["dimensions"]["accuracy"]["reason"].lower()
