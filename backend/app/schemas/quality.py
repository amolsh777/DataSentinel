"""Request/response models for validation rules, ad-hoc validation and schema comparison."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RuleType = Literal[
    "missing_values", "duplicate_rows", "unique", "required_columns", "unexpected_columns",
    "data_type", "numeric_range", "allowed_values", "date_range", "constant_column",
    "schema_match", "business_rule", "referential_integrity",
]
Severity = Literal["info", "low", "medium", "high", "critical"]


class RuleCreate(BaseModel):
    dataset_id: str | None = Field(None, description="Omit for a global rule applied to every dataset.")
    name: str | None = Field(None, max_length=200)
    rule_type: RuleType
    column: str | None = Field(None, max_length=255)
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Severity = "medium"
    enabled: bool = True


class RuleUpdate(BaseModel):
    name: str | None = Field(None, max_length=200)
    column: str | None = Field(None, max_length=255)
    params: dict[str, Any] | None = None
    severity: Severity | None = None
    enabled: bool | None = None


class RuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    dataset_id: str | None
    name: str
    rule_type: str
    column: str | None
    params: dict[str, Any]
    severity: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class SuggestedRuleOut(BaseModel):
    name: str
    rule_type: str
    column: str | None
    params: dict[str, Any]
    severity: str
    rationale: str
    source: str


class SuggestRulesOut(BaseModel):
    dataset_id: str
    version_number: int
    saved: bool
    suggestions: list[SuggestedRuleOut]
    saved_rules: list[RuleOut] = []


class CheckResultOut(BaseModel):
    rule_id: str | None
    rule_name: str
    rule_type: str
    rule_severity: str
    columns: list[str]
    outcome: Literal["pass", "fail", "warn", "info", "skipped"]
    severity: str
    observed: Any = None
    expected: str | None = None
    affected_count: int | None = None
    affected_pct: float | None = None
    evidence: dict[str, Any]
    message: str


class ValidationOut(BaseModel):
    dataset_id: str
    version_number: int
    reference_version_number: int | None
    is_synthetic: bool
    rules_evaluated: int
    summary: dict[str, Any]
    results: list[CheckResultOut]
    note: str


class SchemaDiffOut(BaseModel):
    dataset_id: str
    reference_version_number: int
    current_version_number: int
    compatible: bool
    summary: dict[str, Any]
    changes: list[dict[str, Any]]
    columns: list[dict[str, Any]]
    rename_candidates: list[dict[str, Any]]
    thresholds: dict[str, float]
    limitations: list[str]
