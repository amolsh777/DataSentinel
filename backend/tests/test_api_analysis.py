from __future__ import annotations

import pytest


def _upload(client, name, path):
    r = client.post("/api/datasets", data={"name": name, "is_synthetic": "true"},
                    files={"file": (path.name, path.read_bytes(), "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _new_version(client, dataset_id, path):
    r = client.post(f"/api/datasets/{dataset_id}/versions",
                    files={"file": (path.name, path.read_bytes(), "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()["version_number"]


@pytest.fixture
def retail_with_versions(client, sample_dir):
    ds_id = _upload(client, "Retail Transactions", sample_dir / "retail_transactions_v1.csv")
    _upload(client, "Customer Master", sample_dir / "customers_clean.csv")
    # Suggest and save rules from the CLEAN v1 before uploading v2, so bounds and
    # allowed-value lists describe the known-good baseline, not the broken file.
    client.post(f"/api/datasets/{ds_id}/rules/suggest", params={"save": "true", "version": "1"})
    _new_version(client, ds_id, sample_dir / "retail_transactions_v2.csv")
    client.post("/api/rules", json={
        "dataset_id": ds_id, "name": "Customer exists", "rule_type": "referential_integrity",
        "column": "customer_id", "severity": "high",
        "params": {"reference_dataset": "Customer Master", "reference_column": "customer_id"}})
    return ds_id


# ---------------------------------------------------------------- baselines


def test_set_baseline_history_and_confirmation(client, retail_with_versions, sample_dir):
    ds_id = retail_with_versions
    r = client.post(f"/api/datasets/{ds_id}/baseline",
                    json={"version_number": 1, "reason": "clean v1", "set_by": "demo"})
    assert r.status_code == 201 and r.json()["baseline"]["is_active"] is True
    assert r.json()["replaced_version_number"] is None

    # Setting the same version again is a no-op, not an error.
    same = client.post(f"/api/datasets/{ds_id}/baseline",
                       json={"version_number": 1, "reason": "retry"})
    assert same.status_code == 201 and "unchanged" in same.json()["note"].lower()

    # Replacing without confirm is refused.
    refused = client.post(f"/api/datasets/{ds_id}/baseline",
                          json={"version_number": 2, "reason": "move to v2"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "BASELINE_CONFIRMATION_REQUIRED"

    # Replacing with confirm succeeds and deactivates the previous baseline.
    replaced = client.post(f"/api/datasets/{ds_id}/baseline",
                           json={"version_number": 2, "reason": "promote v2", "confirm": True})
    assert replaced.status_code == 201 and replaced.json()["replaced_version_number"] == 1

    hist = client.get(f"/api/datasets/{ds_id}/baselines").json()
    assert len(hist) == 2 and hist[0]["version_number"] == 2 and hist[0]["is_active"]
    assert hist[1]["version_number"] == 1 and not hist[1]["is_active"]
    assert hist[1]["deactivated_at"] is not None


def test_set_baseline_requires_reason(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/baseline",
                    json={"version_number": 1, "reason": "   "})
    assert r.status_code == 422


def test_set_baseline_unknown_version(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/baseline",
                    json={"version_number": 99, "reason": "no"})
    assert r.status_code == 404


# ---------------------------------------------------------------------- drift


def test_drift_requires_a_reference(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/drift", json={"current_version": 2})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REFERENCE_REQUIRED"


def test_drift_uses_active_baseline_by_default(client, retail_with_versions):
    client.post(f"/api/datasets/{retail_with_versions}/baseline",
                json={"version_number": 1, "reason": "demo"})
    r = client.post(f"/api/datasets/{retail_with_versions}/drift",
                    json={"current_version": 2, "exclude_columns": ["transaction_date"]})
    assert r.status_code == 200
    body = r.json()
    assert body["reference_source"]["source"] == "active_baseline"
    assert body["summary"]["status"] == "drifted"
    drifted = {f["name"] for f in body["features"] if f["status"] == "drifted"}
    assert {"unit_price", "revenue", "customer_age", "product_category", "region"} <= drifted


def test_drift_rejects_same_reference_and_current(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/drift",
                    json={"current_version": 1, "reference_version": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "REFERENCE_EQUALS_CURRENT"


def test_drift_custom_thresholds_change_verdict(client, retail_with_versions):
    strict = client.post(f"/api/datasets/{retail_with_versions}/drift", json={
        "current_version": 2, "reference_version": 1,
        "exclude_columns": ["transaction_date"],
        "thresholds": {"psi": {"warn": 0.0001, "high": 0.001}},
    }).json()
    assert strict["summary"]["features_drifted"] >= 5


# ---------------------------------------------------------------------- score


def test_score_breakdown_and_dimensions(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/score",
                    json={"current_version": 1, "reference_version": 1})
    assert r.status_code == 200
    s = r.json()["score"]
    assert s["score"] == 100.0 and s["status"] == "healthy"
    for d in ("completeness", "validity", "uniqueness", "consistency", "accuracy"):
        assert s["dimensions"][d]["score"] == 100.0


def test_score_v2_lower_than_v1(client, retail_with_versions):
    s1 = client.post(f"/api/datasets/{retail_with_versions}/score",
                     json={"current_version": 1, "reference_version": 1}).json()["score"]
    s2 = client.post(f"/api/datasets/{retail_with_versions}/score",
                     json={"current_version": 2, "reference_version": 1}).json()["score"]
    assert s2["score"] < s1["score"]
    assert s2["weakest_dimension"]["name"] in s2["dimensions"]


def test_score_strict_threshold_fails(client, retail_with_versions):
    body = client.post(f"/api/datasets/{retail_with_versions}/score", json={
        "current_version": 2, "reference_version": 1,
        "thresholds": {"overall_min": 99.0, "dimension_min": {"uniqueness": 99.0}},
    }).json()["score"]
    assert body["status"] == "failing" and body["threshold_violations"]


def test_score_rejects_bad_weights(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/score",
                    json={"current_version": 1, "weights": {"completeness": -5}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_SCORE_CONFIG"


def test_score_compare_endpoint(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/score/compare",
                    json={"previous_version": 1, "current_version": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["comparison"]["delta"] < 0
    assert body["comparison"]["regressed_dimensions"]


# ------------------------------------------------------------------------ RCA


def test_rca_combines_quality_schema_and_drift(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/root-cause", json={
        "current_version": 2, "reference_version": 1,
        "thresholds": {"psi": {"warn": 0.01, "high": 0.1}},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total_findings"] >= 5
    assert body["summary"]["by_type"]["quality"] >= 3
    assert body["summary"]["by_type"]["schema"] >= 2
    assert "disclaimer" in body
    for f in body["findings"]:
        for h in f["hypotheses"]:
            assert h["likelihood"] in ("likely", "possible", "unlikely")
            assert h["supporting_evidence"]
        assert f["investigation_steps"] and f["corrective_actions"]
    # Negative-revenue finding should carry drift evidence inside it, not duplicate.
    revenue = next(f for f in body["findings"] if "revenue" in f["columns"] and f["issue_type"] == "quality")
    assert revenue["evidence"]["related_drift"] is not None


def test_rca_without_drift_still_returns_schema_and_quality_findings(client, retail_with_versions):
    r = client.post(f"/api/datasets/{retail_with_versions}/root-cause", json={
        "current_version": 2, "reference_version": 1, "include_drift": False,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["drift_summary"] is None
    assert body["summary"]["by_type"]["drift"] == 0
    assert body["summary"]["total_findings"] >= 5


def test_rca_without_reference_runs_on_quality_only(client, retail_with_versions):
    """RCA still works without a reference — it just can't do schema diff or drift."""
    r = client.post(f"/api/datasets/{retail_with_versions}/root-cause",
                    json={"current_version": 2, "use_active_baseline": False})
    assert r.status_code == 200
    body = r.json()
    assert body["reference_version_number"] is None
    assert body["schema_summary"] is None and body["drift_summary"] is None
    assert body["summary"]["by_type"]["schema"] == 0 and body["summary"]["by_type"]["drift"] == 0
    assert body["summary"]["total_findings"] >= 1  # quality findings still present
