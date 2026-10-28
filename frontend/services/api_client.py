"""Thin REST client for the DataSentinel API. The frontend talks to the backend only through this module."""

from __future__ import annotations

import os
from typing import Any

import requests

DEFAULT_API_URL = "http://127.0.0.1:8000/api"


class ApiError(Exception):
    """An error returned by (or while reaching) the API."""

    def __init__(self, message: str, *, code: str = "ERROR", status: int | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message, self.code, self.status, self.details = message, code, status, details or {}


class ApiClient:
    def __init__(self, base_url: str | None = None, timeout: float = 180.0) -> None:
        self.base_url = (base_url or os.environ.get("DS_API_URL") or DEFAULT_API_URL).rstrip("/")
        if not self.base_url.endswith("/api"):
            self.base_url += "/api"
        self.timeout = timeout
        self._session = requests.Session()

    # ------------------------------------------------------------------ core
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = self._session.request(method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs)
        except requests.ConnectionError:
            raise ApiError(f"Cannot reach the DataSentinel API at {self.base_url}. Is the backend running?",
                           code="API_UNREACHABLE") from None
        except requests.Timeout:
            raise ApiError("The API took too long to respond.", code="API_TIMEOUT") from None
        if resp.status_code >= 400:
            try:
                err = resp.json()["error"]
                raise ApiError(err.get("message", "Request failed."), code=err.get("code", "ERROR"),
                               status=resp.status_code, details=err.get("details"))
            except (ValueError, KeyError, TypeError):
                raise ApiError(f"Request failed with HTTP {resp.status_code}.", status=resp.status_code) from None
        return None if resp.status_code == 204 else resp.json()

    def _get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def _post(self, path: str, body: dict[str, Any] | None = None, **params: Any) -> Any:
        return self._request("POST", path, json=body if body is not None else {},
                             params={k: v for k, v in params.items() if v is not None})

    # --------------------------------------------------------------- datasets
    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def list_datasets(self) -> list[dict[str, Any]]:
        out, offset = [], 0
        while True:
            page = self._get("/datasets", limit=100, offset=offset)
            out.extend(page["items"])
            offset += 100
            if offset >= page["total"]:
                return out

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        return self._get(f"/datasets/{dataset_id}")

    def inspect_file(self, filename: str, content: bytes, sheet_name: str | None = None) -> dict[str, Any]:
        return self._request("POST", "/files/inspect", files={"file": (filename, content)},
                             data={"sheet_name": sheet_name} if sheet_name else None)

    def create_dataset(self, *, name: str, filename: str, content: bytes, description: str | None = None,
                       owner: str | None = None, project: str | None = None, sheet_name: str | None = None,
                       is_synthetic: bool = False, notes: str | None = None) -> dict[str, Any]:
        data = {"name": name, "is_synthetic": str(is_synthetic).lower()}
        for k, v in (("description", description), ("owner", owner), ("project", project),
                     ("sheet_name", sheet_name), ("notes", notes)):
            if v:
                data[k] = v
        return self._request("POST", "/datasets", files={"file": (filename, content)}, data=data)

    def add_version(self, dataset_id: str, *, filename: str, content: bytes, sheet_name: str | None = None,
                    notes: str | None = None, is_synthetic: bool | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if sheet_name:
            data["sheet_name"] = sheet_name
        if notes:
            data["notes"] = notes
        if is_synthetic is not None:
            data["is_synthetic"] = str(is_synthetic).lower()
        return self._request("POST", f"/datasets/{dataset_id}/versions", files={"file": (filename, content)},
                             data=data)

    def profile(self, dataset_id: str, version: int | None = None) -> dict[str, Any]:
        return self._get(f"/datasets/{dataset_id}/profile", version=version)

    def schema(self, dataset_id: str, version: int) -> dict[str, Any]:
        return self._get(f"/datasets/{dataset_id}/versions/{version}/schema")

    def preview(self, dataset_id: str, version: int, rows: int = 50) -> dict[str, Any]:
        return self._get(f"/datasets/{dataset_id}/versions/{version}/preview", rows=rows)

    # ------------------------------------------------------------------ rules
    def list_rules(self, dataset_id: str | None = None, include_global: bool = True) -> list[dict[str, Any]]:
        return self._get("/rules", dataset_id=dataset_id, include_global=str(include_global).lower())

    def create_rule(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._post("/rules", body)

    def update_rule(self, rule_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", f"/rules/{rule_id}", json=body)

    def delete_rule(self, rule_id: str) -> None:
        self._request("DELETE", f"/rules/{rule_id}")

    def suggest_rules(self, dataset_id: str, version: int | None = None, save: bool = False) -> dict[str, Any]:
        return self._post(f"/datasets/{dataset_id}/rules/suggest", version=version, save=str(save).lower())

    # --------------------------------------------------------------- analysis
    def validate(self, dataset_id: str, version: int, reference_version: int | None = None) -> dict[str, Any]:
        return self._post(f"/datasets/{dataset_id}/versions/{version}/validate", reference_version=reference_version)

    def schema_diff(self, dataset_id: str, reference: int, current: int | None = None) -> dict[str, Any]:
        return self._get(f"/datasets/{dataset_id}/schema-diff", reference=reference, current=current)

    def baselines(self, dataset_id: str) -> list[dict[str, Any]]:
        return self._get(f"/datasets/{dataset_id}/baselines")

    def set_baseline(self, dataset_id: str, version_number: int, reason: str, set_by: str | None = None,
                     confirm: bool = False) -> dict[str, Any]:
        return self._post(f"/datasets/{dataset_id}/baseline", {
            "version_number": version_number, "reason": reason, "set_by": set_by or None, "confirm": confirm})

    def drift(self, dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/datasets/{dataset_id}/drift", body)

    def score(self, dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/datasets/{dataset_id}/score", body)

    def score_compare(self, dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/datasets/{dataset_id}/score/compare", body)

    def root_cause(self, dataset_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/datasets/{dataset_id}/root-cause", body)


    # ─────────────────────────────────── monitoring ──────────────────────────
    def monitor(self, dataset_id: str, body: dict = None) -> dict:
        return self._post(f"/datasets/{dataset_id}/monitor", body or {})

    def list_runs(self, dataset_id: str, limit: int = 50) -> dict:
        return self._get(f"/datasets/{dataset_id}/runs", limit=limit)

    def run_history(self, dataset_id: str, limit: int = 20) -> list:
        return self._get(f"/datasets/{dataset_id}/runs/history", limit=limit)

    def get_run(self, dataset_id: str, run_id: str) -> dict:
        return self._get(f"/datasets/{dataset_id}/runs/{run_id}")

    def list_incidents(self, **params) -> dict:
        return self._get("/incidents", **{k: v for k, v in params.items() if v is not None})

    def get_incident(self, incident_id: str) -> dict:
        return self._get(f"/incidents/{incident_id}")

    def update_incident(self, incident_id: str, status: str, note: str = "", changed_by: str = "") -> dict:
        return self._request("PATCH", f"/incidents/{incident_id}",
                             json={"status": status, "note": note, "changed_by": changed_by or None})

    def incident_history(self, incident_id: str) -> list:
        return self._get(f"/incidents/{incident_id}/history")

    def get_report(self, run_id: str, fmt: str = "html") -> bytes:
        try:
            r = self._session.get(f"{self.base_url}/runs/{run_id}/report",
                                  params={"format": fmt}, timeout=self.timeout)
            r.raise_for_status()
            return r.content
        except Exception as exc:
            raise ApiError(str(exc)) from None

    def download_version(self, dataset_id: str, version: int) -> bytes:
        try:
            r = self._session.get(f"{self.base_url}/datasets/{dataset_id}/versions/{version}/download",
                                  timeout=self.timeout)
            r.raise_for_status()
            return r.content
        except Exception as exc:
            raise ApiError(str(exc)) from None

    # ───────────────────────────── dataset lifecycle (soft delete) ─────────────
    def delete_dataset(self, dataset_id: str) -> dict:
        """Soft-delete (hidden from lists, recoverable with restore_dataset)."""
        return self._request("DELETE", f"/datasets/{dataset_id}", params={"confirm": "true"})

    def restore_dataset(self, dataset_id: str) -> dict:
        return self._request("PATCH", f"/datasets/{dataset_id}/restore")

    # ─────────────────────────────────── lineage ─────────────────────────────
    def get_lineage(self) -> dict:
        return self._get("/lineage")

    def create_asset(self, body: dict) -> dict:
        return self._request("POST", "/lineage", json=body)

    def create_edge(self, body: dict) -> dict:
        return self._request("POST", "/lineage/edges", json=body)

    def delete_asset(self, asset_id: str) -> None:
        self._request("DELETE", f"/lineage/{asset_id}")

    def delete_edge(self, edge_id: str) -> None:
        self._request("DELETE", f"/lineage/edges/{edge_id}")

    def get_lineage_impact(self, dataset_id: str, columns: list) -> dict:
        return self._get(f"/lineage/impact/{dataset_id}", columns=columns)

    # ─────────────────────────────────── alerts / scheduler ──────────────────
    def test_alert(self, dataset_id: str, webhook_url: str) -> dict:
        return self._post(f"/datasets/{dataset_id}/alerts/test", {"webhook_url": webhook_url})

    def get_schedule(self, dataset_id: str) -> dict:
        return self._get(f"/datasets/{dataset_id}/schedule")

    def set_schedule(self, dataset_id: str, body: dict) -> dict:
        return self._post(f"/datasets/{dataset_id}/schedule", body)

    # ─────────────────────────────────────── async jobs ───────────────────────
    def submit_monitor_job(self, dataset_id: str, body: dict = None) -> dict:
        return self._request("POST", f"/datasets/{dataset_id}/jobs/monitor", json=body or {})

    def get_job(self, job_id: str) -> dict:
        return self._get(f"/jobs/{job_id}")

    def list_jobs(self, dataset_id: str | None = None, limit: int = 20) -> list:
        return self._get("/jobs", dataset_id=dataset_id, limit=limit)

    def metrics(self) -> str:
        try:
            r = self._session.get(f"{self.base_url}/metrics", timeout=10)
            return r.text
        except Exception:
            return ""
