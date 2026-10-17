"""Validation rules, monitoring runs, results and incidents (used from phase 2 on)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, new_id, utcnow


class ValidationRule(Base):
    __tablename__ = "validation_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str | None] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )  # NULL = global default rule
    name: Mapped[str] = mapped_column(String(200))
    rule_type: Mapped[str] = mapped_column(String(50))
    column_name: Mapped[str | None] = mapped_column(String(255))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MonitoringRun(Base):
    __tablename__ = "monitoring_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    baseline_id: Mapped[str | None] = mapped_column(ForeignKey("baselines.id", ondelete="SET NULL"))
    reference_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL")
    )
    current_version_id: Mapped[str] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|failed
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QualityCheckResult(Base):
    __tablename__ = "quality_check_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("validation_rules.id", ondelete="SET NULL"))
    rule_type: Mapped[str] = mapped_column(String(50))
    rule_name: Mapped[str] = mapped_column(String(200))
    columns: Mapped[list[str]] = mapped_column(JSON, default=list)
    outcome: Mapped[str] = mapped_column(String(10))  # pass|fail|warn|info|skipped
    severity: Mapped[str] = mapped_column(String(20))
    observed: Mapped[Any] = mapped_column(JSON)
    expected: Mapped[str | None] = mapped_column(Text)
    affected_count: Mapped[int | None] = mapped_column(Integer)
    affected_pct: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DriftResult(Base):
    __tablename__ = "drift_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"), index=True
    )
    column_name: Mapped[str] = mapped_column(String(255))
    feature_type: Mapped[str] = mapped_column(String(20))
    method: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # no_drift|drift|insufficient_data|skipped
    drift_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    statistic: Mapped[float | None] = mapped_column(Float)
    p_value: Mapped[float | None] = mapped_column(Float)
    adjusted_p_value: Mapped[float | None] = mapped_column(Float)
    effect_size: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    reference_n: Mapped[int] = mapped_column(Integer, default=0)
    current_n: Mapped[int] = mapped_column(Integer, default=0)
    reference_missing_pct: Mapped[float | None] = mapped_column(Float)
    current_missing_pct: Mapped[float | None] = mapped_column(Float)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (Index("ix_incidents_fingerprint_status", "fingerprint", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    first_run_id: Mapped[str | None] = mapped_column(ForeignKey("monitoring_runs.id", ondelete="SET NULL"))
    last_run_id: Mapped[str | None] = mapped_column(ForeignKey("monitoring_runs.id", ondelete="SET NULL"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    incident_type: Mapped[str] = mapped_column(String(20))  # quality|schema|drift
    rule_name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    columns: Mapped[list[str]] = mapped_column(JSON, default=list)
    severity: Mapped[str] = mapped_column(String(20), index=True)
    observed: Mapped[Any] = mapped_column(JSON)
    expected: Mapped[str | None] = mapped_column(Text)
    affected_count: Mapped[int | None] = mapped_column(Integer)
    affected_pct: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    remediation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    recurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IncidentStatusHistory(Base):
    __tablename__ = "incident_status_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[str | None] = mapped_column(String(120))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportExport(Base):
    __tablename__ = "report_exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("monitoring_runs.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(10))
    storage_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
