"""Deterministic, rule-based root-cause analysis.

Pure functions, no LLM required. Takes the artefacts the rest of the platform
already produces — quality-check results, drift report, schema diff and column
profiles — and emits one *finding* per issue with:

* a plain-language description of the detected issue
* the evidence from the current and baseline (reference) data
* a short list of **hypotheses** labelled ``likely`` / ``possible`` / ``unlikely``
  — never asserted as facts — with the specific evidence that supports or
  contradicts each one
* concrete investigation steps
* concrete corrective actions

Design rules
------------
* Hypotheses are suggestions, not conclusions. Every finding carries a disclaimer
  and labels possibilities by qualitative confidence, never as probabilities.
* A hypothesis is only emitted when at least one piece of evidence from the actual
  data supports it. Hypotheses with no support are omitted.
* The engine never invents a root cause. If the evidence is thin or contradictory,
  it says so and falls back to generic investigation steps.
* Deduplicates findings by (issue type, columns) so the same column failing two
  related rules produces one unified finding, not two parallel ones.
"""

from __future__ import annotations

from typing import Any

from app.utils.jsonsafe import deep_to_py

LIKELIHOOD_ORDER = ["unlikely", "possible", "likely"]
SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]

DISCLAIMER = (
    "These hypotheses are suggestions based on the data DataSentinel can see, "
    "not confirmed root causes. Investigate before acting."
)


# ---------------------------------------------------------------------- helpers


def _max(level: str, *others: str) -> str:
    return max([level, *others], key=LIKELIHOOD_ORDER.index)


def _column_profile(profiles: list[dict[str, Any]] | None, name: str) -> dict[str, Any] | None:
    if not profiles:
        return None
    for p in profiles:
        if p["column_name"] == name:
            return p
    return None


def _schema_changes_for(diff: dict[str, Any] | None, columns: list[str]) -> list[dict[str, Any]]:
    if not diff:
        return []
    cols = set(columns)
    return [c for c in diff.get("changes", []) if any(x in cols for x in c["columns"])]


def _drift_for(drift: dict[str, Any] | None, column: str) -> dict[str, Any] | None:
    if not drift:
        return None
    for f in drift.get("features", []):
        if f["name"] == column:
            return f
    return None


def _related_quality_issues(results: list[dict[str, Any]], column: str,
                            exclude_rule_type: str | None = None) -> list[dict[str, Any]]:
    out = []
    for r in results:
        if r["outcome"] not in ("fail", "warn"):
            continue
        if column not in (r.get("columns") or []):
            continue
        if exclude_rule_type and r["rule_type"] == exclude_rule_type:
            continue
        out.append({"rule_name": r["rule_name"], "rule_type": r["rule_type"],
                    "outcome": r["outcome"], "message": r["message"]})
    return out


def _hypothesis(cause: str, likelihood: str, supporting: list[str],
                contradicting: list[str] | None = None) -> dict[str, Any]:
    return {
        "cause": cause, "likelihood": likelihood,
        "supporting_evidence": supporting,
        "contradicting_evidence": contradicting or [],
    }


def _finding(
    *, finding_id: str, issue_type: str, issue_name: str, columns: list[str],
    severity: str, description: str, evidence: dict[str, Any],
    hypotheses: list[dict[str, Any]], investigation_steps: list[str],
    corrective_actions: list[str],
) -> dict[str, Any]:
    hypotheses = sorted(
        [h for h in hypotheses if h["supporting_evidence"]],
        key=lambda h: -LIKELIHOOD_ORDER.index(h["likelihood"]),
    )
    if not hypotheses:
        hypotheses.append(_hypothesis(
            "Cause could not be narrowed down from the available data",
            "possible",
            ["No specific pattern matched this issue in the current context."],
        ))
    return {
        "id": finding_id, "issue_type": issue_type, "issue_name": issue_name,
        "columns": columns, "severity": severity, "description": description,
        "evidence": evidence, "hypotheses": hypotheses,
        "investigation_steps": investigation_steps,
        "corrective_actions": corrective_actions,
        "disclaimer": DISCLAIMER,
    }


# ------------------------------------------------------------- quality patterns


def _rca_missing_values(result: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
    col = result["columns"][0] if result["columns"] else None
    if not col:
        return None
    ref = _column_profile(ctx.get("reference_profiles"), col)
    cur = _column_profile(ctx.get("current_profiles"), col)
    ref_miss = ref["missing_pct"] if ref else None
    cur_miss = cur["missing_pct"] if cur else (result.get("observed", {}) or {}).get("missing_pct", 0.0)
    delta = (cur_miss - ref_miss) if ref_miss is not None else None
    related_schema = _schema_changes_for(ctx.get("schema_diff"), [col])
    related_drift = _drift_for(ctx.get("drift_report"), col)
    related_quality = _related_quality_issues(ctx.get("quality_results", []), col, exclude_rule_type="missing_values")

    hypotheses: list[dict[str, Any]] = []
    if delta is not None and delta >= 10:
        hypotheses.append(_hypothesis(
            "An upstream source stopped supplying this field (API or integration change)",
            "likely" if delta >= 30 else "possible",
            [f"Missing rate rose from {ref_miss:.2f}% to {cur_miss:.2f}% ({delta:+.2f} pp vs baseline)."],
        ))
        hypotheses.append(_hypothesis(
            "A pipeline step was added that filters or masks this column (e.g. anonymisation)",
            "possible",
            [f"Missingness went from {ref_miss:.2f}% to {cur_miss:.2f}%: a step-change pattern "
             "typical of a transformation rather than gradual data loss."],
        ))
    if cur_miss >= 90 and (ref_miss is None or ref_miss < 20):
        hypotheses.append(_hypothesis(
            "A column rename or schema change occurred; data is landing in a different column",
            "likely",
            [f"Column is now {cur_miss:.2f}% empty while the baseline was populated."],
        ))
    if any(c["change_type"] == "nullability_changed" for c in related_schema):
        hypotheses.append(_hypothesis(
            "An upstream non-null constraint or default value was removed",
            "likely",
            ["Schema drift reports this column lost its non-null guarantee."],
        ))
    if any(c["change_type"] in ("storage_type_changed", "type_changed") for c in related_schema):
        hypotheses.append(_hypothesis(
            "Values are arriving in an unexpected format and failing to parse (treated as null)",
            "possible",
            ["A type-change event for this column is reported in the same comparison."],
        ))
    if not hypotheses:
        hypotheses.append(_hypothesis(
            "Normal variation in collection coverage",
            "possible",
            [f"Missing rate is {cur_miss:.2f}% with no large change from baseline."],
        ))

    return _finding(
        finding_id=f"RCA-missing-{col}",
        issue_type="quality",
        issue_name=f"High missing rate in '{col}'",
        columns=[col], severity=result["severity"],
        description=(
            f"'{col}' is {cur_miss:.2f}% missing"
            + (f" (baseline was {ref_miss:.2f}%; +{delta:.2f} pp)" if delta is not None else "")
            + "."
        ),
        evidence={
            "current": {"missing_pct": cur_miss, "missing_count": result.get("affected_count")},
            "baseline": {"missing_pct": ref_miss} if ref_miss is not None else None,
            "delta_pp": None if delta is None else round(delta, 4),
            "related_schema_changes": [{"type": c["change_type"], "severity": c["severity"],
                                        "explanation": c["explanation"]} for c in related_schema],
            "related_drift": (
                {"status": related_drift["status"], "severity": related_drift["severity"],
                 "missingness": related_drift.get("missingness")} if related_drift else None),
            "related_quality_issues": related_quality,
            "example_rows": result["evidence"].get("example_rows", []),
        },
        hypotheses=hypotheses,
        investigation_steps=[
            f"Compare the share of null '{col}' values day by day since the baseline run to confirm whether "
            "the change is a step or a trend.",
            f"Trace '{col}' upstream: open the source extract (file, API, query) for a few affected rows and "
            "check whether the value is null at origin or dropped on the way.",
            "Check for recent pipeline deployments, library upgrades, or SQL changes near the ingestion step.",
            "If an identifier joins this dataset to a reference table, verify the join is still returning rows.",
        ],
        corrective_actions=[
            "If upstream is the source of truth, request a fix there and backfill the affected records.",
            f"If '{col}' is still being produced but routed elsewhere, update the ingestion mapping.",
            f"If this field is now legitimately optional, lower the completeness threshold for '{col}' and "
            "document the change on the baseline.",
            "Add an alert on the ingestion step so the next regression is caught at the source.",
        ],
    )


def _rca_type_or_storage_change(col: str, change: dict[str, Any], ctx: dict[str, Any],
                                severity: str) -> dict[str, Any]:
    baseline = change.get("baseline"); current = change.get("current")
    evidence = change.get("evidence", {})
    invalid_count = evidence.get("invalid_count")
    examples = evidence.get("invalid_examples", [])
    hypotheses = [
        _hypothesis(
            "The source system started returning a different representation (string sentinel, "
            "trailing text, locale change)",
            "likely",
            [f"{invalid_count} value(s) do not match the baseline type"] if invalid_count
            else [f"The column's type moved from {baseline} to {current}."],
        ),
        _hypothesis(
            "A null-handling change: nulls are being replaced with a sentinel string like 'unknown' or 'N/A'",
            "likely" if examples and any(str(e).lower() in {"unknown", "n/a", "none", "null", "-", ""}
                                         for e in examples) else "possible",
            [f"Non-matching values include: {examples[:3]}" if examples else "Common sentinel strings often appear in this failure mode."],
        ),
        _hypothesis(
            "An upstream integration was swapped and the new version uses a different data contract",
            "possible",
            ["Hard type/storage change is the shape of a vendor or ETL swap, not of gradual drift."],
        ),
        _hypothesis(
            "A locale or date-format change (comma vs point decimals, dd/mm/yyyy vs ISO)",
            "possible" if examples else "unlikely",
            [f"Examples include: {examples[:3]}"] if examples else [],
        ),
    ]
    return _finding(
        finding_id=f"RCA-type-{col}",
        issue_type="schema", issue_name=f"Type change in '{col}'",
        columns=[col], severity=severity,
        description=change["explanation"],
        evidence={
            "baseline": {"type": baseline},
            "current": {"type": current, "invalid_count": invalid_count,
                        "invalid_examples": examples},
            "breaking": change["breaking"],
            "related_quality_issues": _related_quality_issues(ctx.get("quality_results", []), col),
        },
        hypotheses=hypotheses,
        investigation_steps=[
            f"Pull 20 raw rows where '{col}' is one of the non-matching values and inspect the source payload.",
            "Compare the source system's documentation or schema registry entry for this field between the "
            "baseline and current timestamps.",
            "Ask the source team whether anything about this field changed (format, nullability, default).",
            "Check whether a locale change (collection region, server time zone) correlates with the first "
            "appearance of the non-matching values.",
        ],
        corrective_actions=[
            f"Coerce or clean '{col}' at ingestion with a documented rule (e.g. strip 'unknown' -> null) and "
            "re-run profiling.",
            "If the type is intentionally wider now, update the baseline schema and consumer contracts "
            "together, not one at a time.",
            "Add a data-type validation rule to catch future regressions earlier.",
        ],
    )


def _rca_numeric_range(result: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
    col = result["columns"][0] if result["columns"] else None
    if not col:
        return None
    observed = result.get("observed", {})
    affected = result.get("affected_count") or 0
    out_of_range = observed.get("out_of_range") if observed else affected
    obs_min, obs_max = observed.get("observed_min"), observed.get("observed_max")
    is_negative = isinstance(obs_min, (int, float)) and obs_min < 0 and "0" in str(result.get("expected", ""))

    hypotheses: list[dict[str, Any]] = []
    if is_negative:
        hypotheses += [
            _hypothesis(
                "Refunds, credits or reversals are being written into the same column as positive revenue",
                "likely", [f"Minimum observed value is {obs_min}, below the allowed lower bound."],
            ),
            _hypothesis(
                "An upstream sign-flip bug (e.g. amount * -1 for a specific status)",
                "possible", [f"{out_of_range} negative value(s) with the rest of the column still positive."],
            ),
            _hypothesis(
                "A sentinel value is being used to represent missing data",
                "possible" if obs_min in (-1, -999, -9999) else "unlikely",
                [f"Minimum value {obs_min} looks like a common 'missing' sentinel."] if obs_min in (-1, -999, -9999) else [],
            ),
        ]
    else:
        hypotheses += [
            _hypothesis(
                "A unit or currency change (cents vs dollars, grams vs kg) in the source",
                "possible",
                [f"Observed range {obs_min} .. {obs_max} is outside the configured bounds."],
            ),
            _hypothesis(
                "Business expansion into a segment that legitimately exceeds the old bounds",
                "possible",
                [f"{out_of_range} value(s) out of range; see whether they cluster in new categories or regions."],
            ),
            _hypothesis(
                "A data-entry or ETL bug injecting extreme values",
                "possible", [f"Max observed {obs_max} is far outside expected."] if obs_max else [],
            ),
        ]

    related_drift = _drift_for(ctx.get("drift_report"), col)
    if related_drift and related_drift.get("status") == "drifted":
        hypotheses.append(_hypothesis(
            "The distribution has shifted beyond range violation (statistical drift)",
            "likely",
            [f"Drift detector flags '{col}' as drifted "
             f"(effect sizes: {related_drift.get('effect_sizes')})."],
        ))

    return _finding(
        finding_id=f"RCA-range-{col}",
        issue_type="quality", issue_name=f"Values out of range in '{col}'",
        columns=[col], severity=result["severity"],
        description=result["message"],
        evidence={
            "current": {"out_of_range": out_of_range,
                        "observed_min": obs_min, "observed_max": obs_max,
                        "example_values": result["evidence"].get("example_values", []),
                        "example_rows": result["evidence"].get("example_rows", [])},
            "expected": result.get("expected"),
            "related_drift": related_drift,
            "related_quality_issues": _related_quality_issues(ctx.get("quality_results", []), col,
                                                              exclude_rule_type="numeric_range"),
        },
        hypotheses=hypotheses,
        investigation_steps=[
            f"Look at the full distribution of '{col}' and whether the out-of-range values cluster by "
            "category, region, time window or source system.",
            f"Pull the raw source rows for 10–20 out-of-range '{col}' values.",
            "Confirm the unit of measurement with the data owner.",
            "Check whether a new upstream feed, product line, or market was added recently.",
        ],
        corrective_actions=[
            "If the bound is still correct, add a reject-and-quarantine step so bad rows do not reach "
            "downstream analytics.",
            "If values legitimately expanded, widen the bound and record the change on the baseline.",
            f"Add a schema contract with the source team for '{col}'.",
        ],
    )


def _rca_duplicates(result: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    pct = result.get("affected_pct") or 0.0
    hypotheses = [
        _hypothesis(
            "A retried or replayed upstream batch was ingested on top of the original",
            "likely",
            [f"{result.get('affected_count')} duplicate row(s) ({pct:.2f}%); batch replays are the most "
             "common cause of exact-row duplication."],
        ),
        _hypothesis(
            "Deduplication was removed or weakened in a recent pipeline change",
            "possible",
            ["Exact duplicates typically mean the dedup step is missing, not that two identical events happened."],
        ),
        _hypothesis(
            "Two ingestion sources were merged (union all) instead of a true merge",
            "possible", [f"Duplicate share {pct:.2f}% is uniform across the file."] if pct > 0.5 else [],
        ),
    ]
    return _finding(
        finding_id="RCA-duplicates",
        issue_type="quality", issue_name="Duplicate rows",
        columns=[], severity=result["severity"],
        description=result["message"],
        evidence={
            "current": {"extra_copies": result["evidence"].get("extra_copies"),
                        "rows_involved": result["evidence"].get("rows_involved"),
                        "example_rows": result["evidence"].get("example_rows", []),
                        "subset": result["evidence"].get("subset")},
        },
        hypotheses=hypotheses,
        investigation_steps=[
            "Check whether the extract timestamp or ingestion time is the same across the duplicate rows "
            "(if so, likely a retry/replay).",
            "Look at the orchestrator run history for a failed-then-retried job.",
            "Diff the ETL code for the dedup step between the baseline and current runs.",
        ],
        corrective_actions=[
            "Add a deduplication step keyed on the natural primary key before persistence.",
            "If replays are expected, flag the batch id and keep the later copy, not both.",
            "Backfill: delete the extra copies and re-run downstream aggregations for the affected period.",
        ],
    )


def _rca_broken_unique_key(result: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    col = result["columns"][0]
    examples = result["evidence"].get("example_values", {})
    hypotheses = [
        _hypothesis(
            f"The source for '{col}' is reusing values across batches (counter was reset or wrapped)",
            "likely",
            [f"{result['observed']['duplicated_values']} key value(s) appear across "
             f"{result['observed']['rows_with_duplicated_values']} rows."],
        ),
        _hypothesis(
            "Multiple files were concatenated and each had its own id namespace",
            "possible",
            [f"Example repeated keys: {list(examples)[:5]}"] if examples else [],
        ),
        _hypothesis(
            f"'{col}' was never truly unique and the baseline run happened to see no collisions",
            "unlikely",
            ["Only if the baseline sample is small or the key space is very wide."],
        ),
    ]
    return _finding(
        finding_id=f"RCA-key-{col}",
        issue_type="quality", issue_name=f"Duplicate key values in '{col}'",
        columns=[col], severity=result["severity"],
        description=result["message"],
        evidence={
            "current": {"duplicate_key_count": result["observed"]["duplicated_values"],
                        "rows_affected": result["observed"]["rows_with_duplicated_values"],
                        "example_keys": examples},
        },
        hypotheses=hypotheses,
        investigation_steps=[
            f"Inspect two rows that share the same '{col}'. If other columns differ, two real events "
            "share a key (source bug). If they are identical, this is duplication (see duplicate rows).",
            "Trace how the key is generated upstream: database sequence, UUID, hash, external id.",
            "Check whether any recent change moved key generation (e.g. app vs database side).",
        ],
        corrective_actions=[
            "If keys collided due to a counter reset, prefix new keys with a run/batch id or switch to UUIDs.",
            f"If '{col}' was never meant to be unique, drop the unique rule and introduce a surrogate key.",
            "Add a database-level unique constraint on the downstream table so the regression surfaces loudly.",
        ],
    )


def _rca_referential(result: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    col = result["columns"][0]
    reference = result["evidence"].get("reference")
    examples = result["evidence"].get("orphan_examples", {})
    hypotheses = [
        _hypothesis(
            f"The reference table ({reference}) is out of sync and does not yet include the new keys",
            "likely",
            [f"{result['observed']['orphan_rows']} orphan row(s) referencing "
             f"{result['observed']['distinct_orphans']} unknown key(s)."],
        ),
        _hypothesis(
            "A new business process is creating keys without inserting into the reference table first",
            "possible",
            [f"Example orphan keys: {list(examples)[:5]}"] if examples else [],
        ),
        _hypothesis(
            "The join is being made on the wrong field (e.g. internal id vs natural id)",
            "possible", ["The orphan keys do not look like the baseline key format."] if examples else [],
        ),
        _hypothesis(
            "Reference data was partially truncated or filtered",
            "unlikely" if result['observed']['orphan_rows'] < 100 else "possible",
            ["Many orphan keys with no obvious pattern suggests the reference lost rows."]
            if result['observed']['orphan_rows'] >= 100 else [],
        ),
    ]
    return _finding(
        finding_id=f"RCA-ri-{col}",
        issue_type="quality", issue_name=f"Referential-integrity failure on '{col}'",
        columns=[col], severity=result["severity"],
        description=result["message"],
        evidence={
            "current": {"orphan_rows": result['observed']['orphan_rows'],
                        "distinct_orphans": result['observed']['distinct_orphans'],
                        "example_orphan_keys": examples},
            "reference": reference,
        },
        hypotheses=hypotheses,
        investigation_steps=[
            f"Check the latest load time of the reference table versus '{col}'; a lagging reference load "
            "is the most common cause.",
            "Pick one orphan key and look for it in the reference source system directly.",
            f"Verify the join key: compare one '{col}' value against the format of the reference key column.",
            "Check whether the reference table was deliberately filtered or scoped by a recent change.",
        ],
        corrective_actions=[
            "Re-sync the reference table before the next monitoring run and re-check.",
            "Add an ordering guarantee: load the reference table first, then dependent datasets.",
            "If some orphan keys are legitimately expected, quarantine them with a reason rather than "
            "failing the whole run.",
        ],
    )


def _rca_allowed_values(result: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any] | None:
    col = result["columns"][0] if result["columns"] else None
    if not col:
        return None
    unexpected = result["evidence"].get("unexpected_value_counts", {})
    related_drift = _drift_for(ctx.get("drift_report"), col)
    unseen = (related_drift or {}).get("unseen_categories", [])
    hypotheses = [
        _hypothesis(
            f"A new business segment was launched that uses new '{col}' values",
            "likely" if unseen else "possible",
            [f"New categories observed: {[u['value'] for u in unseen[:5]]}"] if unseen
            else [f"Unexpected values: {list(unexpected)[:5]}"],
        ),
        _hypothesis(
            "Source system now emits internal codes or raw values instead of mapped labels",
            "possible",
            [f"Unexpected values look like internal codes: {list(unexpected)[:5]}"]
            if unexpected and any(len(v) <= 4 or v.isupper() for v in unexpected if isinstance(v, str)) else [],
        ),
        _hypothesis(
            "A spelling or capitalisation change (e.g. 'Credit Card' -> 'credit_card')",
            "possible" if any(isinstance(v, str) and v.lower() in {k.lower() for k in result['evidence'].get('unexpected_value_counts', {})}
                              for v in result['evidence'].get('unexpected_value_counts', {})) else "unlikely",
            [],
        ),
        _hypothesis(
            f"The configured allowed-values list for '{col}' is out of date",
            "possible",
            ["The baseline rules were learned from an earlier run; the new values may be legitimate."]
            if ctx.get("rules_source") == "baseline_suggested" else [],
        ),
    ]
    return _finding(
        finding_id=f"RCA-allowed-{col}",
        issue_type="quality", issue_name=f"Unexpected categories in '{col}'",
        columns=[col], severity=result["severity"],
        description=result["message"],
        evidence={
            "current": {"unexpected_values": unexpected,
                        "unexpected_count": result.get("affected_count")},
            "related_drift": related_drift,
        },
        hypotheses=hypotheses,
        investigation_steps=[
            f"Confirm with the business owner whether the new '{col}' values are legitimate (new product, "
            "region, status) or an error.",
            f"Check whether the unexpected '{col}' values appear only after a specific date or in a specific "
            "source system.",
            f"Compare the raw source for a row with an unexpected '{col}' value against a baseline row.",
        ],
        corrective_actions=[
            f"If legitimate: add the new values to the allowed list and update any downstream enum/mapping.",
            "If they are codes that should have been mapped, fix the mapping step and backfill.",
            "If they are errors, reject or correct them at ingestion.",
        ],
    )


def _rca_schema_match(result: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    changes = result["evidence"].get("changes", [])
    cols = sorted({c for ch in changes for c in ch["columns"]})
    hypotheses = [
        _hypothesis(
            "A deliberate upstream schema change was deployed without updating the baseline here",
            "likely",
            [f"{len(changes)} schema change(s) between baseline and current, affecting: {cols[:8]}."],
        ),
        _hypothesis(
            "An ETL mapping or SELECT list was edited and columns were accidentally dropped or renamed",
            "possible",
            [f"Breaking changes present: " + ", ".join(c["change_type"] for c in changes if c["breaking"])]
            if any(c["breaking"] for c in changes) else [],
        ),
        _hypothesis(
            "A source API version bump changed the response shape",
            "possible",
            ["Multiple added+removed columns together often indicate an API version change."]
            if any(c["change_type"] == "added_column" for c in changes)
               and any(c["change_type"] == "removed_column" for c in changes) else [],
        ),
    ]
    return _finding(
        finding_id="RCA-schema-match",
        issue_type="schema", issue_name="Schema does not match baseline",
        columns=cols, severity=result["severity"],
        description=result["message"],
        evidence={"changes": changes},
        hypotheses=hypotheses,
        investigation_steps=[
            "Review each breaking change and identify the owner of the upstream system that produces it.",
            "Check whether any of the removed/renamed columns are used by downstream reports (lineage).",
            "Compare the current ETL job's SELECT list with the baseline version.",
        ],
        corrective_actions=[
            "Decide per change: accept (update baseline + consumers) or revert (fix at the source).",
            "Introduce a schema contract/test on the source so changes are proposed before they land.",
        ],
    )


def _rca_removed_column(change: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    col = change["columns"][0]
    renames = [c for c in (ctx.get("schema_diff") or {}).get("rename_candidates", []) if c["removed"] == col]
    hypotheses = [
        _hypothesis(
            f"'{col}' was renamed — a candidate successor appeared with a similar name/type",
            "likely" if renames else "unlikely",
            [f"Rename candidate: '{renames[0]['added']}' (name similarity {renames[0]['name_similarity']})"]
            if renames else [],
        ),
        _hypothesis(
            f"'{col}' was deliberately removed upstream (deprecation)",
            "possible",
            [f"'{col}' is simply absent from the current data with no obvious successor."]
            if not renames else [],
        ),
        _hypothesis(
            "An ETL SELECT clause was edited and the column was dropped by mistake",
            "possible",
            ["Hard removal with no successor is a common pattern for an accidental SELECT list edit."]
            if not renames else [],
        ),
    ]
    return _finding(
        finding_id=f"RCA-removed-{col}",
        issue_type="schema", issue_name=f"'{col}' is missing from the current data",
        columns=[col], severity=change["severity"],
        description=change["explanation"],
        evidence={"baseline": change["baseline"], "rename_candidates": renames},
        hypotheses=hypotheses,
        investigation_steps=[
            f"Confirm whether '{col}' still exists in the source system; if yes, the loss is in the pipeline.",
            "If a rename candidate exists, confirm with the data owner before merging the two columns.",
            f"Check downstream consumers that read '{col}' and estimate blast radius.",
        ],
        corrective_actions=[
            f"If deliberate: update downstream queries, mark '{col}' as retired, and move the baseline.",
            "If accidental: restore the column in the ETL and backfill the missing records.",
            "If renamed: alias the new name to the old at the consumer boundary during a transition period.",
        ],
    )


# ---------------------------------------------------------------- drift pattern


def _rca_drift(feature: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    col = feature["name"]
    schema_changes = _schema_changes_for(ctx.get("schema_diff"), [col])
    related_quality = _related_quality_issues(ctx.get("quality_results", []), col)

    hypotheses: list[dict[str, Any]] = []
    if feature["kind"] == "numeric":
        ref_mean = feature["stats"]["reference"]["mean"]
        cur_mean = feature["stats"]["current"]["mean"]
        direction = "increased" if cur_mean > ref_mean else "decreased"
        hypotheses += [
            _hypothesis(
                f"A legitimate business change (pricing, mix, policy) {direction} '{col}'",
                "likely",
                [f"Mean {direction} by {abs(cur_mean - ref_mean):.4g}; "
                 f"effect sizes: {feature['effect_sizes']}."],
            ),
            _hypothesis(
                "A unit or scale change in the source (cents vs dollars, grams vs kg)",
                "possible" if abs(cur_mean / ref_mean - 1) > 0.5 and ref_mean != 0 else "unlikely",
                [f"Current mean is {cur_mean / ref_mean:.2f}x the baseline mean."]
                if ref_mean != 0 and abs(cur_mean / ref_mean - 1) > 0.5 else [],
            ),
            _hypothesis(
                f"New data segments contribute values that pull '{col}' away from the baseline range",
                "possible",
                ["Investigate whether the shift is uniform or concentrated in specific categories."],
            ),
        ]
    else:
        unseen = feature.get("unseen_categories", [])
        vanished = feature.get("vanished_categories", [])
        if unseen:
            hypotheses.append(_hypothesis(
                "A new business segment/product/region is now generating records",
                "likely",
                [f"{len(unseen)} new category value(s): "
                 + ", ".join(f"'{u['value']}' ({u['current_pct']:.2f}%)" for u in unseen[:5])],
            ))
        if vanished:
            hypotheses.append(_hypothesis(
                "A segment was deprecated, filtered out upstream, or renamed",
                "likely" if len(vanished) <= 3 else "possible",
                [f"Baseline values no longer present: {vanished[:5]}"],
            ))
        if feature.get("missingness", {}).get("status") != "ok":
            hypotheses.append(_hypothesis(
                "An upstream change moved values into or out of null state",
                "possible",
                [f"Missing rate changed by {feature['missingness']['delta_pp']:+.2f} pp."],
            ))

    if schema_changes:
        hypotheses.append(_hypothesis(
            "A schema change on this column coincides with the drift",
            "likely",
            [f"Related schema change: {c['change_type']} — {c['explanation']}" for c in schema_changes[:3]],
        ))
    if related_quality:
        hypotheses.append(_hypothesis(
            "The drift is the surface effect of a data-quality failure (type, nullability, range)",
            "possible",
            [f"Also failing: {q['rule_name']}" for q in related_quality[:3]],
        ))

    severity = feature["severity"]
    return _finding(
        finding_id=f"RCA-drift-{col}",
        issue_type="drift", issue_name=f"Distribution drift in '{col}'",
        columns=[col], severity=severity,
        description=feature["explanation"],
        evidence={
            "current": {
                "reference_n": feature["reference_n"], "current_n": feature["current_n"],
                "effect_sizes": feature["effect_sizes"], "missingness": feature.get("missingness"),
            },
            "baseline": {"stats": feature.get("stats", {}).get("reference")} if feature.get("stats") else None,
            "related_schema_changes": [{"type": c["change_type"], "severity": c["severity"],
                                        "explanation": c["explanation"]} for c in schema_changes],
            "related_quality_issues": related_quality,
            "unseen_categories": feature.get("unseen_categories", []),
        },
        hypotheses=hypotheses,
        investigation_steps=[
            f"Split '{col}' by category or time window and look at whether the shift is uniform or "
            "concentrated in a sub-segment.",
            "Ask the data owner whether a business change (pricing, product launch, policy) explains the shift.",
            "Confirm no unit, currency or encoding change has happened upstream.",
            "Compare one specific baseline row with one shifted current row to see what differs beyond "
            f"the '{col}' value itself.",
        ],
        corrective_actions=[
            "If the shift reflects a real business change, re-baseline after acknowledging it.",
            "If the shift is a data problem, fix at the source and backfill; do not re-baseline yet.",
            "Add drift thresholds to a monitored alert so the next move is caught early.",
        ],
    )


# -------------------------------------------------------------------- dispatch


_QUALITY_HANDLERS = {
    "missing_values":        _rca_missing_values,
    "numeric_range":         _rca_numeric_range,
    "duplicate_rows":        _rca_duplicates,
    "unique":                _rca_broken_unique_key,
    "referential_integrity": _rca_referential,
    "allowed_values":        _rca_allowed_values,
    "schema_match":          _rca_schema_match,
}


def analyze(
    *,
    current_profile: dict[str, Any],
    quality_results: list[dict[str, Any]] | None = None,
    drift_report: dict[str, Any] | None = None,
    schema_diff: dict[str, Any] | None = None,
    reference_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce a root-cause report over all detected issues.

    Pass whichever artefacts are available. The engine merges findings across
    quality, drift and schema evidence; the same column failing a quality rule
    *and* drifting produces one finding with both pieces of evidence linked.
    """
    quality_results = quality_results or []
    ctx = {
        "quality_results": quality_results,
        "drift_report": drift_report,
        "schema_diff": schema_diff,
        "reference_profiles": (reference_profile or {}).get("columns") if reference_profile else None,
        "current_profiles": current_profile["columns"],
    }

    findings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _add(f: dict[str, Any] | None) -> None:
        if f is None:
            return
        if f["id"] in seen_ids:
            return
        seen_ids.add(f["id"])
        findings.append(f)

    # Quality-driven findings
    for result in quality_results:
        if result["outcome"] not in ("fail", "warn"):
            continue
        handler = _QUALITY_HANDLERS.get(result["rule_type"])
        if handler is not None:
            _add(handler(result, ctx))

    # Schema-driven findings for breaking removals and hard type changes.
    if schema_diff:
        for ch in schema_diff.get("changes", []):
            if ch["change_type"] == "removed_column":
                _add(_rca_removed_column(ch, ctx))
            elif ch["change_type"] in ("type_changed", "storage_type_changed"):
                col = ch["columns"][0]
                _add(_rca_type_or_storage_change(col, ch, ctx, ch["severity"]))

    # Drift-driven findings (skip those already covered by a quality finding on the same column).
    if drift_report:
        quality_findings_by_col: dict[str, dict[str, Any]] = {}
        for f in findings:
            if f["issue_type"] != "quality":
                continue
            for col in f["columns"]:
                quality_findings_by_col.setdefault(col, f)
        for feature in drift_report.get("features", []):
            if feature.get("status") != "drifted":
                continue
            existing = quality_findings_by_col.get(feature["name"])
            if existing is not None:
                existing["evidence"]["related_drift"] = {
                    "status": feature["status"], "severity": feature["severity"],
                    "effect_sizes": feature["effect_sizes"],
                    "missingness": feature.get("missingness"),
                    "unseen_categories": feature.get("unseen_categories", []),
                }
                continue
            _add(_rca_drift(feature, ctx))

    findings.sort(key=lambda f: -SEVERITY_ORDER.index(f["severity"]))
    summary = {
        "total_findings": len(findings),
        "by_type": {t: sum(1 for f in findings if f["issue_type"] == t)
                    for t in ("quality", "drift", "schema")},
        "by_severity": {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITY_ORDER},
        "max_severity": findings[0]["severity"] if findings else "info",
    }
    return deep_to_py({
        "summary": summary, "findings": findings, "disclaimer": DISCLAIMER,
    })


__all__ = ["analyze"]
