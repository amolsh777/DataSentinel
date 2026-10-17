"""Schema drift: compare a current dataset's column profiles with a reference (baseline).

Pure functions. Inputs are column-profile dicts as produced by
``app.analytics.profiling.profile_column`` (or rebuilt from ``column_profiles`` rows).

Change types
------------
``added_column``, ``removed_column``, ``rename_candidate``, ``type_changed``,
``storage_type_changed``, ``nullability_changed``, ``missingness_changed``,
``new_categories``, ``vanished_categories``, ``identifier_uniqueness_lost``,
``became_constant``, ``column_reordered``.

Every change carries ``breaking`` (likely to break typed queries, reports or models
that read the column) and a ``severity`` (info < low < medium < high < critical).
Severity here is a documented triage heuristic, not a probability of impact.

Design rules
------------
* A removed column and an added column are never merged automatically. Similar pairs
  are reported as ``rename_candidate`` with their evidence, and both the removal and
  the addition stay in the change list until a person confirms the rename.
* Category comparisons use the stored top-``N`` category lists. When either side's
  list was truncated (``other_count > 0``) the change is marked ``certain=False``.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]

# Thresholds (percentage points unless noted). Override via ``thresholds=``.
DEFAULT_THRESHOLDS: dict[str, float] = {
    "missingness_change_pp": 5.0,       # |current - baseline| missing % to report
    "missingness_high_pp": 20.0,        # ... to escalate to high severity
    "identifier_unique_pct": 99.9,      # baseline unique % that marks a strict key
    "rename_name_similarity": 0.6,      # SequenceMatcher ratio for rename candidates
    "category_min_pct": 0.5,            # ignore new/vanished categories below this share
}

_NUMERIC = {"integer", "float"}
_STRING_LIKE = {"categorical", "text"}


def _severity_max(*levels: str) -> str:
    return max(levels, key=SEVERITY_ORDER.index)


def _change(
    change_type: str,
    columns: list[str],
    *,
    severity: str,
    breaking: bool,
    baseline: Any,
    current: Any,
    explanation: str,
    downstream_risk: str,
    certain: bool = True,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "change_type": change_type,
        "columns": columns,
        "severity": severity,
        "breaking": breaking,
        "certain": certain,
        "baseline": baseline,
        "current": current,
        "explanation": explanation,
        "downstream_risk": downstream_risk,
        "evidence": evidence or {},
    }


def _is_numeric_dtype(dtype: str) -> bool:
    return dtype.startswith(("int", "uint", "float", "Int", "UInt", "Float"))


# ----------------------------------------------------------------- type changes


def classify_type_change(old: str, new: str) -> tuple[bool, str, str]:
    """Return ``(breaking, severity, explanation)`` for a semantic type change."""
    if old == new:
        return False, "info", "No change."
    if old == "integer" and new == "float":
        return False, "low", (
            "Integer values now include decimals (widening). Usually readable by numeric "
            "consumers, but integer casts and equality joins may be affected."
        )
    if old == "float" and new == "integer":
        return False, "info", "All current values are whole numbers; still numeric."
    if {old, new} <= _STRING_LIKE:
        return False, "low", (
            f"Cardinality pattern changed ({old} -> {new}); the column is still string-valued."
        )
    if new == "empty":
        return True, "high", "Column is now entirely empty."
    if old == "empty":
        return False, "low", f"Previously empty column now contains {new} values."
    return True, "high", (
        f"Semantic type changed from {old} to {new}. Consumers that parse, aggregate or "
        f"join on this column as {old} are likely to fail or silently miscompute."
    )


# ----------------------------------------------------------- rename candidates


def _rename_candidates(
    removed: list[dict[str, Any]], added: list[dict[str, Any]], min_similarity: float
) -> list[dict[str, Any]]:
    out = []
    for r in removed:
        for a in added:
            name_sim = SequenceMatcher(
                None, r["column_name"].lower(), a["column_name"].lower()
            ).ratio()
            type_match = r["inferred_type"] == a["inferred_type"]
            if name_sim < min_similarity or not type_match:
                continue
            missing_gap = abs(r["missing_pct"] - a["missing_pct"])
            unique_gap = abs(r["unique_pct"] - a["unique_pct"])
            out.append(
                {
                    "removed": r["column_name"],
                    "added": a["column_name"],
                    "name_similarity": round(name_sim, 3),
                    "type_match": type_match,
                    "missing_pct_gap": round(missing_gap, 3),
                    "unique_pct_gap": round(unique_gap, 3),
                }
            )
    return sorted(out, key=lambda c: -c["name_similarity"])


# ------------------------------------------------------------------ categories


def _category_map(profile: dict[str, Any]) -> tuple[dict[str, float], bool]:
    stats = profile.get("stats") or {}
    values = {v["value"]: float(v["pct"]) for v in stats.get("top_values", [])}
    truncated = bool(stats.get("other_count", 0))
    return values, truncated


def _category_changes(
    name: str, ref: dict[str, Any], cur: dict[str, Any], min_pct: float
) -> list[dict[str, Any]]:
    if ref["inferred_type"] != "categorical" or cur["inferred_type"] != "categorical":
        return []
    ref_vals, ref_trunc = _category_map(ref)
    cur_vals, cur_trunc = _category_map(cur)
    changes = []

    new = {k: v for k, v in cur_vals.items() if k not in ref_vals and v >= min_pct}
    if new:
        new_share = round(sum(new.values()), 4)
        changes.append(
            _change(
                "new_categories",
                [name],
                severity="high" if new_share >= 5 else "medium",
                breaking=False,
                certain=not ref_trunc,
                baseline=sorted(ref_vals),
                current=sorted(cur_vals),
                explanation=(
                    f"{len(new)} category value(s) not seen in the baseline cover "
                    f"{new_share:.2f}% of non-null rows: "
                    + ", ".join(f"'{k}' ({v:.2f}%)" for k, v in sorted(new.items(), key=lambda x: -x[1]))
                    + ("." if not ref_trunc else
                       ". The baseline category list was truncated, so some may have existed.")
                ),
                downstream_risk=(
                    "Hard-coded category filters, mappings and one-hot encoded ML features "
                    "will not recognise these values."
                ),
                evidence={"new_values": new},
            )
        )

    vanished = {k: v for k, v in ref_vals.items() if k not in cur_vals and v >= min_pct}
    if vanished:
        changes.append(
            _change(
                "vanished_categories",
                [name],
                severity="medium",
                breaking=False,
                certain=not cur_trunc,
                baseline=sorted(ref_vals),
                current=sorted(cur_vals),
                explanation=(
                    f"{len(vanished)} baseline category value(s) are absent now: "
                    + ", ".join(f"'{k}' (was {v:.2f}%)" for k, v in vanished.items())
                    + "."
                ),
                downstream_risk="Segment-level reports for these values will show gaps or zeros.",
                evidence={"vanished_values": vanished},
            )
        )
    return changes


# ------------------------------------------------------------------- main entry


def _side(p: dict[str, Any] | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {
        "position": p["position"],
        "inferred_type": p["inferred_type"],
        "pandas_dtype": p["pandas_dtype"],
        "missing_pct": p["missing_pct"],
        "unique_pct": p["unique_pct"],
        "is_potential_identifier": p["is_potential_identifier"],
        "invalid_count": p.get("invalid_count", 0),
    }


def compare_schemas(
    reference: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compare reference vs current column profiles.

    Returns ``{"compatible", "summary", "changes", "columns", "rename_candidates",
    "thresholds", "limitations"}`` where ``columns`` is a side-by-side comparison.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    ref = {c["column_name"]: c for c in reference}
    cur = {c["column_name"]: c for c in current}
    changes: list[dict[str, Any]] = []

    removed = [ref[n] for n in ref if n not in cur]
    added = [cur[n] for n in cur if n not in ref]
    common = [n for n in ref if n in cur]

    for r in removed:
        changes.append(
            _change(
                "removed_column",
                [r["column_name"]],
                severity="high",
                breaking=True,
                baseline=_side(r),
                current=None,
                explanation=f"Column '{r['column_name']}' exists in the baseline but not in the current data.",
                downstream_risk=(
                    "Any query, transformation, metric or model feature that reads this column will "
                    "fail or receive nulls."
                ),
            )
        )
    for a in added:
        changes.append(
            _change(
                "added_column",
                [a["column_name"]],
                severity="low",
                breaking=False,
                baseline=None,
                current=_side(a),
                explanation=(
                    f"Column '{a['column_name']}' ({a['inferred_type']}) is new; it is not part of "
                    "the baseline schema."
                ),
                downstream_risk=(
                    "Usually harmless, but strict schema contracts, positional readers and "
                    "SELECT * loads into fixed tables can break."
                ),
            )
        )

    candidates = _rename_candidates(removed, added, th["rename_name_similarity"])
    for c in candidates:
        changes.append(
            _change(
                "rename_candidate",
                [c["removed"], c["added"]],
                severity="info",
                breaking=False,
                certain=False,
                baseline=c["removed"],
                current=c["added"],
                explanation=(
                    f"'{c['removed']}' may have been renamed to '{c['added']}' (name similarity "
                    f"{c['name_similarity']}, same inferred type). This is a candidate only; "
                    "confirm before treating them as the same field."
                ),
                downstream_risk="If confirmed, consumers must be updated to the new name.",
                evidence=c,
            )
        )

    ref_order = [n for n in ref if n in cur]
    cur_order = [n for n in cur if n in ref]
    if ref_order != cur_order:
        moved = [n for i, n in enumerate(ref_order) if cur_order[i] != n]
        changes.append(
            _change(
                "column_reordered",
                moved,
                severity="info",
                breaking=False,
                baseline=ref_order,
                current=cur_order,
                explanation=f"{len(moved)} shared column(s) changed position.",
                downstream_risk="Only consumers that read columns by position are affected.",
            )
        )

    for name in common:
        r, c = ref[name], cur[name]

        if r["inferred_type"] != c["inferred_type"]:
            breaking, sev, why = classify_type_change(r["inferred_type"], c["inferred_type"])
            changes.append(
                _change(
                    "type_changed",
                    [name],
                    severity=sev,
                    breaking=breaking,
                    baseline=r["inferred_type"],
                    current=c["inferred_type"],
                    explanation=why,
                    downstream_risk=(
                        "Typed transformations and model features expecting the baseline type."
                        if breaking else "Low risk for most consumers."
                    ),
                )
            )
        elif (
            _is_numeric_dtype(r["pandas_dtype"])
            and not _is_numeric_dtype(c["pandas_dtype"])
            and c["inferred_type"] in _NUMERIC
        ):
            n_bad = c.get("invalid_count", 0)
            examples = c.get("invalid_examples", [])
            changes.append(
                _change(
                    "storage_type_changed",
                    [name],
                    severity="high",
                    breaking=True,
                    baseline=r["pandas_dtype"],
                    current=c["pandas_dtype"],
                    explanation=(
                        f"Values are still mostly {c['inferred_type']}, but {n_bad} non-numeric "
                        f"value(s) such as {examples} force the column to be read as "
                        f"'{c['pandas_dtype']}' instead of '{r['pandas_dtype']}'."
                    ),
                    downstream_risk=(
                        "Arithmetic and aggregations fail or require casting; casting with "
                        "coercion silently turns these values into nulls."
                    ),
                    evidence={"invalid_count": n_bad, "invalid_examples": examples},
                )
            )

        r_miss, c_miss = r["missing_pct"], c["missing_pct"]
        delta = c_miss - r_miss
        if r_miss == 0 and c_miss > 0:
            changes.append(
                _change(
                    "nullability_changed",
                    [name],
                    severity="high" if delta >= th["missingness_high_pp"] else "medium",
                    breaking=True,
                    baseline={"nullable": False, "missing_pct": r_miss},
                    current={"nullable": True, "missing_pct": c_miss},
                    explanation=(
                        f"Column was fully populated in the baseline; now {c_miss:.2f}% of rows are missing."
                    ),
                    downstream_risk=(
                        "NOT NULL constraints, inner joins and models without imputation for this "
                        "feature can fail or drop rows."
                    ),
                )
            )
        elif abs(delta) >= th["missingness_change_pp"]:
            changes.append(
                _change(
                    "missingness_changed",
                    [name],
                    severity=(
                        "high" if delta >= th["missingness_high_pp"]
                        else "medium" if delta > 0 else "low"
                    ),
                    breaking=False,
                    baseline={"missing_pct": r_miss},
                    current={"missing_pct": c_miss},
                    explanation=(
                        f"Missing rate changed from {r_miss:.2f}% to {c_miss:.2f}% "
                        f"({delta:+.2f} percentage points)."
                    ),
                    downstream_risk=(
                        "Averages and counts computed over this column now cover a different "
                        "subset of rows."
                        if delta > 0 else "Coverage improved; check whether a default value is being filled in."
                    ),
                )
            )

        if (
            r["is_potential_identifier"]
            and r["unique_pct"] >= th["identifier_unique_pct"]
            and c["unique_pct"] < th["identifier_unique_pct"]
        ):
            changes.append(
                _change(
                    "identifier_uniqueness_lost",
                    [name],
                    severity="high",
                    breaking=True,
                    baseline={"unique_pct": r["unique_pct"]},
                    current={"unique_pct": c["unique_pct"]},
                    explanation=(
                        f"Key-like column was {r['unique_pct']:.2f}% unique in the baseline and is "
                        f"{c['unique_pct']:.2f}% unique now, so duplicate key values exist."
                    ),
                    downstream_risk=(
                        "Joins on this key will fan out and double-count; primary-key loads will fail."
                    ),
                )
            )

        if c.get("is_constant") and not r.get("is_constant"):
            changes.append(
                _change(
                    "became_constant",
                    [name],
                    severity="medium",
                    breaking=False,
                    baseline={"unique_count": r.get("unique_count")},
                    current={"unique_count": c.get("unique_count")},
                    explanation="Column now holds a single distinct value.",
                    downstream_risk=(
                        "Often a sign of a broken upstream default; the feature carries no information."
                    ),
                )
            )

        changes.extend(_category_changes(name, r, c, th["category_min_pct"]))

    changed_names = {col for ch in changes if ch["change_type"] != "column_reordered" for col in ch["columns"]}
    columns_side_by_side = []
    for name in list(ref) + [n for n in cur if n not in ref]:
        status = (
            "removed" if name not in cur
            else "added" if name not in ref
            else "changed" if name in changed_names
            else "unchanged"
        )
        columns_side_by_side.append(
            {"name": name, "status": status, "baseline": _side(ref.get(name)), "current": _side(cur.get(name))}
        )

    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {s: 0 for s in SEVERITY_ORDER}
    for ch in changes:
        by_type[ch["change_type"]] = by_type.get(ch["change_type"], 0) + 1
        by_severity[ch["severity"]] += 1
    breaking = [ch for ch in changes if ch["breaking"]]
    changes.sort(key=lambda ch: (-SEVERITY_ORDER.index(ch["severity"]), ch["change_type"], ch["columns"]))

    return {
        "compatible": not breaking,
        "summary": {
            "baseline_columns": len(ref),
            "current_columns": len(cur),
            "added": len(added),
            "removed": len(removed),
            "common": len(common),
            "total_changes": len(changes),
            "breaking_changes": len(breaking),
            "by_type": by_type,
            "by_severity": by_severity,
            "max_severity": _severity_max(*(ch["severity"] for ch in changes)) if changes else None,
        },
        "changes": changes,
        "columns": columns_side_by_side,
        "rename_candidates": candidates,
        "thresholds": th,
        "limitations": [
            "Category comparisons use the stored top-20 values per column; changes marked "
            "certain=false involve truncated lists.",
            "Rename candidates are suggestions based on name similarity and type; they are never "
            "applied automatically.",
            "Breaking/compatible labels describe typical consumers; your pipelines may differ.",
        ],
    }


__all__ = ["DEFAULT_THRESHOLDS", "SEVERITY_ORDER", "classify_type_change", "compare_schemas"]
