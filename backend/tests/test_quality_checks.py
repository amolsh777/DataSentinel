import numpy as np
import pandas as pd
import pytest

from app.analytics.profiling import profile_dataframe
from app.analytics.quality_checks import (
    RuleValidationError,
    run_quality_checks,
    suggest_rules_from_profile,
    summarize_results,
    validate_rule,
)

NOW = "2026-10-17"


@pytest.fixture
def df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [1, 2, 3, 4, 4, 6, 7, 8, 9, 10],
            "amount": [10.0, 20.5, -5.0, 30.0, 30.0, np.nan, 50.0, 60.0, 70.0, 1000.0],
            "qty": ["1", "2", "3", "x", "x", "6", "7", "8", "9", "10"],
            "city": ["A", "B", "A", "Z", "Z", "B", "A", "B", "a", "A"],
            "day": ["2025-01-01", "2025-01-02", "bad", "2025-01-04", "2025-01-04",
                    "2025-01-06", "2027-01-01", "2025-01-08", "2025-01-09", "2025-01-10"],
            "flag": ["on"] * 10,
        }
    )


def one(df, rule, ctx=None):
    results = run_quality_checks(df, [rule], ctx or {"now": NOW})
    assert len(results) == 1, results
    return results[0]


# ---------------------------------------------------------------- per rule type


def test_missing_values_fail_warn_pass(df):
    assert one(df, {"rule_type": "missing_values", "column": "amount", "params": {"max_missing_pct": 5}})["outcome"] == "fail"
    r = one(df, {"rule_type": "missing_values", "column": "amount",
                 "params": {"max_missing_pct": 20, "warn_missing_pct": 5}})
    assert r["outcome"] == "warn" and r["affected_count"] == 1 and r["affected_pct"] == 10.0
    assert one(df, {"rule_type": "missing_values", "column": "id", "params": {"max_missing_pct": 0}})["outcome"] == "pass"


def test_missing_values_without_column_expands_per_column(df):
    res = run_quality_checks(df, [{"rule_type": "missing_values", "params": {"max_missing_pct": 0}}])
    assert len(res) == df.shape[1]
    assert [r["columns"][0] for r in res if r["outcome"] == "fail"] == ["amount"]


def test_duplicate_rows(df):
    r = one(df, {"rule_type": "duplicate_rows", "params": {}})
    assert r["outcome"] == "fail" and r["affected_count"] == 1
    assert r["evidence"]["rows_involved"] == 2 and r["evidence"]["example_rows"] == [4]
    assert one(df, {"rule_type": "duplicate_rows", "params": {"max_duplicate_pct": 15}})["outcome"] == "warn"
    assert one(df.drop_duplicates(), {"rule_type": "duplicate_rows", "params": {}})["outcome"] == "pass"


def test_duplicate_rows_on_subset(df):
    r = one(df, {"rule_type": "duplicate_rows", "params": {"subset": ["city"]}})
    assert r["affected_count"] == 6  # A x3 extra... A,B,A,Z,Z,B,A,B,a,A -> 10 - 4 distinct


def test_unique(df):
    r = one(df, {"rule_type": "unique", "column": "id", "severity": "high"})
    assert r["outcome"] == "fail" and r["severity"] == "high"
    assert r["observed"] == {"duplicated_values": 1, "rows_with_duplicated_values": 2}
    assert r["evidence"]["example_values"] == {"4": 2}


def test_required_and_unexpected_columns(df):
    r = one(df, {"rule_type": "required_columns", "params": {"columns": ["id", "revenue"]}})
    assert r["outcome"] == "fail" and r["columns"] == ["revenue"]
    r = one(df, {"rule_type": "unexpected_columns", "params": {"allowed": ["id", "amount", "qty", "city", "day"]}})
    assert r["outcome"] == "warn" and r["columns"] == ["flag"]


def test_data_type(df):
    r = one(df, {"rule_type": "data_type", "column": "qty", "params": {"expected_type": "integer"}})
    assert r["outcome"] == "fail" and r["affected_count"] == 2
    assert r["evidence"]["invalid_examples"] == {"x": 2}
    tolerant = one(df, {"rule_type": "data_type", "column": "qty",
                        "params": {"expected_type": "integer", "max_invalid_pct": 25}})
    assert tolerant["outcome"] == "warn"
    assert one(df, {"rule_type": "data_type", "column": "amount", "params": {"expected_type": "integer"}})["outcome"] == "fail"
    assert one(df, {"rule_type": "data_type", "column": "amount", "params": {"expected_type": "numeric"}})["outcome"] == "pass"


def test_numeric_range_ignores_non_numeric_values(df):
    r = one(df, {"rule_type": "numeric_range", "column": "amount", "params": {"min": 0, "max": 100}})
    assert r["outcome"] == "fail" and r["affected_count"] == 2
    assert r["observed"]["observed_min"] == -5.0 and r["evidence"]["example_rows"] == [2, 9]
    q = one(df, {"rule_type": "numeric_range", "column": "qty", "params": {"min": 1}})
    assert q["outcome"] == "pass" and q["evidence"]["non_numeric_excluded"] == 2


def test_allowed_values_case_sensitivity(df):
    r = one(df, {"rule_type": "allowed_values", "column": "city", "params": {"values": ["A", "B"]}})
    assert r["outcome"] == "fail" and r["affected_count"] == 3
    assert r["evidence"]["unexpected_value_counts"] == {"Z": 2, "a": 1}
    r = one(df, {"rule_type": "allowed_values", "column": "city",
                 "params": {"values": ["A", "B"], "case_sensitive": False}})
    assert r["affected_count"] == 2


def test_date_range(df):
    r = one(df, {"rule_type": "date_range", "column": "day", "params": {"allow_future": False}})
    assert r["outcome"] == "fail"
    assert r["observed"]["unparseable"] == 1 and r["observed"]["out_of_range"] == 1
    r = one(df, {"rule_type": "date_range", "column": "day",
                 "params": {"min": "2025-01-03", "max_invalid_pct": 50}})
    assert r["observed"]["out_of_range"] == 2


def test_date_range_only_unparseable_within_tolerance_warns():
    frame = pd.DataFrame({"d": ["2025-01-01", "nope", "2025-01-03", "2025-01-04"]})
    r = one(frame, {"rule_type": "date_range", "column": "d", "params": {"max_invalid_pct": 30}})
    assert r["outcome"] == "warn"


def test_constant_column(df):
    res = run_quality_checks(df, [{"rule_type": "constant_column", "params": {}}])
    warned = [r["columns"][0] for r in res if r["outcome"] == "warn"]
    assert warned == ["flag"]


def test_schema_match_needs_baseline(df):
    assert one(df, {"rule_type": "schema_match"}, {})["outcome"] == "skipped"
    base = profile_dataframe(df)["columns"]
    assert one(df, {"rule_type": "schema_match"}, {"baseline_columns": base})["outcome"] == "pass"
    r = one(df.drop(columns=["city"]), {"rule_type": "schema_match"}, {"baseline_columns": base})
    assert r["outcome"] == "fail" and "city" in r["columns"]


def test_business_rule(df):
    frame = pd.DataFrame({"q": [1, 2, 3, None], "p": [2.0, 2.0, 2.0, 2.0], "rev": [2.0, 4.0, 7.0, 8.0]})
    r = one(frame, {"rule_type": "business_rule", "params": {"expression": "abs(rev - q * p) <= 0.01"}})
    assert r["outcome"] == "fail" and r["affected_count"] == 1
    assert r["observed"]["not_evaluable"] == 1 and r["evidence"]["example_rows"] == [2]
    assert r["columns"] == ["rev", "q", "p"]


def test_business_rule_skips_when_columns_absent(df):
    r = one(df, {"rule_type": "business_rule", "params": {"expression": "discount >= 0"}})
    assert r["outcome"] == "skipped" and "discount" in r["message"]


def test_referential_integrity(df):
    r = one(df, {"rule_type": "referential_integrity", "column": "city",
                 "params": {"reference_values": ["A", "B", "a"]}})
    assert r["outcome"] == "fail" and r["affected_count"] == 2
    ref = pd.DataFrame({"code": ["A", "B", "Z", "a"]})
    rule = {"rule_type": "referential_integrity", "column": "city",
            "params": {"reference_dataset": "cities", "reference_column": "code"}}
    assert one(df, rule, {"reference_frames": {"cities": ref}})["outcome"] == "pass"
    assert one(df, rule, {})["outcome"] == "skipped"


# ------------------------------------------------------------ engine behaviour


def test_missing_target_column_is_skipped_not_passed(df):
    r = one(df, {"rule_type": "unique", "column": "nope"})
    assert r["outcome"] == "skipped" and "not present" in r["message"]


def test_invalid_rule_reported_as_skipped(df):
    r = one(df, {"rule_type": "numeric_range", "column": "amount", "params": {}})
    assert r["outcome"] == "skipped" and "Invalid rule" in r["message"]


def test_disabled_rules_are_ignored(df):
    assert run_quality_checks(df, [{"rule_type": "duplicate_rows", "enabled": False}]) == []


def test_warn_severity_is_one_level_below_rule(df):
    r = one(df, {"rule_type": "duplicate_rows", "severity": "high", "params": {"max_duplicate_pct": 50}})
    assert r["outcome"] == "warn" and r["severity"] == "medium"


@pytest.mark.parametrize(
    "rule",
    [
        {"rule_type": "nope"},
        {"rule_type": "unique"},
        {"rule_type": "duplicate_rows", "column": "x"},
        {"rule_type": "missing_values", "column": "a", "params": {"max_missing_pct": 150}},
        {"rule_type": "missing_values", "column": "a", "params": {"max_missing_pct": 5, "warn_missing_pct": 9}},
        {"rule_type": "numeric_range", "column": "a", "params": {"min": 5, "max": 1}},
        {"rule_type": "data_type", "column": "a", "params": {"expected_type": "money"}},
        {"rule_type": "allowed_values", "column": "a", "params": {"values": []}},
        {"rule_type": "date_range", "column": "a", "params": {"min": "not-a-date"}},
        {"rule_type": "business_rule", "params": {"expression": "__import__('os')"}},
        {"rule_type": "referential_integrity", "column": "a", "params": {}},
        {"rule_type": "unique", "column": "a", "severity": "urgent"},
    ],
)
def test_validate_rule_rejects_bad_definitions(rule):
    with pytest.raises(RuleValidationError):
        validate_rule(rule)


def test_summary_status_levels(df):
    healthy = summarize_results(run_quality_checks(df, [{"rule_type": "required_columns", "params": {"columns": ["id"]}}]))
    assert healthy["status"] == "healthy"
    degraded = summarize_results(run_quality_checks(df, [{"rule_type": "duplicate_rows", "severity": "medium"}]))
    assert degraded["status"] == "degraded"
    failing = summarize_results(run_quality_checks(df, [{"rule_type": "unique", "column": "id", "severity": "high"},
                                                        {"rule_type": "schema_match"}]))
    assert failing["status"] == "failing" and failing["top_issues"][0]["rule_type"] == "unique"
    assert failing["skipped"][0]["rule_name"] == "schema_match"


def test_suggested_rules_pass_on_their_own_baseline(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    prof = profile_dataframe(v1)
    rules = suggest_rules_from_profile(prof["columns"], prof["dataset"], now=pd.Timestamp(NOW))
    kinds = {r["rule_type"] for r in rules}
    assert {"required_columns", "unique", "data_type", "allowed_values", "numeric_range", "missing_values"} <= kinds
    assert not any(r["rule_type"] == "numeric_range" and "max" in r["params"] for r in rules)
    res = run_quality_checks(v1, rules, {"baseline_columns": prof["columns"], "now": NOW})
    assert summarize_results(res)["status"] == "healthy"


def test_demo_v2_detects_injected_quality_issues(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    v2 = pd.read_csv(sample_dir / "retail_transactions_v2.csv")
    customers = pd.read_csv(sample_dir / "customers_clean.csv")
    prof = profile_dataframe(v1)
    rules = suggest_rules_from_profile(prof["columns"], prof["dataset"], now=pd.Timestamp(NOW)) + [
        {"name": "Customer exists", "rule_type": "referential_integrity", "column": "customer_id",
         "severity": "high", "params": {"reference_dataset": "customers", "reference_column": "customer_id"}},
    ]
    res = run_quality_checks(v2, rules, {"baseline_columns": prof["columns"], "now": NOW,
                                         "reference_frames": {"customers": customers}})
    bad = {(r["rule_type"], tuple(r["columns"])) for r in res if r["outcome"] in ("fail", "warn")}
    assert ("unexpected_columns", ("loyalty_tier",)) in bad                  # ISS-01
    assert ("required_columns", ("discount_pct",)) in bad                    # ISS-02
    assert ("data_type", ("quantity",)) in bad                               # ISS-03
    assert ("missing_values", ("customer_age",)) in bad                      # ISS-04
    assert ("missing_values", ("payment_method",)) in bad                    # ISS-04
    assert ("allowed_values", ("product_category",)) in bad                  # ISS-06
    assert ("allowed_values", ("region",)) in bad                            # ISS-06
    assert ("duplicate_rows", ()) in bad                                     # ISS-07
    assert ("unique", ("transaction_id",)) in bad                            # ISS-07
    assert ("numeric_range", ("revenue",)) in bad                            # ISS-08
    assert ("referential_integrity", ("customer_id",)) in bad                # ISS-09
    neg = next(r for r in res if r["rule_type"] == "numeric_range" and r["columns"] == ["revenue"])
    assert neg["affected_count"] == 30
