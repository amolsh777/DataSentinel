# DataSentinel — How to Use It

This guide covers every page and every option in the DataSentinel dashboard.
Open it when you are not sure what something does or how to load data for the first time.

---

## Table of contents

1. [Before you start — what DataSentinel does](#1-before-you-start)
2. [Starting the application](#2-starting-the-application)
3. [Overview page](#3-overview-page)
4. [Run History page](#4-run-history-page)
5. [Datasets page](#5-datasets-page)
6. [Data Quality page](#6-data-quality-page)
7. [Drift Monitoring page](#7-drift-monitoring-page)
8. [Schema Comparison page](#8-schema-comparison-page)
9. [Root Cause Analysis page](#9-root-cause-analysis-page)
10. [Incident Center page](#10-incident-center-page)
11. [Data Lineage page](#11-data-lineage-page)
12. [Rules & Baselines page](#12-rules--baselines-page)
13. [Sidebar controls](#13-sidebar-controls)
14. [API and connections](#14-api-and-connections)
15. [Troubleshooting](#15-troubleshooting)

---

## 1. Before you start

DataSentinel answers ten questions about your data:

| Question | Where to find the answer |
|----------|--------------------------|
| Is my dataset healthy? | Overview → Run assessment |
| What changed vs last time? | Drift Monitoring / Schema Comparison |
| Which rules failed? | Data Quality → Validation results |
| Has the distribution of features changed? | Drift Monitoring |
| Is the change meaningful or just noise? | Drift Monitoring (effect size vs threshold) |
| Which downstream reports are affected? | Incident Center → Downstream impact tab |
| How severe is the issue? | Incident Center (0–100 severity score) |
| What changed over time? | Run History |
| Why did it fail? | Root Cause Analysis |
| What should I do? | Root Cause Analysis → Recommendations |

DataSentinel does **not** require a data warehouse, a cloud provider or an API key.
It works entirely on files you upload, locally.

---

## 2. Starting the application

```powershell
# From the repository root (Windows PowerShell).
# Run the backend in one terminal:
cd backend
uvicorn app.asgi:app --port 8000

# Run the frontend in a second terminal (stay at the repository root):
streamlit run frontend\app.py
```

Then open **http://localhost:8501** in your browser.

The backend must be running before the frontend. If the API status badge shows
**FAILING** (top-right of every page), go back to the terminal that runs the
backend and check for error messages.

---

## 3. Overview page

**Purpose:** See the health of all your datasets at a glance, and run a
full assessment on any two dataset versions in one click.

### Dataset table

The top table lists every registered dataset with:

- **Dataset** — the name you gave it when you uploaded it.
- **Project** — an optional project label (set at upload time).
- **Versions** — how many file versions have been uploaded for this dataset.
- **Latest rows** — row count of the most recently uploaded version.
- **Profile score** — an automatic 0–100 quality score computed at upload
  time from completeness, uniqueness, type consistency and duplicate rate.
  This score has no rules — it is always available.
- **Baseline** — the version that has been approved as the reference
  (set on the Rules & Baselines page). "not set" means no baseline yet.
- **Data** — "synthetic" for demo data, "uploaded" for your own files.

### Run an assessment

This section lets you compare **any two dataset versions** — they can be from
the same dataset or two completely different datasets.

**How to load datasets before running an assessment:**

1. Go to **Datasets → Demo data** and click the three buttons in order
   (Load, Configure rules + baseline, Add v2). This takes about 10 seconds
   and requires no file upload.
2. Or go to **Datasets → Register / upload**, choose a CSV or Excel file
   from your computer, fill in the name, and click **Register dataset**.
3. Once loaded, the dataset name appears in both dropdowns in the assessment
   section on this page.

**How to run an assessment:**

1. Under **① Current dataset and version**, pick the dataset and version you
   want to analyse. This is usually your latest upload — the one you suspect
   has problems.
2. Under **② Reference dataset and version**, pick the dataset and version to
   compare against. This is usually the clean version (your baseline).
   - To compare two versions of the same dataset, leave the reference dataset
     the same and pick an earlier version number.
   - To compare two different datasets (for example, staging vs production),
     choose a different dataset in the reference dropdown.
   - If you have set a baseline on the Rules & Baselines page, it is
     pre-selected here automatically.
3. Click **Run assessment**.

The assessment runs:
- **Quality checks** against the current version's validation rules.
- **Quality score** (five dimensions: completeness, validity, uniqueness,
  consistency, accuracy) with configurable weights.
- **Drift detection** (PSI, KS, Wasserstein, Jensen-Shannon) for every
  numeric and categorical feature.
- **Root-cause analysis** — evidence-backed hypotheses for each issue found.

Results appear below the button: KPI cards, a dimension bar chart, and a
prioritised "what to investigate first" list.

---

## 4. Run History page

**Purpose:** See a timeline of every monitoring run for the active dataset.
This is what makes DataSentinel a monitoring tool rather than a one-shot
analyser.

### What is shown

- **Score trend chart** — quality score (line) and rule failure count (bar)
  over time. A falling line or rising bars means quality is getting worse.
- **Run log table** — one row per run with health status, score, failures,
  warnings, drifted features, schema changes, and incident counts.
- Clicking a run in the table shows its full detail: check results, drift
  results and report download.

### Running a new monitoring check

At the bottom of the page:

1. Pick the **current version** and **reference version** using the dropdowns.
2. Choose columns to exclude from drift (datetime columns are excluded by
   default — they always differ between time periods and would flood the
   results).
3. Click **Submit as background job (recommended)** to start the run without
   blocking your browser. The page shows the job ID; refresh after a few
   seconds.
4. Or click **Run now (synchronous)** to wait for the result in the browser
   (suitable for small datasets, up to a few thousand rows).

**Background jobs** persist results even if you close the browser tab.
Monitor their status in the job list at the bottom of the page.

### Download a report

Select a run from the log table, open the **Download report** tab, and choose:
- **HTML** — a self-contained, shareable page with KPIs, all findings,
  recommendations and a data disclaimer.
- **JSON** — machine-readable, suitable for importing into other tools.
- **CSV** — a flat table of check results and incidents for spreadsheets.

---

## 5. Datasets page

**Purpose:** Upload files, browse versions, inspect column profiles, and
override inferred column types.

### Tab: Explorer

Select the active dataset from the sidebar, then choose a version from the
dropdown. You can browse:

- **Preview** — the first N rows of the file (up to 100, adjustable with the
  slider). A **Download original file** button saves the uploaded file to your
  computer.
- **Schema** — every column with its inferred type, pandas dtype, nullable
  flag, missing percentage, unique percentage and identifier flag.
  - **Override column types:** select a column from the "Column to override"
    dropdown, choose a type, and click **Apply type override**. Useful when
    DataSentinel infers zip codes or phone numbers as integers. Setting a
    column to "auto" reverts it to the inferred type. Overrides persist and
    are applied every time the file is loaded.
- **Column profiles** — per-column statistics: missing rate, unique rate,
  invalid values, top category values. Select a column to see its chart and
  statistics in detail.
- **Profile score** — the automatic intrinsic score and its component
  breakdown (completeness, uniqueness, type consistency, duplicate rate).
  This score does not require any rules and is always available. For a
  rules-based score see the Data Quality page.

### Tab: Register / upload

Upload a new CSV or Excel file.

1. Drag or click to select a file. Supported formats: `.csv`, `.tsv`, `.txt`
   (all treated as CSV with auto-detected delimiter), `.xlsx`.
2. For Excel files with multiple sheets, a sheet selector appears after the
   file is parsed.
3. A preview and inferred column types are shown. Check them before registering.
4. Warnings about encoding or format issues appear in orange.
5. Choose **Register a new dataset** (creates a brand-new entry) or
   **Upload a new version of the active dataset** (adds a version to an
   existing entry).
6. Fill in the name, description, owner and project (all optional except the
   name for new datasets).
7. Check **This is synthetic / demonstration data** if the file is not real
   data — this label appears on all reports.
8. Click the register button.

**File limits:** 50 MB maximum per file (configurable with `DS_MAX_UPLOAD_MB`),
1,000,000 rows maximum (configurable with `DS_MAX_ROWS`). Files above 500,000
rows are parsed in chunks; a "sampled" warning appears in the results wherever
only a subset was analysed.

**Identical content is rejected** — uploading the same file twice returns a
409 Conflict, not a duplicate.

### Tab: Demo data

Loads three built-in synthetic datasets designed to show every feature.

**Step 1 — Load Retail Transactions v1 + Customer Master**

Registers two datasets:
- **Retail Transactions** — 5,000 synthetic transactions with columns for
  customer ID, product, quantity, price, discount, revenue, region and date.
- **Customer Master** — 800 synthetic customer records for referential
  integrity checks.

**Step 2 — Configure demo rules + baseline**

Does three things automatically:
- Learns 34 validation rules from the clean v1 data (completeness thresholds,
  data types, allowed categories, unique key checks, non-negativity).
- Adds two hand-written rules: a revenue formula check
  (`revenue = quantity × unit_price × (1 − discount/100), within 0.01`)
  and a customer-exists referential integrity check.
- Approves v1 as the active baseline so drift and root-cause analysis work
  immediately.

**Step 3 — Add Retail Transactions v2**

Adds a deliberately broken version with nine injected issues including a
removed column, a type-changed column, new categories, duplicate keys,
negative revenues, missing values and referential integrity failures. Run
a monitoring check or assessment after this step to see all the features in
action.

**Resetting:** Run `python scripts\reset_demo.py --yes` from the repository
root to wipe the database, recreate all sample data and re-seed rules and the
baseline. Add `--with-v2` to also add the broken version automatically.

---

## 6. Data Quality page

**Purpose:** Run your configured validation rules against any dataset version
and see a transparent, dimension-based quality score.

### Selecting versions

Use the **Current version** and **Reference version** dropdowns at the top.
The reference version is used for `schema_match` checks (comparing the schema
of the current version against the reference). Leave it as "None" if you only
want to run checks that do not require a baseline.

### Tab: Validation results

Click **Run validation** to execute all enabled rules.

**Outcome filter** — show only failures, warnings, or all results.
**Severity filter** — filter by severity level.
**Search box** — filter by rule name or column name.

Each result row shows:
- **Outcome** — `fail` (hard threshold exceeded), `warn` (soft threshold
  exceeded), `pass`, or `skipped` (check could not run — see the reason).
- **Severity** — from the rule definition (info, low, medium, high, critical).
- **Rule** — the rule name.
- **Type** — the rule type (missing_values, numeric_range, etc.).
- **Columns** — which columns are involved.
- **Affected %** — percentage of rows that failed the check.
- **Message** — a plain-language description of what was found.

Clicking a result in the **Inspect evidence** dropdown shows:
- **Expected** — what the rule required.
- **Observed** — what was actually found (counts, percentages, example values).
- **Evidence** — detailed breakdown including example row numbers, example
  values, and related findings.

**Skipped checks** are listed in an expander at the bottom. A check is
skipped — never silently counted as passing — when the column it applies to
is absent, when the reference dataset for a referential integrity check is not
registered, or when the rule definition itself is invalid.

**Downloads:** JSON (full results) or CSV (issues only).

### Tab: Quality score

Click **Calculate score** to compute the configurable quality score.

**Weights** (0–100, at least one must be positive):
- Completeness — `missing_values` rules.
- Validity — `data_type`, `numeric_range`, `allowed_values`, `date_range`,
  `business_rule`, `constant_column` rules.
- Uniqueness — `unique`, `duplicate_rows` rules.
- Consistency — `schema_match`, `required_columns`, `unexpected_columns` rules.
- Accuracy — `referential_integrity` rules only. If no such rule exists,
  this dimension shows "not evaluated" — DataSentinel never claims to measure
  accuracy without trusted reference data.

**Minimum thresholds** — set the minimum acceptable score for each dimension
and for the overall score. A dimension below its minimum is flagged as a
threshold violation.

**Results:**
- **Gauge** — overall score with the minimum threshold marked as a line.
- **Dimension bars** — score per dimension with threshold indicators.
- **Radar** — pentagon view for quick shape comparison across dimensions.
- **Score breakdown** — every rule that contributed to each dimension, with
  its ratio and the weight it carried.
- **Methodology note** — explains exactly how the score was calculated.

**Compare versions:** pick two version numbers and click **Compare two
versions** to see a per-dimension delta table and a chart showing which
dimensions improved or regressed.

**Score every version:** scores all versions and shows a trend chart.

---

## 7. Drift Monitoring page

**Purpose:** Detect changes in the statistical distribution of features
between two dataset versions.

### How to use it

1. Choose a **Current version** (the new data) and a **Reference version**
   (the baseline). You must pick a reference that is different from the
   current version.
2. Adjust thresholds and options if needed (see below).
3. Click **Detect drift**.

### Thresholds and options

All thresholds are configurable. A feature is flagged when an **effect size**
exceeds its warn threshold — p-values are reported alongside but **never
decide the verdict**. (With large samples, a trivially small shift becomes
"statistically significant" but is operationally irrelevant.)

| Threshold | What it controls | Default |
|-----------|-----------------|---------|
| PSI warn / high | Population Stability Index thresholds for numeric features | 0.10 / 0.25 |
| Wasserstein/IQR warn / high | Wasserstein distance (normalised by reference IQR) | 0.10 / 0.30 |
| JS distance warn / high | Jensen–Shannon distance for categorical features | 0.10 / 0.20 |
| New categories % warn / high | Share of rows with unseen category values | 1% / 5% |
| Missingness change warn / high | Change in missing-value rate (percentage points) | 5 pp / 20 pp |
| PSI bins | Number of histogram bins (built from reference quantiles) | 10 |
| Exclude columns | Columns to skip entirely | datetime columns |

**Exclude datetime columns** from drift by default — they always differ
between time periods and would produce false drift alerts.

### Reading the results

**Dataset-level status:** `drifted` (any high-severity drifted feature),
`warn` (any drifted feature), `stable` (none).

**Effect size chart:** all features sorted by how far their effect size
exceeds the warn threshold. Colour indicates severity.

**Feature table:** one row per evaluated feature with status, severity,
reference N, current N, primary effect size and explanation.

**Feature detail:** select a feature to see its distribution chart:
- Numeric features: PSI bin comparison (reference % vs current %).
- Categorical features: category share comparison.

**Method table:** PSI and Wasserstein decide the verdict for numeric features.
KS statistic and p-value (Benjamini-Hochberg corrected) are reported as
context — they help you interpret significance on large samples but never
alone trigger a "drifted" label.

**Missingness changes:** listed separately from distribution drift. A column
whose missing rate rose from 1% to 18% is a data problem, not a distribution
shift.

**Skipped columns:** listed with reasons. Identifier-like columns, constant
columns and excluded columns are always skipped.

---

## 8. Schema Comparison page

**Purpose:** Compare the column structure of two dataset versions to find
breaking and non-breaking schema changes.

### How to use it

1. Choose a **Reference version** (the approved schema) and a **Current
   version** (what you received today).
2. Results appear immediately.

### Side-by-side comparison table

Every column is shown with its status:
- **unchanged** — same type, same nullability, same key pattern.
- **changed** — at least one structural property differs.
- **added** — column exists in current but not in reference.
- **removed** — column exists in reference but not in current.

Use **Show unchanged columns** to toggle unchanged columns on or off.

### Changes list

Each change is shown with:
- **Change type** — `added_column`, `removed_column`, `type_changed`,
  `storage_type_changed`, `nullability_changed`, `missingness_changed`,
  `new_categories`, `vanished_categories`, `identifier_uniqueness_lost`,
  `became_constant`, `column_reordered`, `rename_candidate`.
- **Severity** — info, low, medium, high or critical.
- **Breaking** — whether the change would break a typed query, JOIN or model
  that reads this column.
- **Certain** — `false` on rename candidates (based on name similarity and
  matching type; never confirmed automatically) and on category changes when
  the stored category list was truncated.
- **Baseline / Current** — what the old and new values look like.
- **Downstream risk** — which types of consumers are likely to break.

**Rename candidates** are reported separately. DataSentinel shows them as
possibilities ("may have been renamed") but never merges them automatically.
You confirm whether it is a rename or an unrelated pair.

---

## 9. Root Cause Analysis page

**Purpose:** For each issue found, see the evidence, a ranked list of
hypotheses about why it happened, concrete investigation steps, and corrective
actions.

### How to use it

1. Choose a **Current version** and a **Reference version**.
2. Optionally uncheck **Include statistical drift evidence** to run on quality
   and schema only (faster on large datasets).
3. Click **Analyse root causes**.

### Reading findings

Each finding has four tabs:

**Evidence:** what DataSentinel can actually see in the data.
- Current run — counts, percentages, example values, example row numbers.
- Baseline — what the same metric was in the reference version.
- Related schema changes — schema events on the same column.
- Related drift — drift results for the same column.

**Possible causes (hypotheses):** DataSentinel is explicit that these are
hypotheses, not confirmed facts. Each has:
- A **likelihood** label — `likely`, `possible`, or `unlikely`.
- **Supporting evidence** — the specific observation that supports this
  hypothesis.
- **Contradicting evidence** — any observations that argue against it.

Only hypotheses with at least one piece of supporting evidence are shown.
If no pattern matches the data, the finding says so rather than inventing a
cause.

**How to investigate:** 3–6 concrete, ordered steps. These tell you exactly
which systems to check and in which order.

**Corrective actions:** what to do once the cause is confirmed.

### Downloading the report

- **Markdown** — formatted text suitable for a Jira ticket, Confluence page
  or email.
- **JSON** — structured data for integrating into other tools or pipelines.

---

## 10. Incident Center page

**Purpose:** Track all quality and drift issues over time with a full status
workflow.

### What is an incident?

An incident is created automatically when a monitoring run finds a failing or
warning check. Two runs that find the same issue on the same column produce
**one incident**, not two — DataSentinel deduplicates by a fingerprint
(`dataset + rule name + sorted columns`). The second run updates the incident's
`occurrence_count` instead of creating a duplicate.

### Filters

- **Status** — open (default), acknowledged, resolved, ignored.
- **Severity** — critical, high, medium, low, info.
- **Type** — quality, schema, drift.
- **Search** — filter by title or column name.

### KPI cards

- **Critical/High** — the count that needs immediate attention.
- **Open / Acknowledged / Resolved** — status distribution.
- **Severity donut** — visual breakdown.

### Incident detail tabs

Select an incident from the dropdown to open its detail.

**Evidence:** what was observed, what was expected, the raw evidence (counts,
example values), and the severity score breakdown. The severity score (0–100)
is a documented heuristic — rule severity contributes 0-20 points, affected
percentage 0-40, incident type 0-20, downstream asset count 0-20 and
recurrence count 0-10. It is never presented as a probability.

**Recommendations:** an ordered list of concrete actions (most impactful
first). When downstream assets are affected, "pause outputs from X" is
prepended.

**Downstream impact:** which lineage assets are affected by this column's
issue. Empty if no lineage has been configured — go to the Data Lineage page
to add it.

**Status history:** a full audit trail of every status change, who made it,
when, and what note they left.

**Update status:** change the status with a note and your name. Status
transitions:

```
open → acknowledged  (you are investigating)
open → resolved      (it is fixed)
open → ignored       (permanent suppression)
acknowledged → resolved / open / ignored
resolved → open      (condition recurred; recurrence_count increments)
ignored → open       (un-suppress)
```

---

## 11. Data Lineage page

**Purpose:** Define which downstream reports, metrics and model features
depend on which dataset columns, so that an incident on a source column
can be linked to the assets it will break.

### Tab: Graph

An interactive node graph. Nodes are assets (datasets, transformations,
metrics, reports, ML features). Edges are directed dependencies (A → B means
B depends on A).

- **Highlight asset** — picks an asset by name and highlights it orange.
- Click a node to see its name, type, description and columns.

Edge colours:
- **Teal** — `known` dependency (you defined it explicitly).
- **Grey** — `inferred` dependency (marked as uncertain).

### Tab: Impact analysis

Select columns that have a quality or drift issue and click **Find affected
assets** to trace all downstream assets reachable from those columns.

Example: if `revenue` has a negative-value incident, the impact analysis
shows Revenue KPI → Executive Dashboard — so you know to pause or flag
those outputs until the issue is resolved.

Results also appear on each incident's **Downstream impact** tab once lineage
is configured.

### Tab: Edit lineage

**Add an asset:**
- Name (must be unique across all assets).
- Type — `dataset`, `transformation`, `metric`, `report`, `ml_feature`.
- Description (optional).
- Link to the active dataset (ticks it as the source for impact analysis).
- Columns (comma-separated; used to filter impact by column).

**Add an edge:**
- Source (upstream) and target (downstream) asset.
- Dependency kind — `known` or `inferred`.

**Remove an asset or edge:** select from the dropdowns and click delete.

DataSentinel enforces acyclicity — any edge that would create a cycle is
rejected with a clear error message.

### Demo lineage

The demo reset seeds a pre-built lineage graph:
- Retail Transactions → Daily Revenue Aggregation
- Daily Revenue Aggregation → Monthly Revenue KPI
- Monthly Revenue KPI → Executive Dashboard
- Retail Transactions → Customer Churn Features
- Customer Churn Features → Churn Model
- Customer Churn Features → Churn Alert Report
- Customer Master → Customer Churn Features

---

## 12. Rules & Baselines page

**Purpose:** Configure what "healthy" means for your dataset (validation
rules) and approve a reference version (baseline).

### Tab: Baselines

A baseline is the approved, clean version of a dataset. It is used as the
reference for:
- Schema comparison (is the structure the same?).
- Drift detection (has the distribution changed?).
- Root-cause analysis (what was the baseline value?).

**Setting a baseline:**
1. Choose a version from the dropdown.
2. Write a reason (mandatory — it is stored in the audit trail).
3. Optionally fill in "Approved by".
4. If a baseline already exists, check **I confirm replacing the current
   baseline** to proceed.

The previous baseline is never deleted — it stays as an inactive audit row
in the history table with a `deactivated_at` timestamp.

### Tab: Validation rules

Rules define what DataSentinel should check every time it validates the dataset.

**Suggest rules from a version:** pick the clean version (usually v1 or
your last known-good version) and click **Preview suggestions** to see what
DataSentinel would create. Click **Save as rules** to save them. Rules with
names that already exist for this dataset are skipped — your edits are
preserved.

Suggested rules include:
- Completeness thresholds per column (warn at baseline missing % + 2 pp,
  fail at + 10 pp).
- Data-type rules for numeric, datetime and boolean columns.
- Uniqueness for identifier-like columns.
- Non-negativity for numeric columns with no negative baseline values.
- Allowed-values lists for categorical columns with up to 30 fully listed
  categories.
- Schema match against the baseline.
- Duplicate-rows limit.

**Edit or delete a rule:** select it from the dropdown, change the severity,
enabled flag or parameters (JSON), then click Save changes or Delete rule.

**Create a rule:** choose a rule type, column, severity and scope:
- **This dataset** — applies only to this dataset.
- **All datasets (global)** — applies to every dataset.

Rule types and their required parameters are described in `docs/RULES.md`.
The most useful ones for getting started:

| Rule type | What it checks | Key parameter |
|-----------|----------------|---------------|
| `missing_values` | Missing-value rate | `max_missing_pct: 5` |
| `duplicate_rows` | Duplicate rows | `max_duplicate_pct: 0` |
| `unique` | No duplicate values in a column | *(none)* |
| `data_type` | Column parses as expected type | `expected_type: "integer"` |
| `numeric_range` | Values within min/max | `min: 0` or `max: 1000` |
| `allowed_values` | Values from a known set | `values: ["A","B","C"]` |
| `business_rule` | Custom expression | `expression: "qty > 0"` |
| `referential_integrity` | Every value exists in a reference | `reference_dataset: "Customer Master"` |

### Tab: Alerting

Configure a webhook URL to receive a notification when a monitoring run
fails or creates incidents.

**Supported webhook formats (auto-detected):**
- **Slack** (`hooks.slack.com`) — posts a formatted Slack message with blocks.
- **Microsoft Teams** (`webhook.office.com`) — posts an Adaptive Card.
- **Generic** — posts a JSON object to any endpoint.

Click **Send test alert** to send a clearly-labelled test payload and verify
delivery before the first real run.

### Tab: Scheduler

Configure DataSentinel to monitor a dataset automatically at regular intervals
without clicking Run manually.

1. Set **Run every N hours** (0 = disabled).
2. Optionally paste a webhook URL to alert on failures.
3. Optionally list columns to exclude from drift (datetime columns
   recommended).
4. Click **Save schedule**.

The schedule survives a backend restart. The next scheduled run time is shown
in the status callout.

---

## 13. Sidebar controls

The sidebar is visible on every page.

**Active dataset** — the dataset all pages use by default. Changing it here
updates every page.

**Synthetic demo data badge** — appears when the active dataset is marked as
synthetic. All report exports include a "SYNTHETIC" label on synthetic data.

**Connection → API URL** — the URL of the backend API. Default:
`http://127.0.0.1:8000/api`. Change this to connect to a remote backend.
The status badge (green OK / red FAILING) shows whether the API is reachable.

---

## 14. API and connections

All analysis that DataSentinel performs is available directly as a REST API.
Open **http://localhost:8000/docs** to browse every endpoint with interactive
examples.

Key endpoints for integration:

| Method | Path | What it does |
|--------|------|-------------|
| POST | `/api/datasets` | Upload a new dataset |
| POST | `/api/datasets/{id}/versions` | Upload a new version |
| POST | `/api/datasets/{id}/monitor` | Run the full monitoring pipeline (synchronous) |
| POST | `/api/v1/datasets/{id}/jobs/monitor` | Submit as a background job (returns job_id) |
| GET | `/api/v1/jobs/{job_id}` | Poll a background job |
| GET | `/api/datasets/{id}/runs/history` | Run history summary |
| GET | `/api/incidents` | All incidents (filterable) |
| PATCH | `/api/incidents/{id}` | Update incident status |
| GET | `/api/runs/{run_id}/report?format=html` | Download a report |
| GET | `/api/metrics` | Prometheus metrics |
| GET | `/api/v1/health` | Health check |

**`/api` and `/api/v1` are identical** — the v1 prefix exists so you can
pin client code to a specific contract version while the unversioned prefix
keeps working for humans and tools that do not care about versioning.

**Database:** SQLite by default (`data/datasentinel.db`). For production use
PostgreSQL by setting `DS_DATABASE_URL=postgresql+psycopg://user:pass@host/db`.
Alembic migrations run automatically at startup (`alembic upgrade head`).

**Large files:** files above 500,000 rows are read in 500,000-row chunks.
Analysis that runs on a sample shows a "sampled: true" flag and a warning
stating how many rows were used.

**Parquet cache:** each version's CSV or Excel file is converted to Parquet
after the first load and served from cache on every subsequent request.
This makes drift and quality checks on large files much faster.

---

## 15. Troubleshooting

**"Cannot reach the DataSentinel API"**
The backend is not running. Start it with `uvicorn app.asgi:app --port 8000`
from the `backend/` directory.

**"No datasets yet" on the Overview page**
Go to **Datasets → Demo data** and click the first button, or upload a CSV
on **Datasets → Register / upload**.

**"No baseline set" on the Overview page**
A baseline is required for schema diff, drift, and root-cause analysis.
Go to **Rules & Baselines → Baselines**, pick version 1, write a reason,
and click Set baseline.

**Run assessment button is greyed out**
Current and reference versions must be different. Pick different version
numbers or use different datasets in the two dropdowns.

**Drift shows 0 features evaluated**
All columns were skipped. Check the **Skipped columns** expander —
the most common reason is that every column is identifier-like (high
unique percentage) or constant.

**A validation check is "skipped"**
- `referential_integrity`: the reference dataset named in the rule is not
  registered. Register it first on the Datasets page.
- `schema_match`: no baseline is set. Set one on Rules & Baselines.
- `column not found`: the column named in the rule does not exist in this
  version. Check the column name for typos.

**Quality score shows "accuracy: not_evaluated"**
Accuracy requires at least one `referential_integrity` rule. Add one on the
Rules & Baselines page (or click Suggest rules and save).

**Background job stays in "queued" state**
Background jobs run in the FastAPI thread pool. They execute when the current
request has returned. If the server is very busy or the dataset is very large,
they may queue briefly. Refresh after a few seconds.

**Lineage graph is empty**
Add assets and edges on the **Data Lineage → Edit lineage** tab. If you are
using the demo data, reset with `python scripts\reset_demo.py --yes` — this
seeds the demo lineage graph automatically.

**Alert test failed**
- Check that the webhook URL is correct and reachable from the machine
  running the backend.
- For Slack, the URL must start with `https://hooks.slack.com/services/`.
- For Teams, the URL must contain `webhook.office.com` or `outlook.office.com`.

**File rejected as "possible zip bomb"**
The Excel file's uncompressed content exceeds 500 MB. This limit exists to
prevent denial-of-service attacks. If the file is genuinely that large,
export it as CSV and upload that instead.
