# Drift detection and quality score reference

## Baselines

A baseline is the approved reference version of a dataset. Changing it is never
silent.

| Action | Endpoint | Behaviour |
|--------|----------|-----------|
| Approve a version | `POST /api/datasets/{id}/baseline` | Needs `version_number` and a non-empty `reason`; `set_by` is optional |
| Replace the active baseline | same, with `"confirm": true` | Without `confirm`, the request is refused (`BASELINE_CONFIRMATION_REQUIRED`) |
| Re-approve the active version | same | No-op: returns "Baseline unchanged" |
| History | `GET /api/datasets/{id}/baselines` | Newest first. Replaced baselines stay as inactive audit rows with `deactivated_at` |

Reference resolution for drift, score and root cause: an explicit
`reference_version` wins; otherwise the active baseline is used (unless
`use_active_baseline` is false); otherwise drift returns `REFERENCE_REQUIRED`.

**Limitation.** Drift re-reads the stored reference file, so statistical tests use
the full reference data rather than a stored summary. Deleting stored files would
break comparisons; a summary-only baseline is a possible future change.

## Drift: `POST /api/datasets/{id}/drift`

```json
{
  "current_version": 2,
  "reference_version": null,
  "use_active_baseline": true,
  "exclude_columns": ["transaction_date"],
  "thresholds": {"psi": {"warn": 0.1, "high": 0.25}}
}
```

### Methods

| Feature type | Always | Added when conditions hold |
|--------------|--------|----------------------------|
| Numeric / datetime | PSI (reference-quantile bins, default 10, epsilon-smoothed), normalised Wasserstein | KS test (>= 30 values per side) |
| Categorical / boolean | Jensen-Shannon distance (base 2, range 0 to 1), unseen-category share | Chi-square, only if every expected cell is >= 5 after pooling rare categories |

Wasserstein is divided by the reference IQR so it is comparable across columns;
if the IQR is zero it is reported as undefined rather than invented.

### What decides "drifted"

**Effect size, never p-values.** A feature is `drifted` when PSI or normalised
Wasserstein (numeric), or JS distance or unseen-category share (categorical),
reaches the warn threshold. KS and chi-square p-values are reported next to it
(Benjamini-Hochberg corrected across features, `significant_after_bh`) but never
trigger the verdict. With 50,000 rows a 2% mean shift is "significant" and
operationally irrelevant; the test suite pins this behaviour.

| Metric | warn (medium) | high | Notes |
|--------|--------------:|-----:|-------|
| PSI | 0.10 | 0.25 | Conventional bands; guidelines, not laws |
| Wasserstein / IQR | 0.10 | 0.30 | |
| JS distance | 0.10 | 0.20 | |
| Unseen categories (% of rows) | 1.0 | 5.0 | |
| Missingness change (pp) | 5 | 20 | Reported separately from distribution drift |

Override any of these in the request. Unknown threshold keys are ignored.

### Guard rails

- Fewer than 30 non-null values per side gives `insufficient_data` with a reason, never `stable`.
- Identifier-like columns, columns constant on both sides, and `exclude_columns` are skipped; `skipped` lists each with its reason.
- Columns absent from the current data are skipped here and reported by schema comparison.
- Dataset status: `drifted` if any drifted feature has high severity, `warn` if any drifted, else `stable`.

### Reading the result

Each feature carries `status`, `severity`, `primary_method`, `reference_n`,
`current_n`, `effect_sizes`, `missingness`, its thresholds, a plain-language
`explanation`, and the full PSI bin table (reference % vs current %) for charting.

### What drift does not tell you

Data drift is a change in input distributions. It is not concept drift, and it is
not evidence that model accuracy or business results changed. Treat it as a
prompt to investigate.

## Quality score: `POST /api/datasets/{id}/score`

The score is computed from the results of your configured validation rules.

| Dimension | Rule types counted | Default weight | Default minimum |
|-----------|--------------------|---------------:|----------------:|
| Completeness | missing_values | 25 | 70 |
| Validity | data_type, numeric_range, allowed_values, date_range, business_rule, constant_column | 25 | 70 |
| Uniqueness | unique, duplicate_rows | 15 | 80 |
| Consistency | schema_match, required_columns, unexpected_columns | 15 | 80 |
| Accuracy | referential_integrity | 20 | 80 |

**Per result:** pass = 1.0; warn or fail = `1 - affected_pct/100`; skipped and
info are ignored (they neither pass nor penalise).

**Per dimension:** mean of its result ratios, weighted by rule severity
(info 1, low 2, medium 3, high 4, critical 5).

**Overall:** weighted average over the *evaluated* dimensions only, so a dimension
with no applicable rules cannot inflate or deflate the score.

**Accuracy honesty.** Accuracy is evaluated only when a `referential_integrity`
rule produced a result, because that is the only check that compares data with
trusted reference data. Otherwise it reports `not_evaluated` with that reason.
DataSentinel does not claim to measure real-world accuracy.

**Thresholds.** `overall_min` and per-dimension `dimension_min` are configurable.
Status is `failing` if the overall score is below `overall_min` or a dimension
below its minimum has a hard failure; `degraded` if a dimension misses its
minimum without one; `healthy` otherwise. A dimension with issues that still
meets its minimum is shown as `degraded` at dimension level.

Weights must be non-negative and not all zero (`INVALID_SCORE_CONFIG` otherwise).

`POST /api/datasets/{id}/score/compare` scores two versions against the same rules
and returns per-dimension deltas plus `regressed_dimensions` (fell by more than 1
point) and `improved_dimensions`.

**Reading a score honestly.** The score is driven by *affected percentages*, so a
few severe failures (30 negative revenue rows out of 5,150) cost very little.
That is why v2 of the demo data scores about 97.9 while carrying ten failed
checks. Use the dimension breakdown, the `worst_rules` list and the check results
to see what is wrong; use the score to track the trend.
