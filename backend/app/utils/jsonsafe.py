"""Convert pandas/numpy values into strict-JSON-safe Python values."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd


def to_py(value: Any) -> Any:
    """Return a JSON-safe scalar: numpy -> python, NaN/inf/NaT -> None, timestamps -> ISO."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        if pd.isna(value):
            return None
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, (bool, int, str)):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def deep_to_py(obj: Any) -> Any:
    """Recursively apply :func:`to_py` through dicts, lists and tuples."""
    if isinstance(obj, dict):
        return {str(k): deep_to_py(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [deep_to_py(v) for v in obj]
    return to_py(obj)


def dataframe_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Rows as JSON-safe dicts (used for previews)."""
    return [
        {str(col): to_py(val) for col, val in zip(df.columns, row)}
        for row in df.itertuples(index=False, name=None)
    ]
