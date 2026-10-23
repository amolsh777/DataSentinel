"""Async job management: create, poll, run in BackgroundTasks.

Every long analysis (monitor, drift, score, rca) is submitted as a job.
The endpoint returns immediately with ``{"job_id": "...", "status": "queued"}``.
The client polls ``GET /api/v1/jobs/{job_id}`` until ``status`` is ``completed``
or ``failed``.

The worker runs in FastAPI's BackgroundTasks thread pool — no external queue
needed for a hackathon. For production, replace ``_run_in_thread`` with Celery.
"""

from __future__ import annotations

import datetime
import logging
import traceback
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.database.base import new_id, utcnow
from app.models.monitoring import AnalysisJob
from app.utils.jsonsafe import deep_to_py

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────── CRUD ────────────────────────


def create_job(db: Session, dataset_id: str, job_type: str,
               request_body: dict[str, Any]) -> AnalysisJob:
    job = AnalysisJob(
        id=new_id(), dataset_id=dataset_id, job_type=job_type,
        status="queued", request_body=request_body,
        queued_at=utcnow(),
    )
    db.add(job); db.commit(); db.refresh(job)
    return job


def get_job(db: Session, job_id: str) -> AnalysisJob:
    j = db.get(AnalysisJob, job_id)
    if not j:
        raise NotFoundError(f"Job '{job_id}' not found.", code="JOB_NOT_FOUND")
    return j


def list_jobs(db: Session, dataset_id: str | None = None, limit: int = 50) -> list[AnalysisJob]:
    q = select(AnalysisJob).order_by(AnalysisJob.queued_at.desc()).limit(limit)
    if dataset_id:
        q = q.where(AnalysisJob.dataset_id == dataset_id)
    return list(db.scalars(q))


def job_to_dict(job: AnalysisJob) -> dict[str, Any]:
    return {
        "job_id": job.id, "dataset_id": job.dataset_id, "job_type": job.job_type,
        "status": job.status, "run_id": job.run_id,
        "queued_at": str(job.queued_at)[:19] if job.queued_at else None,
        "started_at": str(job.started_at)[:19] if job.started_at else None,
        "finished_at": str(job.finished_at)[:19] if job.finished_at else None,
        "error_message": job.error_message,
        "result_summary": _condensed_result(job.result) if job.result else None,
    }


def _condensed_result(result: dict | None) -> dict | None:
    if not result:
        return None
    return {k: result[k] for k in ("summary", "score", "schema_summary", "drift_summary", "rca_summary")
            if k in result}


# ──────────────────────────────────── worker ─────────────────────────────────


def run_job_background(
    session_factory,
    settings,
    job_id: str,
    fn: Callable[..., Any],
    **kwargs: Any,
) -> None:
    """Execute ``fn`` in a background thread and persist status/result.

    ``fn`` receives ``db`` and ``settings`` as kwargs plus whatever extra kwargs
    you pass. The return value is persisted as ``job.result`` (JSON-safe).
    """
    with session_factory() as db:
        job = db.get(AnalysisJob, job_id)
        if not job:
            logger.error("Job %s not found in background worker.", job_id)
            return
        job.status = "running"
        job.started_at = utcnow()
        db.commit()

    try:
        with session_factory() as db:
            result = fn(db=db, settings=settings, **kwargs)
            result_data = deep_to_py(result) if result is not None else {}
            run_id = result_data.get("id")  # MonitoringRun.id for monitor jobs

        with session_factory() as db:
            job = db.get(AnalysisJob, job_id)
            if job:
                job.status = "completed"
                job.finished_at = utcnow()
                job.result = result_data
                if run_id:
                    job.run_id = run_id
                db.commit()
        logger.info("Job %s (%s) completed.", job_id, fn.__name__)

    except Exception as exc:
        tb = traceback.format_exc()
        logger.error("Job %s failed: %s\n%s", job_id, exc, tb)
        with session_factory() as db:
            job = db.get(AnalysisJob, job_id)
            if job:
                job.status = "failed"
                job.finished_at = utcnow()
                job.error_message = f"{type(exc).__name__}: {exc}"
                db.commit()


__all__ = ["create_job", "get_job", "list_jobs", "job_to_dict", "run_job_background"]
