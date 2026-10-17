from __future__ import annotations

import json

import numpy as np
import pandas as pd

from app.analytics.profiling import (
    compute_quality_score,
    infer_column_type,
    profile_dataframe,
    schema_from_profile,
)


def _col(profile: dict, name: str) -> dict:
    return next(c for c in profile["columns"] if c["column_name"] == name)


def test_missing_values_counted():
    df = pd.DataFrame({"a": [1, None, 3, None], "b": ["x", "y", None, "z"]})
    p = profile_dataframe(df)
    assert p["dataset"]["missing_cells"] == 3
    assert p["dataset"]["missing_pct"] == 37.5
    assert _col(p, "a")["missing_count"] == 2 and _col(p, "a")["missing_pct"] == 50.0


def test_duplicate_rows():
    df = pd.DataFrame({"a": [1, 1, 2, 2, 3], "b": ["x", "x", "y", "y", "z"]})
    d = profile_dataframe(df)["dataset"]
    assert d["duplicate_rows"] == 2 and d["duplicate_pct"] == 40.0


def test_empty_dataframe_does_not_crash():
    p = profile_dataframe(pd.DataFrame({"a": pd.Series(dtype=float)}))
    assert p["dataset"]["row_count"] == 0
    assert p["dataset"]["quality_score"] is None
    assert _col(p, "a")["inferred_type"] == "empty"


def test_constant_and_empty_columns():
    df = pd.DataFrame({"const": ["same"] * 10, "empty": [None] * 10, "v": range(10)})
    p = profile_dataframe(df)
    assert _col(p, "const")["is_constant"]
    assert _col(p, "empty")["inferred_type"] == "empty" and not _col(p, "empty")["is_constant"]
    assert p["dataset"]["constant_columns"] == ["const"]
    assert p["dataset"]["empty_columns"] == ["empty"]


def test_near_constant():
    df = pd.DataFrame({"flag": ["A"] * 99 + ["B"]})
    c = _col(profile_dataframe(df), "flag")
    assert c["is_near_constant"] and not c["is_constant"]


def test_mixed_type_numeric_column():
    s = pd.Series(["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "unknown"] * 10)
    inf = infer_column_type(s)
    assert inf["type"] == "integer"
    assert inf["mixed_types"] and inf["invalid_count"] == 10
    assert inf["invalid_examples"] == ["unknown"]


def test_mostly_strings_not_numeric():
    s = pd.Series(["a", "b", "1", "c"] * 10)
    assert infer_column_type(s)["type"] == "categorical"


def test_large_numeric_range():
    df = pd.DataFrame({"x": [1e-9, 1.0, 1e6, 1e12, 1e15] * 4})
    st = _col(profile_dataframe(df), "x")["stats"]
    assert st["min"] == 1e-9 and st["max"] == 1e15
    assert all(v is None or np.isfinite(v) for v in st.values() if isinstance(v, (int, float)))


def test_numeric_stats_values():
    df = pd.DataFrame({"x": [1, 2, 3, 4, 100]})
    st = _col(profile_dataframe(df), "x")["stats"]
    assert st["median"] == 3 and st["mean"] == 22 and st["iqr_outliers"] == 1


def test_infinite_values_are_json_safe():
    df = pd.DataFrame({"x": [1.0, np.inf, -np.inf, 2.0]})
    json.dumps(profile_dataframe(df), allow_nan=False)


def test_high_cardinality_text_and_identifier():
    df = pd.DataFrame({"code": [f"C{i:05d}" for i in range(500)]})
    c = _col(profile_dataframe(df), "code")
    assert c["inferred_type"] == "text" and c["is_potential_identifier"]


def test_high_cardinality_categorical_warning():
    values = [f"cat{i}" for i in range(80)] * 10
    c = _col(profile_dataframe(pd.DataFrame({"c": values})), "c")
    assert c["inferred_type"] == "categorical"
    assert any("High-cardinality" in w for w in c["warnings"])


def test_prose_text_not_identifier():
    df = pd.DataFrame({"comment": [f"customer said thing number {i}" for i in range(100)]})
    assert not _col(profile_dataframe(df), "comment")["is_potential_identifier"]


def test_id_column_with_duplicates_warns():
    ids = list(range(100)) + [1, 2]
    c = _col(profile_dataframe(pd.DataFrame({"order_id": ids})), "order_id")
    assert c["is_potential_identifier"]
    assert any("not fully unique" in w for w in c["warnings"])


def test_datetime_inference_from_strings():
    s = pd.Series(pd.date_range("2024-01-01", periods=30).strftime("%Y-%m-%d"))
    c = _col(profile_dataframe(pd.DataFrame({"d": s})), "d")
    assert c["inferred_type"] == "datetime"
    assert c["stats"]["range_days"] == 29


def test_boolean_inference():
    assert infer_column_type(pd.Series([True, False, True]))["type"] == "boolean"
    assert infer_column_type(pd.Series(["yes", "no", "Yes"]))["type"] == "boolean"


def test_float_with_integral_values_is_integer():
    assert infer_column_type(pd.Series([1.0, 2.0, None, 4.0]))["type"] == "integer"


def test_quality_score_perfect_and_degraded():
    clean = pd.DataFrame({"a": range(20), "b": [f"x{i % 4}" for i in range(20)]})
    assert profile_dataframe(clean)["dataset"]["quality_score"] == 100.0

    dirty = clean.copy().astype(object)
    dirty.loc[:9, "b"] = None
    dirty = pd.concat([dirty, dirty.head(5)], ignore_index=True)
    q = profile_dataframe(dirty)["dataset"]["quality_breakdown"]
    assert q["score"] < 100
    assert abs(sum(c["points"] for c in q["components"].values()) - q["score"]) < 0.01
    assert q["components"]["completeness"]["ratio"] < 1
    assert q["components"]["uniqueness"]["ratio"] < 1


def test_quality_score_weights_sum_to_100():
    q = compute_quality_score(row_count=1, column_count=1, missing_cells=0, duplicate_rows=0,
                              columns=[{"missing_count": 0, "invalid_count": 0,
                                        "is_constant": False, "inferred_type": "integer"}])
    assert q["score"] == 100.0


def test_sampling_flag_and_full_counts():
    df = pd.DataFrame({"a": list(range(1000)), "b": [None] * 100 + list(range(900))})
    p = profile_dataframe(df, sample_rows=200)
    assert p["dataset"]["sampled"] and p["dataset"]["sample_size"] == 200
    assert _col(p, "b")["missing_count"] == 100  # from full data, not the sample


def test_profile_is_strict_json():
    df = pd.DataFrame({"a": [1, None], "d": pd.to_datetime(["2024-01-01", None])})
    json.dumps(profile_dataframe(df), allow_nan=False)


def test_schema_from_profile():
    p = profile_dataframe(pd.DataFrame({"a": [1, None], "b": ["x", "y"]}))
    schema = schema_from_profile(p["columns"])
    assert [c["name"] for c in schema] == ["a", "b"]
    assert schema[0]["nullable"] and not schema[1]["nullable"]
