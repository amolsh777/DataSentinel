import numpy as np
import pandas as pd
import pytest

from app.analytics.data_drift import (
    DEFAULT_THRESHOLDS,
    benjamini_hochberg,
    chi_square,
    detect_drift,
    jensen_shannon,
    kolmogorov_smirnov,
    population_stability_index,
    wasserstein_normalized,
)
from app.analytics.profiling import profile_dataframe


# ------------------------------------------------------------------ primitives


def test_psi_near_zero_for_identical_distributions():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, 5000)
    assert population_stability_index(x, x)["psi"] < 0.01


def test_psi_rises_with_mean_shift():
    rng = np.random.default_rng(1)
    ref = rng.normal(0, 1, 5000)
    psi_small = population_stability_index(ref, rng.normal(0.2, 1, 5000))["psi"]
    psi_big = population_stability_index(ref, rng.normal(2.0, 1, 5000))["psi"]
    assert 0.01 < psi_small < 0.1 < psi_big


def test_psi_reports_truncated_bins_on_tied_reference():
    ref = np.r_[np.zeros(100), np.ones(100)]
    cur = np.r_[np.zeros(50), np.ones(150)]
    res = population_stability_index(ref, cur, bins=10)
    assert res["effective_bins"] < 10 and res["note"] is not None


def test_psi_undefined_on_constant_reference():
    assert population_stability_index(np.zeros(500), np.ones(500))["psi"] is None


def test_ks_requires_minimum_sample_size():
    assert kolmogorov_smirnov(np.arange(10.0), np.arange(10.0))["note"] == "insufficient samples"
    res = kolmogorov_smirnov(np.arange(100.0), np.arange(100.0) + 1)
    assert res["statistic"] is not None and 0 <= res["p_value"] <= 1


def test_wasserstein_normalised_by_reference_iqr_and_handles_zero_iqr():
    rng = np.random.default_rng(2)
    ref, cur = rng.normal(0, 1, 1000), rng.normal(0, 1, 1000) + 0.5
    res = wasserstein_normalized(ref, cur)
    assert 0.3 < res["normalized"] < 0.9
    const = wasserstein_normalized(np.zeros(100), np.ones(100))
    assert const["normalized"] is None and "undefined" in const["note"]


def test_jensen_shannon_bounded_zero_and_one():
    ref = pd.Series(list("AAAABBBBCC") * 50)
    assert jensen_shannon(ref, ref)["distance"] < 0.01
    cur = pd.Series(list("DDDDDEEEEE") * 50)
    assert 0.9 < jensen_shannon(ref, cur)["distance"] <= 1.0


def test_chi_square_skips_when_assumptions_fail():
    # Rare categories pool but even pooled the second group is one obs.
    assert chi_square(pd.Series(["A"] * 100), pd.Series(["B"]))["statistic"] is None


def test_benjamini_hochberg_matches_known_decision():
    p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.60]
    assert benjamini_hochberg(p, 0.05) == [True, True, False, False, False,
                                           False, False, False, False, False]
    assert benjamini_hochberg([None, None], 0.05) == [False, False]


# ------------------------------------------------------------- feature pipeline


@pytest.fixture
def ref_frame() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    n = 500
    return pd.DataFrame({
        "amount": rng.normal(100, 10, n),
        "qty": rng.integers(1, 10, n),
        "city": rng.choice(["A", "B", "C"], n, p=[0.5, 0.3, 0.2]),
        "flag": [True] * n,
        "customer_id": [f"CUST-{i:05d}" for i in range(n)],
    })


def _profiles(df):
    return profile_dataframe(df)["columns"]


def test_identical_frames_report_stable(ref_frame):
    r = detect_drift(ref_frame, ref_frame.copy(),
                     reference_profiles=_profiles(ref_frame), current_profiles=_profiles(ref_frame))
    assert r["summary"]["status"] == "stable" and r["summary"]["features_drifted"] == 0


def test_identifier_and_constant_columns_are_skipped(ref_frame):
    r = detect_drift(ref_frame, ref_frame, reference_profiles=_profiles(ref_frame),
                     current_profiles=_profiles(ref_frame))
    skipped = {s["column"] for s in r["skipped"]}
    assert "customer_id" in skipped and "flag" in skipped


def test_excluded_columns_report_reason(ref_frame):
    r = detect_drift(ref_frame, ref_frame, reference_profiles=_profiles(ref_frame),
                     current_profiles=_profiles(ref_frame), exclude_columns=["amount"])
    assert {"column": "amount", "reason": "excluded by request"} in r["skipped"]


def test_numeric_shift_is_reported_with_effect_sizes(ref_frame):
    rng = np.random.default_rng(99)
    cur = ref_frame.copy()
    cur["amount"] = rng.normal(130, 10, len(cur))  # mean shifted +30%
    r = detect_drift(ref_frame, cur, reference_profiles=_profiles(ref_frame),
                     current_profiles=_profiles(cur))
    amt = next(f for f in r["features"] if f["name"] == "amount")
    assert amt["status"] == "drifted" and amt["severity"] in ("medium", "high")
    assert amt["effect_sizes"]["psi"] > DEFAULT_THRESHOLDS["psi"]["warn"]
    assert amt["ks"]["significant_after_bh"] is True


def test_small_sample_returns_insufficient_data(ref_frame):
    cur = ref_frame.head(20).copy()
    r = detect_drift(ref_frame, cur, reference_profiles=_profiles(ref_frame),
                     current_profiles=_profiles(cur))
    assert any(f.get("status") == "insufficient_data" for f in r["features"])


def test_categorical_unseen_categories_drive_drift(ref_frame):
    cur = ref_frame.copy()
    cur.loc[:49, "city"] = "NEW"  # 10% unseen
    r = detect_drift(ref_frame, cur, reference_profiles=_profiles(ref_frame),
                     current_profiles=_profiles(cur))
    city = next(f for f in r["features"] if f["name"] == "city")
    assert city["status"] == "drifted"
    assert city["unseen_count"] == 1 and city["effect_sizes"]["unseen_share_pct"] >= 5
    assert city["severity"] == "high"


def test_missingness_change_reported_separately(ref_frame):
    cur = ref_frame.copy()
    cur.loc[:149, "amount"] = np.nan
    r = detect_drift(ref_frame, cur, reference_profiles=_profiles(ref_frame),
                     current_profiles=_profiles(cur))
    assert any(m["column"] == "amount" and m["status"] in ("warn", "high")
               for m in r["missingness_changes"])


def test_effect_size_gates_verdict_on_large_samples():
    rng = np.random.default_rng(7)
    ref = pd.DataFrame({"x": rng.normal(0, 1, 50_000)})
    cur = pd.DataFrame({"x": rng.normal(0.02, 1, 50_000)})  # trivially small shift
    r = detect_drift(ref, cur, reference_profiles=_profiles(ref), current_profiles=_profiles(cur))
    x = next(f for f in r["features"] if f["name"] == "x")
    # KS almost certainly rejects on 50k samples, but PSI is tiny → not drifted.
    assert x["effect_sizes"]["psi"] < DEFAULT_THRESHOLDS["psi"]["warn"]
    assert x["status"] == "stable"


def test_configurable_thresholds_affect_verdict(ref_frame):
    rng = np.random.default_rng(3)
    cur = ref_frame.copy()
    cur["amount"] = rng.normal(100.2, 10, len(cur))
    base = detect_drift(ref_frame, cur, reference_profiles=_profiles(ref_frame),
                        current_profiles=_profiles(cur))
    strict = detect_drift(ref_frame, cur, reference_profiles=_profiles(ref_frame),
                          current_profiles=_profiles(cur),
                          thresholds={"psi": {"warn": 0.0001, "high": 0.001}})
    base_amt = next(f for f in base["features"] if f["name"] == "amount")
    strict_amt = next(f for f in strict["features"] if f["name"] == "amount")
    assert base_amt["status"] == "stable" and strict_amt["status"] == "drifted"


def test_demo_stable_variant_has_no_drift(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    v1b = pd.read_csv(sample_dir / "retail_transactions_v1b_stable.csv")
    r = detect_drift(v1, v1b, reference_profiles=_profiles(v1),
                     current_profiles=_profiles(v1b), exclude_columns=["transaction_date"])
    assert r["summary"]["features_drifted"] == 0 and r["summary"]["status"] == "stable"


def test_demo_v2_detects_injected_statistical_drift(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    v2 = pd.read_csv(sample_dir / "retail_transactions_v2.csv")
    r = detect_drift(v1, v2, reference_profiles=_profiles(v1),
                     current_profiles=_profiles(v2), exclude_columns=["transaction_date"])
    drifted = {f["name"] for f in r["features"] if f["status"] == "drifted"}
    assert {"unit_price", "revenue", "customer_age", "product_category", "region"} <= drifted
    unit_price = next(f for f in r["features"] if f["name"] == "unit_price")
    assert unit_price["effect_sizes"]["psi"] > 0 and unit_price["effect_sizes"]["wasserstein_normalized"] > 0.1
    cats = next(f for f in r["features"] if f["name"] == "product_category")
    assert {u["value"] for u in cats["unseen_categories"]} >= {"Toys", "Grocery"}
    # Missingness change for payment_method / customer_age is separately reported.
    miss_cols = {m["column"] for m in r["missingness_changes"]}
    assert {"payment_method", "customer_age"} <= miss_cols
