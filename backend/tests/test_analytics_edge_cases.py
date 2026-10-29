"""Edge cases for the analytics layer: empty, all-null, constant and single-row inputs must not crash."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.analytics.data_drift import detect_drift, population_stability_index
from app.analytics.profiling import profile_dataframe


def test_profile_all_null_column_does_not_crash():
    df = pd.DataFrame({"a": [None, None, None], "b": [1, 2, 3]})
    p = profile_dataframe(df)
    col = next(c for c in p["columns"] if c["column_name"] == "a")
    assert col["missing_count"] == 3


def test_profile_single_row():
    p = profile_dataframe(pd.DataFrame({"a": [1], "b": ["x"]}))
    assert p["dataset"]["row_count"] == 1


def test_profile_empty_frame_with_columns():
    p = profile_dataframe(pd.DataFrame({"a": [], "b": []}))
    assert p["dataset"]["row_count"] == 0


def test_psi_empty_inputs_return_undefined_not_error():
    res = population_stability_index(np.array([]), np.array([1.0, 2.0]))
    assert res["psi"] is None


def test_drift_identical_frames_report_no_error():
    df = pd.DataFrame({"x": np.arange(200.0), "c": ["a", "b"] * 100})
    prof = profile_dataframe(df)["columns"]
    res = detect_drift(df, df.copy(), reference_profiles=prof, current_profiles=prof)
    assert "summary" in res and "features" in res
