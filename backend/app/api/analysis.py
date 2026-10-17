"""Baseline management, drift, score and root-cause endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings_dep
from app.core.config import Settings
from app.schemas.analysis import (
    BaselineOut,
    BaselineSet,
    BaselineSetOut,
    DriftOut,
    DriftRequest,
    RcaOut,
    RcaRequest,
    ScoreCompareOut,
    ScoreCompareRequest,
    ScoreOut,
    ScoreRequest,
)
from app.schemas.common import ErrorResponse
from app.services import analysis_service as analysis
from app.services import baseline_service, dataset_service as ds

_ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}

router = APIRouter(prefix="/datasets", tags=["analysis"])


# ------------------------------------------------------------------ baselines


@router.get("/{dataset_id}/baselines", response_model=list[BaselineOut], responses=_ERRORS)
def list_baselines(dataset_id: str, db: Session = Depends(get_db)) -> list[BaselineOut]:
    """Full baseline history for a dataset (newest first; inactive rows are audit history)."""
    return [BaselineOut(**b) for b in baseline_service.list_baselines(db, dataset_id)]


@router.post(
    "/{dataset_id}/baseline", response_model=BaselineSetOut,
    status_code=status.HTTP_201_CREATED, responses=_ERRORS,
)
def set_baseline(
    dataset_id: str, body: BaselineSet, db: Session = Depends(get_db),
) -> BaselineSetOut:
    """Approve a version as the baseline. Requires ``confirm=true`` to replace an existing baseline."""
    ds.get_dataset(db, dataset_id)
    result = baseline_service.set_baseline(
        db, dataset_id, body.version_number,
        reason=body.reason, set_by=body.set_by, confirm=body.confirm,
    )
    return BaselineSetOut(baseline=BaselineOut(**result["baseline"]),
                          replaced_version_number=result["replaced_version_number"],
                          note=result["note"])


# ---------------------------------------------------------------------- drift


@router.post("/{dataset_id}/drift", response_model=DriftOut, responses=_ERRORS)
def run_drift(
    dataset_id: str, body: DriftRequest = Body(default_factory=DriftRequest),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
) -> DriftOut:
    """Detect distribution drift of a version against the baseline (or an explicit reference)."""
    data = analysis.run_drift(
        db, settings, dataset_id, body.current_version, body.reference_version,
        thresholds=body.thresholds, exclude_columns=body.exclude_columns,
        use_active_baseline=body.use_active_baseline,
    )
    return DriftOut(**data)


# ---------------------------------------------------------------------- score


@router.post("/{dataset_id}/score", response_model=ScoreOut, responses=_ERRORS)
def compute_score(
    dataset_id: str, body: ScoreRequest = Body(default_factory=ScoreRequest),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
) -> ScoreOut:
    """Compute the configurable, rule-based quality score with its full breakdown."""
    data = analysis.compute_score(
        db, settings, dataset_id, body.current_version, body.reference_version,
        use_active_baseline=body.use_active_baseline,
        weights=body.weights, thresholds=body.thresholds,
    )
    return ScoreOut(**data)


@router.post("/{dataset_id}/score/compare", response_model=ScoreCompareOut, responses=_ERRORS)
def compare_score(
    dataset_id: str, body: ScoreCompareRequest,
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
) -> ScoreCompareOut:
    data = analysis.compare_versions_score(
        db, settings, dataset_id, body.current_version, body.previous_version,
        weights=body.weights, thresholds=body.thresholds,
    )
    return ScoreCompareOut(**data)


# ------------------------------------------------------------------------ RCA


@router.post("/{dataset_id}/root-cause", response_model=RcaOut, responses=_ERRORS)
def run_rca(
    dataset_id: str, body: RcaRequest = Body(default_factory=RcaRequest),
    db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep),
) -> RcaOut:
    """Root-cause analysis: evidence-backed hypotheses, investigation steps and corrective actions."""
    data = analysis.run_rca(
        db, settings, dataset_id, body.current_version, body.reference_version,
        use_active_baseline=body.use_active_baseline, include_drift=body.include_drift,
        thresholds=body.thresholds,
    )
    return RcaOut(**data)
