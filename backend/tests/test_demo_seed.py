from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.demo.seed import reset_demo
from app.main import create_app


def test_reset_demo_seeds_rules_baseline_and_enables_analysis(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'demo.db').as_posix()}",
        storage_dir=tmp_path / "storage",
    )
    result = reset_demo(settings, with_v2=True, sample_dir=tmp_path / "sample")
    assert result["rules"] == 36 and result["baseline_version"] == 1

    with TestClient(create_app(settings)) as c:
        ds_id = c.get("/api/datasets", params={"search": "Retail"}).json()["items"][0]["id"]
        baselines = c.get(f"/api/datasets/{ds_id}/baselines").json()
        assert len(baselines) == 1 and baselines[0]["is_active"] and baselines[0]["version_number"] == 1

        # Drift and RCA work immediately, using the seeded baseline.
        drift = c.post(f"/api/datasets/{ds_id}/drift",
                       json={"current_version": 2, "exclude_columns": ["transaction_date"]}).json()
        assert drift["reference_source"]["source"] == "active_baseline"
        assert drift["summary"]["features_drifted"] >= 5

        rca = c.post(f"/api/datasets/{ds_id}/root-cause", json={"current_version": 2}).json()
        assert rca["summary"]["total_findings"] >= 8
        assert rca["summary"]["by_type"]["schema"] >= 2 and rca["summary"]["by_type"]["quality"] >= 3
