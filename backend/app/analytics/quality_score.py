"""Transparent, configurable, rule-based data-quality score.

Pure functions. Takes the results produced by :mod:`app.analytics.quality_checks`
and returns a 0–100 score with a full breakdown.

Dimensions
----------
Five standard dimensions, each driven by specific rule types. Only dimensions that
have at least one evaluated rule contribute; empty dimensions are listed as
``not_evaluated`` with a stated reason so the score cannot pretend to measure what
it has no evidence for.

======================  =======================================================
Dimension               Rule types that count toward it
======================  =======================================================
completeness            missing_values
validity                data_type, numeric_range, allowed_values, date_range,
                        business_rule, constant_column
uniqueness              unique, duplicate_rows
consistency             schema_match, required_columns, unexpected_columns
accuracy                referential_integrity          (only when a trusted
                                                        reference is present)
======================  =======================================================

How a dimension's ratio is computed
-----------------------------------
Each contributing rule result produces a ratio in ``[0, 1]`` from the counts it
already reports:

* ``pass``  – 1.0
* ``warn``  – ``1 - affected_pct/100`` (soft penalty)
* ``fail``  – ``1 - affected_pct/100`` (hard penalty, but see thresholds below)
* ``skipped`` and ``info`` – ignored (do not pass, do not penalise)

The dimension ratio is the mean of its contributing ratios, with each rule
re-weighted by its ``rule_severity`` (info 1, low 2, medium 3, high 4, critical 5)
so hard-failing a critical rule hurts more than hard-failing an info one.

The final score is ``sum(weight_i * ratio_i) / sum(weight_i) * 100``, rounded to
one decimal, where ``i`` runs over the evaluated dimensions.

Thresholds
----------
Users can set a minimum acceptable score for the whole dataset
(``overall_min``) and for each dimension. The status is ``healthy`` when every
threshold is met, ``degraded`` when only soft thresholds are missed and no hard
threshold is violated, ``failing`` when any hard threshold is violated or the
overall score is below ``overall_min``.

Accuracy honesty
----------------
"Accuracy" is only evaluated when at least one ``referential_integrity`` result is
present — the only rule type we offer that genuinely compares against trusted
reference data. Otherwise the dimension is marked ``not_evaluated`` with the
reason: *no trusted reference data configured*.
"""

from __future__ import annotations

from typing import Any

DEFAULT_WEIGHTS = {
    "completeness": 25.0,
    "validity": 25.0,
    "uniqueness": 15.0,
    "consistency": 15.0,
    "accuracy": 20.0,
}

DEFAULT_THRESHOLDS = {
    "overall_min": 70.0,
    "dimension_min": {
        "completeness": 70.0, "validity": 70.0, "uniqueness": 80.0,
        "consistency": 80.0, "accuracy": 80.0,
    },
}

DIMENSION_RULES: dict[str, set[str]] = {
    "completeness":  {"missing_values"},
    "validity":      {"data_type", "numeric_range", "allowed_values", "date_range",
                      "business_rule", "constant_column"},
    "uniqueness":    {"unique", "duplicate_rows"},
    "consistency":   {"schema_match", "required_columns", "unexpected_columns"},
    "accuracy":      {"referential_integrity"},
}

_RULE_TO_DIMENSION = {rt: dim for dim, rts in DIMENSION_RULES.items() for rt in rts}

_SEVERITY_WEIGHT = {"info": 1.0, "low": 2.0, "medium": 3.0, "high": 4.0, "critical": 5.0}


def _ratio_from_result(result: dict[str, Any]) -> float:
    """Convert one check result to a 0-1 ratio."""
    outcome = result["outcome"]
    if outcome == "pass":
        return 1.0
    if outcome in ("warn", "fail"):
        affected = result.get("affected_pct")
        return max(0.0, 1.0 - float(affected or 0.0) / 100.0)
    return 1.0  # defensive; skipped/info are filtered before this call


def _normalise_weights(weights: dict[str, float] | None) -> dict[str, float]:
    merged = {**DEFAULT_WEIGHTS, **(weights or {})}
    out = {}
    for k in DEFAULT_WEIGHTS:
        v = merged.get(k, DEFAULT_WEIGHTS[k])
        if not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"Weight for '{k}' must be a non-negative number, got {v!r}.")
        out[k] = float(v)
    if sum(out.values()) == 0:
        raise ValueError("At least one dimension weight must be positive.")
    return out


def _normalise_thresholds(thresholds: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    dim_min = {**DEFAULT_THRESHOLDS["dimension_min"], **(merged.get("dimension_min") or {})}
    for k, v in dim_min.items():
        if not isinstance(v, (int, float)) or not 0 <= v <= 100:
            raise ValueError(f"dimension_min['{k}'] must be between 0 and 100.")
    overall = merged.get("overall_min", DEFAULT_THRESHOLDS["overall_min"])
    if not isinstance(overall, (int, float)) or not 0 <= overall <= 100:
        raise ValueError("overall_min must be between 0 and 100.")
    return {"overall_min": float(overall), "dimension_min": {k: float(v) for k, v in dim_min.items()}}


def compute_quality_score(
    results: list[dict[str, Any]],
    *,
    weights: dict[str, float] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the configurable quality score from a list of check results.

    ``results`` is a list of dicts as produced by
    :func:`app.analytics.quality_checks.run_quality_checks`. Returns a report
    suitable for direct JSON serialisation.
    """
    w = _normalise_weights(weights)
    th = _normalise_thresholds(thresholds)

    # Bucket results by dimension; count outcomes transparently.
    dimensions: dict[str, dict[str, Any]] = {
        d: {"contributing_results": [], "outcomes": {"pass": 0, "warn": 0, "fail": 0,
                                                     "skipped": 0, "info": 0}}
        for d in DEFAULT_WEIGHTS
    }
    for r in results:
        dim = _RULE_TO_DIMENSION.get(r["rule_type"])
        if dim is None:
            continue
        dimensions[dim]["outcomes"][r["outcome"]] = dimensions[dim]["outcomes"].get(r["outcome"], 0) + 1
        if r["outcome"] in ("pass", "warn", "fail"):
            dimensions[dim]["contributing_results"].append(r)

    # Compute per-dimension ratio.
    for dim, data in dimensions.items():
        contrib = data["contributing_results"]
        if not contrib:
            data["ratio"] = None
            data["score"] = None
            data["weighted_rules"] = 0
            data["status"] = "not_evaluated"
            data["reason"] = _not_evaluated_reason(dim, data["outcomes"])
            continue
        weighted_sum, weight_total = 0.0, 0.0
        rule_breakdown = []
        for r in contrib:
            ratio = _ratio_from_result(r)
            rw = _SEVERITY_WEIGHT.get(r.get("rule_severity", "medium"), 3.0)
            weighted_sum += ratio * rw
            weight_total += rw
            rule_breakdown.append({
                "rule_name": r["rule_name"], "rule_type": r["rule_type"],
                "rule_severity": r.get("rule_severity"), "outcome": r["outcome"],
                "columns": r.get("columns", []),
                "affected_pct": r.get("affected_pct"), "ratio": round(ratio, 6),
                "weight": rw,
            })
        ratio = weighted_sum / weight_total if weight_total else 1.0
        data["ratio"] = round(ratio, 6)
        data["score"] = round(ratio * 100.0, 2)
        data["weighted_rules"] = len(contrib)
        data["rule_breakdown"] = rule_breakdown
        data["has_issue"] = any(b["outcome"] != "pass" for b in rule_breakdown)
        data["worst_rules"] = sorted(
            [b for b in rule_breakdown if b["outcome"] != "pass"],
            key=lambda b: (b["ratio"], -b["weight"]),
        )[:5]

    # Overall score: weighted average over evaluated dimensions only.
    evaluated = {d: data for d, data in dimensions.items() if data["ratio"] is not None}
    if not evaluated:
        overall = None
        overall_status = "unknown"
    else:
        active_weight_total = sum(w[d] for d in evaluated)
        overall = round(
            sum(w[d] * dimensions[d]["ratio"] for d in evaluated) / active_weight_total * 100.0, 1
        )
        overall_status = "healthy"

    # Status: compare each evaluated dimension against its threshold.
    dim_min = th["dimension_min"]
    violations: list[dict[str, Any]] = []
    for d, data in evaluated.items():
        if data["score"] < dim_min[d]:
            severity = "hard" if _dimension_has_fail(data) else "soft"
            violations.append({"dimension": d, "score": data["score"],
                               "threshold": dim_min[d], "severity": severity})
            data["threshold"] = dim_min[d]
            data["meets_threshold"] = False
        else:
            data["threshold"] = dim_min[d]
            data["meets_threshold"] = True

    overall_meets = overall is not None and overall >= th["overall_min"]
    if evaluated:
        if any(v["severity"] == "hard" for v in violations) or not overall_meets:
            overall_status = "failing"
        elif violations:
            overall_status = "degraded"

    unevaluated = [{"dimension": d, "reason": data["reason"]}
                   for d, data in dimensions.items() if data["ratio"] is None]

    weakest = min(
        ((d, data["score"]) for d, data in evaluated.items()), key=lambda x: x[1], default=(None, None)
    )
    return {
        "score": overall,
        "status": overall_status,
        "overall_threshold": th["overall_min"],
        "overall_meets_threshold": overall_meets,
        "dimensions": {
            d: {
                "weight": w[d],
                "score": data.get("score"),
                "ratio": data.get("ratio"),
                "threshold": data.get("threshold"),
                "meets_threshold": data.get("meets_threshold", None),
                "status": data.get("status", _dimension_status(data)),
                "weighted_rules": data["weighted_rules"],
                "outcomes": data["outcomes"],
                "reason": data.get("reason"),
                "rule_breakdown": data.get("rule_breakdown", []),
                "worst_rules": data.get("worst_rules", []),
            }
            for d, data in dimensions.items()
        },
        "weakest_dimension": {"name": weakest[0], "score": weakest[1]} if weakest[0] else None,
        "threshold_violations": violations,
        "unevaluated_dimensions": unevaluated,
        "weights": w,
        "thresholds": th,
        "methodology": (
            "Weighted average of evaluated dimensions. Each dimension's ratio is the "
            "severity-weighted mean of (1 - affected_pct/100) over its contributing rule "
            "results; pass counts as 1.0; skipped/info are ignored. Dimensions without any "
            "contributing rules are listed as not_evaluated so the score never claims to "
            "measure what it has no evidence for."
        ),
        "notes": [
            "Accuracy is only evaluated when at least one referential_integrity rule produced "
            "a result; otherwise it is reported as not_evaluated (no trusted reference data).",
            "This score is a transparent heuristic driven by the configured rules — not a "
            "calibrated probability of correctness or business impact.",
        ],
    }


def _dimension_has_fail(data: dict[str, Any]) -> bool:
    outcomes = data.get("outcomes", {})
    return outcomes.get("fail", 0) > 0


def _dimension_status(data: dict[str, Any]) -> str:
    """Status reflects score vs threshold; a passing threshold with issues is 'degraded'."""
    if data.get("ratio") is None:
        return "not_evaluated"
    outcomes = data.get("outcomes", {})
    has_issue = outcomes.get("fail", 0) > 0 or outcomes.get("warn", 0) > 0
    meets = data.get("meets_threshold")
    if meets is False:
        return "failing"
    if has_issue:
        return "degraded"
    return "healthy"


def _not_evaluated_reason(dim: str, outcomes: dict[str, int]) -> str:
    if outcomes.get("skipped", 0) > 0:
        return "No rules contributed a pass/warn/fail; the applicable rules were skipped (see rule results)."
    if dim == "accuracy":
        return "No trusted reference data configured. Add referential_integrity rule(s) to evaluate accuracy."
    return f"No {dim} rules evaluated for this dataset."


def compare_scores(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Compare two score reports and return a per-dimension delta."""
    def _delta(a, b):
        return None if a is None or b is None else round(b - a, 2)
    deltas = {d: _delta(previous["dimensions"][d]["score"], current["dimensions"][d]["score"])
              for d in DEFAULT_WEIGHTS}
    return {
        "previous_score": previous["score"], "current_score": current["score"],
        "delta": _delta(previous["score"], current["score"]),
        "dimension_deltas": deltas,
        "regressed_dimensions": [d for d, v in deltas.items() if v is not None and v < -1.0],
        "improved_dimensions": [d for d, v in deltas.items() if v is not None and v > 1.0],
    }


__all__ = ["DEFAULT_WEIGHTS", "DEFAULT_THRESHOLDS", "DIMENSION_RULES",
           "compare_scores", "compute_quality_score"]
