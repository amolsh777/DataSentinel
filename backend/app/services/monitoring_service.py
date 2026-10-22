"""Persisted monitoring runs, incident deduplication and status flow.

Public entry points
-------------------
``run_monitor``       — the main pipeline; runs quality + drift + score + RCA,
                        persists everything, generates/updates incidents.
``list_runs``         — paginated run history per dataset.
``get_run``           — full run detail including check results and drift.
``list_incidents``    — filtered, paginated incident list.
``get_incident``      — incident detail with lineage impact and history.
``update_incident``   — open → acknowledged → resolved → reopened.

Status flow
-----------
open → acknowledged → resolved
resolved → open (re-opened: recurrence_count += 1)
Any → ignored (permanent suppression)

Fingerprint deduplication
-------------------------
Same dataset + rule name + sorted columns = same fingerprint. A new run that
fires the same fingerprint as an already-open incident updates `last_seen_at`
and `occurrence_count`. A run that fires the same fingerprint as a *resolved*
incident re-opens it and increments `recurrence_count`.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.analytics.data_drift import detect_drift
from app.analytics.quality_checks import run_quality_checks, summarize_results
from app.analytics.quality_score import compute_quality_score
from app.analytics.root_cause import analyze as rca_analyze
from app.analytics.schema_drift import compare_schemas
from app.analytics.severity import (
    SEVERITY_ORDER,
    build_recommendations,
    compute_severity_score,
    make_fingerprint,
)
from app.core.config import Settings
from app.core.errors import AppError, NotFoundError
from app.models import (
    DriftResult,
    Incident,
    IncidentStatusHistory,
    MonitoringRun,
    QualityCheckResult,
)
from app.services import baseline_service, dataset_service as ds, lineage_service, quality_service
from app.utils.jsonsafe import deep_to_py, to_py

logger = logging.getLogger(__name__)

VALID_TRANSITIONS = {
    "open":         {"acknowledged", "resolved", "ignored"},
    "acknowledged": {"resolved", "open", "ignored"},
    "resolved":     {"open"},
    "ignored":      {"open"},
}


class MonitorError(AppError):
    status_code = 422
    code = "MONITOR_ERROR"


class StatusError(AppError):
    status_code = 422
    code = "STATUS_ERROR"


# ─────────────────────────────────── run helpers ─────────────────────────────


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _finish(db: Session, run: MonitoringRun, summary: dict[str, Any],
            status: str, error: str | None = None) -> None:
    run.status = status
    run.summary = deep_to_py(summary)
    run.error_message = error
    run.finished_at = _now()
    db.commit()


# ────────────────────────── incident deduplication ───────────────────────────


def _upsert_incident(
    db: Session, run: MonitoringRun, result: dict[str, Any],
    incident_type: str, downstream_assets: list[str],
) -> Incident | None:
    """Create or update an incident for one quality/schema/drift finding."""
    col = result.get("columns", [])
    rule_name = result.get("rule_name") or result.get("issue_name") or incident_type
    rule_type = result.get("rule_type") or incident_type
    rule_severity = result.get("rule_severity") or result.get("severity") or "medium"
    affected_pct = result.get("affected_pct") or 0.0

    fp = make_fingerprint(run.dataset_id, incident_type, rule_name, col)
    score, severity_label, breakdown = compute_severity_score(
        rule_severity, affected_pct, incident_type, len(downstream_assets),
    )
    recs = build_recommendations(incident_type, rule_name, rule_type, col, affected_pct,
                                 downstream_assets, score)

    existing = db.scalar(
        select(Incident).where(
            and_(Incident.fingerprint == fp,
                 Incident.status.in_(["open", "acknowledged", "ignored"]))
        )
    )
    now = _now()
    if existing:
        # Update without re-firing if already acknowledged/ignored.
        existing.last_run_id = run.id
        existing.last_seen_at = now
        existing.occurrence_count += 1
        existing.observed = deep_to_py(result.get("observed"))
        existing.affected_count = result.get("affected_count")
        existing.affected_pct = affected_pct
        existing.evidence = deep_to_py(result.get("evidence", {}))
        existing.remediation = {"downstream_assets": downstream_assets, "recommendations": recs,
                                "severity_score": score, "severity_breakdown": breakdown}
        existing.severity = severity_label
        db.commit()
        return existing

    # Resolved incident re-opens.
    resolved = db.scalar(
        select(Incident).where(
            and_(Incident.fingerprint == fp, Incident.status == "resolved")
        ).order_by(Incident.resolved_at.desc())
    )
    if resolved:
        resolved.status = "open"
        resolved.last_run_id = run.id
        resolved.last_seen_at = now
        resolved.occurrence_count += 1
        resolved.recurrence_count += 1
        resolved.resolved_at = None
        resolved.observed = deep_to_py(result.get("observed"))
        resolved.affected_count = result.get("affected_count")
        resolved.affected_pct = affected_pct
        resolved.evidence = deep_to_py(result.get("evidence", {}))
        resolved.remediation = {"downstream_assets": downstream_assets, "recommendations": recs,
                                "severity_score": score, "severity_breakdown": breakdown}
        resolved.severity = severity_label
        db.add(IncidentStatusHistory(
            incident_id=resolved.id, from_status="resolved", to_status="open",
            note="Condition recurred.", changed_at=now))
        db.commit()
        return resolved

    # New incident.
    incident = Incident(
        dataset_id=run.dataset_id, first_run_id=run.id, last_run_id=run.id,
        fingerprint=fp, incident_type=incident_type, rule_name=rule_name,
        title=result.get("message") or rule_name,
        columns=col, severity=severity_label,
        observed=deep_to_py(result.get("observed")),
        expected=result.get("expected"),
        affected_count=result.get("affected_count"),
        affected_pct=affected_pct,
        evidence=deep_to_py(result.get("evidence", {})),
        remediation={"downstream_assets": downstream_assets, "recommendations": recs,
                     "severity_score": score, "severity_breakdown": breakdown},
        status="open",
        first_seen_at=now, last_seen_at=now,
    )
    db.add(incident)
    db.commit()
    db.refresh(incident)
    db.add(IncidentStatusHistory(
        incident_id=incident.id, from_status=None, to_status="open",
        note="First detected.", changed_at=now))
    db.commit()
    return incident


# ────────────────────────────── main pipeline ────────────────────────────────


def run_monitor(
    db: Session,
    settings: Settings,
    dataset_id: str,
    current_version_number: int | None,
    reference_version_number: int | None = None,
    *,
    config: dict[str, Any] | None = None,
    exclude_drift_columns: list[str] | None = None,
) -> MonitoringRun:
    """Run the full monitoring pipeline and persist everything.

    Steps:
    1. Resolve current and reference versions.
    2. Run quality checks + quality score.
    3. Run schema diff (if reference available).
    4. Run statistical drift detection (if reference available).
    5. Generate root-cause findings.
    6. Resolve downstream lineage impact per issue.
    7. Persist run, check results, drift results.
    8. Upsert incidents (deduplicate by fingerprint).
    9. Return the completed MonitoringRun.
    """
    cfg = config or {}
    current = ds.get_version(db, dataset_id, current_version_number)
    reference, ref_source = baseline_service.resolve_reference_version(
        db, dataset_id,
        reference_version_number=reference_version_number,
        use_active_baseline=True,
    )

    run = MonitoringRun(
        dataset_id=dataset_id,
        current_version_id=current.id,
        reference_version_id=reference.id if reference else None,
        baseline_id=(ref_source or {}).get("baseline_id"),
        status="running",
        config=cfg,
        started_at=_now(),
    )
    db.add(run); db.commit(); db.refresh(run)
    logger.info("Monitor run %s started — dataset %s v%s", run.id, dataset_id, current.version_number)

    try:
        cur_df = ds.load_version_dataframe(settings, current)
        cur_profiles = ds.column_profiles_as_dicts(current)
        ref_df = ds.load_version_dataframe(settings, reference) if reference else None
        ref_profiles = ds.column_profiles_as_dicts(reference) if reference else None

        # ── quality ──────────────────────────────────────────────────────────
        rules = [quality_service.rule_to_dict(r) for r in quality_service.list_rules(
            db, dataset_id=dataset_id, enabled_only=True)]
        quality_ctx = quality_service.build_validation_context(db, settings, rules, reference, current)
        check_results = run_quality_checks(cur_df, rules, quality_ctx)
        quality_summary = summarize_results(check_results)
        score_report = compute_quality_score(check_results)

        # ── schema diff ───────────────────────────────────────────────────────
        schema_diff = compare_schemas(ref_profiles, cur_profiles) if ref_profiles else None

        # ── drift ─────────────────────────────────────────────────────────────
        drift_report = None
        if ref_df is not None and ref_profiles is not None:
            drift_report = detect_drift(
                ref_df, cur_df,
                reference_profiles=ref_profiles, current_profiles=cur_profiles,
                exclude_columns=exclude_drift_columns,
                thresholds=cfg.get("drift_thresholds"),
            )

        # ── root cause ────────────────────────────────────────────────────────
        rca = rca_analyze(
            current_profile={"columns": cur_profiles},
            quality_results=check_results,
            drift_report=drift_report,
            schema_diff=schema_diff,
            reference_profile={"columns": ref_profiles} if ref_profiles else None,
        )

        # ── persist check results ─────────────────────────────────────────────
        for r in check_results:
            db.add(QualityCheckResult(
                run_id=run.id,
                rule_id=r.get("rule_id"),
                rule_type=r["rule_type"],
                rule_name=r["rule_name"],
                columns=r.get("columns", []),
                outcome=r["outcome"],
                severity=r["severity"],
                observed=deep_to_py(r.get("observed")),
                expected=r.get("expected"),
                affected_count=r.get("affected_count"),
                affected_pct=r.get("affected_pct"),
                evidence=deep_to_py(r.get("evidence", {})),
            ))

        # ── persist drift results ─────────────────────────────────────────────
        if drift_report:
            for f in drift_report["features"]:
                es = f.get("effect_sizes") or {}
                db.add(DriftResult(
                    run_id=run.id,
                    column_name=f["name"],
                    feature_type=f.get("kind", ""),
                    method=f.get("primary_method", ""),
                    status=f.get("status", ""),
                    drift_detected=f.get("status") == "drifted",
                    statistic=es.get("psi") or es.get("js_distance"),
                    effect_size=es.get("wasserstein_normalized") or es.get("unseen_share_pct"),
                    reference_n=f.get("reference_n", 0),
                    current_n=f.get("current_n", 0),
                    reference_missing_pct=(f.get("missingness") or {}).get("reference_pct"),
                    current_missing_pct=(f.get("missingness") or {}).get("current_pct"),
                    details=deep_to_py({k: v for k, v in f.items()
                                       if k not in ("name", "kind", "status")}),
                ))
        db.commit()

        # ── incidents ─────────────────────────────────────────────────────────
        incidents_created = 0
        incidents_updated = 0

        def _impact(cols: list[str]) -> list[str]:
            affected = lineage_service.get_affected_assets(db, dataset_id, cols)
            return [a["name"] for a in affected]

        for r in check_results:
            if r["outcome"] not in ("fail", "warn"):
                continue
            before = db.scalar(select(func.count(Incident.id)).where(
                Incident.fingerprint == make_fingerprint(
                    dataset_id, "quality", r["rule_name"], r.get("columns", []))))
            _upsert_incident(db, run, r, "quality", _impact(r.get("columns", [])))
            after = db.scalar(select(func.count(Incident.id)).where(
                Incident.fingerprint == make_fingerprint(
                    dataset_id, "quality", r["rule_name"], r.get("columns", []))))
            incidents_created += int(after > before)
            incidents_updated += int(after == before)

        if schema_diff:
            for ch in schema_diff.get("changes", []):
                if ch["severity"] in ("info",):
                    continue
                finding = {"rule_name": ch["change_type"], "rule_type": "schema",
                           "columns": ch["columns"], "rule_severity": ch["severity"],
                           "severity": ch["severity"], "message": ch["explanation"],
                           "evidence": deep_to_py(ch.get("evidence", {})),
                           "observed": {"change_type": ch["change_type"]}}
                before = db.scalar(select(func.count(Incident.id)).where(
                    Incident.fingerprint == make_fingerprint(
                        dataset_id, "schema", ch["change_type"], ch["columns"])))
                _upsert_incident(db, run, finding, "schema", _impact(ch["columns"]))
                after = db.scalar(select(func.count(Incident.id)).where(
                    Incident.fingerprint == make_fingerprint(
                        dataset_id, "schema", ch["change_type"], ch["columns"])))
                incidents_created += int(after > before)
                incidents_updated += int(after == before)

        if drift_report:
            for f in drift_report["features"]:
                if f.get("status") != "drifted":
                    continue
                finding = {"rule_name": f"drift:{f['name']}", "rule_type": "drift",
                           "columns": [f["name"]], "rule_severity": f["severity"],
                           "severity": f["severity"], "message": f.get("explanation", ""),
                           "evidence": deep_to_py(f.get("effect_sizes") or {}),
                           "observed": deep_to_py(f.get("effect_sizes"))}
                before = db.scalar(select(func.count(Incident.id)).where(
                    Incident.fingerprint == make_fingerprint(
                        dataset_id, "drift", f"drift:{f['name']}", [f["name"]])))
                _upsert_incident(db, run, finding, "drift", _impact([f["name"]]))
                after = db.scalar(select(func.count(Incident.id)).where(
                    Incident.fingerprint == make_fingerprint(
                        dataset_id, "drift", f"drift:{f['name']}", [f["name"]])))
                incidents_created += int(after > before)
                incidents_updated += int(after == before)

        summary = {
            "current_version_number": current.version_number,
            "reference_version_number": reference.version_number if reference else None,
            "reference_source": ref_source,
            "quality": quality_summary,
            "score": {"overall": score_report.get("score"), "status": score_report.get("status")},
            "schema": schema_diff["summary"] if schema_diff else None,
            "drift": drift_report["summary"] if drift_report else None,
            "rca": rca.get("summary"),
            "incidents": {"created": incidents_created, "updated": incidents_updated},
            "rules_evaluated": len(rules),
        }
        _finish(db, run, summary, "completed")
        logger.info("Monitor run %s completed — %d incidents created, %d updated",
                    run.id, incidents_created, incidents_updated)
        return run

    except Exception as exc:
        logger.exception("Monitor run %s failed: %s", run.id, exc)
        _finish(db, run, {}, "failed", str(exc))
        raise


# ────────────────────────────── run queries ──────────────────────────────────


def list_runs(db: Session, dataset_id: str | None = None,
              limit: int = 50, offset: int = 0) -> tuple[list[MonitoringRun], int]:
    q = select(MonitoringRun).order_by(MonitoringRun.started_at.desc())
    if dataset_id:
        q = q.where(MonitoringRun.dataset_id == dataset_id)
    total = db.scalar(q.with_only_columns(func.count(MonitoringRun.id)))
    runs = list(db.scalars(q.limit(limit).offset(offset)))
    return runs, total or 0


def get_run(db: Session, run_id: str) -> MonitoringRun:
    r = db.get(MonitoringRun, run_id)
    if not r:
        raise NotFoundError(f"Run '{run_id}' not found.", code="RUN_NOT_FOUND")
    return r


def get_run_checks(db: Session, run_id: str) -> list[QualityCheckResult]:
    get_run(db, run_id)
    return list(db.scalars(select(QualityCheckResult).where(QualityCheckResult.run_id == run_id)))


def get_run_drift(db: Session, run_id: str) -> list[DriftResult]:
    get_run(db, run_id)
    return list(db.scalars(select(DriftResult).where(DriftResult.run_id == run_id)))


def run_to_dict(run: MonitoringRun) -> dict[str, Any]:
    return {
        "id": run.id, "dataset_id": run.dataset_id, "status": run.status,
        "config": run.config, "summary": run.summary, "error_message": run.error_message,
        "started_at": to_py(run.started_at), "finished_at": to_py(run.finished_at),
        "current_version_id": run.current_version_id,
        "reference_version_id": run.reference_version_id,
        "baseline_id": run.baseline_id,
    }


# ─────────────────────────────── incident queries ────────────────────────────


def list_incidents(
    db: Session, *,
    dataset_id: str | None = None,
    incident_type: str | None = None,
    status: str | list[str] | None = None,
    severity: str | list[str] | None = None,
    search: str | None = None,
    limit: int = 50, offset: int = 0,
) -> tuple[list[Incident], int]:
    q = select(Incident).order_by(
        Incident.severity.in_(["critical", "high"]).desc(),
        Incident.last_seen_at.desc())
    if dataset_id:
        q = q.where(Incident.dataset_id == dataset_id)
    if incident_type:
        q = q.where(Incident.incident_type == incident_type)
    if status:
        statuses = [status] if isinstance(status, str) else status
        q = q.where(Incident.status.in_(statuses))
    if severity:
        severities = [severity] if isinstance(severity, str) else severity
        q = q.where(Incident.severity.in_(severities))
    if search:
        term = f"%{search.lower()}%"
        q = q.where(or_(func.lower(Incident.title).like(term),
                        func.lower(Incident.rule_name).like(term)))
    total = db.scalar(q.with_only_columns(func.count(Incident.id)))
    incidents = list(db.scalars(q.limit(limit).offset(offset)))
    return incidents, total or 0


def get_incident(db: Session, incident_id: str) -> Incident:
    i = db.get(Incident, incident_id)
    if not i:
        raise NotFoundError(f"Incident '{incident_id}' not found.", code="INCIDENT_NOT_FOUND")
    return i


def get_incident_history(db: Session, incident_id: str) -> list[IncidentStatusHistory]:
    get_incident(db, incident_id)
    return list(db.scalars(
        select(IncidentStatusHistory)
        .where(IncidentStatusHistory.incident_id == incident_id)
        .order_by(IncidentStatusHistory.changed_at)))


def update_incident_status(
    db: Session, incident_id: str, new_status: str, *,
    note: str | None = None, changed_by: str | None = None,
) -> Incident:
    incident = get_incident(db, incident_id)
    allowed = VALID_TRANSITIONS.get(incident.status, set())
    if new_status not in allowed:
        raise StatusError(
            f"Cannot transition from '{incident.status}' to '{new_status}'. "
            f"Allowed: {sorted(allowed)}.",
            details={"allowed": sorted(allowed)},
        )
    old_status = incident.status
    incident.status = new_status
    now = _now()
    if new_status == "resolved":
        incident.resolved_at = now
    elif old_status == "resolved":
        incident.recurrence_count += 1
        incident.resolved_at = None
    db.add(IncidentStatusHistory(
        incident_id=incident_id, from_status=old_status, to_status=new_status,
        note=note, changed_by=changed_by, changed_at=now))
    db.commit(); db.refresh(incident)
    return incident


def incident_to_dict(incident: Incident, include_lineage: bool = False,
                     db: Session | None = None) -> dict[str, Any]:
    remediation = incident.remediation or {}
    d: dict[str, Any] = {
        "id": incident.id, "dataset_id": incident.dataset_id,
        "first_run_id": incident.first_run_id, "last_run_id": incident.last_run_id,
        "fingerprint": incident.fingerprint, "incident_type": incident.incident_type,
        "rule_name": incident.rule_name, "title": incident.title,
        "columns": incident.columns or [], "severity": incident.severity,
        "observed": incident.observed, "expected": incident.expected,
        "affected_count": incident.affected_count, "affected_pct": incident.affected_pct,
        "evidence": incident.evidence or {}, "status": incident.status,
        "occurrence_count": incident.occurrence_count,
        "recurrence_count": incident.recurrence_count,
        "first_seen_at": to_py(incident.first_seen_at),
        "last_seen_at": to_py(incident.last_seen_at),
        "resolved_at": to_py(incident.resolved_at),
        "created_at": to_py(incident.created_at),
        "updated_at": to_py(incident.updated_at),
        "downstream_assets": remediation.get("downstream_assets", []),
        "recommendations": remediation.get("recommendations", []),
        "severity_score": remediation.get("severity_score"),
        "severity_breakdown": remediation.get("severity_breakdown"),
    }
    if include_lineage and db:
        affected = lineage_service.get_affected_assets(
            db, incident.dataset_id, incident.columns or [])
        d["lineage_impact"] = affected
    return d


__all__ = [
    "MonitorError", "StatusError", "VALID_TRANSITIONS",
    "run_monitor", "list_runs", "get_run", "get_run_checks", "get_run_drift", "run_to_dict",
    "list_incidents", "get_incident", "get_incident_history",
    "update_incident_status", "incident_to_dict",
]
