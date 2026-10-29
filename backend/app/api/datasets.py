from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings_dep, read_upload
from app.core.config import Settings
from app.core.errors import NotFoundError
from app.models import Dataset
from app.schemas.common import ErrorResponse, Page
from app.schemas.dataset import (
    ColumnProfileOut,
    DatasetDetailOut,
    DatasetOut,
    DatasetVersionOut,
    PreviewOut,
    ProfileOut,
    ProfileSummaryOut,
    SchemaOut,
)
from app.services import dataset_service as svc
from app.utils.jsonsafe import dataframe_records

router = APIRouter(prefix="/datasets", tags=["datasets"])

_ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


def _dataset_out(dataset: Dataset) -> DatasetOut:
    return DatasetOut.model_validate(dataset).model_copy(update=svc.dataset_summary_fields(dataset))


def _detail_out(dataset: Dataset) -> DatasetDetailOut:
    summary = svc.latest_profile_summary(dataset)
    return DatasetDetailOut(
        **_dataset_out(dataset).model_dump(),
        versions=[DatasetVersionOut.model_validate(v) for v in dataset.versions],
        latest_profile=ProfileSummaryOut(**summary) if summary else None,
    )


@router.post("", response_model=DatasetDetailOut, status_code=status.HTTP_201_CREATED, responses=_ERRORS)
async def create_dataset(
    file: UploadFile = File(...),
    name: str = Form(..., min_length=1, max_length=200),
    description: str | None = Form(None, max_length=2000),
    owner: str | None = Form(None, max_length=120),
    project: str | None = Form(None, max_length=120),
    sheet_name: str | None = Form(None),
    is_synthetic: bool = Form(False),
    notes: str | None = Form(None, max_length=2000),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasetDetailOut:
    """Register a new dataset from a CSV/XLSX upload; the file becomes version 1."""
    content = await read_upload(file, settings.max_upload_bytes)
    dataset, _ = svc.register_dataset(
        db,
        settings,
        name=name,
        content=content,
        filename=file.filename or "",
        description=description,
        owner=owner,
        project=project,
        sheet_name=sheet_name,
        is_synthetic=is_synthetic,
        notes=notes,
    )
    db.refresh(dataset)
    return _detail_out(dataset)


@router.get("", response_model=Page[DatasetOut])
def list_datasets(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    project: str | None = None,
    search: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
) -> Page[DatasetOut]:
    items, total = svc.list_datasets(db, limit=limit, offset=offset, project=project, search=search)
    return Page[DatasetOut](items=[_dataset_out(d) for d in items], total=total, limit=limit, offset=offset)


@router.get("/{dataset_id}", response_model=DatasetDetailOut, responses=_ERRORS)
def get_dataset(dataset_id: str, db: Session = Depends(get_db)) -> DatasetDetailOut:
    return _detail_out(svc.get_dataset(db, dataset_id))


@router.get("/{dataset_id}/versions", response_model=list[DatasetVersionOut], responses=_ERRORS)
def list_versions(dataset_id: str, db: Session = Depends(get_db)) -> list[DatasetVersionOut]:
    return [DatasetVersionOut.model_validate(v) for v in svc.get_dataset(db, dataset_id).versions]


@router.post(
    "/{dataset_id}/versions",
    response_model=DatasetVersionOut,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
async def upload_version(
    dataset_id: str,
    file: UploadFile = File(...),
    sheet_name: str | None = Form(None),
    notes: str | None = Form(None, max_length=2000),
    is_synthetic: bool | None = Form(None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasetVersionOut:
    """Upload a new version. Identical content to an existing version is rejected (409)."""
    svc.get_dataset(db, dataset_id)  # 404 before reading a large body
    content = await read_upload(file, settings.max_upload_bytes)
    version = svc.add_version(
        db,
        settings,
        dataset_id,
        content=content,
        filename=file.filename or "",
        sheet_name=sheet_name,
        is_synthetic=is_synthetic,
        notes=notes,
    )
    return DatasetVersionOut.model_validate(version)


@router.get("/{dataset_id}/profile", response_model=ProfileOut, responses=_ERRORS)
def get_profile(
    dataset_id: str,
    version: int | None = Query(None, ge=1, description="Version number; latest if omitted."),
    db: Session = Depends(get_db),
) -> ProfileOut:
    v = svc.get_version(db, dataset_id, version)
    p = v.profile
    if p is None:  # pragma: no cover - profiles are created with every version
        raise NotFoundError("Profile not found for this version.", code="PROFILE_NOT_FOUND")
    return ProfileOut(
        dataset_id=dataset_id,
        version_id=v.id,
        version_number=v.version_number,
        is_synthetic=v.is_synthetic,
        metrics=p.metrics,
        columns=[ColumnProfileOut.model_validate(c) for c in p.columns],
        created_at=p.created_at,
    )


@router.get("/{dataset_id}/versions/{version_number}/schema", response_model=SchemaOut, responses=_ERRORS)
def get_schema(dataset_id: str, version_number: int, db: Session = Depends(get_db)) -> SchemaOut:
    v = svc.get_version(db, dataset_id, version_number)
    return SchemaOut(
        dataset_id=dataset_id,
        version_number=v.version_number,
        columns=v.schema.columns if v.schema else [],
    )


@router.get("/{dataset_id}/versions/{version_number}/preview", response_model=PreviewOut, responses=_ERRORS)
def preview_version(
    dataset_id: str,
    version_number: int,
    rows: int = Query(20, ge=1),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> PreviewOut:
    v = svc.get_version(db, dataset_id, version_number)
    df = svc.load_version_dataframe(settings, v)
    n = min(rows, settings.max_preview_rows)
    return PreviewOut(
        dataset_id=dataset_id,
        version_number=v.version_number,
        columns=[str(c) for c in df.columns],
        rows=dataframe_records(df.head(n)),
        total_rows=len(df),
    )


@router.delete("/{dataset_id}", status_code=200, responses=_ERRORS)
def soft_delete_dataset(
    dataset_id: str,
    confirm: bool = Query(False, description="Must be true to delete."),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Soft-delete a dataset (hidden from lists; recoverable). Pass confirm=true."""
    from app.core.errors import AppError
    if not confirm:
        raise AppError("Pass confirm=true to delete a dataset.", code="CONFIRMATION_REQUIRED")
    dataset = svc.get_dataset(db, dataset_id)
    import datetime
    dataset.is_deleted = True
    dataset.deleted_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    return {"deleted": True, "id": dataset_id}


@router.patch("/{dataset_id}/restore", responses=_ERRORS)
def restore_dataset(dataset_id: str, db: Session = Depends(get_db)) -> DatasetOut:
    """Restore a soft-deleted dataset."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise NotFoundError(f"Dataset '{dataset_id}' not found.")
    dataset.is_deleted = False
    dataset.deleted_at = None
    db.commit()
    db.refresh(dataset)
    return _dataset_out(dataset)


@router.get("/{dataset_id}/versions/{version_number}/download", responses=_ERRORS)
def download_version_file(
    dataset_id: str, version_number: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Download the original uploaded file for a dataset version."""
    svc.get_dataset(db, dataset_id)  # 404 if the dataset does not exist
    version = svc.get_version(db, dataset_id, version_number)
    from app.services import storage
    content = storage.read_version_file(settings, version.storage_path)
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if version.file_format == "excel" else "text/csv"
    return Response(content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{version.original_filename}"'})


@router.patch("/{dataset_id}/versions/{version_number}/type-overrides", responses=_ERRORS)
def set_type_overrides(
    dataset_id: str, version_number: int, body: dict,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Override inferred column types. {col: type} where type in (integer, float, categorical, text, datetime, boolean)."""
    from app.services import storage as stor
    ALLOWED = {"integer", "float", "categorical", "text", "datetime", "boolean", "auto"}
    overrides = {k: v for k, v in body.items() if v in ALLOWED}
    bad = {k: v for k, v in body.items() if v not in ALLOWED}
    version = svc.get_version(db, dataset_id, version_number)
    version.type_overrides = {**(version.type_overrides or {}), **overrides}
    # Invalidate Parquet cache so next read uses the new types
    stor.invalidate_parquet_cache(settings, version)
    version.parquet_path = None
    db.commit()
    return {"type_overrides": version.type_overrides, "invalid_types_ignored": bad}
