"""Baseline management: set, change, and look up the approved reference version.

A baseline is never silently replaced. Setting a new baseline deactivates the
previous one and records who did it, when, and why; the row stays in the table
as audit history.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFoundError
from app.models import Baseline, DatasetVersion
from app.services import dataset_service as ds
from app.utils.jsonsafe import to_py

logger = logging.getLogger(__name__)


class BaselineError(AppError):
    status_code = 422
    code = "BASELINE_ERROR"


def baseline_to_dict(baseline: Baseline, version: DatasetVersion | None = None) -> dict[str, Any]:
    return {
        "id": baseline.id,
        "dataset_id": baseline.dataset_id,
        "version_id": baseline.version_id,
        "version_number": version.version_number if version else None,
        "is_active": baseline.is_active,
        "reason": baseline.reason,
        "set_by": baseline.set_by,
        "created_at": to_py(baseline.created_at),
        "deactivated_at": to_py(baseline.deactivated_at),
    }


def get_active_baseline(db: Session, dataset_id: str) -> Baseline | None:
    return db.scalar(
        select(Baseline)
        .where(Baseline.dataset_id == dataset_id, Baseline.is_active.is_(True))
        .order_by(Baseline.created_at.desc())
    )


def list_baselines(db: Session, dataset_id: str) -> list[dict[str, Any]]:
    """Return baselines for a dataset, newest first, with version numbers resolved."""
    ds.get_dataset(db, dataset_id)
    rows = db.scalars(
        select(Baseline).where(Baseline.dataset_id == dataset_id).order_by(Baseline.created_at.desc())
    ).all()
    # Resolve version numbers in one lookup.
    versions = {v.id: v for v in db.scalars(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    ).all()}
    return [baseline_to_dict(b, versions.get(b.version_id)) for b in rows]


def set_baseline(
    db: Session, dataset_id: str, version_number: int, *,
    reason: str, set_by: str | None, confirm: bool,
) -> dict[str, Any]:
    """Make ``version_number`` the active baseline.

    ``confirm=True`` is required when another baseline is already active for this
    dataset; without it, the request is rejected to prevent silent replacement.
    """
    if not reason or not reason.strip():
        raise BaselineError("A reason is required when setting or changing a baseline.",
                            code="BASELINE_REASON_REQUIRED")
    version = ds.get_version(db, dataset_id, version_number)
    existing = get_active_baseline(db, dataset_id)
    replaced_version_number: int | None = None
    if existing is not None:
        if existing.version_id == version.id:
            return {
                "baseline": baseline_to_dict(existing, version),
                "replaced_version_number": None,
                "note": "Baseline unchanged — this version is already the active baseline.",
            }
        if not confirm:
            previous = db.get(DatasetVersion, existing.version_id)
            raise BaselineError(
                f"Dataset already has an active baseline (version "
                f"{previous.version_number if previous else '?'}). "
                "Pass confirm=true to replace it; the previous baseline is kept as audit history.",
                code="BASELINE_CONFIRMATION_REQUIRED",
                details={"active_baseline_id": existing.id,
                         "active_version_number": previous.version_number if previous else None},
            )
        import datetime as _dt
        existing.is_active = False
        existing.deactivated_at = _dt.datetime.now(_dt.timezone.utc)
        previous = db.get(DatasetVersion, existing.version_id)
        replaced_version_number = previous.version_number if previous else None
        logger.info("Deactivated baseline %s for dataset %s", existing.id, dataset_id)

    baseline = Baseline(
        dataset_id=dataset_id, version_id=version.id, is_active=True,
        reason=reason.strip(), set_by=(set_by or "").strip() or None,
    )
    db.add(baseline)
    db.commit()
    db.refresh(baseline)
    logger.info("Set baseline for dataset %s to version %s", dataset_id, version_number)
    return {
        "baseline": baseline_to_dict(baseline, version),
        "replaced_version_number": replaced_version_number,
        "note": "Baseline updated." if replaced_version_number is not None else "Baseline created.",
    }


def resolve_reference_version(
    db: Session, dataset_id: str, *,
    reference_version_number: int | None = None,
    use_active_baseline: bool = True,
) -> tuple[DatasetVersion | None, dict[str, Any] | None]:
    """Resolve which version to use as the reference for a comparison.

    Explicit ``reference_version_number`` wins; otherwise the active baseline is used.
    Returns ``(version, source_metadata)``; both are ``None`` when none is available.
    """
    if reference_version_number is not None:
        v = ds.get_version(db, dataset_id, reference_version_number)
        return v, {"source": "explicit", "version_number": v.version_number}
    if use_active_baseline:
        active = get_active_baseline(db, dataset_id)
        if active is not None:
            v = db.get(DatasetVersion, active.version_id)
            if v is None:
                raise NotFoundError("The active baseline refers to a missing version.",
                                    code="BASELINE_VERSION_MISSING")
            return v, {"source": "active_baseline", "baseline_id": active.id,
                       "version_number": v.version_number, "reason": active.reason}
    return None, None


__all__ = ["BaselineError", "baseline_to_dict", "get_active_baseline",
           "list_baselines", "resolve_reference_version", "set_baseline"]
