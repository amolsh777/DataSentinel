"""Monitoring runs, incidents, lineage, reports, alerting and scheduler endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings_dep
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.models import Dataset
from app.schemas.common import ErrorResponse
from app.services import alert_service, lineage_service, monitoring_service, report_service, scheduler_service
from app.services import dataset_service as ds

_E = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}

runs_router = APIRouter(prefix="/datasets", tags=["monitoring runs"])
incidents_router = APIRouter(prefix="", tags=["incidents"])
lineage_router = APIRouter(prefix="/lineage", tags=["lineage"])
reports_router = APIRouter(prefix="/runs", tags=["reports"])
alert_router = APIRouter(prefix="/datasets", tags=["alerts"])
scheduler_router = APIRouter(prefix="/datasets", tags=["scheduler"])


# ─────────────────────────────────── monitoring runs ─────────────────────────

@runs_router.post("/{dataset_id}/monitor", responses=_E)
def trigger_monitor(
    dataset_id: str,
    body: dict[str, Any] = {},
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    background_tasks: BackgroundTasks = None,
) -> dict[str, Any]:
    """Run the full monitoring pipeline and persist results. Returns immediately with the run id."""
    ds.get_dataset(db, dataset_id)
    run = monitoring_service.run_monitor(
        db, settings, dataset_id,
        current_version_number=body.get("current_version"),
        reference_version_number=body.get("reference_version"),
        config=body.get("config"),
        exclude_drift_columns=body.get("exclude_drift_columns"),
    )
    # Fire webhook if configured in the alert settings.
    webhook = body.get("alert_webhook") or (run.config or {}).get("alert_webhook")
    if webhook and run.status == "completed":
        alert_service.send_webhook(webhook, run.summary or {}, dataset_id, run.id)
    return monitoring_service.run_to_dict(run)


@runs_router.get("/{dataset_id}/runs", responses=_E)
def list_runs(
    dataset_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ds.get_dataset(db, dataset_id)
    runs, total = monitoring_service.list_runs(db, dataset_id, limit, offset)
    return {"total": total, "items": [monitoring_service.run_to_dict(r) for r in runs]}


@runs_router.get("/{dataset_id}/runs/history", responses=_E)
def run_history_summary(
    dataset_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Condensed timeline: one row per run, for the Run History page."""
    ds.get_dataset(db, dataset_id)
    runs, _ = monitoring_service.list_runs(db, dataset_id, limit, 0)
    result = []
    for r in runs:
        s = r.summary or {}
        score = (s.get("score") or {}).get("overall")
        q = s.get("quality") or {}
        drift = s.get("drift") or {}
        schema = s.get("schema") or {}
        inc = s.get("incidents") or {}
        result.append({
            "run_id": r.id, "status": r.status,
            "started_at": str(r.started_at)[:19],
            "finished_at": str(r.finished_at)[:19] if r.finished_at else None,
            "current_version": s.get("current_version_number"),
            "reference_version": s.get("reference_version_number"),
            "health": q.get("status"), "score": score,
            "failures": (q.get("outcomes") or {}).get("fail", 0),
            "warnings": (q.get("outcomes") or {}).get("warn", 0),
            "drifted_features": drift.get("features_drifted", 0),
            "schema_changes": schema.get("total_changes", 0),
            "breaking_changes": schema.get("breaking_changes", 0),
            "incidents_created": inc.get("created", 0),
            "incidents_updated": inc.get("updated", 0),
        })
    return result


@runs_router.get("/{dataset_id}/runs/{run_id}", responses=_E)
def get_run(dataset_id: str, run_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    run = monitoring_service.get_run(db, run_id)
    if run.dataset_id != dataset_id:
        raise NotFoundError("Run not found for this dataset.", code="RUN_NOT_FOUND")
    checks = monitoring_service.get_run_checks(db, run_id)
    drift = monitoring_service.get_run_drift(db, run_id)
    return {
        **monitoring_service.run_to_dict(run),
        "check_results": [
            {"rule_name": r.rule_name, "rule_type": r.rule_type, "outcome": r.outcome,
             "severity": r.severity, "columns": r.columns or [],
             "affected_pct": r.affected_pct, "affected_count": r.affected_count,
             "evidence": r.evidence or {}}
            for r in checks
        ],
        "drift_results": [
            {"column": r.column_name, "kind": r.feature_type, "status": r.status,
             "drift_detected": r.drift_detected, "effect_size": r.effect_size,
             "statistic": r.statistic, "details": r.details or {}}
            for r in drift
        ],
    }


# ──────────────────────────────────── incidents ──────────────────────────────

@incidents_router.get("/incidents", tags=["incidents"], responses=_E)
def list_incidents_global(
    dataset_id: str | None = Query(None),
    incident_type: str | None = Query(None),
    status: list[str] | None = Query(None),
    severity: list[str] | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    incidents, total = monitoring_service.list_incidents(
        db, dataset_id=dataset_id, incident_type=incident_type,
        status=status or ["open", "acknowledged"],
        severity=severity, search=search, limit=limit, offset=offset)
    return {
        "total": total,
        "items": [monitoring_service.incident_to_dict(i) for i in incidents],
    }


@incidents_router.get("/incidents/{incident_id}", tags=["incidents"], responses=_E)
def get_incident(incident_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return monitoring_service.incident_to_dict(
        monitoring_service.get_incident(db, incident_id),
        include_lineage=True, db=db)


@incidents_router.patch("/incidents/{incident_id}", tags=["incidents"], responses=_E)
def update_incident(
    incident_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Transition status: open → acknowledged → resolved → open (recurrence)."""
    new_status = body.get("status")
    if not new_status:
        raise AppError("'status' is required.", code="MISSING_FIELD")
    incident = monitoring_service.update_incident_status(
        db, incident_id, new_status,
        note=body.get("note"), changed_by=body.get("changed_by"))
    return monitoring_service.incident_to_dict(incident)


@incidents_router.get("/incidents/{incident_id}/history", tags=["incidents"], responses=_E)
def get_incident_history(incident_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    history = monitoring_service.get_incident_history(db, incident_id)
    return [
        {"from_status": h.from_status, "to_status": h.to_status,
         "note": h.note, "changed_by": h.changed_by, "changed_at": str(h.changed_at)[:19]}
        for h in history
    ]


# ────────────────────────────────────── lineage ──────────────────────────────

@lineage_router.get("", tags=["lineage"], responses=_E)
def get_graph(db: Session = Depends(get_db)) -> dict[str, Any]:
    return lineage_service.get_full_graph(db)


@lineage_router.post("", tags=["lineage"], status_code=201, responses=_E)
def create_asset(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    return lineage_service.asset_to_dict(lineage_service.create_asset(db, body))


@lineage_router.patch("/{asset_id}", tags=["lineage"], responses=_E)
def update_asset(asset_id: str, body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    return lineage_service.asset_to_dict(lineage_service.update_asset(db, asset_id, body))


@lineage_router.delete("/{asset_id}", tags=["lineage"], status_code=204, responses=_E)
def delete_asset(asset_id: str, db: Session = Depends(get_db)) -> Response:
    lineage_service.delete_asset(db, asset_id)
    return Response(status_code=204)


@lineage_router.post("/edges", tags=["lineage"], status_code=201, responses=_E)
def create_edge(body: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    edge = lineage_service.create_edge(
        db, body["source_asset_id"], body["target_asset_id"],
        source_columns=body.get("source_columns"),
        dependency_kind=body.get("dependency_kind", "known"),
        description=body.get("description"),
    )
    assets = {a.id: a for a in lineage_service._all_assets(db)}
    return lineage_service.edge_to_dict(edge, assets)


@lineage_router.delete("/edges/{edge_id}", tags=["lineage"], status_code=204, responses=_E)
def delete_edge(edge_id: str, db: Session = Depends(get_db)) -> Response:
    lineage_service.delete_edge(db, edge_id)
    return Response(status_code=204)


@lineage_router.get("/{asset_id}/upstream", tags=["lineage"], responses=_E)
def get_upstream(asset_id: str, depth: int = Query(10, ge=1, le=20), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return lineage_service.get_upstream(db, asset_id, depth)


@lineage_router.get("/{asset_id}/downstream", tags=["lineage"], responses=_E)
def get_downstream(asset_id: str, depth: int = Query(10, ge=1, le=20), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return lineage_service.get_downstream(db, asset_id, depth)


@lineage_router.get("/impact/{dataset_id}", tags=["lineage"], responses=_E)
def get_impact(
    dataset_id: str,
    columns: list[str] = Query(...),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ds.get_dataset(db, dataset_id)
    affected = lineage_service.get_affected_assets(db, dataset_id, columns)
    return {"dataset_id": dataset_id, "columns": columns, "affected_assets": affected}


# ─────────────────────────────────────── reports ─────────────────────────────

@reports_router.get("/{run_id}/report", tags=["reports"], responses=_E)
def get_report(
    run_id: str,
    format: str = Query("html", pattern="^(html|json|csv)$"),
    db: Session = Depends(get_db),
) -> Response:
    run = monitoring_service.get_run(db, run_id)
    dataset = db.get(Dataset, run.dataset_id)
    if not dataset:
        raise NotFoundError("Dataset not found.", code="DATASET_NOT_FOUND")
    checks = monitoring_service.get_run_checks(db, run_id)
    drift = monitoring_service.get_run_drift(db, run_id)
    incidents_list, _ = monitoring_service.list_incidents(
        db, dataset_id=run.dataset_id, limit=200, status=None)
    inc_dicts = [monitoring_service.incident_to_dict(i) for i in incidents_list]
    schema_diff = (run.summary or {}).get("schema_diff_snapshot")

    if format == "html":
        html = report_service.generate_html_report(run, dataset, checks, drift, inc_dicts, schema_diff)
        return HTMLResponse(content=html, headers={
            "Content-Disposition": f'attachment; filename="report_{run_id[:8]}.html"'})
    if format == "json":
        body = report_service.generate_json_report(run, dataset, checks, drift, inc_dicts, schema_diff)
        return Response(content=body, media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="report_{run_id[:8]}.json"'})
    body = report_service.generate_csv_report(checks, inc_dicts)
    return Response(content=body, media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="report_{run_id[:8]}.csv"'})


# ─────────────────────────────────────── alerts ──────────────────────────────

@alert_router.post("/{dataset_id}/alerts/test", tags=["alerts"], responses=_E)
def test_alert(dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
    webhook_url = body.get("webhook_url")
    if not webhook_url:
        raise AppError("'webhook_url' is required.", code="MISSING_FIELD")
    return alert_service.send_test_alert(webhook_url)


# ──────────────────────────────────── scheduler ──────────────────────────────

@scheduler_router.post("/{dataset_id}/schedule", tags=["scheduler"], responses=_E)
def set_schedule(
    dataset_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    """Set or remove automatic monitoring. ``interval_hours: null`` removes the schedule."""
    return scheduler_service.set_schedule(
        db, settings, dataset_id,
        interval_hours=body.get("interval_hours"),
        exclude_drift_columns=body.get("exclude_drift_columns"),
        alert_webhook=body.get("alert_webhook"),
    )


@scheduler_router.get("/{dataset_id}/schedule", tags=["scheduler"], responses=_E)
def get_schedule(dataset_id: str, db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings_dep)) -> dict[str, Any]:
    return scheduler_service.get_schedule(db, dataset_id)
