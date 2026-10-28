"""Session state and API helpers shared by every page."""

from __future__ import annotations

import streamlit as st

from components import insights, ui
from services.api_client import ApiClient, ApiError

DEMO_RULES = [
    {"name": "Revenue formula", "rule_type": "business_rule", "severity": "high",
     "params": {"expression": "abs(revenue - quantity * unit_price * (1 - discount_pct / 100)) <= 0.01",
                "description": "revenue = quantity x unit_price x (1 - discount_pct/100), within 0.01"}},
    {"name": "Customer exists in Customer Master", "rule_type": "referential_integrity", "column": "customer_id",
     "severity": "high", "params": {"reference_dataset": "Customer Master", "reference_column": "customer_id"}},
]


def client() -> ApiClient:
    return ApiClient(st.session_state.get("api_url") or None)


def api(fn, *args, **kwargs):
    """Call the API; on error show it and stop the page."""
    try:
        return fn(*args, **kwargs)
    except ApiError as exc:
        ui.show_error(exc)
        st.stop()


def current_dataset() -> dict:
    """The dataset chosen in the sidebar (falls back to the first one)."""
    c = client()
    did = st.session_state.get("dataset_id")
    if did:
        try:
            return c.get_dataset(did)
        except ApiError as exc:
            if exc.status != 404:
                ui.show_error(exc)
                st.stop()
    datasets = api(c.list_datasets)
    if not datasets:
        ui.callout("info", "No datasets registered yet. Open the Datasets page to upload a file or load the synthetic demo data.")
        st.stop()
    pick = next((d for d in datasets if d["name"] == "Retail Transactions"), datasets[0])
    st.session_state["dataset_id"] = pick["id"]
    return api(c.get_dataset, pick["id"])


def versions_of(dataset: dict) -> list[int]:
    return sorted(v["version_number"] for v in dataset["versions"])


def version_label(dataset: dict, number: int) -> str:
    v = next(v for v in dataset["versions"] if v["version_number"] == number)
    return f"v{number} · {v['row_count']:,} rows · {v['original_filename']}"


def active_baseline_version(dataset_id: str) -> int | None:
    try:
        active = [b for b in client().baselines(dataset_id) if b["is_active"]]
    except ApiError:
        return None
    return active[0]["version_number"] if active else None


def select_dataset_by_name(name: str) -> None:
    """Applied by app.py before the sidebar widget is created on the next run."""
    st.session_state["_select_dataset"] = name


def version_selectors(dataset: dict, prefix: str, *, with_reference: bool = True):
    """Current + reference pickers. Reference defaults to the active baseline; returns (current, reference|None)."""
    vers = versions_of(dataset)
    baseline = active_baseline_version(dataset["id"])
    cols = st.columns(2 if with_reference else 1)
    current = cols[0].selectbox("Current version", vers, index=len(vers) - 1, key=f"{prefix}_cur",
                                format_func=lambda n: version_label(dataset, n))
    if not with_reference:
        return current, None
    options = [None] + vers
    default = options.index(baseline) if baseline in vers else (1 if len(vers) > 1 and vers[0] != current else 0)
    reference = cols[1].selectbox(
        "Reference version" + (f" (active baseline: v{baseline})" if baseline else " (no baseline set)"),
        options, index=default, key=f"{prefix}_ref",
        format_func=lambda n: "None" if n is None else version_label(dataset, n))
    return current, reference


def analysis_body(current: int, reference: int | None, **extra) -> dict:
    """Request body for score / drift / root-cause. Reference None means 'no reference', not 'use baseline'."""
    return {"current_version": current, "reference_version": reference,
            "use_active_baseline": reference is not None, **extra}


# ------------------------------------------------------------------ v8 helpers
@st.cache_data(ttl=300, show_spinner=False)
def _cached_run_detail(base_url: str, dataset_id: str, run_id: str) -> dict:
    """Run detail is immutable once finished, so it is safe to cache (keyed by URL + ids, not by the client)."""
    return ApiClient(base_url).get_run(dataset_id, run_id)


def run_detail(dataset_id: str, run_id: str) -> dict:
    return _cached_run_detail(client().base_url, dataset_id, run_id)


def runs_with_checks(dataset_id: str, runs_newest_first: list[dict], max_runs: int = 20) -> list[tuple[dict, list[dict]]]:
    """(run row, check_results) pairs, oldest -> newest, for completed runs. Fetches (cached) run details."""
    done = [r for r in runs_newest_first if r.get("status") == "completed"][:max_runs]
    out = []
    for r in insights.runs_oldest_first(done):
        try:
            out.append((r, run_detail(dataset_id, r["run_id"]).get("check_results", [])))
        except ApiError:
            continue
    return out


def fleet_snapshot(datasets: list[dict], runs_per_dataset: int = 12) -> tuple[list[dict], list[dict]]:
    """(rows, incidents): one headline row per dataset (latest run, score trend, open incidents) plus the raw
    incident list. One incidents call for the whole fleet."""
    c = client()
    try:
        incidents = c.list_incidents(limit=200)["items"]
    except ApiError:
        incidents = []
    by_ds: dict[str, list[dict]] = {}
    for i in incidents:
        by_ds.setdefault(i.get("dataset_id"), []).append(i)
    rows = []
    for d in datasets:
        try:
            runs = c.run_history(d["id"], limit=runs_per_dataset)
        except ApiError:
            runs = []
        rows.append(insights.fleet_row(d, runs, by_ds.get(d["id"], [])))
    return rows, incidents


def open_incident_counts() -> dict:
    """Cheap fleet-wide counts for the sidebar alert: {'open': n, 'critical_high': n}. Never raises."""
    c = client()
    try:
        total = c.list_incidents(limit=1)["total"]
        ch = c.list_incidents(severity=["critical", "high"], limit=1)["total"]
        return {"open": total, "critical_high": ch}
    except ApiError:
        return {"open": 0, "critical_high": 0}


def open_page(page: str, dataset_name: str | None = None) -> None:
    """Switch to a registered page, optionally selecting a dataset first (applied by app.py on the next run)."""
    if dataset_name:
        select_dataset_by_name(dataset_name)
    st.switch_page(page)
