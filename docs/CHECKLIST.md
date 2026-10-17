# Implementation checklist

Legend: ✅ done & tested · ⏳ next · ⬜ planned

## Phase 1 — Foundation, ingestion, profiling ✅
- ✅ Repository structure, env-driven config (`DS_*`), consistent JSON error envelope
- ✅ All 15 database tables defined (datasets → lineage), SQLite FK enforcement
- ✅ CSV ingestion: delimiter sniffing, UTF-8/BOM/cp1252/latin-1, size/row/column limits
- ✅ Rejects: empty file, header-only, duplicate/missing headers, malformed rows, binary, wrong extension
- ✅ Excel ingestion with sheet listing/selection, corrupted-workbook handling
- ✅ Managed file storage (generated names, path-traversal guard), content-hash duplicate detection
- ✅ Profiling: semantic types, invalid values, numeric/datetime/categorical/text/boolean stats,
      constant/near-constant, identifier detection, sampling, explainable quality score
- ✅ API: health, file inspect, dataset register/list/detail, versions, profile, schema, preview
- ✅ Deterministic synthetic data (v1, v1b stable, v2 with 9 documented issues, customers, xlsx, lineage JSON)
- ✅ `reset_demo.py` with explicit `--yes` confirmation
- ✅ 71 automated tests passing (ingestion, profiling, sample data, API)

## Phase 2 — Validation rules & schema drift ✅
- ✅ Rule CRUD API (`/api/rules`), dataset-scoped and global rules, per-type parameter validation
- ✅ Rule suggestions from a version profile (`/rules/suggest`, optional save, never duplicates by name)
- ✅ 12 check types + unexpected columns: missing threshold, duplicate rows, unique key, required /
      unexpected columns, data type, numeric range, allowed values, date parse/range, constant,
      schema-vs-baseline, business rule, referential integrity
- ✅ Outcomes pass / warn / fail / skipped (with reason); evidence on every result; health summary
- ✅ Safe business-rule expressions (AST whitelist, no eval), injection attempts tested
- ✅ Schema comparison: added/removed, rename candidates (never merged), type & storage type,
      nullability/missingness, key uniqueness, new/vanished categories, reordering, became-constant
- ✅ Breaking vs compatible classification, side-by-side column view, severity ordering
- ✅ Ad-hoc validation and schema-diff endpoints; demo reset seeds 36 rules
- ✅ Verified against the synthetic ground truth: every injected v2 issue except ISS-05
      (statistical drift, Phase 3) is detected; v1 and the stable v1b produce no findings
- ✅ 162 automated tests passing

## Phase 3 — Baselines, drift, quality score, root cause ✅
- ✅ Baseline set/replace with required reason, explicit confirmation, audit history
- ✅ PSI, KS, Wasserstein (IQR-normalised), JS distance, chi-square with assumption checks
- ✅ Benjamini–Hochberg, minimum sample sizes, missingness change, exclusions, dataset-level summary
- ✅ Effect-size-gated verdicts (verified on a 50k-row trivial shift), configurable thresholds
- ✅ Configurable rule-based quality score: 5 dimensions, weights, thresholds, per-rule breakdown,
      accuracy only with referential evidence, cross-version compare
- ✅ Root-cause analysis: evidence-gated hypotheses, investigation steps, corrective actions,
      quality/schema/drift evidence merged per column
- ✅ Demo reset approves the v1 baseline; drift/RCA verified against all injected v2 issues
- ✅ 227 automated tests passing

## Phase 4 — Monitoring runs & incidents ⏳
- ⬜ Run orchestration + persistence, severity scoring, incident fingerprint dedup, recurrence, status history

## Phase 5 — Lineage, recommendations, reports
- ⬜ Lineage CRUD, traversal, cycle detection, impact per incident, known vs inferred
- ⬜ Rule-based recommendations (symptom / possible explanations / investigation)
- ⬜ JSON, CSV, HTML report export

## Phase 6 — Streamlit dashboard (10 pages, Plotly)
## Phase 7 — Docker, deployment, end-to-end tests, demo script, slides
