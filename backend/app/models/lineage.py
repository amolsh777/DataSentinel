"""Lineage graph: assets (nodes) and dependencies (edges). Used from phase 5."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, new_id, utcnow


class LineageAsset(Base):
    __tablename__ = "lineage_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    # dataset | transformation | metric | report | ml_feature
    asset_type: Mapped[str] = mapped_column(String(30))
    dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="SET NULL"), index=True
    )
    description: Mapped[str | None] = mapped_column(Text)
    columns: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LineageEdge(Base):
    __tablename__ = "lineage_edges"
    __table_args__ = (UniqueConstraint("source_asset_id", "target_asset_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_asset_id: Mapped[str] = mapped_column(
        ForeignKey("lineage_assets.id", ondelete="CASCADE"), index=True
    )
    target_asset_id: Mapped[str] = mapped_column(
        ForeignKey("lineage_assets.id", ondelete="CASCADE"), index=True
    )
    source_columns: Mapped[list[str]] = mapped_column(JSON, default=list)
    dependency_kind: Mapped[str] = mapped_column(String(10), default="known")  # known | inferred
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
