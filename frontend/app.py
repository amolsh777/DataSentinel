"""DataSentinel — Streamlit entry point.  Run from the repo root:  streamlit run frontend/app.py"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402

from components import state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

st.set_page_config(page_title="DataSentinel", page_icon=":material/shield:", layout="wide",
                   initial_sidebar_state="expanded")
ui.inject_css()

# A page asked to switch dataset (can't edit a widget's state after it is created, so it is applied here).
if "_select_dataset" in st.session_state:
    st.session_state["sidebar_dataset"] = st.session_state.pop("_select_dataset")

with st.sidebar:
    st.markdown('<div class="ds-brand"><span class="dot"></span>DataSentinel</div>', unsafe_allow_html=True)
    st.caption("Data quality, drift & pipeline intelligence")
    c = state.client()
    try:
        c.health()
        datasets = c.list_datasets()
        online = True
    except ApiError as exc:
        datasets, online = [], False
        ui.show_error(exc)
    if datasets:
        by_name = {d["name"]: d for d in datasets}
        # Seed the selection in session state so the widget's own arguments never change between runs.
        if st.session_state.get("sidebar_dataset") not in by_name:
            st.session_state["sidebar_dataset"] = "Retail Transactions" if "Retail Transactions" in by_name else next(iter(by_name))
        name = st.selectbox("Active dataset", list(by_name), key="sidebar_dataset")
        st.session_state["dataset_id"] = by_name[name]["id"]
        if by_name[name]["is_synthetic"]:
            st.markdown(ui.badge("synthetic demo data", "#a78bfa"), unsafe_allow_html=True)
    elif online:
        st.info("No datasets yet.")
    with st.expander("Connection"):
        st.text_input("API URL", value=c.base_url, key="api_url")
        st.markdown(ui.status_badge("healthy" if online else "failing"), unsafe_allow_html=True)

pages = {
    "Monitor": [
        st.Page("pages/overview.py", title="Overview", icon=":material/space_dashboard:", default=True),
        st.Page("pages/run_history.py", title="Run History", icon=":material/history:"),
        st.Page("pages/datasets.py", title="Datasets", icon=":material/database:"),
        st.Page("pages/quality.py", title="Data Quality", icon=":material/verified:"),
        st.Page("pages/drift.py", title="Drift Monitoring", icon=":material/monitoring:"),
        st.Page("pages/schema.py", title="Schema Comparison", icon=":material/compare_arrows:"),
    ],
    "Investigate": [
        st.Page("pages/root_cause.py", title="Root Cause Analysis", icon=":material/manage_search:"),
        st.Page("pages/incidents.py", title="Incident Center", icon=":material/warning:"),
        st.Page("pages/lineage.py", title="Data Lineage", icon=":material/account_tree:"),
    ],
    "Configure": [
        st.Page("pages/settings.py", title="Rules & Baselines", icon=":material/tune:"),
    ],
}
st.navigation(pages).run()
