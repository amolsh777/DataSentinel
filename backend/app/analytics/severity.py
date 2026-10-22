"""Incident severity scoring and prioritised recommendations.

Severity is a documented, rule-based heuristic. It is never presented as a
probability. The formula is transparent and shown to the user.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# ─────────────────────────────────────────── severity score ──────────────────

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]

# Weights: rule severity (0-4), affected proportion (0-40), incident type (0-20),
# downstream lineage impact (0-20), recurrence (0-10). Max = 94. Bucketed to labels.
_RULE_SEVERITY_WEIGHT = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
_TYPE_WEIGHT = {"quality": 10, "schema": 15, "drift": 8}


def compute_severity_score(
    rule_severity: str,
    affected_pct: float | None,
    incident_type: str,
    downstream_assets: int = 0,
    recurrence_count: int = 0,
) -> tuple[int, str, dict[str, Any]]:
    """Return (raw_score 0-100, severity_label, breakdown_dict).

    breakdown_dict lets the UI show exactly what drove the score.
    """
    rule_pts = _RULE_SEVERITY_WEIGHT.get(rule_severity, 2) * 5        # 0-20
    affected_pts = min(int((affected_pct or 0) * 0.4), 40)            # 0-40
    type_pts = _TYPE_WEIGHT.get(incident_type, 10)                     # 0-20
    lineage_pts = min(downstream_assets * 5, 20)                       # 0-20
    recur_pts = min(recurrence_count * 2, 10)                          # 0-10
    total = rule_pts + affected_pts + type_pts + lineage_pts + recur_pts

    if total >= 75:
        label = "critical"
    elif total >= 55:
        label = "high"
    elif total >= 35:
        label = "medium"
    elif total >= 15:
        label = "low"
    else:
        label = "info"

    breakdown = {
        "total": total,
        "rule_severity": rule_pts,
        "affected_pct": affected_pts,
        "incident_type": type_pts,
        "downstream_assets": lineage_pts,
        "recurrence": recur_pts,
        "note": (
            "Severity is a rule-based heuristic (0-100 → label), "
            "not a probability of business impact."
        ),
    }
    return total, label, breakdown


# ────────────────────────────────────────── fingerprinting ───────────────────


def make_fingerprint(
    dataset_id: str,
    incident_type: str,
    rule_name: str,
    columns: list[str],
) -> str:
    """Stable SHA-256 fingerprint for deduplication.

    The fingerprint is intentionally coarse — same dataset + rule + sorted columns.
    It does NOT include the affected percentage so that worsening conditions update the
    existing incident rather than creating a duplicate.
    """
    key = json.dumps(
        {
            "dataset_id": dataset_id,
            "incident_type": incident_type,
            "rule_name": rule_name,
            "columns": sorted(columns),
        },
        sort_keys=True,
    )
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# ─────────────────────────────────────── recommendations ─────────────────────

_RULE_ACTIONS: dict[str, list[str]] = {
    "missing_values": [
        "Trace the null values upstream: open the source extract for a few affected rows and "
        "confirm whether the field is null at the origin or dropped in transit.",
        "Check the pipeline deployment history for any step added or changed near the "
        "ingestion of this column.",
        "If the upstream source is the fix, request a patch and schedule a backfill.",
        "Lower the missing-value threshold only after the underlying issue is resolved.",
    ],
    "duplicate_rows": [
        "Check the orchestrator run history for a failed-then-retried job whose output was "
        "ingested twice.",
        "Add a deduplication step keyed on the natural primary key before writing to the "
        "target table.",
        "If retries are expected, track the batch id and keep only the most recent copy.",
        "Backfill downstream aggregations for the affected period after deduplicating.",
    ],
    "unique": [
        "Pull two rows that share the same key and compare other fields: if they differ, "
        "this is a source key-collision bug; if they are identical, it is row duplication.",
        "Trace key generation upstream (database sequence, UUID, external id). A counter "
        "reset or multi-source merge is the most common cause.",
        "Consider switching to UUIDs if the key space can wrap around.",
    ],
    "numeric_range": [
        "Verify the unit of measurement with the data owner (cents vs dollars, grams vs kg).",
        "If refunds or reversals are expected, route them to a separate column rather than "
        "allowing negatives in a revenue field.",
        "Add a reject-and-quarantine step at ingestion so bad rows don't reach analytics.",
        "If the valid range legitimately expanded, update the rule and re-baseline.",
    ],
    "allowed_values": [
        "Confirm with the business owner whether the new values are legitimate (new product, "
        "region, payment method).",
        "If they are codes that should have been mapped, fix the mapping step and backfill.",
        "If values are errors, reject them at ingestion and alert the source system owner.",
        "Update the allowed-values list and downstream enums/mappings together.",
    ],
    "referential_integrity": [
        "Compare the load timestamps: a lagging reference table is the most common cause.",
        "Add an ordering guarantee so the reference is loaded before dependent tables.",
        "Pick one orphan key and look it up in the source system directly to confirm it exists.",
        "Quarantine orphan rows with a reason code rather than silently dropping them.",
    ],
    "business_rule": [
        "Inspect the raw source rows for a sample of violating records.",
        "Confirm with the business owner whether the rule itself needs updating.",
        "If the violation is a pipeline bug, fix it and backfill the affected period.",
    ],
    "data_type": [
        "Pull raw source rows where the value is non-numeric/non-date and confirm the format "
        "at origin.",
        "Check whether nulls are being replaced with sentinel strings like 'unknown' or 'N/A'.",
        "Fix the coercion or cleaning step at ingestion; add a type-check validation rule.",
    ],
    "schema_match": [
        "Review each breaking change and identify the owner of the upstream system.",
        "Decide per change: accept (update the baseline and all consumers) or revert (fix at "
        "the source).",
        "Add a schema contract so changes are proposed before they land.",
    ],
    "required_columns": [
        "Check whether the column was renamed (see rename candidates in the schema diff).",
        "If removed deliberately, update all downstream consumers together.",
        "If removed accidentally, restore it in the ETL and backfill missing records.",
    ],
}

_DRIFT_ACTIONS = [
    "Split the feature by category or time window to check whether the shift is uniform or "
    "concentrated in a sub-segment.",
    "Ask the data owner whether a business change (pricing, product launch, policy) explains "
    "the shift.",
    "Confirm no unit, currency or encoding change happened upstream.",
    "If the shift reflects a real business change, re-baseline after documenting the reason.",
    "If the shift is a data problem, fix it at the source and backfill; do not re-baseline "
    "until the fix is confirmed.",
]

_SCHEMA_DRIFT_ACTIONS = [
    "Review the change with the upstream team before taking any action.",
    "For breaking changes: update all downstream consumers and the baseline together in a "
    "planned window.",
    "For non-breaking changes: accept and update the baseline.",
    "Add a schema contract test to the source pipeline so future changes surface early.",
]


def build_recommendations(
    incident_type: str,
    rule_name: str,
    rule_type: str,
    columns: list[str],
    affected_pct: float | None,
    downstream_assets: list[str],
    severity_score: int,
) -> list[dict[str, Any]]:
    """Return a short ordered list of concrete actions, most impactful first."""
    actions: list[str] = []

    if incident_type == "drift":
        actions = _DRIFT_ACTIONS[:]
    elif incident_type in ("schema", "schema_drift"):
        actions = _SCHEMA_DRIFT_ACTIONS[:]
    else:
        actions = _RULE_ACTIONS.get(rule_type, [
            "Inspect the affected rows in the source system.",
            "Contact the data owner to confirm whether the condition is expected.",
            "Fix at the origin and rerun the monitoring check.",
        ])[:]

    # Prepend a lineage-specific action when downstream assets are affected.
    if downstream_assets:
        asset_list = ", ".join(downstream_assets[:5])
        actions.insert(
            0,
            f"Pause or flag outputs from the affected downstream asset(s) until this is "
            f"resolved: {asset_list}.",
        )

    # Always end with a re-baseline reminder for high-severity issues.
    if severity_score >= 55:
        actions.append(
            "After the fix is confirmed, run a fresh monitoring check and re-baseline only "
            "if the result is healthy."
        )

    return [
        {"priority": i + 1, "action": a, "source": "deterministic"}
        for i, a in enumerate(actions[:8])
    ]


__all__ = [
    "SEVERITY_ORDER",
    "build_recommendations",
    "compute_severity_score",
    "make_fingerprint",
]
