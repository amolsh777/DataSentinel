# DataSentinel — Architecture & MVP Plan

AI-assisted data quality, drift detection and pipeline intelligence platform.
This document is the planning baseline. It is updated as phases land; anything
marked *(planned)* is not implemented yet.

---

## 1. Proposed solution (summary)

DataSentinel is a modular monolith: a **FastAPI backend** that owns ingestion,
profiling, validation, drift analytics, incidents and lineage, persisting
**metadata only** in SQLite/PostgreSQL, plus a **Streamlit frontend** that talks
to the backend exclusively over REST.

Raw uploaded files live in a managed storage directory (`data/storage/`), never
as database blobs. Every uploaded file becomes an immutable **dataset version**.
A user approves one version as the **baseline**; each new version is compared
against it in a **monitoring run** that produces quality results, schema
changes, feature-level drift results and deduplicated **incidents**. Incidents
are traced through a manually configured **lineage graph** to show which
reports, KPIs or ML features may be affected. Recommendations are deterministic
and rule-based; no LLM is required.

Guiding principles: evidence before verdicts, effect size alongside p-values,
explicit "insufficient data" states, no invented root causes, synthetic data
always labelled as synthetic.

## 2. MVP features vs deferred features

**MVP (must work in the live demo)**

| # | Feature | Phase |
|---|---------|-------|
| 1 | CSV + Excel (sheet selection) ingestion with validation & limits | 1 ✅ |
| 2 | Dataset registry, immutable versions, file storage | 1 ✅ |
| 3 | Dataset + column profiling with documented quality score | 1 ✅ |
| 4 | Deterministic synthetic demo data + reset script | 1 ✅ |
| 5 | Configurable validation rules (12 check types) | 2 ✅ |
| 6 | Schema drift vs baseline (added/removed/type/nullability/category) | 2 ✅ |
| 7 | Baseline management with confirmation + audit reason | 3 |
| 8 | Statistical drift: PSI, KS, Wasserstein, JS divergence, chi-square, BH correction | 3 |
| 9 | Monitoring runs, incidents, fingerprint deduplication, status history | 4 |
| 10 | Lineage graph, traversal, impact analysis, cycle detection | 5 |
| 11 | Rule-based recommendations | 5 |
| 12 | CSV/JSON/HTML report export | 5 |
| 13 | Streamlit dashboard (10 pages), Plotly charts | 6 |
| 14 | Docker, README, demo script, end-to-end tests | 7 |

**Deferred (only after the core pipeline works)**: SQL-database ingestion,
PDF export, authentication/multi-user roles, Evidently integration (our own
implementations are used and tested instead; Evidently can be added as a
cross-check), external alerting (Slack/email), scheduled monitoring, Alembic
migrations (we use `create_all` + reset script for the hackathon), LLM
explanations.

## 3. Architecture and data flow

```
┌──────────────────────┐   HTTP/JSON   ┌───────────────────────────────────────┐
│ Streamlit frontend   │ ────────────▶ │ FastAPI backend (app/)                │
│ pages/ components/   │ ◀──────────── │  api/       routers, error handlers   │
│ services/api_client  │               │  services/  orchestration, storage    │
└──────────────────────┘               │  analytics/ pure pandas/scipy logic   │
                                       │  models/    SQLAlchemy ORM            │
                                       └───────┬─────────────────┬─────────────┘
                                               │                 │
                                   metadata    ▼                 ▼  raw files
                                ┌──────────────────┐   ┌────────────────────┐
                                │ SQLite / Postgres│   │ data/storage/<id>/ │
                                └──────────────────┘   └────────────────────┘
```

Layering rule (prevents circular imports): `api → services → analytics`.
`analytics/` never imports from `api/`, `services/` or `models/`; it takes and
returns pandas objects / plain dicts, which keeps it unit-testable.

**Upload flow**: file bytes → size check → format/content validation →
parse (pandas) → header checks (empty/duplicate names) → row/column limits →
store original bytes under a generated name → `dataset_versions` row →
schema snapshot → profile + column profiles.

**Monitoring flow** *(planned, phases 2–4)*: load baseline version + current
version → schema comparison → validation rules → drift per eligible feature →
multiple-testing correction → severity scoring → incident upsert by
fingerprint → lineage impact → persisted run summary.

## 4. Database schema

All tables are created now (phase 1) so later phases don't need disruptive
schema changes. IDs are UUID strings; all timestamps are UTC.

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `datasets` | Registered dataset | `name` (unique), `source_type`, `owner`, `project`, `is_synthetic` |
| `dataset_versions` | Immutable uploads | FK `dataset_id`, `version_number` (unique per dataset), `storage_path`, `content_hash`, `sheet_name`, `row_count`, `ingestion_warnings` |
| `schema_snapshots` | Column list per version | FK `version_id` (unique), `columns` JSON |
| `dataset_profiles` | Dataset-level metrics | FK `version_id` (unique), `quality_score`, `metrics` JSON, `sampled` |
| `column_profiles` | Column-level metrics | FK `profile_id`, `column_name`, `inferred_type`, missing/unique stats, `stats` JSON |
| `baselines` | Approved reference versions + audit | FK `dataset_id`, FK `version_id`, `is_active`, `reason`, `set_by` |
| `validation_rules` | Configurable checks | FK `dataset_id` (null = global), `rule_type`, `column_name`, `params`, `severity` |
| `monitoring_runs` | One comparison execution | FK dataset, baseline, reference/current versions, `status`, `summary` |
| `quality_check_results` | Outcome per rule per run | FK `run_id`, `outcome` (pass/fail/warn/info/skipped), evidence |
| `drift_results` | Outcome per feature per run | method, statistic, p/adjusted p, effect size, threshold, sample sizes |
| `incidents` | Deduplicated issues | `fingerprint`, `status`, `severity`, occurrence/recurrence counts |
| `incident_status_history` | Status audit trail | from/to status, note, actor, time |
| `lineage_assets` | Graph nodes | `asset_type` (dataset/transformation/metric/report/ml_feature) |
| `lineage_edges` | Graph edges | source/target, `source_columns`, `dependency_kind` (known/inferred) |
| `report_exports` | Generated report metadata | FK `run_id`, `format`, `storage_path` |

Indexes on all foreign keys, `incidents(fingerprint, status)`,
`incidents.severity`, `incidents.status`, `baselines.is_active`,
`dataset_versions.content_hash`.

## 5. Analytics methods and limitations

**Type inference.** Native pandas dtype first; object columns are tested for
boolean tokens, numeric parse ratio (≥ 90 % ⇒ numeric, remainder counted as
*invalid values*), date-like patterns (≥ 90 % parse ⇒ datetime), then
categorical vs free text by cardinality. Limitation: heuristic; a column of
zip codes will be inferred as integer.

**Quality score (0–100, heuristic, not calibrated).**
Completeness 40 × (1 − missing-cell fraction) + Uniqueness 20 ×
(1 − duplicate-row fraction) + Validity 25 × (1 − mean invalid-value fraction
across columns) + Consistency 15 × (1 − constant-column fraction). The
breakdown is always shown next to the score. Phase 2 will fold failed
validation rules into Validity.

**Validation (phase 2).** Twelve rule types (see `docs/RULES.md`). Every result is
pass / warn / fail / skipped with evidence; a rule that cannot run (missing column,
missing reference data) is *skipped with a reason*, never counted as a pass. Health
status: *failing* = a high/critical hard failure, *degraded* = other failures or
warnings, *healthy* = none. Suggested rules encode validity seen in the baseline
(types, keys, categories, non-negativity, completeness) and deliberately avoid
min/max bounds taken from baseline values — distribution change is drift's job.

**Schema drift (phase 2).** Column profiles are compared; each change is labelled
breaking/compatible with a severity. Rename candidates need name similarity ≥ 0.6
and the same inferred type, and are never merged automatically. Category changes
use the stored top-20 lists (`certain=false` when truncated).

**Drift (phase 3).**
- PSI on reference-quantile bins (default 10), epsilon smoothing for empty
  bins; conventional bands 0.1 / 0.25 shown as *guidelines*, not truths.
- Two-sample KS for continuous numeric features with n ≥ 30 per side.
- Wasserstein distance normalised by the reference IQR (effect size).
- Jensen–Shannon distance (base 2, 0–1) for categoricals, plus unseen /
  vanished categories.
- Chi-square only when every expected cell ≥ 5 (rare categories pooled),
  otherwise skipped with a stated reason.
- Benjamini–Hochberg correction across features; a feature is flagged
  *drifted* only if the effect size exceeds its threshold — significance alone
  never triggers drift (large samples make trivial shifts "significant").
- Missingness change reported separately from distribution change.
- Identifiers, constant columns and user-excluded columns are skipped.

Limitations stated in the UI and reports: data drift ≠ concept drift ≠ model
performance degradation; drift does not prove business impact; if a baseline
is stored as a sample, tests reflect the sample.

**Severity (phase 4)**: documented additive points (rule criticality, affected
%, downstream asset count, confidence) mapped to info/low/medium/high/critical.
It is a triage heuristic, never presented as a probability.

## 6. API contract

Implemented in phases 1–2 (all JSON; errors use
`{"error": {"code", "message", "details"}}`):

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/health` | Liveness + DB check |
| POST | `/api/files/inspect` | Parse an upload without saving: sheets, preview, inferred types, warnings |
| POST | `/api/datasets` | Register dataset + version 1 (multipart) |
| GET | `/api/datasets` | List (pagination `limit`/`offset`, `project`, `search`) |
| GET | `/api/datasets/{id}` | Detail incl. versions and latest profile summary |
| GET | `/api/datasets/{id}/versions` | Version list |
| POST | `/api/datasets/{id}/versions` | Upload a new version (multipart) |
| GET | `/api/datasets/{id}/profile?version=N` | Full profile (latest if omitted) |
| GET | `/api/datasets/{id}/versions/{n}/schema` | Schema snapshot |
| GET | `/api/datasets/{id}/versions/{n}/preview?rows=` | First rows (max 100) |
| GET/POST | `/api/rules` | List / create validation rules |
| GET/PATCH/DELETE | `/api/rules/{rule_id}` | Rule detail / partial update / delete |
| POST | `/api/datasets/{id}/rules/suggest` | Rules learned from a version profile (`save=true` persists) |
| POST | `/api/datasets/{id}/versions/{n}/validate` | Ad-hoc validation (`reference_version` enables schema_match) |
| GET | `/api/datasets/{id}/schema-diff?reference=&current=` | Schema comparison |

Planned: `POST /api/datasets/{id}/baseline`, `GET /api/datasets/{id}/baselines`,
`POST /api/datasets/{id}/monitor`, `GET /api/datasets/{id}/runs`,
`GET /api/runs/{id}`, `GET /api/runs/{id}/drift`, `GET/PATCH /api/incidents...`,
`GET/POST /api/lineage`, `GET /api/incidents/{id}/impact`,
`GET /api/runs/{id}/report?format=json|csv|html`. Interactive docs: `http://127.0.0.1:8000/docs`.

## 7. Repository structure

```
datasentinel/
  backend/
    app/
      main.py            create_app() factory
      asgi.py            module-level `app` for uvicorn
      api/               routers + dependencies
      core/              config, errors, logging
      database/          engine, session, Base
      models/            ORM models (all tables)
      schemas/           Pydantic request/response models
      services/          ingestion, storage, dataset orchestration
      analytics/         profiling (+ quality_checks, schema_drift, data_drift,
                         lineage, severity, recommendations in later phases)
      demo/              synthetic data generator + demo reset
      utils/             JSON-safe conversion helpers
    tests/
    requirements.txt, requirements-dev.txt, pytest.ini
  frontend/              Streamlit app (phase 6)
  data/sample/           generated synthetic datasets + manifest
  data/storage/          uploaded files (git-ignored)
  reports/               generated reports (git-ignored)
  scripts/               generate_sample_data.py, reset_demo.py
  docs/                  ARCHITECTURE.md, CHECKLIST.md
  .env.example  .gitignore  README.md
  Dockerfile  docker-compose.yml   (phase 7)
```

## 8. Seven-day breakdown (team of 2–4)

Roles: **A** backend/API, **B** analytics, **C** frontend, **D** QA/demo/docs
(with 2 people: A+D and B+C).

| Day | A — backend | B — analytics | C — frontend | D — QA/demo |
|-----|-------------|---------------|--------------|-------------|
| 1 | Repo, config, DB, ingestion API | Profiling engine | Streamlit shell, API client | Synthetic data, manifest |
| 2 | Rules CRUD API | Quality checks (12 types) | Dataset Explorer page | Profiling/quality tests |
| 3 | Baseline endpoints | Schema drift, PSI/KS/JS/chi², BH | Schema + Drift pages | Drift test fixtures |
| 4 | Monitoring run orchestration, incident upsert | Severity scoring | Incident Center | Dedup/status tests |
| 5 | Lineage API, report endpoints | Graph traversal, recommendations | Lineage graph page | Lineage tests, report checks |
| 6 | Pagination/limits hardening | Perf on 1M rows (sampling) | Overview, history, exports, styling | End-to-end demo rehearsal |
| 7 | Docker, deployment | Bug fixes | Polish | README, slides, 3-min demo |

Three-day fallback: Day 1 = phase 1 + quality checks; Day 2 = baseline,
schema + statistical drift, runs/incidents; Day 3 = Streamlit pages and
CSV/JSON export.

## 9. Key technical risks and mitigations

| Risk | Mitigation |
|------|------------|
| Misleading drift on large samples | Effect-size thresholds gate the verdict; p-values are secondary |
| Unstable stats on small samples | Minimum-n rules, explicit `insufficient_data` status |
| Messy real-world files | Strict validation with clear error codes; warnings instead of silent fixes |
| Memory on large files | Row/size limits, sampled column profiling (`DS_PROFILE_SAMPLE_ROWS`) |
| Library API drift (pandas 2 vs 3) | Pinned versions; tests run against pinned set |
| Incident spam | Fingerprint = dataset + rule + columns + condition; upsert, not insert |
| Demo failure | Offline synthetic data, `reset_demo.py`, scripted demo order |
| Scope creep | Deferred list above; vertical slice first |

## 10. Windows setup commands

See `README.md` → *Quick start (Windows PowerShell)*.
