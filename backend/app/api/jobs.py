"""Async analysis job endpoints and Prometheus-compatible metrics.

POST /api/v1/datasets/{id}/jobs/monitor   → {job_id, status: queued}
POST /api/v1/datasets/{id}/jobs/drift     → {job_id, status: queued}
POST /api/v1/datasets/{id}/jobs/score     → {job_id, status: queued}
GET  /api/v1/jobs/{job_id}               → job status + condensed result
GET  /api/v1/jobs                        → list recent jobs
GET  /api/v1/metrics                     → Prometheus text format
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings_dep
from app.core.config import Settings
from app.models import Dataset
from app.models.monitoring import AnalysisJob, MonitoringRun, Incident
from app.services import job_service, monitoring_service
from app.services import dataset_service as ds

router = APIRouter(tags=["jobs & metrics"])

_START_TIME = time.time()


# ─────────────────────────────── job submit ───────────────────────────────────


@router.post("/datasets/{dataset_id}/jobs/monitor", tags=["jobs & metrics"])
def submit_monitor(
    dataset_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """Submit a monitoring run as a background job. Returns immediately with job_id."""
    def _run(db, settings, **kw):
        run = monitoring_service.run_monitor(db, settings, **kw)
        return monitoring_service.run_to_dict(run)

    body = body or {}
    ds.get_dataset(db, dataset_id)  # 404 instead of a foreign-key error for unknown datasets
    existing = job_service.find_active_duplicate(db, dataset_id, "monitor", body)
    if existing:
        return {"job_id": existing.id, "status": existing.status, "duplicate": True, "poll_url": f"/api/v1/jobs/{existing.id}"}
    factory = request.app.state.session_factory  # shared engine/pool, not a new engine per request
    job = job_service.create_job(db, dataset_id, "monitor", body)
    background_tasks.add_task(
        job_service.run_job_background,
        session_factory=factory,
        settings=settings,
        job_id=job.id,
        fn=_run,
        dataset_id=dataset_id,
        current_version_number=body.get("current_version"),
        reference_version_number=body.get("reference_version"),
        exclude_drift_columns=body.get("exclude_drift_columns"),
        config=body.get("config"),
    )
    return {"job_id": job.id, "status": "queued", "poll_url": f"/api/v1/jobs/{job.id}"}


@router.post("/datasets/{dataset_id}/jobs/drift", tags=["jobs & metrics"])
def submit_drift(
    dataset_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    from app.services import analysis_service

    def _run(db, settings, **kw):
        return analysis_service.run_drift(db, settings, **kw)

    body = body or {}
    ds.get_dataset(db, dataset_id)
    existing = job_service.find_active_duplicate(db, dataset_id, "drift", body)
    if existing:
        return {"job_id": existing.id, "status": existing.status, "duplicate": True, "poll_url": f"/api/v1/jobs/{existing.id}"}
    factory = request.app.state.session_factory
    job = job_service.create_job(db, dataset_id, "drift", body)
    background_tasks.add_task(
        job_service.run_job_background,
        session_factory=factory,
        settings=settings,
        job_id=job.id,
        fn=_run,
        dataset_id=dataset_id,
        current_version_number=body.get("current_version"),
        reference_version_number=body.get("reference_version"),
        thresholds=body.get("thresholds"),
        exclude_columns=body.get("exclude_columns", []),
    )
    return {"job_id": job.id, "status": "queued", "poll_url": f"/api/v1/jobs/{job.id}"}


# ────────────────────────────── job status ─────────────────────────────────


@router.get("/jobs/{job_id}", tags=["jobs & metrics"])
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Poll a job. When status=completed the run result is in result_summary."""
    return job_service.job_to_dict(job_service.get_job(db, job_id))


@router.get("/jobs", tags=["jobs & metrics"])
def list_jobs(
    dataset_id: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return [job_service.job_to_dict(j) for j in job_service.list_jobs(db, dataset_id, limit)]


# ─────────────────────────── Prometheus metrics ───────────────────────────────


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def prometheus_metrics(db: Session = Depends(get_db)) -> str:
    """Prometheus text format. Scrapeable by Prometheus / Grafana / Datadog."""
    uptime = time.time() - _START_TIME
    n_datasets = db.scalar(select(func.count(Dataset.id)).where(Dataset.is_deleted.is_(False))) or 0
    n_runs = db.scalar(select(func.count(MonitoringRun.id))) or 0
    n_open = db.scalar(select(func.count(Incident.id)).where(Incident.status.in_(["open", "acknowledged"]))) or 0
    n_jobs_queued = db.scalar(select(func.count(AnalysisJob.id)).where(AnalysisJob.status == "queued")) or 0
    n_jobs_running = db.scalar(select(func.count(AnalysisJob.id)).where(AnalysisJob.status == "running")) or 0
    n_jobs_failed = db.scalar(select(func.count(AnalysisJob.id)).where(AnalysisJob.status == "failed")) or 0

    lines = [
        "# HELP datasentinel_uptime_seconds Seconds since the process started.",
        "# TYPE datasentinel_uptime_seconds gauge",
        f"datasentinel_uptime_seconds {uptime:.1f}",
        "# HELP datasentinel_datasets_total Active (non-deleted) registered datasets.",
        "# TYPE datasentinel_datasets_total gauge",
        f"datasentinel_datasets_total {n_datasets}",
        "# HELP datasentinel_monitoring_runs_total Total monitoring runs ever.",
        "# TYPE datasentinel_monitoring_runs_total counter",
        f"datasentinel_monitoring_runs_total {n_runs}",
        "# HELP datasentinel_open_incidents Open or acknowledged incidents.",
        "# TYPE datasentinel_open_incidents gauge",
        f"datasentinel_open_incidents {n_open}",
        "# HELP datasentinel_jobs_queued Analysis jobs waiting to start.",
        "# TYPE datasentinel_jobs_queued gauge",
        f"datasentinel_jobs_queued {n_jobs_queued}",
        "# HELP datasentinel_jobs_running Analysis jobs currently running.",
        "# TYPE datasentinel_jobs_running gauge",
        f"datasentinel_jobs_running {n_jobs_running}",
        "# HELP datasentinel_jobs_failed_total Analysis jobs that ended in error.",
        "# TYPE datasentinel_jobs_failed_total counter",
        f"datasentinel_jobs_failed_total {n_jobs_failed}",
        "",
    ]
    return "\n".join(lines)
