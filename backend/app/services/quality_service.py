"""Validation-rule CRUD, rule suggestion, ad-hoc validation and schema comparison.

Ad-hoc validation here does not persist results. Persisted monitoring runs and incident
generation arrive in phase 4 and will reuse :func:`build_validation_context`.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.analytics.quality_checks import (
    RuleValidationError,
    run_quality_checks,
    suggest_rules_from_profile,
    summarize_results,
    validate_rule,
)
from app.analytics.schema_drift import compare_schemas
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.models import DatasetVersion, ValidationRule
from app.services import dataset_service as ds

logger = logging.getLogger(__name__)


class InvalidRuleError(AppError):
    status_code = 422
    code = "INVALID_RULE"


# --------------------------------------------------------------------- mapping


def rule_to_dict(rule: ValidationRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "dataset_id": rule.dataset_id,
        "name": rule.name,
        "rule_type": rule.rule_type,
        "column": rule.column_name,
        "params": rule.params or {},
        "severity": rule.severity,
        "enabled": rule.enabled,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _validated(spec: dict[str, Any]) -> dict[str, Any]:
    try:
        return validate_rule(spec)
    except RuleValidationError as exc:
        raise InvalidRuleError(str(exc)) from None


# ------------------------------------------------------------------------ CRUD


def create_rule(db: Session, spec: dict[str, Any]) -> ValidationRule:
    if spec.get("dataset_id"):
        ds.get_dataset(db, spec["dataset_id"])
    clean = _validated(spec)
    rule = ValidationRule(
        dataset_id=spec.get("dataset_id"),
        name=clean["name"],
        rule_type=clean["rule_type"],
        column_name=clean["column"],
        params=clean["params"],
        severity=clean["severity"],
        enabled=clean["enabled"],
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def get_rule(db: Session, rule_id: str) -> ValidationRule:
    rule = db.get(ValidationRule, rule_id)
    if rule is None:
        raise NotFoundError(f"Rule '{rule_id}' not found.", code="RULE_NOT_FOUND")
    return rule


def list_rules(
    db: Session, *, dataset_id: str | None, include_global: bool = True, enabled_only: bool = False
) -> list[ValidationRule]:
    query = select(ValidationRule)
    if dataset_id:
        ds.get_dataset(db, dataset_id)
        cond = ValidationRule.dataset_id == dataset_id
        query = query.where(or_(cond, ValidationRule.dataset_id.is_(None)) if include_global else cond)
    if enabled_only:
        query = query.where(ValidationRule.enabled.is_(True))
    return list(db.scalars(query.order_by(ValidationRule.created_at, ValidationRule.name)))


def update_rule(db: Session, rule_id: str, changes: dict[str, Any]) -> ValidationRule:
    """``changes`` holds only the fields the client sent; ``column: None`` clears the column."""
    rule = get_rule(db, rule_id)
    merged = {**rule_to_dict(rule), **changes}
    clean = _validated(merged)
    rule.name = clean["name"]
    rule.column_name = clean["column"]
    rule.params = clean["params"]
    rule.severity = clean["severity"]
    rule.enabled = clean["enabled"]
    db.commit()
    db.refresh(rule)
    return rule


def delete_rule(db: Session, rule_id: str) -> None:
    db.delete(get_rule(db, rule_id))
    db.commit()


# ------------------------------------------------------------------ suggestion


def suggest_rules(
    db: Session, dataset_id: str, version_number: int | None, *, save: bool
) -> tuple[DatasetVersion, list[dict[str, Any]], list[ValidationRule]]:
    """Suggest rules from a version's profile; optionally save them for the dataset."""
    version = ds.get_version(db, dataset_id, version_number)
    suggestions = suggest_rules_from_profile(
        ds.column_profiles_as_dicts(version), version.profile.metrics
    )
    saved: list[ValidationRule] = []
    if save:
        existing = {r.name for r in list_rules(db, dataset_id=dataset_id, include_global=False)}
        for s in suggestions:
            if s["name"] in existing:
                continue  # keep user edits; never duplicate a rule by name
            saved.append(
                ValidationRule(
                    dataset_id=dataset_id, name=s["name"], rule_type=s["rule_type"],
                    column_name=s["column"], params=s["params"], severity=s["severity"], enabled=True,
                )
            )
        db.add_all(saved)
        db.commit()
        for r in saved:
            db.refresh(r)
    return version, suggestions, saved


# ------------------------------------------------------------------ validation


def build_validation_context(
    db: Session, settings: Settings, rules: list[dict[str, Any]], reference: DatasetVersion | None,
    current: DatasetVersion,
) -> dict[str, Any]:
    """Collect what the checks need: baseline/current profiles and referenced datasets."""
    ctx: dict[str, Any] = {"current_columns": ds.column_profiles_as_dicts(current)}
    if reference is not None:
        ctx["baseline_columns"] = ds.column_profiles_as_dicts(reference)
    frames: dict[str, pd.DataFrame] = {}
    for r in rules:
        name = (r.get("params") or {}).get("reference_dataset")
        if r["rule_type"] != "referential_integrity" or not name or name in frames:
            continue
        ref_ds = ds.get_dataset_by_name(db, name)
        if ref_ds is None or not ref_ds.versions:
            continue  # the check reports itself as skipped with a reason
        frames[name] = ds.load_version_dataframe(settings, ref_ds.versions[-1])
    ctx["reference_frames"] = frames
    return ctx


def validate_version(
    db: Session, settings: Settings, dataset_id: str, version_number: int | None,
    reference_version_number: int | None,
) -> dict[str, Any]:
    """Run the dataset's enabled rules (plus global rules) against one version, without persisting."""
    current = ds.get_version(db, dataset_id, version_number)
    reference = (
        ds.get_version(db, dataset_id, reference_version_number)
        if reference_version_number is not None else None
    )
    rules = [rule_to_dict(r) for r in list_rules(db, dataset_id=dataset_id, enabled_only=True)]
    df = ds.load_version_dataframe(settings, current)
    ctx = build_validation_context(db, settings, rules, reference, current)
    results = run_quality_checks(df, rules, ctx)
    logger.info(
        "Validated dataset %s v%s against %s rules", dataset_id, current.version_number, len(rules)
    )
    return {
        "dataset_id": dataset_id,
        "version_number": current.version_number,
        "reference_version_number": reference.version_number if reference else None,
        "is_synthetic": current.is_synthetic,
        "rules_evaluated": len(rules),
        "summary": summarize_results(results),
        "results": results,
        "note": (
            "Ad-hoc validation; results are not stored. "
            + ("" if rules else "No rules are configured: create rules or call the suggest endpoint. ")
            + ("" if reference else "No reference version given, so schema_match checks are skipped.")
        ).strip(),
    }


def schema_diff(
    db: Session, dataset_id: str, reference_version_number: int, current_version_number: int | None
) -> dict[str, Any]:
    reference = ds.get_version(db, dataset_id, reference_version_number)
    current = ds.get_version(db, dataset_id, current_version_number)
    result = compare_schemas(ds.column_profiles_as_dicts(reference), ds.column_profiles_as_dicts(current))
    return {
        "dataset_id": dataset_id,
        "reference_version_number": reference.version_number,
        "current_version_number": current.version_number,
        **result,
    }
