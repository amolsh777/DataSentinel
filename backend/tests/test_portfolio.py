"""The demo portfolio generators are deterministic, v1 is clean, later versions are measurably worse."""

from __future__ import annotations

import pandas as pd

from app.analytics.profiling import profile_dataframe
from app.analytics.quality_checks import run_quality_checks, validate_rule
from app.analytics.quality_score import compute_quality_score
from app.demo import portfolio


def test_ten_unique_datasets_and_valid_rules():
    assert len(portfolio.SPECS) == 10
    assert len({s.slug for s in portfolio.SPECS}) == 10 and len({s.name for s in portfolio.SPECS}) == 10
    for s in portfolio.SPECS:
        for r in s.rules:
            validate_rule(r)  # raises if a demo rule is malformed
        assert s.history in portfolio.HISTORY


def test_generation_is_deterministic():
    spec = portfolio.SPECS[0]
    a = portfolio.generate_version(spec, 3, scale=0.02)
    b = portfolio.generate_version(spec, 3, scale=0.02)
    pd.testing.assert_frame_equal(a, b)


def test_reference_is_clean_and_degraded_versions_score_lower():
    for spec in portfolio.SPECS[:4]:
        scores = {}
        for v in (1, 4):
            df = portfolio.generate_version(spec, v, scale=0.03)
            res = run_quality_checks(df, [validate_rule(r) for r in spec.rules])
            scores[v] = compute_quality_score(res)["score"]
        assert scores[1] == 100.0 and scores[4] < scores[1], (spec.name, scores)


def test_severe_version_changes_schema():
    spec = portfolio.SPECS[0]
    cols1 = set(portfolio.generate_version(spec, 1, scale=0.02).columns)
    cols4 = set(portfolio.generate_version(spec, 4, scale=0.02).columns)
    assert "legacy_flag" in cols4 and cols1 != cols4


def test_profile_runs_on_every_version():
    spec = portfolio.SPECS[5]
    for v in portfolio.VERSION_PLAN:
        assert profile_dataframe(portfolio.generate_version(spec, v, scale=0.02))["dataset"]["row_count"] > 100
