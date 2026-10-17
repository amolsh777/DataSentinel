# Root-cause analysis reference

`POST /api/datasets/{id}/root-cause`

```json
{
  "current_version": 2,
  "reference_version": null,
  "use_active_baseline": true,
  "include_drift": true,
  "thresholds": {"psi": {"warn": 0.1, "high": 0.25}}
}
```

The endpoint runs the dataset's enabled rules, a schema comparison and a drift
analysis, then turns the results into one *finding* per issue. It is
deterministic and rule-based: no LLM is involved and none is required.

Without a reference version (no baseline, `use_active_baseline: false`) it still
runs on quality results alone; schema and drift findings are simply absent and
`schema_summary` / `drift_summary` are null.

## What a finding contains

| Field | Meaning |
|-------|---------|
| `issue_name`, `issue_type` | What was detected; `quality`, `schema` or `drift` |
| `columns`, `severity`, `description` | Affected columns and a plain-language summary |
| `evidence` | Current values, baseline values, deltas, related schema changes, related drift, related failed rules, example rows |
| `hypotheses` | Possible causes, each with `likelihood`, `supporting_evidence` and `contradicting_evidence` |
| `investigation_steps` | What to check, in order |
| `corrective_actions` | What to do once the cause is confirmed |
| `disclaimer` | Hypotheses are suggestions, not confirmed causes |

## How hypotheses are produced

- **Hypotheses, not facts.** Likelihood is a qualitative label (`likely`, `possible`, `unlikely`), never a probability.
- **Evidence-gated.** A hypothesis is emitted only when it cites at least one concrete observation from the data (for example "missing rate rose from 1.02% to 18.66%"). Hypotheses with no supporting observation are dropped. If nothing matches, the finding says the cause could not be narrowed down rather than guessing.
- **Ordered** most likely first.
- **Linked across analyses.** A column that fails a quality rule *and* drifts yields one finding with the drift evidence attached, not two competing ones.

## Patterns covered

| Detected issue | Example hypotheses (each needs supporting evidence) |
|----------------|------------------------------------------------------|
| Missing-value spike | Upstream source stopped supplying the field; a pipeline step masks it; a non-null constraint or default was removed; values failing to parse |
| Type or storage change | Source switched representation; nulls replaced by a sentinel such as `unknown`; vendor or data-contract swap; locale or format change |
| Out-of-range values | Refunds or reversals in a positive column; sign-flip bug; sentinel value; unit change; legitimate expansion |
| Duplicate rows | Replayed batch; dedup step removed; union instead of merge |
| Broken unique key | Counter reset or reuse across batches; concatenated files with separate id namespaces |
| Referential-integrity failure | Reference table out of sync; keys created without reference insert; wrong join field |
| Unexpected categories | New business segment; internal codes instead of labels; spelling change; stale allowed-values list |
| Schema mismatch / removed column | Deliberate upstream change; accidental SELECT edit; API version change; rename candidate |
| Statistical drift | Business change; unit change; new segments; coinciding schema change |

## Reading the demo output

On the synthetic v2 data the engine reports 13 findings (8 quality, 3 schema,
2 drift). For example, `customer_age` missingness (1.02% to 18.66%) is shown with
baseline and current rates, the delta, and "upstream source stopped supplying the
field" as a *possible* cause. The word "possible" is deliberate: the data shows
that the rate rose, not why.

Note that the demo's own injected-issue manifest describes causes (for example
"2% of quantity values set to `unknown`"). DataSentinel does not know that: it
sees the symptom and proposes explanations.

## Limitations

- Pattern matching, not inference: it cannot discover causes outside its catalogue.
- It sees only the data and rules it is given; it has no view of pipeline code, deployment history or source systems, which is why every finding ends with steps for a person to check them.
- Some hypotheses lean on name and value heuristics (for example sentinel strings, rename similarity) and can be wrong.
- Drift findings inherit the limits in `DRIFT.md`: data drift is not proof of business impact.
