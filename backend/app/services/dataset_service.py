"""Dataset registration, versioning and retrieval.

Orchestrates ingestion -> storage -> profiling -> persistence. Analytics code is
called through pure functions; this module owns all database writes.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.analytics.profiling import profile_column, profile_dataframe, schema_from_profile
from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import ColumnProfile, Dataset, DatasetProfile, DatasetVersion, SchemaSnapshot
from app.services import storage
from app.services.ingestion import ParsedFile, parse_file, safe_display_filename
from app.utils.jsonsafe import dataframe_records

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ inspection


def inspect_file(
    settings: Settings, content: bytes, filename: str, sheet_name: str | None, preview_rows: int = 10
) -> dict[str, Any]:
    """Parse an upload without saving it (for previews and sheet selection)."""
    parsed = _parse(settings, content, filename, sheet_name)
    df = parsed.df
    columns = []
    for i, col in enumerate(df.columns):
        prof = profile_column(df[col], position=i)
        columns.append(
            {
                "name": str(col),
                "pandas_dtype": prof["pandas_dtype"],
                "inferred_type": prof["inferred_type"],
                "missing_pct": prof["missing_pct"],
            }
        )
    return {
        "filename": safe_display_filename(filename),
        "file_format": parsed.file_format,
        "sheet_names": parsed.sheet_names,
        "selected_sheet": parsed.sheet_name,
        "row_count": len(df),
        "column_count": df.shape[1],
        "columns": columns,
        "preview": dataframe_records(df.head(preview_rows)),
        "warnings": parsed.warnings,
    }


# ---------------------------------------------------------------- registration


def register_dataset(
    db: Session,
    settings: Settings,
    *,
    name: str,
    content: bytes,
    filename: str,
    description: str | None = None,
    owner: str | None = None,
    project: str | None = None,
    sheet_name: str | None = None,
    is_synthetic: bool = False,
    notes: str | None = None,
) -> tuple[Dataset, DatasetVersion]:
    """Create a dataset and its version 1 atomically. Nothing is saved if parsing fails."""
    name = name.strip()
    if not name:
        raise AppError("Dataset name is required.", code="INVALID_NAME", status_code=422)
    if db.scalar(select(Dataset.id).where(Dataset.name == name)):
        raise ConflictError(f"A dataset named '{name}' already exists.", code="DATASET_EXISTS")

    parsed = _parse(settings, content, filename, sheet_name)
    dataset = Dataset(
        name=name,
        description=description,
        source_type=parsed.file_format,
        owner=owner,
        project=project,
        is_synthetic=is_synthetic,
    )
    db.add(dataset)
    db.flush()
    version = _create_version(
        db, settings, dataset, parsed, content, filename, is_synthetic=is_synthetic, notes=notes
    )
    return dataset, version


def add_version(
    db: Session,
    settings: Settings,
    dataset_id: str,
    *,
    content: bytes,
    filename: str,
    sheet_name: str | None = None,
    is_synthetic: bool | None = None,
    notes: str | None = None,
) -> DatasetVersion:
    """Append a new immutable version to an existing dataset."""
    dataset = get_dataset(db, dataset_id)
    digest = storage.content_hash(content)
    existing = db.scalar(
        select(DatasetVersion.version_number).where(
            DatasetVersion.dataset_id == dataset.id, DatasetVersion.content_hash == digest
        )
    )
    if existing is not None:
        raise ConflictError(
            f"This exact file was already uploaded as version {existing}.",
            code="DUPLICATE_VERSION",
            details={"existing_version": existing},
        )
    parsed = _parse(settings, content, filename, sheet_name)
    synthetic = dataset.is_synthetic if is_synthetic is None else is_synthetic
    return _create_version(
        db, settings, dataset, parsed, content, filename, is_synthetic=synthetic, notes=notes
    )


def _parse(settings: Settings, content: bytes, filename: str, sheet_name: str | None) -> ParsedFile:
    return parse_file(
        content,
        filename,
        sheet_name=sheet_name or None,
        max_rows=settings.max_rows,
        max_columns=settings.max_columns,
    )


def _create_version(
    db: Session,
    settings: Settings,
    dataset: Dataset,
    parsed: ParsedFile,
    content: bytes,
    filename: str,
    *,
    is_synthetic: bool,
    notes: str | None,
) -> DatasetVersion:
    next_number = (
        db.scalar(
            select(func.max(DatasetVersion.version_number)).where(
                DatasetVersion.dataset_id == dataset.id
            )
        )
        or 0
    ) + 1
    relative_path = storage.save_version_file(
        settings, dataset.id, next_number, parsed.file_format, content
    )
    try:
        profile = profile_dataframe(parsed.df, sample_rows=settings.profile_sample_rows)
        version = DatasetVersion(
            dataset_id=dataset.id,
            version_number=next_number,
            original_filename=safe_display_filename(filename),
            file_format=parsed.file_format,
            sheet_name=parsed.sheet_name,
            encoding=parsed.encoding,
            delimiter=parsed.delimiter,
            storage_path=relative_path,
            file_size_bytes=len(content),
            content_hash=storage.content_hash(content),
            row_count=len(parsed.df),
            column_count=parsed.df.shape[1],
            is_synthetic=is_synthetic,
            notes=notes,
            ingestion_warnings=parsed.warnings,
        )
        db.add(version)
        db.flush()
        db.add(SchemaSnapshot(version_id=version.id, columns=schema_from_profile(profile["columns"])))
        _persist_profile(db, version, profile)
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_file(settings, relative_path)
        raise
    db.refresh(version)
    logger.info(
        "Registered dataset %s version %s (%s rows, %s cols)",
        dataset.id, next_number, version.row_count, version.column_count,
    )
    return version


def _persist_profile(db: Session, version: DatasetVersion, profile: dict[str, Any]) -> None:
    ds = profile["dataset"]
    db_profile = DatasetProfile(
        version_id=version.id,
        row_count=ds["row_count"],
        column_count=ds["column_count"],
        duplicate_rows=ds["duplicate_rows"],
        duplicate_pct=ds["duplicate_pct"],
        missing_cells=ds["missing_cells"],
        missing_pct=ds["missing_pct"],
        memory_bytes=ds["memory_bytes"],
        quality_score=ds["quality_score"],
        metrics=ds,
        sampled=ds["sampled"],
        sample_size=ds["sample_size"],
    )
    db.add(db_profile)
    db.flush()
    for col in profile["columns"]:
        db.add(
            ColumnProfile(
                profile_id=db_profile.id,
                column_name=col["column_name"],
                position=col["position"],
                inferred_type=col["inferred_type"],
                pandas_dtype=col["pandas_dtype"],
                missing_count=col["missing_count"],
                missing_pct=col["missing_pct"],
                unique_count=col["unique_count"],
                unique_pct=col["unique_pct"],
                is_constant=col["is_constant"],
                is_near_constant=col["is_near_constant"],
                is_potential_identifier=col["is_potential_identifier"],
                invalid_count=col["invalid_count"],
                stats={**col["stats"], "invalid_examples": col["invalid_examples"]},
                warnings=col["warnings"],
            )
        )


# -------------------------------------------------------------------- retrieval


def list_datasets(
    db: Session, *, limit: int, offset: int, project: str | None = None, search: str | None = None
) -> tuple[list[Dataset], int]:
    query = select(Dataset).where(Dataset.is_deleted.is_(False))
    if project:
        query = query.where(Dataset.project == project)
    if search:
        query = query.where(Dataset.name.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(
        query.options(selectinload(Dataset.versions))
        .order_by(Dataset.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return list(items), int(total)


def get_dataset(db: Session, dataset_id: str) -> Dataset:
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"Dataset '{dataset_id}' not found.", code="DATASET_NOT_FOUND")
    return dataset


def get_version(db: Session, dataset_id: str, version_number: int | None = None) -> DatasetVersion:
    """Return a specific version, or the latest when ``version_number`` is None."""
    get_dataset(db, dataset_id)
    query = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    if version_number is None:
        query = query.order_by(DatasetVersion.version_number.desc()).limit(1)
    else:
        query = query.where(DatasetVersion.version_number == version_number)
    version = db.scalar(query)
    if version is None:
        raise NotFoundError(
            f"Version {version_number if version_number is not None else '(latest)'} not found.",
            code="VERSION_NOT_FOUND",
        )
    return version


def load_version_dataframe(settings: Settings, version: DatasetVersion) -> pd.DataFrame:
    """Load from Parquet cache when available, parse + cache otherwise."""
    return storage.load_version_dataframe_cached(settings, version)


def dataset_summary_fields(dataset: Dataset) -> dict[str, Any]:
    versions = dataset.versions
    return {
        "version_count": len(versions),
        "latest_version_number": versions[-1].version_number if versions else None,
    }


def latest_profile_summary(dataset: Dataset) -> dict[str, Any] | None:
    if not dataset.versions:
        return None
    version = dataset.versions[-1]
    p = version.profile
    if p is None:
        return None
    return {
        "version_number": version.version_number,
        "row_count": p.row_count,
        "column_count": p.column_count,
        "duplicate_rows": p.duplicate_rows,
        "duplicate_pct": p.duplicate_pct,
        "missing_cells": p.missing_cells,
        "missing_pct": p.missing_pct,
        "quality_score": p.quality_score,
        "sampled": p.sampled,
    }


def column_profiles_as_dicts(version: DatasetVersion) -> list[dict[str, Any]]:
    """Rebuild analytics-shaped column profiles (as produced by ``profile_column``) from the DB."""
    if version.profile is None:  # pragma: no cover - profiles are created with every version
        raise NotFoundError("Profile not found for this version.", code="PROFILE_NOT_FOUND")
    out = []
    for c in version.profile.columns:
        stats = dict(c.stats or {})
        invalid_examples = stats.pop("invalid_examples", [])
        out.append(
            {
                "column_name": c.column_name,
                "position": c.position,
                "inferred_type": c.inferred_type,
                "pandas_dtype": c.pandas_dtype,
                "missing_count": c.missing_count,
                "missing_pct": c.missing_pct,
                "unique_count": c.unique_count,
                "unique_pct": c.unique_pct,
                "is_constant": c.is_constant,
                "is_near_constant": c.is_near_constant,
                "is_potential_identifier": c.is_potential_identifier,
                "invalid_count": c.invalid_count,
                "invalid_examples": invalid_examples,
                "stats": stats,
                "warnings": list(c.warnings or []),
            }
        )
    return out


def get_dataset_by_name(db: Session, name: str) -> Dataset | None:
    return db.scalar(select(Dataset).where(Dataset.name == name))
