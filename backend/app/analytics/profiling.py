"""Dataset and column profiling.

Pure functions: DataFrame in, JSON-safe dicts out. No database or HTTP imports.

Semantic types
--------------
``integer``, ``float``, ``boolean``, ``datetime``, ``categorical``, ``text``, ``empty``.

Inference order for object (string) columns:
1. boolean tokens (true/false/yes/no/y/n/t/f)
2. numeric parse ratio >= ``NUMERIC_PARSE_THRESHOLD`` -> integer/float; failures are *invalid values*
3. date-like pattern and datetime parse ratio >= ``DATETIME_PARSE_THRESHOLD`` -> datetime
4. categorical if low cardinality, otherwise free text

Quality score (heuristic, 0-100, not a calibrated probability)
-------------------------------------------------------------
    completeness 40 * (1 - missing_cells / total_cells)
  + uniqueness   20 * (1 - duplicate_rows / rows)
  + validity     25 * (1 - mean over columns of invalid_values / non_null_values)
  + consistency  15 * (1 - constant_columns / columns)
"""

from __future__ import annotations

import re
import warnings
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd

from app.utils.jsonsafe import deep_to_py, to_py

NUMERIC_PARSE_THRESHOLD = 0.90
DATETIME_PARSE_THRESHOLD = 0.90
NEAR_CONSTANT_TOP_SHARE = 0.98
NEAR_CONSTANT_MIN_NON_NULL = 50
IDENTIFIER_MIN_UNIQUE_SHARE = 0.98
IDENTIFIER_MIN_ROWS = 20
CATEGORICAL_MAX_UNIQUE = 100
CATEGORICAL_MAX_UNIQUE_SHARE = 0.5
HIGH_CARDINALITY_WARNING = 50
TOP_VALUES_LIMIT = 20
INVALID_EXAMPLES_LIMIT = 5

QUALITY_WEIGHTS = {"completeness": 40.0, "uniqueness": 20.0, "validity": 25.0, "consistency": 15.0}

_BOOL_TOKENS = {"true", "false", "yes", "no", "y", "n", "t", "f"}
_TRUE_TOKENS = {"true", "yes", "y", "t"}
_DATE_LIKE = re.compile(r"^\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2})?.*)?$")
_ID_NAME = re.compile(r"(^id$|_id$|^id_|uuid|guid|(^|_)key$|_no$|_number$)", re.IGNORECASE)


# ------------------------------------------------------------------ type inference


def _is_integral(values: pd.Series) -> bool:
    finite = values[np.isfinite(values)]
    return bool(len(finite)) and bool(np.all(np.mod(finite, 1) == 0))


def infer_column_type(series: pd.Series) -> dict[str, Any]:
    """Infer a semantic type and report values that do not fit it.

    Returns ``{"type", "invalid_count", "invalid_examples", "mixed_types", "parsed"}`` where
    ``parsed`` is the non-null values converted to the inferred type (numeric/datetime) or None.
    """
    non_null = series.dropna()
    result: dict[str, Any] = {
        "type": "empty",
        "invalid_count": 0,
        "invalid_examples": [],
        "mixed_types": False,
        "parsed": None,
    }
    if non_null.empty:
        return result

    if pd.api.types.is_bool_dtype(series):
        result["type"] = "boolean"
        result["parsed"] = non_null.astype(bool)
        return result
    if pd.api.types.is_numeric_dtype(series):
        values = non_null.astype(float)
        result["type"] = "integer" if _is_integral(values) else "float"
        result["parsed"] = values
        return result
    if pd.api.types.is_datetime64_any_dtype(series):
        result["type"] = "datetime"
        result["parsed"] = pd.to_datetime(non_null)
        return result

    as_str = non_null.astype(str).str.strip()
    lowered = as_str.str.lower()
    if set(lowered.unique()) <= _BOOL_TOKENS:
        result["type"] = "boolean"
        result["parsed"] = lowered.isin(_TRUE_TOKENS)
        return result

    numeric = pd.to_numeric(as_str, errors="coerce")
    ratio = float(numeric.notna().mean())
    if ratio >= NUMERIC_PARSE_THRESHOLD:
        valid = numeric.dropna().astype(float)
        result["type"] = "integer" if _is_integral(valid) else "float"
        _record_invalid(result, as_str[numeric.isna()])
        result["parsed"] = valid
        return result

    sample = as_str.head(500)
    if sample.str.match(_DATE_LIKE).mean() >= DATETIME_PARSE_THRESHOLD:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(as_str, errors="coerce", format="mixed")
        if parsed.notna().mean() >= DATETIME_PARSE_THRESHOLD:
            result["type"] = "datetime"
            _record_invalid(result, as_str[parsed.isna()])
            result["parsed"] = parsed.dropna()
            return result

    n_unique = as_str.nunique()
    unique_share = n_unique / len(as_str)
    is_categorical = (
        n_unique <= CATEGORICAL_MAX_UNIQUE and unique_share <= CATEGORICAL_MAX_UNIQUE_SHARE
    ) or unique_share <= 0.05
    result["type"] = "categorical" if is_categorical else "text"
    return result


def _record_invalid(result: dict[str, Any], invalid: pd.Series) -> None:
    result["invalid_count"] = int(len(invalid))
    result["mixed_types"] = bool(len(invalid))
    result["invalid_examples"] = [str(v) for v in invalid.unique()[:INVALID_EXAMPLES_LIMIT]]


# ---------------------------------------------------------------- column profile


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 4) if whole else 0.0


def _numeric_stats(values: pd.Series) -> dict[str, Any]:
    v = values[np.isfinite(values)]
    if v.empty:
        return {}
    q = v.quantile([0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99])
    iqr = q[0.75] - q[0.25]
    lo, hi = q[0.25] - 1.5 * iqr, q[0.75] + 1.5 * iqr
    return {
        "min": v.min(),
        "max": v.max(),
        "mean": v.mean(),
        "median": q[0.5],
        "std": v.std(ddof=1) if len(v) > 1 else 0.0,
        "q01": q[0.01],
        "q05": q[0.05],
        "q25": q[0.25],
        "q75": q[0.75],
        "q95": q[0.95],
        "q99": q[0.99],
        "iqr": iqr,
        "zeros": int((v == 0).sum()),
        "negatives": int((v < 0).sum()),
        "iqr_outliers": int(((v < lo) | (v > hi)).sum()),
        "skewness": v.skew() if len(v) > 2 else None,
    }


def _top_values(values: pd.Series, limit: int = TOP_VALUES_LIMIT) -> dict[str, Any]:
    counts = values.astype(str).value_counts()
    total = int(counts.sum())
    top = counts.head(limit)
    return {
        "n_categories": int(len(counts)),
        "top_values": [
            {"value": k, "count": int(c), "pct": _pct(c, total)} for k, c in top.items()
        ],
        "other_count": int(total - top.sum()),
    }


def profile_column(
    series: pd.Series,
    *,
    position: int,
    full_missing_count: int | None = None,
    full_row_count: int | None = None,
) -> dict[str, Any]:
    """Profile one column. ``full_*`` let missing counts come from un-sampled data."""
    name = str(series.name)
    row_count = full_row_count if full_row_count is not None else len(series)
    missing = full_missing_count if full_missing_count is not None else int(series.isna().sum())
    non_null = series.dropna()
    inference = infer_column_type(series)
    sem_type = inference["type"]

    unique_count = int(non_null.astype(str).nunique()) if not non_null.empty else 0
    unique_pct = _pct(unique_count, len(non_null))
    col_warnings: list[str] = []

    stats: dict[str, Any] = {}
    parsed = inference["parsed"]
    if sem_type in ("integer", "float") and parsed is not None:
        stats = _numeric_stats(parsed)
    elif sem_type == "datetime" and parsed is not None and not parsed.empty:
        stats = {
            "min": parsed.min(),
            "max": parsed.max(),
            "range_days": (parsed.max() - parsed.min()).total_seconds() / 86400,
        }
    elif sem_type == "boolean" and parsed is not None:
        true_count = int(parsed.sum())
        stats = {
            "true_count": true_count,
            "false_count": int(len(parsed) - true_count),
            "true_pct": _pct(true_count, len(parsed)),
        }
    elif sem_type == "categorical":
        stats = _top_values(non_null)
        if stats["n_categories"] > HIGH_CARDINALITY_WARNING:
            col_warnings.append(
                f"High-cardinality categorical ({stats['n_categories']} categories)."
            )
    elif sem_type == "text":
        lengths = non_null.astype(str).str.len()
        stats = {
            "min_length": lengths.min(),
            "max_length": lengths.max(),
            "mean_length": lengths.mean(),
            **_top_values(non_null, limit=5),
        }

    if inference["mixed_types"]:
        col_warnings.append(
            f"{inference['invalid_count']} value(s) do not match inferred type '{sem_type}', "
            f"e.g. {inference['invalid_examples']}."
        )

    is_constant = sem_type != "empty" and unique_count == 1
    top_share = (
        non_null.astype(str).value_counts(normalize=True).iloc[0] if not non_null.empty else 0.0
    )
    is_near_constant = (
        not is_constant
        and len(non_null) >= NEAR_CONSTANT_MIN_NON_NULL
        and top_share >= NEAR_CONSTANT_TOP_SHARE
    )

    looks_like_id_name = bool(_ID_NAME.search(name))
    prose_like = sem_type == "text" and non_null.astype(str).str.contains(r"\s").mean() > 0.5
    is_identifier = not prose_like and (
        sem_type in ("integer", "text", "categorical")
        and len(non_null) >= IDENTIFIER_MIN_ROWS
        and unique_count / max(len(non_null), 1) >= IDENTIFIER_MIN_UNIQUE_SHARE
    ) or (looks_like_id_name and unique_pct >= 90.0 and len(non_null) >= IDENTIFIER_MIN_ROWS)
    if looks_like_id_name and is_identifier and unique_pct < 100.0:
        col_warnings.append(
            f"Identifier-like column is not fully unique ({unique_pct:.2f}% unique); "
            "duplicate keys may exist."
        )
    if sem_type == "empty":
        col_warnings.append("Column is entirely missing.")
    if is_constant:
        col_warnings.append("Column has a single distinct value.")
    if is_near_constant:
        col_warnings.append(f"Near-constant: top value covers {top_share * 100:.2f}% of rows.")

    return deep_to_py(
        {
            "column_name": name,
            "position": position,
            "inferred_type": sem_type,
            "pandas_dtype": str(series.dtype),
            "missing_count": missing,
            "missing_pct": _pct(missing, row_count),
            "unique_count": unique_count,
            "unique_pct": unique_pct,
            "is_constant": is_constant,
            "is_near_constant": is_near_constant,
            "is_potential_identifier": bool(is_identifier),
            "invalid_count": inference["invalid_count"],
            "invalid_examples": inference["invalid_examples"],
            "stats": stats,
            "warnings": col_warnings,
        }
    )


# --------------------------------------------------------------- dataset profile


def compute_quality_score(
    *,
    row_count: int,
    column_count: int,
    missing_cells: int,
    duplicate_rows: int,
    columns: list[dict[str, Any]],
) -> dict[str, Any]:
    """Explainable heuristic quality score. See module docstring for the formula."""
    if row_count == 0 or column_count == 0:
        return {"score": None, "components": {}, "note": "No data to score."}

    total_cells = row_count * column_count
    invalid_fracs = []
    for c in columns:
        non_null = row_count - c["missing_count"]
        invalid_fracs.append(c["invalid_count"] / non_null if non_null > 0 else 0.0)
    constant = sum(1 for c in columns if c["is_constant"] or c["inferred_type"] == "empty")

    ratios = {
        "completeness": 1 - missing_cells / total_cells,
        "uniqueness": 1 - duplicate_rows / row_count,
        "validity": 1 - (sum(invalid_fracs) / len(invalid_fracs) if invalid_fracs else 0.0),
        "consistency": 1 - constant / column_count,
    }
    components = {
        k: {
            "weight": QUALITY_WEIGHTS[k],
            "ratio": round(max(0.0, min(1.0, r)), 6),
            "points": round(QUALITY_WEIGHTS[k] * max(0.0, min(1.0, r)), 4),
        }
        for k, r in ratios.items()
    }
    score = round(sum(c["points"] for c in components.values()), 2)
    return {
        "score": score,
        "components": components,
        "note": (
            "Heuristic weighted score (completeness 40, uniqueness 20, validity 25, "
            "consistency 15). It summarises observed evidence; it is not a calibrated "
            "probability of correctness or business impact."
        ),
    }


def profile_dataframe(
    df: pd.DataFrame, *, sample_rows: int = 200_000, random_state: int = 42
) -> dict[str, Any]:
    """Profile a DataFrame.

    Dataset-level counts (rows, duplicates, missing cells) always use the full data.
    Column distribution statistics use a reproducible random sample when the frame
    has more than ``sample_rows`` rows; ``sampled`` is then True.
    """
    row_count, column_count = df.shape
    sampled = row_count > sample_rows
    work = df.sample(n=sample_rows, random_state=random_state) if sampled else df

    try:
        duplicate_rows = int(df.duplicated().sum()) if row_count else 0
    except TypeError:  # unhashable cell values
        duplicate_rows = int(df.astype(str).duplicated().sum())
    missing_per_col = df.isna().sum()
    missing_cells = int(missing_per_col.sum())

    columns = [
        profile_column(
            work[col],
            position=i,
            full_missing_count=int(missing_per_col[col]),
            full_row_count=row_count,
        )
        for i, col in enumerate(df.columns)
    ]

    quality = compute_quality_score(
        row_count=row_count,
        column_count=column_count,
        missing_cells=missing_cells,
        duplicate_rows=duplicate_rows,
        columns=columns,
    )
    dataset = {
        "row_count": row_count,
        "column_count": column_count,
        "duplicate_rows": duplicate_rows,
        "duplicate_pct": _pct(duplicate_rows, row_count),
        "missing_cells": missing_cells,
        "missing_pct": _pct(missing_cells, row_count * column_count),
        "memory_bytes": int(df.memory_usage(deep=True).sum()),
        "type_distribution": dict(Counter(c["inferred_type"] for c in columns)),
        "constant_columns": [c["column_name"] for c in columns if c["is_constant"]],
        "near_constant_columns": [c["column_name"] for c in columns if c["is_near_constant"]],
        "empty_columns": [c["column_name"] for c in columns if c["inferred_type"] == "empty"],
        "potential_identifiers": [
            c["column_name"] for c in columns if c["is_potential_identifier"]
        ],
        "mixed_type_columns": [c["column_name"] for c in columns if c["invalid_count"] > 0],
        "quality_score": quality["score"],
        "quality_breakdown": quality,
        "sampled": sampled,
        "sample_size": len(work),
    }
    return {"dataset": deep_to_py(dataset), "columns": columns}


def schema_from_profile(columns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact schema snapshot derived from column profiles."""
    return [
        {
            "name": c["column_name"],
            "position": c["position"],
            "pandas_dtype": c["pandas_dtype"],
            "inferred_type": c["inferred_type"],
            "nullable": c["missing_count"] > 0,
            "missing_pct": c["missing_pct"],
            "unique_pct": c["unique_pct"],
            "is_potential_identifier": c["is_potential_identifier"],
        }
        for c in columns
    ]


__all__ = [
    "compute_quality_score",
    "infer_column_type",
    "profile_column",
    "profile_dataframe",
    "schema_from_profile",
    "to_py",
]
