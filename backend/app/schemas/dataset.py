"""Request/response models for datasets, versions, schemas and profiles."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DatasetVersionOut(_ORM):
    id: str
    dataset_id: str
    version_number: int
    original_filename: str
    file_format: str
    sheet_name: str | None
    encoding: str | None
    delimiter: str | None
    file_size_bytes: int
    content_hash: str
    row_count: int
    column_count: int
    is_synthetic: bool
    notes: str | None
    ingestion_warnings: list[str]
    created_at: datetime


class ProfileSummaryOut(BaseModel):
    version_number: int
    row_count: int
    column_count: int
    duplicate_rows: int
    duplicate_pct: float
    missing_cells: int
    missing_pct: float
    quality_score: float | None
    sampled: bool


class DatasetOut(_ORM):
    id: str
    name: str
    description: str | None
    source_type: str
    owner: str | None
    project: str | None
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime
    version_count: int = 0
    latest_version_number: int | None = None


class DatasetDetailOut(DatasetOut):
    versions: list[DatasetVersionOut]
    latest_profile: ProfileSummaryOut | None


class ColumnProfileOut(_ORM):
    column_name: str
    position: int
    inferred_type: str
    pandas_dtype: str
    missing_count: int
    missing_pct: float
    unique_count: int
    unique_pct: float
    is_constant: bool
    is_near_constant: bool
    is_potential_identifier: bool
    invalid_count: int
    stats: dict[str, Any]
    warnings: list[str]


class ProfileOut(BaseModel):
    dataset_id: str
    version_id: str
    version_number: int
    is_synthetic: bool
    metrics: dict[str, Any]
    columns: list[ColumnProfileOut]
    created_at: datetime


class SchemaOut(BaseModel):
    dataset_id: str
    version_number: int
    columns: list[dict[str, Any]]


class PreviewOut(BaseModel):
    dataset_id: str
    version_number: int
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int


class InspectColumnOut(BaseModel):
    name: str
    pandas_dtype: str
    inferred_type: str
    missing_pct: float


class FileInspectOut(BaseModel):
    filename: str
    file_format: str
    sheet_names: list[str]
    selected_sheet: str | None
    row_count: int
    column_count: int
    columns: list[InspectColumnOut]
    preview: list[dict[str, Any]]
    warnings: list[str]
