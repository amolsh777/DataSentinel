# Validation rules reference

Create rules with `POST /api/rules`. Every rule has:

```json
{
  "dataset_id": "optional — omit for a global rule",
  "name": "optional — defaults to rule_type:column",
  "rule_type": "numeric_range",
  "column": "unit_price",
  "params": {"min": 0},
  "severity": "high",
  "enabled": true
}
```

Severity is one of `info`, `low`, `medium`, `high`, `critical`.

## Outcomes

| Outcome | Meaning | Result severity |
|---------|---------|-----------------|
| `fail` | Hard threshold exceeded | the rule's severity |
| `warn` | Soft threshold exceeded, or a non-breaking finding | one level below the rule's |
| `pass` | Condition holds | `info` |
| `skipped` | Could not be evaluated; `message` says why | `info` |

A skipped check is never counted as a pass. Typical reasons: the column is absent
(the schema checks report that separately), no baseline was supplied for
`schema_match`, or a referenced dataset does not exist.

Thresholds such as `max_missing_pct` are **percentages from 0 to 100**.

## Rule types

| rule_type | column | params | Notes |
|-----------|--------|--------|-------|
| `missing_values` | optional | `max_missing_pct` (required), `warn_missing_pct` | No column ⇒ one result per column |
| `duplicate_rows` | — | `max_duplicate_pct` = 0, `subset` (list) | Counts extra copies; evidence includes rows involved |
| `unique` | required | — | Nulls are ignored and reported |
| `required_columns` | — | `columns` | Fails listing the missing columns |
| `unexpected_columns` | — | `allowed` | Always `warn` (new columns are reviewed, not failed) |
| `data_type` | required | `expected_type`, `max_invalid_pct` = 0 | Types: integer, float, numeric, boolean, datetime, categorical, text |
| `numeric_range` | required | `min` and/or `max`, `max_violation_pct` = 0 | Non-numeric values are excluded and counted, not treated as range violations |
| `allowed_values` | required | `values`, `case_sensitive` = true, `max_violation_pct` = 0 | Evidence lists unexpected value counts |
| `date_range` | required | `min`, `max` (ISO dates), `allow_future` = true, `max_invalid_pct` = 0 | Unparseable dates within tolerance ⇒ `warn` |
| `constant_column` | optional | — | `warn` when a column has one distinct value |
| `schema_match` | — | — | Compares with the reference version's profile (see schema drift) |
| `business_rule` | — | `expression`, `max_violation_pct` = 0, `description` | See below |
| `referential_integrity` | required | `reference_values` **or** `reference_dataset` + `reference_column` | `reference_dataset` is a registered dataset *name*; its latest version is used |

## Business-rule expressions

Expressions use a small, safe subset of Python syntax. They are parsed into an
abstract syntax tree and evaluated node by node against the columns; nothing is
passed to `eval` or `exec`.

Allowed: column names (use backticks for names with spaces: `` `unit price` ``),
numbers, strings, `True`/`False`, `+ - * / %`, comparisons (including chained
`0 <= x <= 100`), `in` / `not in` with a literal list, `and` / `or` / `not`, and
the functions `abs()`, `isnull()`, `notnull()`.

Dates are compared as text, not as dates, so use `date_range` for date logic.

Rejected: attribute access, indexing, other function calls, imports, lambdas,
comprehensions, `**`, conditional expressions, and expressions over 500 characters.

Examples:

```text
abs(revenue - quantity * unit_price * (1 - discount_pct / 100)) <= 0.01
1 <= quantity <= 100
status in ['paid', 'refunded'] or isnull(status)
not (is_returned == True and revenue > 0)
```

Rows where any referenced column is null are **not evaluable**: they are neither
passes nor violations and are reported in `observed.not_evaluable`, unless the
expression handles nulls explicitly with `isnull`/`notnull`. A column takes part in
arithmetic as numbers when at least 90 % of its non-null values parse as numbers;
the remaining values make their rows not evaluable.

## Suggested rules

`POST /api/datasets/{id}/rules/suggest?version=1&save=true` learns a starter set
from a clean version:

- required columns, unexpected columns, schema match, constant columns, duplicate rows
- completeness per column: warn at baseline missing % + 2 pp, fail at + 10 pp
- data type for integer, float (as `numeric`), datetime and boolean columns
- uniqueness for identifier-like columns that were fully unique
- `min: 0` for numeric columns with no negative values
- allowed values for categorical columns with up to 30 fully listed categories
- no-future-dates for datetime columns

Suggestions intentionally do **not** set maximum/minimum bounds from the observed
baseline range. Values outside last quarter's range are not necessarily invalid;
that kind of change is measured by drift detection (Phase 3). Saving skips rules
whose name already exists for the dataset, so your edits are preserved.
