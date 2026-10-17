"""Dataset registry, versions, schemas, profiles and baselines."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, new_id, utcnow


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(20))  # csv | excel | sql
    owner: Mapped[str | None] = mapped_column(String(120))
    project: Mapped[str | None] = mapped_column(String(120), index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[DatasetVersion]] = relationship(
        back_populates="dataset",
        cascade="all, delete-orphan",
        order_by="DatasetVersion.version_number",
    )


class DatasetVersion(Base):
    """An immutable uploaded file. The raw file lives in storage, not the DB."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(10))  # csv | excel
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    encoding: Mapped[str | None] = mapped_column(String(20))
    delimiter: Mapped[str | None] = mapped_column(String(4))
    storage_path: Mapped[str] = mapped_column(String(500))  # relative to storage_dir
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    row_count: Mapped[int] = mapped_column(Integer)
    column_count: Mapped[int] = mapped_column(Integer)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    ingestion_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    dataset: Mapped[Dataset] = relationship(back_populates="versions")
    schema: Mapped[SchemaSnapshot | None] = relationship(
        back_populates="version", cascade="all, delete-orphan", uselist=False
    )
    profile: Mapped[DatasetProfile | None] = relationship(
        back_populates="version", cascade="all, delete-orphan", uselist=False
    )


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), unique=True
    )
    # [{"name", "position", "pandas_dtype", "inferred_type", "nullable", "missing_pct", "unique_pct"}]
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    version: Mapped[DatasetVersion] = relationship(back_populates="schema")


class DatasetProfile(Base):
    __tablename__ = "dataset_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), unique=True
    )
    row_count: Mapped[int] = mapped_column(Integer)
    column_count: Mapped[int] = mapped_column(Integer)
    duplicate_rows: Mapped[int] = mapped_column(Integer)
    duplicate_pct: Mapped[float] = mapped_column(Float)
    missing_cells: Mapped[int] = mapped_column(BigInteger)
    missing_pct: Mapped[float] = mapped_column(Float)
    memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    quality_score: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)  # full dataset-level profile
    sampled: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    version: Mapped[DatasetVersion] = relationship(back_populates="profile")
    columns: Mapped[list[ColumnProfile]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="ColumnProfile.position",
    )


class ColumnProfile(Base):
    __tablename__ = "column_profiles"
    __table_args__ = (UniqueConstraint("profile_id", "column_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_profiles.id", ondelete="CASCADE"), index=True
    )
    column_name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer)
    inferred_type: Mapped[str] = mapped_column(String(20))
    pandas_dtype: Mapped[str] = mapped_column(String(40))
    missing_count: Mapped[int] = mapped_column(Integer)
    missing_pct: Mapped[float] = mapped_column(Float)
    unique_count: Mapped[int] = mapped_column(Integer)
    unique_pct: Mapped[float] = mapped_column(Float)
    is_constant: Mapped[bool] = mapped_column(Boolean, default=False)
    is_near_constant: Mapped[bool] = mapped_column(Boolean, default=False)
    is_potential_identifier: Mapped[bool] = mapped_column(Boolean, default=False)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    profile: Mapped[DatasetProfile] = relationship(back_populates="columns")


class Baseline(Base):
    """Approved reference version. Changing it is recorded, never silent."""

    __tablename__ = "baselines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[str] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    reason: Mapped[str] = mapped_column(Text)
    set_by: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
