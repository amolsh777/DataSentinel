"""Background scheduler for automatic dataset monitoring (APScheduler 3.x).

Each dataset can have a schedule: ``{"interval_hours": N, "enabled": true,
"exclude_drift_columns": [...], "alert_webhook": "..."}``.

Schedules are stored in the dataset's ``project`` field as a JSON comment
(``__schedule__`` key in a JSON object) so we avoid a new table while keeping
everything in the existing schema. For a production system a dedicated table
is the right choice.

Lifecycle
---------
The scheduler starts at FastAPI startup (lifespan) and shuts down cleanly at
shutdown. Each job fetches the latest version of the dataset, runs the monitor
pipeline, and optionally fires the alert webhook if the run failed or produced
critical/high incidents.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# ──────────────────────────────── schedule storage ──────────────────────────
# Schedules are stored as JSON in the dataset's `notes` field under the key
# ``__ds_schedule__``. We read/write it with small helpers so the rest of
# the codebase does not need to know where it lives.

_MARKER = "__ds_schedule__"


def _read_schedule(notes: str | None) -> dict[str, Any] | None:
    if not notes:
        return None
    try:
        data = json.loads(notes)
        return data.get(_MARKER)
    except (json.JSONDecodeError, AttributeError):
        return None


def _write_schedule(notes: str | None, schedule: dict[str, Any] | None) -> str:
    try:
        data = json.loads(notes) if notes else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    if schedule is None:
        data.pop(_MARKER, None)
    else:
        data[_MARKER] = schedule
    return json.dumps(data)


# ─────────────────────────────── job function ────────────────────────────────


def _monitoring_job(dataset_id: str, settings_dict: dict[str, Any]) -> None:
    """Run by APScheduler in a background thread."""
    try:
        from app.core.config import Settings
        from app.database.session import build_engine, build_session_factory
        from app.services import monitoring_service

        settings = Settings(**settings_dict)
        engine = build_engine(settings.database_url)
        Session_ = build_session_factory(engine)
        with Session_() as db:
            run = monitoring_service.run_monitor(db, settings, dataset_id, None)
            summary = run.summary or {}
            schedule = _get_dataset_schedule(db, dataset_id)
            if schedule and schedule.get("alert_webhook"):
                _maybe_alert(schedule["alert_webhook"], summary,
                             dataset_id, run.id, settings)
            logger.info("Scheduled run %s for dataset %s completed", run.id, dataset_id)
    except Exception as exc:
        logger.error("Scheduled monitoring job failed for dataset %s: %s", dataset_id, exc)


def _maybe_alert(webhook_url: str, summary: dict[str, Any],
                 dataset_id: str, run_id: str, settings: Any) -> None:
    from app.services.alert_service import send_webhook

    q = summary.get("quality", {})
    incidents = summary.get("incidents", {})
    should_alert = (
        q.get("status") in ("failing", "degraded")
        or (incidents.get("created", 0) > 0)
    )
    if should_alert:
        send_webhook(webhook_url, summary, dataset_id, run_id)


def _get_dataset_schedule(db: Session, dataset_id: str) -> dict[str, Any] | None:
    from app.models import Dataset
    d = db.get(Dataset, dataset_id)
    return _read_schedule(d.notes if d else None) if d else None


# ─────────────────────────────── public API ──────────────────────────────────


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()
    logger.info("APScheduler started.")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler stopped.")


def _job_id(dataset_id: str) -> str:
    return f"monitor_{dataset_id}"


def set_schedule(
    db: Session,
    settings: Any,
    dataset_id: str,
    interval_hours: int | None,
    *,
    exclude_drift_columns: list[str] | None = None,
    alert_webhook: str | None = None,
) -> dict[str, Any]:
    """Set or remove a schedule for a dataset. ``interval_hours=None`` removes it."""
    from app.models import Dataset

    d = db.get(Dataset, dataset_id)
    if d is None:
        from app.core.errors import NotFoundError
        raise NotFoundError(f"Dataset '{dataset_id}' not found.", code="DATASET_NOT_FOUND")

    job_id = _job_id(dataset_id)
    if _scheduler and _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)

    if interval_hours is None or interval_hours <= 0:
        d.notes = _write_schedule(d.notes, None)
        db.commit()
        return {"scheduled": False, "dataset_id": dataset_id}

    settings_dict = {"database_url": settings.database_url,
                     "storage_dir": str(settings.storage_dir)}
    schedule_cfg = {
        "interval_hours": interval_hours,
        "enabled": True,
        "exclude_drift_columns": exclude_drift_columns or [],
        "alert_webhook": alert_webhook or "",
    }
    d.notes = _write_schedule(d.notes, schedule_cfg)
    db.commit()

    if _scheduler:
        _scheduler.add_job(
            _monitoring_job,
            trigger=IntervalTrigger(hours=interval_hours),
            id=job_id,
            args=[dataset_id, settings_dict],
            replace_existing=True,
        )
        logger.info("Scheduled dataset %s every %d hours", dataset_id, interval_hours)

    return {"scheduled": True, "dataset_id": dataset_id, "interval_hours": interval_hours,
            "config": schedule_cfg}


def get_schedule(db: Session, dataset_id: str) -> dict[str, Any]:
    from app.models import Dataset
    d = db.get(Dataset, dataset_id)
    if d is None:
        from app.core.errors import NotFoundError
        raise NotFoundError(f"Dataset '{dataset_id}' not found.", code="DATASET_NOT_FOUND")
    cfg = _read_schedule(d.notes)
    job_id = _job_id(dataset_id)
    next_run = None
    if _scheduler and (job := _scheduler.get_job(job_id)):
        nr = job.next_run_time
        next_run = nr.isoformat() if nr else None
    return {
        "scheduled": bool(cfg and cfg.get("enabled")),
        "dataset_id": dataset_id,
        "config": cfg,
        "next_run": next_run,
    }


def restore_schedules(db: Session, settings: Any) -> None:
    """Re-register all active schedules on startup (after DB restart)."""
    from sqlalchemy import select
    from app.models import Dataset

    datasets = db.scalars(select(Dataset)).all()
    for d in datasets:
        cfg = _read_schedule(d.notes)
        if not cfg or not cfg.get("enabled") or not cfg.get("interval_hours"):
            continue
        settings_dict = {"database_url": settings.database_url,
                         "storage_dir": str(settings.storage_dir)}
        job_id = _job_id(d.id)
        if _scheduler and not _scheduler.get_job(job_id):
            _scheduler.add_job(
                _monitoring_job,
                trigger=IntervalTrigger(hours=cfg["interval_hours"]),
                id=job_id,
                args=[d.id, settings_dict],
                replace_existing=True,
            )
            logger.info("Restored schedule for dataset %s (%d h)", d.id, cfg["interval_hours"])


__all__ = [
    "start_scheduler", "stop_scheduler",
    "set_schedule", "get_schedule", "restore_schedules",
]
