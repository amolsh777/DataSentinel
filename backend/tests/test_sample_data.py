from __future__ import annotations

import json

import pandas as pd

from app.analytics.profiling import profile_dataframe
from app.demo.sample_data import build_v1, build_v2, generate_all
from app.services.ingestion import parse_file


def test_generation_is_deterministic(tmp_path):
    m1 = generate_all(tmp_path / "a")
    m2 = generate_all(tmp_path / "b")
    for name, meta in m1["files"].items():
        if "sha256" in meta:
            assert meta["sha256"] == m2["files"][name]["sha256"], name


def test_manifest_marks_everything_synthetic(sample_dir):
    manifest = json.loads((sample_dir / "manifest.json").read_text())
    assert manifest["synthetic"] is True
    assert all(f["synthetic"] for f in manifest["files"].values())
    assert len(manifest["files"]["retail_transactions_v2.csv"]["injected_issues"]) == 9


def test_v1_is_clean():
    df = build_v1()
    assert not df.duplicated().any()
    assert df["transaction_id"].is_unique
    assert (df["revenue"] >= 0).all()


def test_v2_injected_issues_present():
    v1, v2 = build_v1(), build_v2()
    assert "loyalty_tier" in v2.columns and "loyalty_tier" not in v1.columns   # ISS-01
    assert "discount_pct" in v1.columns and "discount_pct" not in v2.columns   # ISS-02
    assert (v2["quantity"] == "unknown").any()                                 # ISS-03
    assert v2["customer_age"].isna().mean() > 0.15 > v1["customer_age"].isna().mean()  # ISS-04
    # ISS-05: compare within an existing category; new cheap categories dilute the overall median
    elec1 = v1.loc[v1["product_category"] == "Electronics", "unit_price"].median()
    elec2 = v2.loc[v2["product_category"] == "Electronics", "unit_price"].median()
    assert 1.15 < elec2 / elec1 < 1.45
    assert v2["customer_age"].mean() > v1["customer_age"].mean() + 5
    assert {"Toys", "Grocery"} <= set(v2["product_category"])                  # ISS-06
    assert "Central" in set(v2["region"])
    assert v2.duplicated().sum() == 150                                        # ISS-07
    assert (v2["revenue"] < 0).sum() >= 30                                     # ISS-08


def test_v2_profile_detects_intrinsic_issues(sample_dir):
    content = (sample_dir / "retail_transactions_v2.csv").read_bytes()
    p = profile_dataframe(parse_file(content, "v2.csv").df)
    d = p["dataset"]
    assert d["duplicate_rows"] == 150
    assert "quantity" in d["mixed_type_columns"]
    qty = next(c for c in p["columns"] if c["column_name"] == "quantity")
    assert qty["inferred_type"] == "integer" and qty["pandas_dtype"] == "object"


def test_excel_sample_has_two_sheets(sample_dir):
    content = (sample_dir / "retail_transactions_sample.xlsx").read_bytes()
    parsed = parse_file(content, "s.xlsx", sheet_name="Transactions")
    assert parsed.sheet_names == ["README", "Transactions"] and len(parsed.df) == 500
