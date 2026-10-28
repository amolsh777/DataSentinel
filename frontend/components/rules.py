"""Pure helpers for the guided rule builder (no Streamlit imports, so they are unit-testable)."""

from __future__ import annotations

import re
from typing import Any

RULE_TEMPLATES: dict[str, dict[str, Any]] = {
    "missing_values": {"max_missing_pct": 5, "warn_missing_pct": 2},
    "duplicate_rows": {"max_duplicate_pct": 0},
    "unique": {},
    "required_columns": {"columns": ["column_a", "column_b"]},
    "unexpected_columns": {"allowed": ["column_a", "column_b"]},
    "data_type": {"expected_type": "numeric", "max_invalid_pct": 0},
    "numeric_range": {"min": 0, "max": 1000, "max_violation_pct": 0},
    "allowed_values": {"values": ["A", "B"], "case_sensitive": True, "max_violation_pct": 0},
    "date_range": {"min": "2025-01-01", "allow_future": False, "max_invalid_pct": 0},
    "constant_column": {},
    "schema_match": {},
    "business_rule": {"expression": "abs(revenue - quantity * unit_price) <= 0.01", "max_violation_pct": 0},
    "referential_integrity": {"reference_dataset": "Customer Master", "reference_column": "customer_id"},
}

RULE_HELP = {
    "missing_values": "Flags columns with too many empty values. Leave the column empty to check every column.",
    "duplicate_rows": "Flags rows that are exact copies of another row (optionally only on a subset of columns).",
    "unique": "Every value in the column must appear once (e.g. an ID). Nulls are ignored and reported.",
    "required_columns": "These columns must exist in the file.",
    "unexpected_columns": "Warns when a column appears that is not in the allowed list.",
    "data_type": "Values must parse as the expected type.",
    "numeric_range": "Numbers must fall between a minimum and/or maximum.",
    "allowed_values": "Values must come from a fixed list (e.g. status codes).",
    "date_range": "Dates must be valid and inside an optional window.",
    "constant_column": "Warns when a column holds one single value in every row.",
    "schema_match": "Compares the schema with the reference version's profile.",
    "business_rule": "A custom row-level condition written as a safe expression (see docs/RULES.md).",
    "referential_integrity": "Every value must exist in another dataset's column, or in a literal list.",
}

COLUMN_REQUIRED = {"unique", "data_type", "numeric_range", "allowed_values", "date_range", "referential_integrity"}
COLUMN_OPTIONAL = {"missing_values", "constant_column"}
DATA_TYPES = ["numeric", "integer", "float", "boolean", "datetime", "categorical", "text"]


def column_mode(rule_type: str) -> str:
    """'required', 'optional' or 'none' — whether the rule type takes a column."""
    if rule_type in COLUMN_REQUIRED:
        return "required"
    if rule_type in COLUMN_OPTIONAL:
        return "optional"
    return "none"


def split_values(text: str) -> list[str]:
    """Split a free-text list on newlines, commas or semicolons; trims blanks and keeps first occurrence order."""
    seen, out = set(), []
    for part in re.split(r"[\n,;]+", text or ""):
        item = part.strip()
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def clean_params(params: dict[str, Any]) -> dict[str, Any]:
    """Drop None / empty-string / empty-list entries so the backend only sees parameters the user actually set."""
    return {k: v for k, v in params.items() if v is not None and v != "" and v != []}


def problems(rule_type: str, column: str | None, params: dict[str, Any]) -> list[str]:
    """Client-side sanity checks that give friendlier messages than the API's validation error."""
    out = []
    mode = column_mode(rule_type)
    if mode == "required" and not column:
        out.append(f"'{rule_type}' needs a column — pick one.")
    if rule_type == "required_columns" and not params.get("columns"):
        out.append("Choose at least one required column.")
    if rule_type == "allowed_values" and not params.get("values"):
        out.append("List at least one allowed value.")
    if rule_type == "numeric_range" and params.get("min") is None and params.get("max") is None:
        out.append("Set a minimum, a maximum, or both.")
    if rule_type == "numeric_range" and None not in (params.get("min"), params.get("max")) and params["min"] > params["max"]:
        out.append("Minimum is greater than maximum.")
    if rule_type == "business_rule" and not str(params.get("expression", "")).strip():
        out.append("Write an expression.")
    if rule_type == "referential_integrity" and not (params.get("reference_values") or
                                                    (params.get("reference_dataset") and params.get("reference_column"))):
        out.append("Pick a reference dataset and column, or give a list of valid values.")
    if rule_type == "missing_values" and params.get("warn_missing_pct") is not None and params.get("max_missing_pct") is not None \
            and params["warn_missing_pct"] > params["max_missing_pct"]:
        out.append("Warn threshold should not exceed the failure threshold.")
    return out
