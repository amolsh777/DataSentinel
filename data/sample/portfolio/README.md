# Demo portfolio (SYNTHETIC)

10 datasets x 5 versions, generated deterministically by `backend/app/demo/portfolio.py` (shipped here at 25% size;
`python scripts/seed_portfolio.py --files-only --scale 1` writes the full ~3.4M-row set).

| Version | File label | Meaning |
|---|---|---|
| v1 | reference_clean | clean reference - approve as baseline |
| v2 | mild_drift | small shifts, a few nulls/outliers |
| v3 | degraded | clear quality problems and drift |
| v4 | severe_incident | heavy problems + schema change (a column dropped, `legacy_flag` added) |
| v5 | recovering | partly fixed |

Upload any two versions of one dataset: one as the reference/baseline, one as current, then run an assessment.
`python scripts/seed_portfolio.py --yes` also creates rules, baselines, lineage and ~30 days of runs and incidents.
