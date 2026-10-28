"""Unit tests for the guided rule-builder helpers (pure Python)."""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from components import rules as rl  # noqa: E402


def test_column_mode_matches_backend_contract():
    assert rl.column_mode("unique") == "required" and rl.column_mode("numeric_range") == "required"
    assert rl.column_mode("missing_values") == "optional" and rl.column_mode("constant_column") == "optional"
    assert rl.column_mode("duplicate_rows") == "none" and rl.column_mode("business_rule") == "none"
    assert set(rl.RULE_TEMPLATES) >= rl.COLUMN_REQUIRED | rl.COLUMN_OPTIONAL
    assert set(rl.RULE_HELP) == set(rl.RULE_TEMPLATES)


def test_split_values_handles_newlines_commas_and_duplicates():
    assert rl.split_values("A, B\nC;; A \n\n  D ") == ["A", "B", "C", "D"]
    assert rl.split_values("") == [] and rl.split_values(None) == []


def test_clean_params_drops_empty_but_keeps_zero_and_false():
    assert rl.clean_params({"a": 0, "b": False, "c": None, "d": "", "e": [], "f": ["x"]}) == {"a": 0, "b": False, "f": ["x"]}


def test_problems_gives_friendly_messages():
    assert rl.problems("unique", None, {}) and not rl.problems("unique", "id", {})
    assert rl.problems("numeric_range", "q", {})  # neither min nor max
    assert rl.problems("numeric_range", "q", {"min": 5, "max": 1})
    assert not rl.problems("numeric_range", "q", {"min": 0})
    assert rl.problems("allowed_values", "s", {"values": []})
    assert rl.problems("required_columns", None, {"columns": []})
    assert rl.problems("business_rule", None, {"expression": "  "})
    assert rl.problems("referential_integrity", "id", {})
    assert not rl.problems("referential_integrity", "id", {"reference_dataset": "X", "reference_column": "id"})
    assert not rl.problems("referential_integrity", "id", {"reference_values": ["a"]})
    assert rl.problems("missing_values", None, {"max_missing_pct": 2, "warn_missing_pct": 5})
    assert not rl.problems("duplicate_rows", None, {"max_duplicate_pct": 0})
