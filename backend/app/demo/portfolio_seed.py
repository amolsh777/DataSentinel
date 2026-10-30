"""Seed the demo portfolio into a database: datasets with five versions each, starter + hand-written rules,
an approved baseline (v1), lineage, and ~30 days of *backdated* monitoring runs with incidents in mixed states,
so the dashboard, run history and incident pages are populated the first time they are opened.

Runs are real: every run executes the full monitoring pipeline on the stored files. Only the timestamps are
moved into the past afterwards (run start/finish, incident first/last seen), because otherwise all history would
carry today's date. Synthetic data only.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.demo import portfolio
from app.models import Incident, MonitoringRun
from app.services import baseline_service, dataset_service, lineage_service, monitoring_service, quality_service

logger = logging.getLogger(__name__)
RETAIL_HISTORY = [1, 1, 2, 1, 2, 2, 1, 2]


def _lineage_json(specs: list[portfolio.Spec], path: Path) -> None:
    assets, edges = [], []
    for sp in specs:
        cols = sp.ids + sp.nums + sp.cats + sp.dates
        etl = f"{sp.name} ETL"
        assets += [{"name": sp.name, "asset_type": "dataset", "dataset_name": sp.name, "columns": cols},
                   {"name": etl, "asset_type": "transformation", "description": f"Nightly load of {sp.name}", "columns": cols}]
        edges.append({"source": sp.name, "target": etl, "source_columns": cols, "kind": "known"})
        for asset_name, asset_type in sp.downstream:
            assets.append({"name": asset_name, "asset_type": asset_type, "columns": sp.nums[:2] + sp.cats[:1]})
            edges.append({"source": etl, "target": asset_name, "source_columns": sp.nums[:2] + sp.cats[:1], "kind": "known"})
    path.write_text(json.dumps({"assets": assets, "edges": edges}, indent=2), encoding="utf-8")


def _backdate(db: Session, run: MonitoringRun, when: dt.datetime) -> None:
    run.started_at = when
    run.finished_at = when + dt.timedelta(seconds=40)
    for inc in db.scalars(select(Incident).where(Incident.last_run_id == run.id)):
        inc.last_seen_at = when
        if inc.first_run_id == run.id:
            inc.first_seen_at = when
            inc.created_at = when
    db.commit()


def _history(db: Session, settings: Settings, dataset_id: str, versions: list[int], *, days: int,
             log: Callable[[str], None]) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    step = days / max(1, len(versions))
    for i, v in enumerate(versions):
        when = now - dt.timedelta(days=days - i * step, hours=(i * 5) % 7)
        try:
            run = monitoring_service.run_monitor(db, settings, dataset_id, v)
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the whole seed; it is reported
            log(f"    run {i + 1} (v{v}) failed: {exc}")
            db.rollback()
            continue
        _backdate(db, run, when)
        if i == len(versions) // 2:  # a team clears its queue half-way; later bad versions reopen some (recurrences)
            for inc in db.scalars(select(Incident).where(Incident.dataset_id == dataset_id, Incident.status == "open")):
                try:
                    monitoring_service.update_incident_status(db, inc.id, "resolved", note="Fixed upstream (demo)", changed_by="demo")
                    inc.resolved_at = when + dt.timedelta(hours=6)
                except Exception:  # noqa: BLE001
                    db.rollback()
            db.commit()
    open_inc = list(db.scalars(select(Incident).where(Incident.dataset_id == dataset_id, Incident.status == "open")
                               .order_by(Incident.first_seen_at)))
    for k, inc in enumerate(open_inc):
        try:
            if k % 4 == 1:
                monitoring_service.update_incident_status(db, inc.id, "acknowledged", note="Investigating (demo)", changed_by="demo")
            elif k % 11 == 5:
                monitoring_service.update_incident_status(db, inc.id, "ignored", note="Known issue (demo)", changed_by="demo")
        except Exception:  # noqa: BLE001
            db.rollback()
    db.commit()


def seed_portfolio(db: Session, settings: Settings, sample_dir: Path, *, scale: float = 1.0, days: int = 30,
                   runs: int | None = None, slugs: list[str] | None = None,
                   log: Callable[[str], None] = print) -> dict:
    """Register the portfolio and build history. Returns counts. Idempotent per dataset name (existing ones are skipped)."""
    specs = [s for s in portfolio.SPECS if not slugs or s.slug in slugs]
    out = sample_dir / "portfolio"
    portfolio.write_files(out, scale=scale, slugs=[s.slug for s in specs])
    _lineage_json(specs, sample_dir / "lineage_portfolio.json")
    ids: dict[str, str] = {}
    n_runs = 0
    for sp in specs:
        if dataset_service.get_dataset_by_name(db, sp.name):
            log(f"  {sp.name}: exists, skipped")
            continue
        log(f"  {sp.name}: registering {len(portfolio.VERSION_PLAN)} versions")
        ds = None
        for v, (label, _sev) in portfolio.VERSION_PLAN.items():
            content = (out / sp.slug / f"v{v}_{label}.csv").read_bytes()
            fname = f"v{v}_{label}.csv"
            if ds is None:
                ds, _ = dataset_service.register_dataset(
                    db, settings, name=sp.name, description=sp.description, owner=sp.owner, project=sp.project,
                    is_synthetic=True, content=content, filename=fname, notes="Reference (clean) version.")
            else:
                dataset_service.add_version(db, settings, ds.id, content=content, filename=fname, notes=f"Demo version: {label}")
        ids[sp.name] = ds.id
        _, _, saved = quality_service.suggest_rules(db, ds.id, 1, save=True)
        added = 0
        for rule in sp.rules:
            try:
                quality_service.create_rule(db, {"dataset_id": ds.id, **rule})
                added += 1
            except Exception as exc:  # noqa: BLE001 - optional demo rule; say so rather than hide it
                log(f"    rule '{rule['name']}' not added: {exc}")
                db.rollback()
        baseline_service.set_baseline(db, ds.id, 1, reason="Clean reference (demo seed)", set_by="demo", confirm=False)
        versions = portfolio.HISTORY[sp.history][: runs or None]
        log(f"    {len(saved) + added} rules, baseline v1, running {len(versions)} backdated runs ({sp.history})")
        _history(db, settings, ds.id, versions, days=days, log=log)
        n_runs += len(versions)
    if ids:
        lineage_service.seed_demo_lineage(db, ids, "lineage_portfolio.json")
    # Retail demo dataset (registered by reset_demo) gets history too.
    retail = dataset_service.get_dataset_by_name(db, "Retail Transactions")
    if retail and len(retail.versions) >= 2 and not db.scalar(select(MonitoringRun).where(MonitoringRun.dataset_id == retail.id)):
        log("  Retail Transactions: backdated runs")
        _history(db, settings, retail.id, RETAIL_HISTORY[: runs or None], days=days, log=log)
        n_runs += len(RETAIL_HISTORY[: runs or None])
    return {"datasets": len(ids), "runs": n_runs}
