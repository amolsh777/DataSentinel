from __future__ import annotations

import pandas as pd

from tests.conftest import csv_bytes, xlsx_bytes


def _create(client, name="Orders", content=None, filename="orders.csv", **form):
    content = content if content is not None else b"id,amount\n1,10\n2,20\n3,30\n"
    return client.post(
        "/api/datasets",
        data={"name": name, **form},
        files={"file": (filename, content, "text/csv")},
    )


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] == "ok"


def test_register_dataset_and_profile(client, small_df):
    r = _create(client, content=csv_bytes(small_df), project="Demo", is_synthetic="true")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "Orders" and body["is_synthetic"] is True
    assert body["version_count"] == 1 and body["versions"][0]["row_count"] == 40
    assert body["latest_profile"]["quality_score"] == 100.0

    prof = client.get(f"/api/datasets/{body['id']}/profile").json()
    assert prof["version_number"] == 1 and len(prof["columns"]) == 3
    assert {c["column_name"]: c["inferred_type"] for c in prof["columns"]}["amount"] == "float"


def test_duplicate_name_conflict(client):
    assert _create(client).status_code == 201
    r = _create(client)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DATASET_EXISTS"


def test_invalid_file_creates_nothing(client):
    r = _create(client, content=b"a,a\n1,2\n")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "DUPLICATE_COLUMNS" and "message" in err
    assert client.get("/api/datasets").json()["total"] == 0


def test_unsupported_format_via_api(client):
    r = _create(client, filename="evil.exe", content=b"MZ...")
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_upload_too_large(client):
    big = b"a\n" + b"1234567890\n" * 120_000  # ~1.3 MB > 1 MB test limit
    r = _create(client, content=big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"


def test_missing_name_is_validation_error(client):
    r = client.post("/api/datasets", files={"file": ("a.csv", b"a\n1\n", "text/csv")})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_add_version_and_duplicate_content(client):
    ds = _create(client).json()
    r = client.post(
        f"/api/datasets/{ds['id']}/versions",
        files={"file": ("v2.csv", b"id,amount,extra\n1,10,x\n2,20,y\n", "text/csv")},
        data={"notes": "second"},
    )
    assert r.status_code == 201 and r.json()["version_number"] == 2
    assert r.json()["notes"] == "second"

    again = client.post(
        f"/api/datasets/{ds['id']}/versions",
        files={"file": ("v2.csv", b"id,amount,extra\n1,10,x\n2,20,y\n", "text/csv")},
    )
    assert again.status_code == 409 and again.json()["error"]["details"]["existing_version"] == 2

    versions = client.get(f"/api/datasets/{ds['id']}/versions").json()
    assert [v["version_number"] for v in versions] == [1, 2]
    v1 = client.get(f"/api/datasets/{ds['id']}/profile", params={"version": 1}).json()
    assert v1["metrics"]["column_count"] == 2


def test_not_found(client):
    r = client.get("/api/datasets/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "DATASET_NOT_FOUND"
    ds = _create(client).json()
    r = client.get(f"/api/datasets/{ds['id']}/profile", params={"version": 9})
    assert r.status_code == 404 and r.json()["error"]["code"] == "VERSION_NOT_FOUND"


def test_list_pagination_and_search(client):
    for i in range(5):
        assert _create(client, name=f"Set {i}", content=f"a\n{i}\n".encode()).status_code == 201
    page = client.get("/api/datasets", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 5 and len(page["items"]) == 2
    assert client.get("/api/datasets", params={"search": "Set 3"}).json()["total"] == 1
    assert client.get("/api/datasets", params={"limit": 500}).status_code == 422


def test_preview_and_schema(client, small_df):
    ds = _create(client, content=csv_bytes(small_df)).json()
    prev = client.get(f"/api/datasets/{ds['id']}/versions/1/preview", params={"rows": 5}).json()
    assert prev["total_rows"] == 40 and len(prev["rows"]) == 5
    assert prev["rows"][0] == {"order_id": 1, "amount": 0.0, "city": "A"}
    big = client.get(f"/api/datasets/{ds['id']}/versions/1/preview", params={"rows": 10_000}).json()
    assert len(big["rows"]) == 40  # capped by data size / max_preview_rows

    schema = client.get(f"/api/datasets/{ds['id']}/versions/1/schema").json()
    assert [c["name"] for c in schema["columns"]] == ["order_id", "amount", "city"]


def test_excel_upload_with_sheet_selection(client, small_df):
    content = xlsx_bytes({"Notes": pd.DataFrame({"n": ["x"]}), "Data": small_df})
    r = client.post(
        "/api/datasets",
        data={"name": "XL", "sheet_name": "Data"},
        files={"file": ("book.xlsx", content, "application/octet-stream")},
    )
    assert r.status_code == 201, r.text
    v = r.json()["versions"][0]
    assert v["sheet_name"] == "Data" and v["row_count"] == 40 and v["file_format"] == "excel"
    prev = client.get(f"/api/datasets/{r.json()['id']}/versions/1/preview").json()
    assert prev["columns"] == ["order_id", "amount", "city"]


def test_inspect_file_does_not_register(client, small_df):
    content = xlsx_bytes({"A": small_df.head(3), "B": small_df})
    r = client.post("/api/files/inspect", files={"file": ("b.xlsx", content, "application/octet-stream")})
    assert r.status_code == 200
    body = r.json()
    assert body["sheet_names"] == ["A", "B"] and body["selected_sheet"] == "A"
    assert body["row_count"] == 3 and body["warnings"]
    assert client.get("/api/datasets").json()["total"] == 0


def test_stored_file_is_not_named_after_upload(client, settings):
    ds = _create(client, filename="../../secret.csv").json()
    v = ds["versions"][0]
    assert v["original_filename"] == "secret.csv"
    stored = list((settings.storage_dir / ds["id"]).iterdir())
    assert len(stored) == 1 and stored[0].name.startswith("v1_") and "secret" not in stored[0].name
