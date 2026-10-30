"""Lineage graph: assets (nodes), edges (directed dependencies), traversal, cycle detection.

The graph is a directed acyclic graph (DAG) from source datasets down to
downstream reports and model features. DataSentinel enforces acyclicity at
write time using DFS cycle detection.

Impact analysis answers: "if this column has a quality or drift issue,
which downstream assets are affected?"
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import LineageAsset, LineageEdge

logger = logging.getLogger(__name__)

ASSET_TYPES = {"dataset", "transformation", "metric", "report", "ml_feature"}
DEPENDENCY_KINDS = {"known", "inferred"}


class CycleError(AppError):
    status_code = 422
    code = "LINEAGE_CYCLE"


class LineageError(AppError):
    status_code = 422
    code = "LINEAGE_ERROR"


# ───────────────────────────────────────────── helpers ──────────────────────


def _all_assets(db: Session) -> list[LineageAsset]:
    return list(db.scalars(select(LineageAsset).order_by(LineageAsset.created_at)))


def _all_edges(db: Session) -> list[LineageEdge]:
    return list(db.scalars(select(LineageEdge)))


def _asset_by_id(db: Session, asset_id: str) -> LineageAsset:
    a = db.get(LineageAsset, asset_id)
    if a is None:
        raise NotFoundError(f"Asset '{asset_id}' not found.", code="ASSET_NOT_FOUND")
    return a


def _asset_by_name(db: Session, name: str) -> LineageAsset | None:
    return db.scalar(select(LineageAsset).where(LineageAsset.name == name))


def _build_adj(edges: list[LineageEdge]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Forward (source→targets) and reverse (target→sources) adjacency lists."""
    fwd: dict[str, list[str]] = defaultdict(list)
    rev: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        fwd[e.source_asset_id].append(e.target_asset_id)
        rev[e.target_asset_id].append(e.source_asset_id)
    return dict(fwd), dict(rev)


def _has_cycle(fwd: dict[str, list[str]], all_ids: list[str]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in all_ids}

    def dfs(node: str) -> bool:
        color[node] = GRAY
        for neighbor in fwd.get(node, []):
            if color[neighbor] == GRAY:
                return True
            if color[neighbor] == WHITE and dfs(neighbor):
                return True
        color[node] = BLACK
        return False

    return any(dfs(n) for n in all_ids if color[n] == WHITE)


def _bfs(start_id: str, adj: dict[str, list[str]], assets_by_id: dict[str, LineageAsset],
         *, depth_limit: int = 10) -> list[dict[str, Any]]:
    seen: set[str] = set()
    q: deque[tuple[str, int]] = deque([(start_id, 0)])
    results: list[dict[str, Any]] = []
    while q:
        node, depth = q.popleft()
        if node in seen or depth >= depth_limit:
            continue
        seen.add(node)
        if node != start_id and (a := assets_by_id.get(node)):
            results.append({"id": node, "name": a.name, "asset_type": a.asset_type, "depth": depth})
        for neighbor in adj.get(node, []):
            q.append((neighbor, depth + 1))
    return results


# ─────────────────────────────────────────────── CRUD ───────────────────────


def asset_to_dict(a: LineageAsset) -> dict[str, Any]:
    return {
        "id": a.id, "name": a.name, "asset_type": a.asset_type, "dataset_id": a.dataset_id,
        "description": a.description, "columns": a.columns or [],
        "is_synthetic": a.is_synthetic, "created_at": a.created_at,
    }


def edge_to_dict(e: LineageEdge, assets_by_id: dict[str, LineageAsset] | None = None) -> dict[str, Any]:
    src = assets_by_id.get(e.source_asset_id).name if assets_by_id else e.source_asset_id
    tgt = assets_by_id.get(e.target_asset_id).name if assets_by_id else e.target_asset_id
    return {
        "id": e.id, "source_asset_id": e.source_asset_id, "target_asset_id": e.target_asset_id,
        "source_name": src, "target_name": tgt, "source_columns": e.source_columns or [],
        "dependency_kind": e.dependency_kind, "description": e.description, "created_at": e.created_at,
    }


def create_asset(db: Session, body: dict[str, Any]) -> LineageAsset:
    if body["asset_type"] not in ASSET_TYPES:
        raise LineageError(f"asset_type must be one of {sorted(ASSET_TYPES)}.")
    if _asset_by_name(db, body["name"]):
        raise ConflictError(f"Asset '{body['name']}' already exists.", code="ASSET_EXISTS")
    a = LineageAsset(
        name=body["name"].strip(), asset_type=body["asset_type"],
        dataset_id=body.get("dataset_id"), description=body.get("description"),
        columns=body.get("columns", []), is_synthetic=bool(body.get("is_synthetic", False)),
    )
    db.add(a); db.commit(); db.refresh(a)
    return a


def update_asset(db: Session, asset_id: str, body: dict[str, Any]) -> LineageAsset:
    a = _asset_by_id(db, asset_id)
    if "name" in body and body["name"] != a.name:
        if _asset_by_name(db, body["name"]):
            raise ConflictError(f"Asset '{body['name']}' already exists.", code="ASSET_EXISTS")
        a.name = body["name"].strip()
    if "description" in body:
        a.description = body["description"]
    if "columns" in body:
        a.columns = body["columns"]
    if "asset_type" in body:
        if body["asset_type"] not in ASSET_TYPES:
            raise LineageError(f"asset_type must be one of {sorted(ASSET_TYPES)}.")
        a.asset_type = body["asset_type"]
    db.commit(); db.refresh(a)
    return a


def delete_asset(db: Session, asset_id: str) -> None:
    a = _asset_by_id(db, asset_id)
    db.delete(a); db.commit()


def create_edge(db: Session, source_id: str, target_id: str, *,
                source_columns: list[str] | None = None,
                dependency_kind: str = "known",
                description: str | None = None) -> LineageEdge:
    _asset_by_id(db, source_id); _asset_by_id(db, target_id)
    if source_id == target_id:
        raise CycleError("An asset cannot depend on itself.")
    if dependency_kind not in DEPENDENCY_KINDS:
        raise LineageError(f"dependency_kind must be one of {sorted(DEPENDENCY_KINDS)}.")
    existing = db.scalar(select(LineageEdge).where(
        LineageEdge.source_asset_id == source_id, LineageEdge.target_asset_id == target_id))
    if existing:
        raise ConflictError("This edge already exists.", code="EDGE_EXISTS")
    # Cycle check with the proposed edge added.
    all_edges = _all_edges(db)
    assets_ids = [a.id for a in _all_assets(db)]
    fwd, _ = _build_adj(all_edges)
    fwd.setdefault(source_id, []).append(target_id)
    if _has_cycle(fwd, assets_ids):
        raise CycleError(f"Adding an edge from '{source_id}' to '{target_id}' would create a cycle.")
    e = LineageEdge(source_asset_id=source_id, target_asset_id=target_id,
                    source_columns=source_columns or [], dependency_kind=dependency_kind,
                    description=description)
    db.add(e); db.commit(); db.refresh(e)
    return e


def delete_edge(db: Session, edge_id: str) -> None:
    e = db.get(LineageEdge, edge_id)
    if not e:
        raise NotFoundError(f"Edge '{edge_id}' not found.", code="EDGE_NOT_FOUND")
    db.delete(e); db.commit()


# ──────────────────────────────────── graph & traversal ─────────────────────


def get_full_graph(db: Session) -> dict[str, Any]:
    assets = _all_assets(db)
    edges = _all_edges(db)
    by_id = {a.id: a for a in assets}
    return {
        "assets": [asset_to_dict(a) for a in assets],
        "edges": [edge_to_dict(e, by_id) for e in edges],
        "node_count": len(assets),
        "edge_count": len(edges),
    }


def get_upstream(db: Session, asset_id: str, depth: int = 10) -> list[dict[str, Any]]:
    _asset_by_id(db, asset_id)
    by_id = {a.id: a for a in _all_assets(db)}
    _, rev = _build_adj(_all_edges(db))
    return _bfs(asset_id, rev, by_id, depth_limit=depth)


def get_downstream(db: Session, asset_id: str, depth: int = 10) -> list[dict[str, Any]]:
    _asset_by_id(db, asset_id)
    by_id = {a.id: a for a in _all_assets(db)}
    fwd, _ = _build_adj(_all_edges(db))
    return _bfs(asset_id, fwd, by_id, depth_limit=depth)


def get_affected_assets(db: Session, dataset_id: str, columns: list[str]) -> list[dict[str, Any]]:
    """All downstream assets reachable from a dataset asset that touches the given columns."""
    by_id = {a.id: a for a in _all_assets(db)}
    all_edges = _all_edges(db)
    fwd, _ = _build_adj(all_edges)
    edge_by_pair = {(e.source_asset_id, e.target_asset_id): e for e in all_edges}
    col_set = set(columns)

    starting_assets = [
        a for a in by_id.values()
        if a.dataset_id == dataset_id and (not a.columns or col_set & set(a.columns))
    ]
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    q: deque[str] = deque(a.id for a in starting_assets)
    for a in starting_assets:
        seen.add(a.id)
    while q:
        node = q.popleft()
        for target in fwd.get(node, []):
            if target in seen:
                continue
            seen.add(target); q.append(target)
            if asset := by_id.get(target):
                edge = edge_by_pair.get((node, target))
                results.append({
                    "id": target, "name": asset.name, "asset_type": asset.asset_type,
                    "dependency_kind": edge.dependency_kind if edge else "known",
                    "via": by_id[node].name if node in by_id else node,
                    "edge_columns": edge.source_columns if edge else [],
                })
    return results


# ──────────────────────────────────── demo seed ─────────────────────────────


def seed_demo_lineage(db: Session, dataset_ids: dict[str, str], filename: str = "lineage_demo.json") -> None:
    """Load `data/sample/<filename>` (default lineage_demo.json) into the DB. Idempotent."""
    sample_dir = Path(__file__).resolve().parents[3] / "data" / "sample"
    demo_file = sample_dir / filename
    if not demo_file.exists():
        logger.warning("%s not found; skipping lineage seed.", demo_file)
        return
    data = json.loads(demo_file.read_text())
    name_to_id: dict[str, str] = {}
    for a in data["assets"]:
        existing = _asset_by_name(db, a["name"])
        if existing:
            name_to_id[a["name"]] = existing.id
            continue
        asset = LineageAsset(
            name=a["name"], asset_type=a["asset_type"],
            dataset_id=dataset_ids.get(a.get("dataset_name", "")),
            description=a.get("description"),
            columns=a.get("columns", []), is_synthetic=True,
        )
        db.add(asset); db.flush()
        name_to_id[a["name"]] = asset.id
    db.commit()
    for e in data.get("edges", []):
        src = name_to_id.get(e["source"])
        tgt = name_to_id.get(e["target"])
        if not src or not tgt:
            continue
        if db.scalar(select(LineageEdge).where(
            LineageEdge.source_asset_id == src, LineageEdge.target_asset_id == tgt)):
            continue
        db.add(LineageEdge(
            source_asset_id=src, target_asset_id=tgt,
            source_columns=e.get("source_columns", []),
            dependency_kind=e.get("dependency_kind", "known"),
            description=e.get("description"),
        ))
    db.commit()
    logger.info("Seeded demo lineage: %d assets, %d edges", len(name_to_id), len(data.get("edges", [])))


__all__ = [
    "CycleError", "LineageError", "ASSET_TYPES",
    "asset_to_dict", "edge_to_dict",
    "create_asset", "update_asset", "delete_asset",
    "create_edge", "delete_edge",
    "get_full_graph", "get_upstream", "get_downstream", "get_affected_assets",
    "seed_demo_lineage",
]
