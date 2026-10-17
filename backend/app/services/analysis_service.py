"""Orchestration for drift, quality-score and root-cause endpoints.

These services run analytics on demand against stored dataset versions. Nothing
is persisted here (persisted monitoring runs with incident deduplication are
Phase 4).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.analytics.data_drift import detect_drift
from app.analytics.quality_checks import run_quality_checks, summarize_results
from app.analytics.quality_score import compare_scores, compute_quality_score
from app.analytics.root_cause import analyze as rca_analyze
from app.analytics.schema_drift import compare_schemas
from app.core.config import Settings
from app.core.errors import AppError
from app.services import baseline_service, dataset_service as ds, quality_service

logger = logging.getLogger(__name__)


class AnalysisError(AppError):
    status_code = 422
    code = "ANALYSIS_ERROR"


def _resolve_reference(db: Session, dataset_id: str,
                       reference_version_number: int | None, use_baseline: bool):
    reference_version, source = baseline_service.resolve_reference_version(
        db, dataset_id,
        reference_version_number=reference_version_number,
        use_active_baseline=use_baseline,
    )
    return reference_version, source


# ------------------------------------------------------------------------ drift


def run_drift(
    db: Session, settings: Settings, dataset_id: str,
    current_version_number: int | None, reference_version_number: int | None = None,
    *, thresholds: dict[str, Any] | None = None, exclude_columns: list[str] | None = None,
    use_active_baseline: bool = True,
) -> dict[str, Any]:
    current = ds.get_version(db, dataset_id, current_version_number)
    reference, source = _resolve_reference(db, dataset_id, reference_version_number, use_active_baseline)
    if reference is None:
        raise AnalysisError(
            "No reference version available. Set an active baseline for this dataset or pass "
            "reference_version explicitly.", code="REFERENCE_REQUIRED",
        )
    if reference.id == current.id:
        raise AnalysisError("Reference and current version are the same.",
                            code="REFERENCE_EQUALS_CURRENT")

    ref_profiles = ds.column_profiles_as_dicts(reference)
    cur_profiles = ds.column_profiles_as_dicts(current)
    ref_df = ds.load_version_dataframe(settings, reference)
    cur_df = ds.load_version_dataframe(settings, current)
    report = detect_drift(ref_df, cur_df,
                          reference_profiles=ref_profiles, current_profiles=cur_profiles,
                          thresholds=thresholds, exclude_columns=exclude_columns)
    logger.info("Drift run: dataset=%s current=v%s reference=v%s status=%s drifted=%s",
                dataset_id, current.version_number, reference.version_number,
                report["summary"]["status"], report["summary"]["features_drifted"])
    return {
        "dataset_id": dataset_id,
        "current_version_number": current.version_number,
        "reference_version_number": reference.version_number,
        "reference_source": source,
        **report,
    }


# ------------------------------------------------------------------- score


def _validation_and_score(
    db: Session, settings: Settings, dataset_id: str, current_version_number: int | None,
    reference_version_number: int | None, *, use_active_baseline: bool,
    weights: dict[str, float] | None, thresholds: dict[str, Any] | None,
) -> dict[str, Any]:
    """Run the enabled rules, compute the configurable score, return both."""
    current = ds.get_version(db, dataset_id, current_version_number)
    reference, source = _resolve_reference(db, dataset_id, reference_version_number, use_active_baseline)

    rules = [quality_service.rule_to_dict(r) for r in quality_service.list_rules(
        db, dataset_id=dataset_id, enabled_only=True
    )]
    df = ds.load_version_dataframe(settings, current)
    ctx = quality_service.build_validation_context(db, settings, rules, reference, current)
    results = run_quality_checks(df, rules, ctx)
    summary = summarize_results(results)
    try:
        score = compute_quality_score(results, weights=weights, thresholds=thresholds)
    except ValueError as exc:
        raise AnalysisError(str(exc), code="INVALID_SCORE_CONFIG") from None
    return {
        "dataset_id": dataset_id,
        "current_version_number": current.version_number,
        "reference_version_number": reference.version_number if reference else None,
        "reference_source": source,
        "is_synthetic": current.is_synthetic,
        "rules_evaluated": len(rules),
        "results": results, "summary": summary, "score": score,
    }


def compute_score(
    db: Session, settings: Settings, dataset_id: str, current_version_number: int | None,
    reference_version_number: int | None = None, *, use_active_baseline: bool = True,
    weights: dict[str, float] | None = None, thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _validation_and_score(
        db, settings, dataset_id, current_version_number, reference_version_number,
        use_active_baseline=use_active_baseline, weights=weights, thresholds=thresholds,
    )
    return {k: v for k, v in data.items() if k != "results"}


def compare_versions_score(
    db: Session, settings: Settings, dataset_id: str, current_version_number: int,
    previous_version_number: int, *, weights: dict[str, float] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score two versions against the same rules and compare them."""
    current = compute_score(db, settings, dataset_id, current_version_number,
                            reference_version_number=previous_version_number,
                            use_active_baseline=False, weights=weights, thresholds=thresholds)
    previous = compute_score(db, settings, dataset_id, previous_version_number,
                             reference_version_number=previous_version_number,
                             use_active_baseline=False, weights=weights, thresholds=thresholds)
    return {
        "dataset_id": dataset_id,
        "previous_version_number": previous_version_number,
        "current_version_number": current_version_number,
        "previous_score": previous["score"],
        "current_score": current["score"],
        "comparison": compare_scores(previous["score"], current["score"]),
    }


# -------------------------------------------------------------------- RCA


def run_rca(
    db: Session, settings: Settings, dataset_id: str, current_version_number: int | None,
    reference_version_number: int | None = None, *, use_active_baseline: bool = True,
    include_drift: bool = True, thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce the full root-cause report combining quality, drift and schema evidence."""
    data = _validation_and_score(
        db, settings, dataset_id, current_version_number, reference_version_number,
        use_active_baseline=use_active_baseline, weights=None, thresholds=None,
    )
    current = ds.get_version(db, dataset_id, data["current_version_number"])
    reference = (ds.get_version(db, dataset_id, data["reference_version_number"])
                 if data["reference_version_number"] else None)

    cur_profiles = ds.column_profiles_as_dicts(current)
    ref_profiles = ds.column_profiles_as_dicts(reference) if reference else None
    current_profile = {"columns": cur_profiles}
    reference_profile = {"columns": ref_profiles} if ref_profiles else None

    schema_diff = compare_schemas(ref_profiles, cur_profiles) if ref_profiles else None

    drift_report = None
    if include_drift and reference is not None:
        ref_df = ds.load_version_dataframe(settings, reference)
        cur_df = ds.load_version_dataframe(settings, current)
        drift_report = detect_drift(
            ref_df, cur_df,
            reference_profiles=ref_profiles, current_profiles=cur_profiles,
            thresholds=thresholds,
        )

    rca = rca_analyze(
        current_profile=current_profile,
        quality_results=data["results"],
        drift_report=drift_report,
        schema_diff=schema_diff,
        reference_profile=reference_profile,
    )
    return {
        "dataset_id": dataset_id,
        "current_version_number": data["current_version_number"],
        "reference_version_number": data["reference_version_number"],
        "reference_source": data["reference_source"],
        "quality_summary": data["summary"],
        "schema_summary": schema_diff["summary"] if schema_diff else None,
        "drift_summary": drift_report["summary"] if drift_report else None,
        **rca,
    }


__all__ = ["AnalysisError", "compare_versions_score", "compute_score", "run_drift", "run_rca"]
