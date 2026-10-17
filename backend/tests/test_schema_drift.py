import numpy as np
import pandas as pd
import pytest

from app.analytics.profiling import profile_dataframe
from app.analytics.schema_drift import classify_type_change, compare_schemas


def cols(df: pd.DataFrame):
    return profile_dataframe(df)["columns"]


@pytest.fixture
def base() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    n = 200
    return pd.DataFrame(
        {
            "order_id": [f"ORD-{i:05d}" for i in range(n)],
            "amount": rng.normal(100, 10, n).round(2),
            "qty": rng.integers(1, 5, n),
            "city": rng.choice(["A", "B", "C"], n),
            "email": [f"u{i}@x.io" for i in range(n)],
        }
    )


def types(result):
    return [c["change_type"] for c in result["changes"]]


def test_identical_frames_have_no_changes(base):
    r = compare_schemas(cols(base), cols(base))
    assert r["changes"] == [] and r["compatible"] is True
    assert {c["status"] for c in r["columns"]} == {"unchanged"}


def test_added_and_removed_columns(base):
    cur = base.drop(columns=["email"]).assign(channel="web")
    r = compare_schemas(cols(base), cols(cur))
    assert r["summary"]["added"] == 1 and r["summary"]["removed"] == 1
    removed = next(c for c in r["changes"] if c["change_type"] == "removed_column")
    added = next(c for c in r["changes"] if c["change_type"] == "added_column")
    assert removed["columns"] == ["email"] and removed["breaking"] is True
    assert added["columns"] == ["channel"] and added["breaking"] is False
    assert r["compatible"] is False


def test_rename_candidate_is_reported_but_never_merged(base):
    cur = base.rename(columns={"amount": "amount_inr"})
    r = compare_schemas(cols(base), cols(cur))
    assert "rename_candidate" in types(r)
    # Both sides are still reported independently.
    assert "removed_column" in types(r) and "added_column" in types(r)
    cand = r["rename_candidates"][0]
    assert cand["removed"] == "amount" and cand["added"] == "amount_inr"
    assert next(c for c in r["changes"] if c["change_type"] == "rename_candidate")["certain"] is False


def test_dissimilar_names_are_not_rename_candidates(base):
    cur = base.drop(columns=["amount"]).assign(zz_flag=base["amount"])
    assert compare_schemas(cols(base), cols(cur))["rename_candidates"] == []


def test_reordering_is_info_and_compatible(base):
    cur = base[["city", "order_id", "amount", "qty", "email"]]
    r = compare_schemas(cols(base), cols(cur))
    assert types(r) == ["column_reordered"]
    assert r["changes"][0]["severity"] == "info" and r["compatible"] is True
    assert all(c["status"] == "unchanged" for c in r["columns"])


def test_storage_type_change_from_stray_strings(base):
    cur = base.copy()
    cur["qty"] = cur["qty"].astype(object)
    cur.loc[:3, "qty"] = "unknown"
    r = compare_schemas(cols(base), cols(cur))
    ch = next(c for c in r["changes"] if c["change_type"] == "storage_type_changed")
    assert ch["columns"] == ["qty"] and ch["breaking"] is True
    assert ch["evidence"]["invalid_count"] == 4 and "unknown" in ch["evidence"]["invalid_examples"]


def test_semantic_type_change_is_breaking(base):
    cur = base.assign(amount=[f"x{i}" for i in range(len(base))])
    ch = next(c for c in compare_schemas(cols(base), cols(cur))["changes"] if c["change_type"] == "type_changed")
    assert ch["baseline"] == "float" and ch["current"] == "text" and ch["breaking"]


@pytest.mark.parametrize(
    "old,new,breaking",
    [("integer", "float", False), ("float", "integer", False), ("categorical", "text", False),
     ("float", "text", True), ("datetime", "categorical", True), ("integer", "empty", True)],
)
def test_type_compatibility_matrix(old, new, breaking):
    assert classify_type_change(old, new)[0] is breaking


def test_nullability_and_missingness(base):
    cur = base.copy()
    cur.loc[:59, "city"] = np.nan            # 0% -> 30%: nullability change, high
    ref = base.copy()
    ref.loc[:9, "amount"] = np.nan           # 5% in baseline
    cur.loc[:39, "amount"] = np.nan          # 20% now: +15 pp, medium
    r = compare_schemas(cols(ref), cols(cur))
    null = next(c for c in r["changes"] if c["change_type"] == "nullability_changed")
    assert null["columns"] == ["city"] and null["severity"] == "high"
    miss = next(c for c in r["changes"] if c["change_type"] == "missingness_changed")
    assert miss["columns"] == ["amount"] and miss["severity"] == "medium"


def test_small_missingness_change_below_threshold_is_ignored(base):
    cur = base.copy()
    cur.loc[:3, "amount"] = np.nan  # 2% < 5 pp default, but baseline had 0 -> nullability
    ref = base.copy()
    ref.loc[:3, "amount"] = np.nan
    cur.loc[:7, "amount"] = np.nan  # 2% -> 4%
    assert "missingness_changed" not in types(compare_schemas(cols(ref), cols(cur)))


def test_identifier_uniqueness_lost(base):
    cur = pd.concat([base, base.head(10)], ignore_index=True)
    ch = [c for c in compare_schemas(cols(base), cols(cur))["changes"]
          if c["change_type"] == "identifier_uniqueness_lost"]
    assert {tuple(c["columns"]) for c in ch} >= {("order_id",)}
    assert all(c["breaking"] for c in ch)


def test_new_and_vanished_categories(base):
    cur = base.copy()
    cur["city"] = np.where(cur["city"] == "C", "D", cur["city"])
    r = compare_schemas(cols(base), cols(cur))
    new = next(c for c in r["changes"] if c["change_type"] == "new_categories")
    gone = next(c for c in r["changes"] if c["change_type"] == "vanished_categories")
    assert "D" in new["evidence"]["new_values"] and "C" in gone["evidence"]["vanished_values"]
    assert new["certain"] is True


def test_truncated_category_lists_are_uncertain():
    rng = np.random.default_rng(1)
    ref = pd.DataFrame({"cat": rng.choice([f"c{i}" for i in range(40)], 4000)})
    cur = pd.DataFrame({"cat": np.r_[rng.choice([f"c{i}" for i in range(40)], 3600), ["NEW"] * 400]})
    ch = next(c for c in compare_schemas(cols(ref), cols(cur))["changes"] if c["change_type"] == "new_categories")
    assert ch["certain"] is False


def test_became_constant(base):
    cur = base.assign(city="A")
    assert "became_constant" in types(compare_schemas(cols(base), cols(cur)))


def test_changes_sorted_by_severity(base):
    cur = base.drop(columns=["email"]).assign(extra=1.0)
    sev = [c["severity"] for c in compare_schemas(cols(base), cols(cur))["changes"]]
    order = ["critical", "high", "medium", "low", "info"]
    assert sev == sorted(sev, key=order.index)


def test_demo_v2_detects_injected_schema_issues(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    v2 = pd.read_csv(sample_dir / "retail_transactions_v2.csv")
    r = compare_schemas(cols(v1), cols(v2))
    found = {(c["change_type"], tuple(c["columns"])) for c in r["changes"]}
    assert ("added_column", ("loyalty_tier",)) in found                       # ISS-01
    assert ("removed_column", ("discount_pct",)) in found                     # ISS-02
    assert ("storage_type_changed", ("quantity",)) in found                   # ISS-03
    assert ("nullability_changed", ("payment_method",)) in found              # ISS-04
    assert ("missingness_changed", ("customer_age",)) in found                # ISS-04
    assert ("new_categories", ("product_category",)) in found                 # ISS-06
    assert ("new_categories", ("region",)) in found                           # ISS-06
    assert ("identifier_uniqueness_lost", ("transaction_id",)) in found       # ISS-07


def test_demo_stable_version_has_no_schema_changes(sample_dir):
    v1 = pd.read_csv(sample_dir / "retail_transactions_v1.csv")
    v1b = pd.read_csv(sample_dir / "retail_transactions_v1b_stable.csv")
    r = compare_schemas(cols(v1), cols(v1b))
    assert r["changes"] == [] and r["compatible"]
