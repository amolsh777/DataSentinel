from __future__ import annotations

import pytest

from tests.conftest import csv_bytes


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
def retail(client, sample_dir):
    ds_id = _upload(client, "Retail Transactions", sample_dir / "retail_transactions_v1.csv")
    _upload(client, "Customer Master", sample_dir / "customers_clean.csv")
    return ds_id


def test_end_to_end_suggest_validate_and_diff(client, retail, sample_dir):
    r = client.post(f"/api/datasets/{retail}/rules/suggest", params={"save": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] and len(body["saved_rules"]) == len(body["suggestions"]) > 20

    # Saving again must not duplicate rules.
    again = client.post(f"/api/datasets/{retail}/rules/suggest", params={"save": "true"}).json()
    assert again["saved_rules"] == []

    r = client.post("/api/rules", json={
        "dataset_id": retail, "name": "Customer exists", "rule_type": "referential_integrity",
        "column": "customer_id", "severity": "high",
        "params": {"reference_dataset": "Customer Master", "reference_column": "customer_id"}})
    assert r.status_code == 201, r.text

    v1 = client.post(f"/api/datasets/{retail}/versions/1/validate", params={"reference_version": 1}).json()
    assert v1["summary"]["status"] == "healthy", v1["summary"]["top_issues"]
    assert v1["is_synthetic"] is True

    _new_version(client, retail, sample_dir / "retail_transactions_v2.csv")
    v2 = client.post(f"/api/datasets/{retail}/versions/2/validate", params={"reference_version": 1}).json()
    assert v2["summary"]["status"] == "failing"
    failed = {(x["rule_type"], tuple(x["columns"])) for x in v2["results"] if x["outcome"] == "fail"}
    assert ("referential_integrity", ("customer_id",)) in failed
    assert ("numeric_range", ("revenue",)) in failed
    assert ("required_columns", ("discount_pct",)) in failed

    diff = client.get(f"/api/datasets/{retail}/schema-diff", params={"reference": 1, "current": 2}).json()
    assert diff["compatible"] is False and diff["summary"]["removed"] == 1
    assert {c["status"] for c in diff["columns"]} >= {"added", "removed", "changed", "unchanged"}
    # latest is used when current is omitted
    assert client.get(f"/api/datasets/{retail}/schema-diff", params={"reference": 1}).json()["current_version_number"] == 2


def test_validate_without_rules_or_reference_explains_itself(client, retail):
    body = client.post(f"/api/datasets/{retail}/versions/1/validate").json()
    assert body["rules_evaluated"] == 0 and body["summary"]["status"] == "healthy"
    assert "No rules are configured" in body["note"]


def test_rule_crud(client, retail):
    r = client.post("/api/rules", json={"dataset_id": retail, "rule_type": "numeric_range",
                                        "column": "unit_price", "params": {"min": 0}})
    assert r.status_code == 201
    rule = r.json()
    assert rule["name"] == "numeric_range:unit_price" and rule["severity"] == "medium"

    r = client.patch(f"/api/rules/{rule['id']}", json={"params": {"min": 0, "max": 99999}, "severity": "high"})
    assert r.status_code == 200 and r.json()["params"]["max"] == 99999 and r.json()["severity"] == "high"

    r = client.patch(f"/api/rules/{rule['id']}", json={"params": {"min": 10, "max": 1}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_RULE"

    listed = client.get("/api/rules", params={"dataset_id": retail}).json()
    assert [x["id"] for x in listed] == [rule["id"]]

    assert client.delete(f"/api/rules/{rule['id']}").status_code == 204
    assert client.get(f"/api/rules/{rule['id']}").status_code == 404


def test_global_rules_apply_to_every_dataset(client, retail):
    client.post("/api/rules", json={"rule_type": "duplicate_rows", "params": {"max_duplicate_pct": 0}})
    assert len(client.get("/api/rules", params={"dataset_id": retail}).json()) == 1
    assert client.get("/api/rules", params={"dataset_id": retail, "include_global": "false"}).json() == []
    res = client.post(f"/api/datasets/{retail}/versions/1/validate").json()
    assert res["rules_evaluated"] == 1 and res["results"][0]["outcome"] == "pass"


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"rule_type": "unique"}, "INVALID_RULE"),
        ({"rule_type": "business_rule", "params": {"expression": "__import__('os').system('x')"}}, "INVALID_RULE"),
        ({"rule_type": "not_a_rule"}, "REQUEST_VALIDATION_ERROR"),
        ({"rule_type": "unique", "column": "a", "severity": "urgent"}, "REQUEST_VALIDATION_ERROR"),
    ],
)
def test_invalid_rule_payloads(client, payload, code):
    r = client.post("/api/rules", json=payload)
    assert r.status_code == 422 and r.json()["error"]["code"] == code


def test_rule_for_unknown_dataset_is_404(client):
    r = client.post("/api/rules", json={"dataset_id": "nope", "rule_type": "duplicate_rows"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_unknown_versions_are_404(client, small_df):
    ds_id = client.post("/api/datasets", data={"name": "Small"},
                        files={"file": ("s.csv", csv_bytes(small_df), "text/csv")}).json()["id"]
    assert client.get(f"/api/datasets/{ds_id}/schema-diff", params={"reference": 9}).status_code == 404
    assert client.post(f"/api/datasets/{ds_id}/versions/3/validate").status_code == 404
    assert client.post("/api/datasets/nope/rules/suggest").status_code == 404
