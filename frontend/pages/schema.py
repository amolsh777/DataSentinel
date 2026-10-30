from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import state, ui  # noqa: E402

c = state.client()
ds = state.current_dataset()
ui.page_header("Schema Comparison", "What changed in the structure of the data, and what might it break downstream?", ds)

if len(state.versions_of(ds)) < 2:
    ui.callout("info", "Schema comparison needs two versions. Upload a second version on the Datasets page.")
    st.stop()
cur, ref = state.version_selectors(ds, "sc")
if ref is None or ref == cur:
    ui.callout("warn", "Select a reference version different from the current one.")
    st.stop()

diff = state.api(c.schema_diff, ds["id"], ref, cur)
sm = diff["summary"]
ui.kpi_cards([
    {"label": "Compatibility", "value": "Compatible" if diff["compatible"] else "Breaking changes",
     "color": "#22c55e" if diff["compatible"] else "#ef4444", "sub": f"v{ref} → v{cur}"},
    {"label": "Breaking changes", "value": sm["breaking_changes"], "color": "#ef4444" if sm["breaking_changes"] else "#22c55e"},
    {"label": "Added columns", "value": sm["added"], "color": "#38bdf8"},
    {"label": "Removed columns", "value": sm["removed"], "color": "#ef4444" if sm["removed"] else "#22c55e"},
    {"label": "All changes", "value": sm["total_changes"], "sub": f"max severity: {sm['max_severity'] or 'none'}"}])

st.subheader("Side-by-side comparison")
show_all = st.checkbox("Show unchanged columns", value=True, key="sc_all")
rows = []
for col in diff["columns"]:
    if not show_all and col["status"] == "unchanged":
        continue
    b, n = col["baseline"] or {}, col["current"] or {}
    rows.append({"column": col["name"], "status": col["status"],
                 "baseline type": b.get("inferred_type", "—"), "current type": n.get("inferred_type", "—"),
                 "baseline dtype": b.get("pandas_dtype", "—"), "current dtype": n.get("pandas_dtype", "—"),
                 "baseline missing %": b.get("missing_pct"), "current missing %": n.get("missing_pct"),
                 "baseline unique %": b.get("unique_pct"), "current unique %": n.get("unique_pct")})
styled = pd.DataFrame(rows).style.map(lambda v: f"color: {ui.STATUS_COLORS.get(v, 'inherit')}; font-weight: 600", subset=["status"])
ui.show_df(styled)

st.subheader("Changes")
f1, f2 = st.columns(2)
sevs = f1.multiselect("Severity", ["critical", "high", "medium", "low", "info"], default=["critical", "high", "medium", "low", "info"], key="sc_sev")
only_breaking = f2.checkbox("Breaking only", key="sc_brk")
changes = [ch for ch in diff["changes"] if ch["severity"] in sevs and (ch["breaking"] or not only_breaking)]
if not changes:
    ui.callout("success", "No changes match the current filters." if diff["changes"] else "The schemas are identical.")
for i, ch in enumerate(changes):
    with st.expander(f"{ch['change_type'].replace('_', ' ')} — {', '.join(ch['columns'])}", expanded=ch["breaking"] and i < 3):
        st.markdown(f"{ui.severity_badge(ch['severity'])} "
                    f"{ui.badge('breaking' if ch['breaking'] else 'compatible', '#ef4444' if ch['breaking'] else '#22c55e')} "
                    + ("" if ch["certain"] else ui.badge("uncertain", "#eab308")), unsafe_allow_html=True)
        st.write(ch["explanation"])
        st.markdown(f"**Possible downstream impact:** {ch['downstream_risk']}")
        a, b = st.columns(2)
        a.markdown("**Baseline**"); a.json(ch["baseline"] if ch["baseline"] is not None else {}, expanded=False)
        b.markdown("**Current**"); b.json(ch["current"] if ch["current"] is not None else {}, expanded=False)

if diff["rename_candidates"]:
    st.subheader("Rename candidates")
    ui.callout("info", "These are suggestions only. A removed and an added column are never merged automatically.")
    ui.show_df(pd.DataFrame(diff["rename_candidates"]))
with st.expander("Limitations"):
    for lim in diff["limitations"]:
        st.write("• " + lim)
st.download_button("Download schema diff (JSON)", json.dumps(diff, indent=2, default=str), "schema_diff.json", "application/json")
