"""Request/response models for baselines, drift, quality score and RCA."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- baselines


class BaselineSet(BaseModel):
    version_number: int = Field(..., ge=1, description="Version to approve as baseline.")
    reason: str = Field(..., min_length=1, max_length=1000,
                        description="Human-readable reason recorded in the audit trail.")
    set_by: str | None = Field(None, max_length=120, description="Name or id of the person approving.")
    confirm: bool = Field(False, description="Required when the dataset already has an active baseline.")


class BaselineOut(BaseModel):
    id: str
    dataset_id: str
    version_id: str
    version_number: int | None
    is_active: bool
    reason: str
    set_by: str | None
    created_at: datetime
    deactivated_at: datetime | None


class BaselineSetOut(BaseModel):
    baseline: BaselineOut
    replaced_version_number: int | None
    note: str


# -------------------------------------------------------------------- drift


class DriftRequest(BaseModel):
    current_version: int | None = Field(None, ge=1,
                                         description="Current version number; latest if omitted.")
    reference_version: int | None = Field(None, ge=1,
                                           description="Explicit reference version; otherwise the active baseline is used.")
    use_active_baseline: bool = Field(True, description="Fall back to the active baseline when no reference_version given.")
    exclude_columns: list[str] = Field(default_factory=list)
    thresholds: dict[str, Any] | None = Field(
        None, description="Override PSI/KS/JS/Wasserstein/missingness thresholds (see docs/DRIFT.md).",
    )


class DriftOut(BaseModel):
    dataset_id: str
    current_version_number: int
    reference_version_number: int
    reference_source: dict[str, Any] | None
    summary: dict[str, Any]
    features: list[dict[str, Any]]
    missingness_changes: list[dict[str, Any]]
    skipped: list[dict[str, Any]]
    multiple_testing: dict[str, Any]
    thresholds: dict[str, Any]
    limitations: list[str]


# -------------------------------------------------------------------- score


class ScoreRequest(BaseModel):
    current_version: int | None = Field(None, ge=1)
    reference_version: int | None = Field(None, ge=1)
    use_active_baseline: bool = True
    weights: dict[str, float] | None = Field(
        None, description="Dimension weights (completeness, validity, uniqueness, consistency, accuracy)."
    )
    thresholds: dict[str, Any] | None = Field(
        None, description="{'overall_min': 70, 'dimension_min': {'completeness': 70, ...}}",
    )


class ScoreOut(BaseModel):
    dataset_id: str
    current_version_number: int
    reference_version_number: int | None
    reference_source: dict[str, Any] | None
    is_synthetic: bool
    rules_evaluated: int
    summary: dict[str, Any]
    score: dict[str, Any]


class ScoreCompareRequest(BaseModel):
    previous_version: int = Field(..., ge=1)
    current_version: int = Field(..., ge=1)
    weights: dict[str, float] | None = None
    thresholds: dict[str, Any] | None = None


class ScoreCompareOut(BaseModel):
    dataset_id: str
    previous_version_number: int
    current_version_number: int
    previous_score: dict[str, Any]
    current_score: dict[str, Any]
    comparison: dict[str, Any]


# ---------------------------------------------------------------------- RCA


class RcaRequest(BaseModel):
    current_version: int | None = Field(None, ge=1)
    reference_version: int | None = Field(None, ge=1)
    use_active_baseline: bool = True
    include_drift: bool = True
    thresholds: dict[str, Any] | None = None


class RcaOut(BaseModel):
    dataset_id: str
    current_version_number: int
    reference_version_number: int | None
    reference_source: dict[str, Any] | None
    quality_summary: dict[str, Any]
    schema_summary: dict[str, Any] | None
    drift_summary: dict[str, Any] | None
    summary: dict[str, Any]
    findings: list[dict[str, Any]]
    disclaimer: str
