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
# Put the brand + dataset picker above the page list (falls back to Streamlit's default order if the selectors change).
st.markdown("""<style>
[data-testid="stSidebarContent"] {display:flex; flex-direction:column;}
[data-testid="stSidebarHeader"] {order:0;} [data-testid="stSidebarUserContent"] {order:1;} [data-testid="stSidebarNav"] {order:2;}
</style>""", unsafe_allow_html=True)

# A page asked to switch dataset (can't edit a widget's state after it is created, so it is applied here).
if "_select_dataset" in st.session_state:
    st.session_state["sidebar_dataset"] = st.session_state.pop("_select_dataset")

with st.sidebar:
    st.markdown(f'<div class="ds-brand">{ui.LOGO_SVG}<div>DataSentinel<small>data quality · drift · lineage</small></div></div>',
                unsafe_allow_html=True)
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
            st.markdown(ui.badge("synthetic demo data", ui.VIOLET), unsafe_allow_html=True)
    elif online:
        st.info("No datasets yet.")

    if online and datasets:
        counts = state.open_incident_counts()
        if counts["critical_high"]:
            color, text = "#ef4444", f"<b>{counts['critical_high']}</b> critical / high incident{'s' if counts['critical_high'] != 1 else ''} open"
        elif counts["open"]:
            color, text = "#eab308", f"<b>{counts['open']}</b> open incident{'s' if counts['open'] != 1 else ''}"
        else:
            color, text = "#22c55e", "All clear — no open incidents"
        st.markdown(f'<div class="ds-side-alert" style="--c:{color}"><span class="ds-pulse"></span><span>{text}</span></div>',
                    unsafe_allow_html=True)
        if counts["open"] and st.button("Review incidents", key="side_review", use_container_width=True):
            st.switch_page("pages/incidents.py")

    with st.expander("Connection"):
        st.text_input("API URL", value=c.base_url, key="api_url")
        st.text_input("API key", type="password", key="api_key", help="Only needed when the backend sets DS_API_KEY. Kept in this browser session only.")
        st.markdown(ui.status_badge("healthy" if online else "failing"), unsafe_allow_html=True)
        if st.button("Refresh data", key="side_refresh", use_container_width=True, help="Clear cached run details and reload everything."):
            st.cache_data.clear()
            st.rerun()

pages = {
    "Monitor": [
        st.Page("pages/command_center.py", title="Command Center", icon=":material/radar:", default=True),
        st.Page("pages/dashboard.py", title="Quality Trends", icon=":material/monitoring:"),
        st.Page("pages/overview.py", title="Assessment", icon=":material/space_dashboard:"),
        st.Page("pages/run_history.py", title="Run History", icon=":material/history:"),
        st.Page("pages/datasets.py", title="Datasets", icon=":material/database:"),
        st.Page("pages/quality.py", title="Data Quality", icon=":material/verified:"),
        st.Page("pages/drift.py", title="Drift Monitoring", icon=":material/query_stats:"),
        st.Page("pages/schema.py", title="Schema Comparison", icon=":material/compare_arrows:"),
    ],
    "Investigate": [
        st.Page("pages/incidents.py", title="Incident Center", icon=":material/warning:"),
        st.Page("pages/root_cause.py", title="Root Cause Analysis", icon=":material/manage_search:"),
        st.Page("pages/lineage.py", title="Data Lineage", icon=":material/account_tree:"),
    ],
    "Configure": [
        st.Page("pages/settings.py", title="Rules & Baselines", icon=":material/tune:"),
        st.Page("pages/help.py", title="Guide", icon=":material/menu_book:"),
    ],
}
st.navigation(pages).run()
