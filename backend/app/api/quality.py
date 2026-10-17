"""Validation rules CRUD, rule suggestion, ad-hoc validation and schema comparison."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings_dep
from app.core.config import Settings
from app.models import ValidationRule
from app.schemas.common import ErrorResponse
from app.schemas.quality import (
    RuleCreate,
    RuleOut,
    RuleUpdate,
    SchemaDiffOut,
    SuggestRulesOut,
    ValidationOut,
)
from app.services import quality_service as svc

_ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}

rules_router = APIRouter(prefix="/rules", tags=["validation rules"])
dataset_quality_router = APIRouter(prefix="/datasets", tags=["data quality"])


def _out(rule: ValidationRule) -> RuleOut:
    return RuleOut(**svc.rule_to_dict(rule))


@rules_router.get("", response_model=list[RuleOut], responses=_ERRORS)
def list_rules(
    dataset_id: str | None = Query(None, description="Filter to one dataset (plus global rules)."),
    include_global: bool = True,
    db: Session = Depends(get_db),
) -> list[RuleOut]:
    return [_out(r) for r in svc.list_rules(db, dataset_id=dataset_id, include_global=include_global)]


@rules_router.post("", response_model=RuleOut, status_code=status.HTTP_201_CREATED, responses=_ERRORS)
def create_rule(body: RuleCreate, db: Session = Depends(get_db)) -> RuleOut:
    """Create a rule. Parameters are validated per rule type (see docs/RULES.md)."""
    return _out(svc.create_rule(db, body.model_dump()))


@rules_router.get("/{rule_id}", response_model=RuleOut, responses=_ERRORS)
def get_rule(rule_id: str, db: Session = Depends(get_db)) -> RuleOut:
    return _out(svc.get_rule(db, rule_id))


@rules_router.patch("/{rule_id}", response_model=RuleOut, responses=_ERRORS)
def update_rule(rule_id: str, body: RuleUpdate, db: Session = Depends(get_db)) -> RuleOut:
    return _out(svc.update_rule(db, rule_id, body.model_dump(exclude_unset=True)))


@rules_router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
def delete_rule(rule_id: str, db: Session = Depends(get_db)) -> Response:
    svc.delete_rule(db, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@dataset_quality_router.post("/{dataset_id}/rules/suggest", response_model=SuggestRulesOut, responses=_ERRORS)
def suggest_rules(
    dataset_id: str,
    version: int | None = Query(None, ge=1, description="Version to learn from; latest if omitted."),
    save: bool = Query(False, description="Persist suggestions (rules with existing names are skipped)."),
    db: Session = Depends(get_db),
) -> SuggestRulesOut:
    v, suggestions, saved = svc.suggest_rules(db, dataset_id, version, save=save)
    return SuggestRulesOut(
        dataset_id=dataset_id, version_number=v.version_number, saved=save,
        suggestions=suggestions, saved_rules=[_out(r) for r in saved],
    )


@dataset_quality_router.post(
    "/{dataset_id}/versions/{version_number}/validate", response_model=ValidationOut, responses=_ERRORS
)
def validate_version(
    dataset_id: str,
    version_number: int,
    reference_version: int | None = Query(None, ge=1, description="Version used for schema_match checks."),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ValidationOut:
    """Run the dataset's enabled rules (and global rules) against a version. Not persisted."""
    return ValidationOut(**svc.validate_version(db, settings, dataset_id, version_number, reference_version))


@dataset_quality_router.get("/{dataset_id}/schema-diff", response_model=SchemaDiffOut, responses=_ERRORS)
def schema_diff(
    dataset_id: str,
    reference: int = Query(..., ge=1, description="Reference (baseline) version number."),
    current: int | None = Query(None, ge=1, description="Current version number; latest if omitted."),
    db: Session = Depends(get_db),
) -> SchemaDiffOut:
    return SchemaDiffOut(**svc.schema_diff(db, dataset_id, reference, current))
