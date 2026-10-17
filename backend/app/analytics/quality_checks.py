"""Configurable data-quality validation engine.

Pure functions: a DataFrame plus rule dicts in, JSON-safe result dicts out.

Rule dict
---------
``{"id"?, "name", "rule_type", "column"?, "params": {...}, "severity", "enabled"?}``
Validate with :func:`validate_rule` before persisting; :func:`run_quality_checks`
validates again and reports invalid rules as ``skipped`` instead of crashing.

Rule types (12)
---------------
==========================  =========  ==========================================
rule_type                   column     params
==========================  =========  ==========================================
missing_values              optional*  max_missing_pct, warn_missing_pct?
duplicate_rows              -          max_duplicate_pct=0, subset?
unique                      required   (none)
required_columns            -          columns
unexpected_columns          -          allowed
data_type                   required   expected_type, max_invalid_pct=0
numeric_range               required   min? max? (one required), max_violation_pct=0
allowed_values              required   values, case_sensitive=True, max_violation_pct=0
date_range                  required   min?, max?, allow_future=True, max_invalid_pct=0
constant_column             optional*  (none)
schema_match                -          (uses the baseline column profiles in context)
business_rule               -          expression, max_violation_pct=0
referential_integrity       required   reference_values | (reference_dataset, reference_column)
==========================  =========  ==========================================

``*`` a rule with no column is expanded to one result per column.

Outcomes
--------
``fail`` (hard failure: threshold exceeded), ``warn`` (soft threshold exceeded or a
non-breaking finding), ``pass``, ``info`` and ``skipped`` (rule could not be evaluated:
column absent, no reference data, invalid rule, etc. — never silently counted as pass).

Result severity: the rule's severity for ``fail``; one level lower for ``warn``
(minimum ``info``); ``info`` for pass/info/skipped.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.expressions import ExpressionError, compile_expression, evaluate
from app.analytics.profiling import infer_column_type, profile_dataframe
from app.analytics.schema_drift import SEVERITY_ORDER, compare_schemas
from app.utils.jsonsafe import deep_to_py

RULE_TYPES = (
    "missing_values", "duplicate_rows", "unique", "required_columns", "unexpected_columns",
    "data_type", "numeric_range", "allowed_values", "date_range", "constant_column",
    "schema_match", "business_rule", "referential_integrity",
)
SEVERITIES = tuple(SEVERITY_ORDER)
SEMANTIC_TYPES = ("integer", "float", "numeric", "boolean", "datetime", "categorical", "text")
COLUMN_REQUIRED = {"unique", "data_type", "numeric_range", "allowed_values", "date_range",
                   "referential_integrity"}
COLUMN_OPTIONAL = {"missing_values", "constant_column"}
EXAMPLES_LIMIT = 10


class RuleValidationError(ValueError):
    """A rule definition is invalid."""


# ------------------------------------------------------------------ validation


def _num(params: dict[str, Any], key: str, *, lo: float = 0.0, hi: float = 100.0,
         required: bool = False, default: float | None = None) -> float | None:
    if key not in params or params[key] is None:
        if required:
            raise RuleValidationError(f"Parameter '{key}' is required.")
        return default
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleValidationError(f"Parameter '{key}' must be a number.")
    if not lo <= value <= hi:
        raise RuleValidationError(f"Parameter '{key}' must be between {lo} and {hi}.")
    return float(value)


def _str_list(params: dict[str, Any], key: str, *, required: bool = True) -> list[str] | None:
    value = params.get(key)
    if value is None:
        if required:
            raise RuleValidationError(f"Parameter '{key}' is required.")
        return None
    if not isinstance(value, list) or not value or not all(isinstance(v, (str, int, float, bool)) for v in value):
        raise RuleValidationError(f"Parameter '{key}' must be a non-empty list of values.")
    return [str(v) for v in value]


def _timestamp(params: dict[str, Any], key: str) -> pd.Timestamp | None:
    if params.get(key) in (None, ""):
        return None
    try:
        return pd.Timestamp(params[key])
    except (ValueError, TypeError):
        raise RuleValidationError(f"Parameter '{key}' must be an ISO date, e.g. 2025-01-31.") from None


def validate_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Return a normalised copy of ``rule`` or raise :class:`RuleValidationError`."""
    rule_type = rule.get("rule_type")
    if rule_type not in RULE_TYPES:
        raise RuleValidationError(f"Unknown rule_type '{rule_type}'. Allowed: {', '.join(RULE_TYPES)}.")
    severity = rule.get("severity", "medium")
    if severity not in SEVERITIES:
        raise RuleValidationError(f"severity must be one of {', '.join(SEVERITIES)}.")
    column = rule.get("column")
    if column is not None and (not isinstance(column, str) or not column.strip()):
        raise RuleValidationError("column must be a non-empty string when given.")
    if rule_type in COLUMN_REQUIRED and not column:
        raise RuleValidationError(f"Rule type '{rule_type}' requires a column.")
    if column and rule_type not in COLUMN_REQUIRED | COLUMN_OPTIONAL:
        raise RuleValidationError(f"Rule type '{rule_type}' does not take a column.")
    params = dict(rule.get("params") or {})
    if not isinstance(params, dict):
        raise RuleValidationError("params must be an object.")

    if rule_type == "missing_values":
        mx = _num(params, "max_missing_pct", required=True)
        wn = _num(params, "warn_missing_pct")
        if wn is not None and wn > mx:
            raise RuleValidationError("warn_missing_pct must not exceed max_missing_pct.")
    elif rule_type == "duplicate_rows":
        params["max_duplicate_pct"] = _num(params, "max_duplicate_pct", default=0.0)
        if params.get("subset") is not None:
            params["subset"] = _str_list(params, "subset")
    elif rule_type == "required_columns":
        params["columns"] = _str_list(params, "columns")
    elif rule_type == "unexpected_columns":
        params["allowed"] = _str_list(params, "allowed")
    elif rule_type == "data_type":
        if params.get("expected_type") not in SEMANTIC_TYPES:
            raise RuleValidationError(f"expected_type must be one of {', '.join(SEMANTIC_TYPES)}.")
        params["max_invalid_pct"] = _num(params, "max_invalid_pct", default=0.0)
    elif rule_type == "numeric_range":
        lo = _num(params, "min", lo=-np.inf, hi=np.inf)
        hi = _num(params, "max", lo=-np.inf, hi=np.inf)
        if lo is None and hi is None:
            raise RuleValidationError("numeric_range needs at least one of 'min' or 'max'.")
        if lo is not None and hi is not None and lo > hi:
            raise RuleValidationError("'min' must not exceed 'max'.")
        params["max_violation_pct"] = _num(params, "max_violation_pct", default=0.0)
    elif rule_type == "allowed_values":
        params["values"] = _str_list(params, "values")
        params["case_sensitive"] = bool(params.get("case_sensitive", True))
        params["max_violation_pct"] = _num(params, "max_violation_pct", default=0.0)
    elif rule_type == "date_range":
        lo, hi = _timestamp(params, "min"), _timestamp(params, "max")
        if lo is not None and hi is not None and lo > hi:
            raise RuleValidationError("'min' must not be after 'max'.")
        params["allow_future"] = bool(params.get("allow_future", True))
        params["max_invalid_pct"] = _num(params, "max_invalid_pct", default=0.0)
    elif rule_type == "business_rule":
        try:
            compile_expression(params.get("expression", ""))
        except ExpressionError as exc:
            raise RuleValidationError(f"Invalid expression: {exc}") from None
        params["max_violation_pct"] = _num(params, "max_violation_pct", default=0.0)
    elif rule_type == "referential_integrity":
        if params.get("reference_values") is not None:
            params["reference_values"] = _str_list(params, "reference_values")
        elif not (params.get("reference_dataset") and params.get("reference_column")):
            raise RuleValidationError(
                "Provide 'reference_values' or both 'reference_dataset' and 'reference_column'."
            )

    name = rule.get("name") or (f"{rule_type}:{column}" if column else rule_type)
    return {**rule, "name": str(name)[:200], "rule_type": rule_type, "column": column,
            "params": params, "severity": severity, "enabled": rule.get("enabled", True)}


# --------------------------------------------------------------------- helpers


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 4) if whole else 0.0


def _warn_severity(severity: str) -> str:
    return SEVERITY_ORDER[max(0, SEVERITY_ORDER.index(severity) - 1)]


def _result(rule: dict[str, Any], outcome: str, *, message: str, observed: Any = None,
            expected: str | None = None, affected_count: int | None = None,
            affected_pct: float | None = None, evidence: dict[str, Any] | None = None,
            columns: list[str] | None = None) -> dict[str, Any]:
    severity = (rule["severity"] if outcome == "fail"
                else _warn_severity(rule["severity"]) if outcome == "warn" else "info")
    cols = columns if columns is not None else ([rule["column"]] if rule.get("column") else [])
    return deep_to_py({
        "rule_id": rule.get("id"),
        "rule_name": rule["name"],
        "rule_type": rule["rule_type"],
        "rule_severity": rule["severity"],
        "columns": cols,
        "outcome": outcome,
        "severity": severity,
        "observed": observed,
        "expected": expected,
        "affected_count": affected_count,
        "affected_pct": affected_pct,
        "evidence": evidence or {},
        "message": message,
    })


def _skip(rule: dict[str, Any], reason: str) -> dict[str, Any]:
    return _result(rule, "skipped", message=reason, evidence={"reason": reason})


def _examples(values: pd.Series) -> list[Any]:
    return [str(v) for v in pd.Series(values).astype(str).unique()[:EXAMPLES_LIMIT]]


def _value_counts(values: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in values.astype(str).value_counts().head(EXAMPLES_LIMIT).items()}


def _row_examples(mask: pd.Series) -> list[int]:
    """Zero-based row positions (not labels) of the first matching rows."""
    return [int(i) for i in np.flatnonzero(mask.to_numpy())[:EXAMPLES_LIMIT]]


def _threshold_outcome(violation_pct: float, max_pct: float, count: int) -> str:
    if count == 0:
        return "pass"
    return "fail" if violation_pct > max_pct else "warn"


def _coerce(series: pd.Series, expected: str) -> tuple[pd.Series, pd.Series]:
    """Return ``(parsed_non_null, invalid_mask_over_non_null)`` for an expected type."""
    non_null = series.dropna()
    if expected in ("integer", "float", "numeric"):
        if pd.api.types.is_bool_dtype(series):
            return non_null, pd.Series(True, index=non_null.index)
        parsed = pd.to_numeric(non_null if pd.api.types.is_numeric_dtype(series)
                               else non_null.astype(str).str.strip(), errors="coerce")
        invalid = parsed.isna()
        if expected == "integer":
            ok = parsed.notna()
            invalid = invalid | (ok & (np.mod(parsed.where(ok, 0), 1) != 0))
        return parsed, invalid
    if expected == "datetime":
        if pd.api.types.is_datetime64_any_dtype(series):
            return non_null, pd.Series(False, index=non_null.index)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(non_null.astype(str).str.strip(), errors="coerce", format="mixed")
        return parsed, parsed.isna()
    if expected == "boolean":
        if pd.api.types.is_bool_dtype(series):
            return non_null, pd.Series(False, index=non_null.index)
        tokens = non_null.astype(str).str.strip().str.lower()
        return tokens, ~tokens.isin({"true", "false", "yes", "no", "y", "n", "t", "f", "1", "0"})
    return non_null, pd.Series(False, index=non_null.index)  # categorical/text accept any string


# ---------------------------------------------------------------------- checks


def _check_missing(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    col, p = rule["column"], rule["params"]
    n = len(df)
    missing = int(df[col].isna().sum())
    pct = _pct(missing, n)
    mx, wn = p["max_missing_pct"], p.get("warn_missing_pct")
    if pct > mx:
        outcome = "fail"
    elif wn is not None and pct > wn:
        outcome = "warn"
    else:
        outcome = "pass"
    expected = f"missing <= {mx}%" + (f" (warn above {wn}%)" if wn is not None else "")
    return _result(rule, outcome, observed={"missing_pct": pct}, expected=expected,
                   affected_count=missing, affected_pct=pct,
                   evidence={"missing_count": missing, "row_count": n,
                             "example_rows": _row_examples(df[col].isna())},
                   message=f"'{col}' is {pct:.2f}% missing ({missing} of {n} rows); limit {mx}%.")


def _check_duplicate_rows(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    p = rule["params"]
    subset = p.get("subset")
    if subset and (absent := [c for c in subset if c not in df.columns]):
        return _skip(rule, f"Subset column(s) not present: {absent}.")
    try:
        dup = df.duplicated(subset=subset, keep="first")
        dup_all = df.duplicated(subset=subset, keep=False)
    except TypeError:
        frame = df.astype(str)
        dup = frame.duplicated(subset=subset, keep="first")
        dup_all = frame.duplicated(subset=subset, keep=False)
    count, n = int(dup.sum()), len(df)
    pct = _pct(count, n)
    return _result(
        rule, _threshold_outcome(pct, p["max_duplicate_pct"], count),
        columns=subset or [], observed={"duplicate_rows": count, "duplicate_pct": pct},
        expected=f"duplicate rows <= {p['max_duplicate_pct']}%",
        affected_count=count, affected_pct=pct,
        evidence={"extra_copies": count, "rows_involved": int(dup_all.sum()),
                  "example_rows": _row_examples(dup), "subset": subset or "all columns"},
        message=f"{count} duplicate row(s) ({pct:.2f}%) found"
                + (f" on {subset}." if subset else " across all columns."))


def _check_unique(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    col = rule["column"]
    s = df[col].dropna()
    dup_mask = s.astype(str).duplicated(keep=False)
    rows = int(dup_mask.sum())
    keys = s[dup_mask].astype(str)
    n_keys = int(keys.nunique())
    pct = _pct(rows, len(df))
    return _result(
        rule, "fail" if rows else "pass",
        observed={"duplicated_values": n_keys, "rows_with_duplicated_values": rows},
        expected="every non-null value is unique",
        affected_count=rows, affected_pct=pct,
        evidence={"example_values": _value_counts(keys), "nulls_ignored": int(df[col].isna().sum())},
        message=(f"'{col}' has {n_keys} value(s) repeated across {rows} rows."
                 if rows else f"'{col}' is unique."))


def _check_required_columns(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    required = rule["params"]["columns"]
    missing = [c for c in required if c not in df.columns]
    return _result(rule, "fail" if missing else "pass", columns=missing,
                   observed={"missing_columns": missing}, expected=f"columns present: {required}",
                   affected_count=len(missing),
                   evidence={"required": required, "present": [str(c) for c in df.columns]},
                   message=(f"Missing required column(s): {missing}." if missing
                            else "All required columns are present."))


def _check_unexpected_columns(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    allowed = set(rule["params"]["allowed"])
    extra = [str(c) for c in df.columns if str(c) not in allowed]
    return _result(rule, "warn" if extra else "pass", columns=extra,
                   observed={"unexpected_columns": extra}, expected="only the allowed columns",
                   affected_count=len(extra), evidence={"allowed": sorted(allowed)},
                   message=(f"Unexpected column(s): {extra}." if extra else "No unexpected columns."))


def _check_data_type(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    col, p = rule["column"], rule["params"]
    expected = p["expected_type"]
    _, invalid = _coerce(df[col], expected)
    count = int(invalid.sum())
    non_null = int(df[col].notna().sum())
    pct = _pct(count, non_null)
    bad = df[col].dropna()[invalid]
    return _result(
        rule, _threshold_outcome(pct, p["max_invalid_pct"], count),
        observed={"invalid_values": count, "invalid_pct_of_non_null": pct,
                  "pandas_dtype": str(df[col].dtype), "inferred_type": infer_column_type(df[col])["type"]},
        expected=f"values parse as {expected} (invalid <= {p['max_invalid_pct']}% of non-null)",
        affected_count=count, affected_pct=_pct(count, len(df)),
        evidence={"invalid_examples": _value_counts(bad)},
        message=(f"{count} value(s) in '{col}' are not valid {expected} ({pct:.2f}% of non-null), "
                 f"e.g. {_examples(bad)[:5]}." if count else f"All values in '{col}' are valid {expected}."))


def _check_numeric_range(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    col, p = rule["column"], rule["params"]
    parsed, invalid = _coerce(df[col], "numeric")
    valid = parsed[~invalid].astype(float)
    lo, hi = p.get("min"), p.get("max")
    mask = pd.Series(False, index=valid.index)
    if lo is not None:
        mask |= valid < lo
    if hi is not None:
        mask |= valid > hi
    count = int(mask.sum())
    pct = _pct(count, len(valid))
    bounds = " and ".join(x for x in [f">= {lo}" if lo is not None else "",
                                      f"<= {hi}" if hi is not None else ""] if x)
    return _result(
        rule, _threshold_outcome(pct, p["max_violation_pct"], count),
        observed={"out_of_range": count, "observed_min": valid.min() if len(valid) else None,
                  "observed_max": valid.max() if len(valid) else None},
        expected=f"values {bounds}", affected_count=count, affected_pct=_pct(count, len(df)),
        evidence={"example_values": _examples(valid[mask]), "non_numeric_excluded": int(invalid.sum()),
                  "example_rows": _row_examples(pd.Series(df.index.isin(valid[mask].index), index=df.index))},
        message=(f"{count} value(s) in '{col}' fall outside {bounds}."
                 + (f" {int(invalid.sum())} non-numeric value(s) were excluded and are covered by data-type checks."
                    if int(invalid.sum()) else "")
                 if count else f"All numeric values in '{col}' are within {bounds}."))


def _check_allowed_values(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    col, p = rule["column"], rule["params"]
    s = df[col].dropna().astype(str).str.strip()
    allowed = p["values"]
    if p["case_sensitive"]:
        bad = s[~s.isin(allowed)]
    else:
        bad = s[~s.str.lower().isin({v.lower() for v in allowed})]
    count = len(bad)
    pct = _pct(count, len(s))
    return _result(
        rule, _threshold_outcome(pct, p["max_violation_pct"], count),
        observed={"invalid_values": count, "distinct_invalid": int(bad.nunique())},
        expected=f"values in {allowed}", affected_count=count, affected_pct=_pct(count, len(df)),
        evidence={"unexpected_value_counts": _value_counts(bad)},
        message=(f"{count} value(s) in '{col}' ({pct:.2f}% of non-null) are not in the allowed set: "
                 f"{list(_value_counts(bad))[:5]}." if count else f"All values in '{col}' are allowed."))


def _check_date_range(df: pd.DataFrame, rule: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    col, p = rule["column"], rule["params"]
    parsed, invalid = _coerce(df[col], "datetime")
    dates = parsed[~invalid]
    if getattr(dates.dt, "tz", None) is not None:
        dates = dates.dt.tz_convert(None)
    lo, hi = _timestamp(p, "min"), _timestamp(p, "max")
    now = pd.Timestamp(ctx.get("now") or pd.Timestamp.now())
    mask = pd.Series(False, index=dates.index)
    if lo is not None:
        mask |= dates < lo
    if hi is not None:
        mask |= dates > hi
    future = dates > now
    if not p["allow_future"]:
        mask |= future
    n_invalid, n_out = int(invalid.sum()), int(mask.sum())
    non_null = int(df[col].notna().sum())
    invalid_pct = _pct(n_invalid, non_null)
    if n_invalid and invalid_pct > p["max_invalid_pct"]:
        outcome = "fail"
    elif n_out:
        outcome = "fail"
    elif n_invalid:
        outcome = "warn"
    else:
        outcome = "pass"
    bounds = ", ".join(x for x in [f">= {lo.date()}" if lo is not None else "",
                                   f"<= {hi.date()}" if hi is not None else "",
                                   "not in the future" if not p["allow_future"] else ""] if x) or "parseable dates"
    return _result(
        rule, outcome,
        observed={"unparseable": n_invalid, "out_of_range": n_out,
                  "observed_min": dates.min() if len(dates) else None,
                  "observed_max": dates.max() if len(dates) else None},
        expected=bounds, affected_count=n_invalid + n_out, affected_pct=_pct(n_invalid + n_out, len(df)),
        evidence={"unparseable_examples": _value_counts(df[col].dropna()[invalid]),
                  "out_of_range_examples": _examples(dates[mask]), "reference_now": now},
        message=(f"'{col}': {n_invalid} unparseable date(s), {n_out} outside {bounds}."
                 if outcome != "pass" else f"All dates in '{col}' are valid ({bounds})."))


def _check_constant(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    col = rule["column"]
    s = df[col].dropna()
    if s.empty:
        return _result(rule, "warn", observed={"distinct_values": 0}, expected="more than one distinct value",
                       message=f"'{col}' is entirely missing.", evidence={"all_missing": True})
    distinct = int(s.astype(str).nunique())
    return _result(rule, "warn" if distinct == 1 else "pass", observed={"distinct_values": distinct},
                   expected="more than one distinct value",
                   evidence={"value": str(s.iloc[0])} if distinct == 1 else {},
                   message=(f"'{col}' holds a single value ('{s.iloc[0]}')." if distinct == 1
                            else f"'{col}' has {distinct} distinct values."))


def _check_schema_match(df: pd.DataFrame, rule: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    baseline = ctx.get("baseline_columns")
    if not baseline:
        return _skip(rule, "No baseline schema available; set a baseline to enable this check.")
    current = ctx.get("current_columns") or profile_dataframe(df)["columns"]
    diff = compare_schemas(baseline, current)
    breaking = [c for c in diff["changes"] if c["breaking"]]
    other = [c for c in diff["changes"] if not c["breaking"] and c["severity"] != "info"]
    outcome = "fail" if breaking else "warn" if other else "pass"
    cols = sorted({col for c in breaking + other for col in c["columns"]})
    return _result(
        rule, outcome, columns=cols,
        observed={"breaking_changes": len(breaking), "non_breaking_changes": len(other)},
        expected="schema compatible with baseline", affected_count=len(breaking) + len(other),
        evidence={"changes": [{k: c[k] for k in ("change_type", "columns", "severity", "breaking", "explanation")}
                              for c in breaking + other][:25]},
        message=(f"{len(breaking)} breaking and {len(other)} other schema change(s) vs the baseline."
                 if outcome != "pass" else "Schema matches the baseline."))


def _check_business_rule(df: pd.DataFrame, rule: dict[str, Any], _ctx: dict[str, Any]) -> dict[str, Any]:
    p = rule["params"]
    expr = compile_expression(p["expression"])
    absent = [c for c in expr.columns if c not in df.columns]
    if absent:
        return _skip(rule, f"Column(s) used by the rule are not present: {absent}.")
    try:
        res = evaluate(expr, df)
    except ExpressionError as exc:
        return _skip(rule, f"Rule could not be evaluated: {exc}")
    violations = res["evaluable"] & ~res["passed"]
    count, evaluable = int(violations.sum()), int(res["evaluable"].sum())
    pct = _pct(count, evaluable)
    not_eval = len(df) - evaluable
    return _result(
        rule, _threshold_outcome(pct, p["max_violation_pct"], count), columns=list(expr.columns),
        observed={"violations": count, "violation_pct_of_evaluable": pct, "not_evaluable": not_eval},
        expected=p.get("description") or p["expression"],
        affected_count=count, affected_pct=_pct(count, len(df)),
        evidence={"expression": p["expression"], "example_rows": _row_examples(violations),
                  "example_records": df.loc[violations, list(expr.columns)].head(5).to_dict("records")},
        message=(f"{count} row(s) ({pct:.2f}% of evaluable rows) violate: {p['expression']}"
                 + (f"; {not_eval} row(s) not evaluable due to missing values." if not_eval else "")
                 if count else f"All evaluable rows satisfy: {p['expression']}"))


def _check_referential(df: pd.DataFrame, rule: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    col, p = rule["column"], rule["params"]
    if p.get("reference_values") is not None:
        ref = set(p["reference_values"])
        ref_label = f"{len(ref)} listed values"
    else:
        frames = ctx.get("reference_frames") or {}
        frame = frames.get(p["reference_dataset"])
        if frame is None:
            return _skip(rule, f"Reference dataset '{p['reference_dataset']}' is not available.")
        if p["reference_column"] not in frame.columns:
            return _skip(rule, f"Reference column '{p['reference_column']}' not found.")
        ref = set(frame[p["reference_column"]].dropna().astype(str).str.strip())
        ref_label = f"{p['reference_dataset']}.{p['reference_column']}"
    s = df[col].dropna().astype(str).str.strip()
    orphans = s[~s.isin(ref)]
    count = len(orphans)
    return _result(
        rule, "fail" if count else "pass",
        observed={"orphan_rows": count, "distinct_orphans": int(orphans.nunique())},
        expected=f"every value exists in {ref_label}", affected_count=count,
        affected_pct=_pct(count, len(df)),
        evidence={"orphan_examples": _value_counts(orphans), "reference": ref_label},
        message=(f"{count} row(s) in '{col}' reference {orphans.nunique()} value(s) missing from {ref_label}."
                 if count else f"All values in '{col}' exist in {ref_label}."))


_CHECKS = {
    "missing_values": _check_missing, "duplicate_rows": _check_duplicate_rows, "unique": _check_unique,
    "required_columns": _check_required_columns, "unexpected_columns": _check_unexpected_columns,
    "data_type": _check_data_type, "numeric_range": _check_numeric_range,
    "allowed_values": _check_allowed_values, "date_range": _check_date_range,
    "constant_column": _check_constant, "schema_match": _check_schema_match,
    "business_rule": _check_business_rule, "referential_integrity": _check_referential,
}


# ------------------------------------------------------------------ public API


def run_quality_checks(df: pd.DataFrame, rules: list[dict[str, Any]],
                       context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run every enabled rule against ``df``.

    ``context`` may hold ``baseline_columns`` / ``current_columns`` (column profiles),
    ``reference_frames`` ({dataset name: DataFrame}) and ``now`` (for date checks).
    """
    ctx = context or {}
    results: list[dict[str, Any]] = []
    for raw in rules:
        if not raw.get("enabled", True):
            continue
        try:
            rule = validate_rule(raw)
        except RuleValidationError as exc:
            fallback = {"name": raw.get("name") or str(raw.get("rule_type")), "rule_type": str(raw.get("rule_type")),
                        "column": raw.get("column"), "severity": "info", "id": raw.get("id")}
            results.append(_skip(fallback, f"Invalid rule: {exc}"))
            continue
        targets = ([str(c) for c in df.columns]
                   if rule["rule_type"] in COLUMN_OPTIONAL and not rule["column"] else [rule["column"]])
        for target in targets:
            r = {**rule, "column": target} if target else rule
            if target and target not in df.columns:
                results.append(_skip(r, f"Column '{target}' is not present in this dataset."))
                continue
            results.append(_CHECKS[rule["rule_type"]](df, r, ctx))
    return results


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate outcomes into an evidence-backed health status.

    ``failing``  — at least one ``fail`` with severity high or critical
    ``degraded`` — any other ``fail`` or any ``warn``
    ``healthy``  — no fails or warnings (skipped checks are listed, not hidden)
    """
    outcomes = {o: 0 for o in ("pass", "fail", "warn", "info", "skipped")}
    failed_by_severity = {s: 0 for s in SEVERITY_ORDER}
    for r in results:
        outcomes[r["outcome"]] += 1
        if r["outcome"] == "fail":
            failed_by_severity[r["severity"]] += 1
    serious = failed_by_severity["high"] + failed_by_severity["critical"]
    status = "failing" if serious else "degraded" if outcomes["fail"] or outcomes["warn"] else "healthy"
    issues = sorted((r for r in results if r["outcome"] in ("fail", "warn")),
                    key=lambda r: (r["outcome"] != "fail", -SEVERITY_ORDER.index(r["severity"]),
                                   -(r["affected_pct"] or 0)))
    evaluated = outcomes["pass"] + outcomes["fail"] + outcomes["warn"]
    return {
        "status": status,
        "total_checks": len(results),
        "outcomes": outcomes,
        "failed_by_severity": failed_by_severity,
        "pass_rate_pct": _pct(outcomes["pass"], evaluated),
        "top_issues": [{k: r[k] for k in ("rule_name", "rule_type", "columns", "outcome", "severity", "message")}
                       for r in issues[:10]],
        "skipped": [{"rule_name": r["rule_name"], "reason": r["message"]}
                    for r in results if r["outcome"] == "skipped"],
        "status_definition": "failing = high/critical hard failure; degraded = other failures or warnings; "
                             "healthy = no failures or warnings among evaluated checks.",
    }


def suggest_rules_from_profile(columns: list[dict[str, Any]], dataset_metrics: dict[str, Any],
                               *, now: pd.Timestamp | None = None) -> list[dict[str, Any]]:
    """Derive a starter rule set from a baseline profile.

    Suggestions encode *validity* expectations seen in the baseline (types, keys, allowed
    categories, non-negativity, completeness). They deliberately avoid min/max bounds
    taken from baseline values: distribution changes belong to drift detection.
    """
    now = pd.Timestamp(now or pd.Timestamp.now())
    names = [c["column_name"] for c in columns]
    rules: list[dict[str, Any]] = [
        {"name": "Required baseline columns", "rule_type": "required_columns", "severity": "high",
         "params": {"columns": names}, "rationale": "All columns present in the baseline."},
        {"name": "No unexpected columns", "rule_type": "unexpected_columns", "severity": "low",
         "params": {"allowed": names}, "rationale": "New columns are flagged for review, not failed."},
        {"name": "Schema matches baseline", "rule_type": "schema_match", "severity": "high", "params": {},
         "rationale": "Compares types, nullability, keys and categories with the active baseline."},
        {"name": "Constant columns", "rule_type": "constant_column", "column": None, "severity": "low",
         "params": {}, "rationale": "Single-value columns often indicate a broken upstream default."},
    ]
    base_dup = float(dataset_metrics.get("duplicate_pct") or 0.0)
    rules.append({"name": "Duplicate rows", "rule_type": "duplicate_rows", "severity": "medium",
                  "params": {"max_duplicate_pct": 0.0 if base_dup == 0 else round(min(base_dup + 1.0, 100.0), 2)},
                  "rationale": f"Baseline duplicate rate was {base_dup:.2f}%."})

    for c in columns:
        name, t = c["column_name"], c["inferred_type"]
        miss = float(c["missing_pct"])
        rules.append({
            "name": f"Completeness: {name}", "rule_type": "missing_values", "column": name, "severity": "medium",
            "params": {"warn_missing_pct": round(min(miss + 2.0, 100.0), 2),
                       "max_missing_pct": round(min(miss + 10.0, 100.0), 2)},
            "rationale": f"Baseline missing rate {miss:.2f}%; warn at +2 pp, fail at +10 pp."})
        if t in ("integer", "float", "datetime", "boolean"):
            rules.append({"name": f"Type: {name} is {t}", "rule_type": "data_type", "column": name,
                          "severity": "high", "params": {"expected_type": "numeric" if t == "float" else t},
                          "rationale": f"Baseline inferred type is {t}."})
        if c["is_potential_identifier"] and c["unique_pct"] >= 99.9 and t != "float":
            rules.append({"name": f"Unique key: {name}", "rule_type": "unique", "column": name,
                          "severity": "high", "params": {},
                          "rationale": f"Baseline values were {c['unique_pct']:.2f}% unique."})
        stats = c.get("stats") or {}
        if t in ("integer", "float") and not c["is_potential_identifier"] and stats.get("min") is not None \
                and stats["min"] >= 0:
            rules.append({"name": f"Non-negative: {name}", "rule_type": "numeric_range", "column": name,
                          "severity": "high", "params": {"min": 0},
                          "rationale": "No negative values in the baseline."})
        if t == "categorical" and stats.get("other_count", 1) == 0 and 0 < stats.get("n_categories", 0) <= 30:
            rules.append({"name": f"Allowed values: {name}", "rule_type": "allowed_values", "column": name,
                          "severity": "medium",
                          "params": {"values": [v["value"] for v in stats["top_values"]], "max_violation_pct": 0.0},
                          "rationale": f"The {stats['n_categories']} categories seen in the baseline."})
        if t == "datetime" and stats.get("max") is not None and pd.Timestamp(stats["max"]).tz_localize(None) <= now:
            rules.append({"name": f"Valid dates: {name}", "rule_type": "date_range", "column": name,
                          "severity": "medium", "params": {"allow_future": False},
                          "rationale": "Baseline had no future dates."})
    out = []
    for r in rules:
        rationale = r.pop("rationale")
        out.append({**validate_rule(r), "rationale": rationale, "source": "suggested_from_baseline"})
    return out


__all__ = ["RULE_TYPES", "RuleValidationError", "run_quality_checks", "suggest_rules_from_profile",
           "summarize_results", "validate_rule"]
