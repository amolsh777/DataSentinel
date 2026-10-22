from __future__ import annotations
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import streamlit as st
from streamlit_agraph import agraph, Node, Edge, Config
from components import charts, state, ui
from services.api_client import ApiError

TYPE_COLORS = {
    "dataset": "#2dd4bf", "transformation": "#38bdf8",
    "metric": "#a78bfa", "report": "#f472b6", "ml_feature": "#fb923c",
}
TYPE_ICONS = {
    "dataset": "🗄️", "transformation": "⚙️",
    "metric": "📊", "report": "📋", "ml_feature": "🤖",
}
ASSET_TYPES = ["dataset", "transformation", "metric", "report", "ml_feature"]

c = state.client()
ds = state.current_dataset()
ui.page_header("Data Lineage", "What depends on this dataset, and what would break if it changed?", ds)
graph_data = state.api(c.get_lineage)
assets = graph_data["assets"]
edges  = graph_data["edges"]
by_id  = {a["id"]: a for a in assets}

ui.kpi_cards([
    {"label": "Assets", "value": graph_data["node_count"], "sub": "nodes in the lineage graph"},
    {"label": "Edges", "value": graph_data["edge_count"], "sub": "directed dependencies"},
    {"label": "Datasets", "value": sum(1 for a in assets if a["asset_type"] == "dataset"), "color": "#2dd4bf"},
    {"label": "Reports / KPIs", "value": sum(1 for a in assets if a["asset_type"] in ("report","metric")), "color": "#a78bfa"},
])

tab_graph, tab_impact, tab_edit = st.tabs(["Graph", "Impact analysis", "Edit lineage"])

# ── graph ─────────────────────────────────────────────────────────────────────
with tab_graph:
    if not assets:
        ui.callout("info", "No lineage graph yet. Use the 'Edit lineage' tab or the Demo data loader to add assets and edges.")
    else:
        highlight_name = st.selectbox("Highlight asset", ["(none)"] + [a["name"] for a in assets], key="lg_hl")
        highlight_id = next((a["id"] for a in assets if a["name"] == highlight_name), None)

        # streamlit-agraph interactive graph.
        nodes = [Node(
            id=a["id"], label=TYPE_ICONS.get(a["asset_type"],"") + " " + a["name"],
            color=("#f97316" if a["id"] == highlight_id else TYPE_COLORS.get(a["asset_type"],"#64748b")),
            size=22 if a["id"] == highlight_id else 16,
            title=f"{a['asset_type']}: {(a.get('description') or '')[:80]}",
        ) for a in assets]
        graph_edges = [Edge(
            source=e["source_asset_id"], target=e["target_asset_id"],
            label=e["dependency_kind"],
            color="#2dd4bf" if e["dependency_kind"] == "known" else "#64748b",
        ) for e in edges]
        config = Config(
            width="100%", height=520,
            directed=True, hierarchical=False,
            nodeHighlightBehavior=True, highlightColor="#f97316",
            physics=True, collapsible=False,
            node={"color": "#2dd4bf"},
            link={"renderLabel": False, "highlightColor": "#f97316"},
        )
        selected = agraph(nodes=nodes, edges=graph_edges, config=config)
        if selected:
            sel_asset = by_id.get(selected)
            if sel_asset:
                st.markdown(f"**Selected:** {TYPE_ICONS.get(sel_asset['asset_type'],'')} {sel_asset['name']} "
                            f"({sel_asset['asset_type']})")
                if sel_asset.get("description"):
                    st.caption(sel_asset["description"])
                if sel_asset.get("columns"):
                    st.caption("Columns: " + ", ".join(sel_asset["columns"]))

        st.caption("Blue = known dependency · Grey = inferred · Orange = highlighted asset. Click a node to inspect it.")

# ── impact ─────────────────────────────────────────────────────────────────────
with tab_impact:
    st.markdown("**What downstream assets are affected if columns in this dataset change?**")
    if not assets:
        st.info("Add lineage assets first.")
    else:
        all_schema_cols = state.api(c.schema, ds["id"], state.versions_of(ds)[-1])["columns"]
        sel_cols = st.multiselect("Affected columns", [x["name"] for x in all_schema_cols],
                                  key="lg_cols", help="Select the columns that changed or have a quality issue.")
        if sel_cols and st.button("Find affected assets", type="primary"):
            try:
                impact = c.get_lineage_impact(ds["id"], sel_cols)
                affected = impact["affected_assets"]
                if not affected:
                    ui.callout("success", "No downstream assets found for the selected columns. "
                               "Either no assets are linked to this dataset, or none use these specific columns.")
                else:
                    ui.kpi_cards([{"label": "Affected assets", "value": len(affected),
                                   "sub": ", ".join(sorted({a["asset_type"] for a in affected}))}])
                    for a in affected:
                        cols_used = ", ".join(a.get("edge_columns") or []) or "unspecified"
                        st.markdown(
                            f'<div class="ds-card"><div class="ds-row">'
                            f'{ui.badge(a["asset_type"], TYPE_COLORS.get(a["asset_type"],"#64748b"))}'
                            f'<b>{ui.esc(a["name"])}</b> '
                            f'{ui.badge(a["dependency_kind"],"#334155")}</div>'
                            f'<div class="ds-muted">via {ui.esc(a["via"])} · columns: {ui.esc(cols_used)}</div></div>',
                            unsafe_allow_html=True)
                    # Show the subgraph highlighted.
                    if assets:
                        highlight_ids = {a["id"] for a in affected}
                        ui.show_chart(charts.network_plotly(assets, edges, highlight_ids), key="lg_impact_graph")
            except ApiError as exc:
                ui.show_error(exc)

# ── edit ──────────────────────────────────────────────────────────────────────
with tab_edit:
    st.markdown("**Add an asset**")
    with st.form("add_asset"):
        name   = st.text_input("Name (must be unique)")
        atype  = st.selectbox("Type", ASSET_TYPES)
        desc   = st.text_input("Description (optional)")
        link_ds = st.checkbox("Link to the active dataset", value=False)
        cols   = st.text_input("Columns (comma-separated, optional)")
        if st.form_submit_button("Add asset"):
            try:
                body = {"name": name.strip(), "asset_type": atype, "description": desc or None,
                        "dataset_id": ds["id"] if link_ds else None,
                        "columns": [x.strip() for x in cols.split(",") if x.strip()]}
                c.create_asset(body)
                st.success(f"Asset '{name}' added."); st.rerun()
            except ApiError as exc:
                ui.show_error(exc)

    st.markdown("**Add an edge (dependency)**")
    if assets:
        names = [a["name"] for a in assets]
        with st.form("add_edge"):
            src_name = st.selectbox("Source (upstream)", names, key="ed_src")
            tgt_name = st.selectbox("Target (downstream)", names, key="ed_tgt")
            kind     = st.radio("Dependency kind", ["known", "inferred"], horizontal=True)
            edge_desc = st.text_input("Description (optional)")
            if st.form_submit_button("Add edge"):
                src_id = next(a["id"] for a in assets if a["name"] == src_name)
                tgt_id = next(a["id"] for a in assets if a["name"] == tgt_name)
                try:
                    c.create_edge({"source_asset_id": src_id, "target_asset_id": tgt_id,
                                   "dependency_kind": kind, "description": edge_desc or None})
                    st.success("Edge added."); st.rerun()
                except ApiError as exc:
                    ui.show_error(exc)

    st.markdown("**Remove an asset or edge**")
    c1, c2 = st.columns(2)
    if assets:
        del_asset = c1.selectbox("Delete asset", ["(none)"] + [a["name"] for a in assets], key="del_a")
        if c1.button("Delete asset", disabled=del_asset == "(none)"):
            aid = next(a["id"] for a in assets if a["name"] == del_asset)
            try:
                c.delete_asset(aid); st.success("Deleted."); st.rerun()
            except ApiError as exc:
                ui.show_error(exc)
    if edges:
        edge_labels = [f"{e['source_name']} → {e['target_name']}" for e in edges]
        del_edge = c2.selectbox("Delete edge", ["(none)"] + edge_labels, key="del_e")
        if c2.button("Delete edge", disabled=del_edge == "(none)"):
            eid = edges[edge_labels.index(del_edge)]["id"]
            try:
                c.delete_edge(eid); st.success("Deleted."); st.rerun()
            except ApiError as exc:
                ui.show_error(exc)
