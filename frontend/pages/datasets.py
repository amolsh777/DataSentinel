from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import charts, state, ui  # noqa: E402
from services.api_client import ApiError  # noqa: E402

SAMPLE_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "sample"
c = state.client()
datasets = state.api(c.list_datasets)

ui.page_header("Datasets", "Register files, browse versions and inspect profiles.")
tab_explore, tab_upload, tab_demo = st.tabs(["Explorer", "Register / upload", "Demo data"])


# ---------------------------------------------------------------- explorer
with tab_explore:
    if not datasets:
        ui.callout("info", "No datasets yet — use the other tabs to upload a file or load the demo data.")
    else:
        ds = state.current_dataset()
        st.subheader(ds["name"])
        st.caption(ds.get("description") or "No description.")
        vers = state.versions_of(ds)
        ver = st.selectbox("Version", vers, index=len(vers) - 1, key="ex_ver",
                           format_func=lambda n: state.version_label(ds, n))
        vmeta = next(v for v in ds["versions"] if v["version_number"] == ver)
        st.caption(f"{vmeta['file_format'].upper()} · {vmeta['file_size_bytes']:,} bytes · sha256 {vmeta['content_hash'][:12]}… · "
                   f"uploaded {vmeta['created_at'][:19].replace('T', ' ')} UTC"
                   + (" · synthetic" if vmeta["is_synthetic"] else ""))
        for w in vmeta.get("ingestion_warnings", []):
            ui.callout("warn", w)

        t_prev, t_schema, t_cols, t_score = st.tabs(["Preview", "Schema", "Column profiles", "Profile score"])
        with t_prev:
            n = st.slider("Rows", 5, 100, 25, key="ex_rows")
            prev = state.api(c.preview, ds["id"], ver, n)
            st.caption(f"Showing {len(prev['rows'])} of {prev['total_rows']:,} rows")
            ui.show_df(pd.DataFrame(prev["rows"], columns=prev["columns"]))


            dl_col1, dl_col2 = st.columns([1, 4])
            if dl_col1.button("Download original file", key="dl_file"):
                try:
                    content = c._session.get(f"{c.base_url}/datasets/{ds['id']}/versions/{ver}/download",
                                              timeout=180).content
                    dl_col2.download_button("Save file", content, vmeta["original_filename"])
                except Exception as exc:
                    ui.show_error(exc)
        with t_schema:
            sch = state.api(c.schema, ds["id"], ver)
            ui.show_df(pd.DataFrame(sch["columns"])[["position", "name", "inferred_type", "pandas_dtype", "nullable",
                                                     "missing_pct", "unique_pct", "is_potential_identifier"]])
            st.markdown("**Override column types** — useful for zip codes, phone numbers and IDs with leading zeros that are wrongly inferred as integers.")
            cur_overrides = vmeta.get("type_overrides") or {}
            col_names = [x["name"] for x in sch["columns"]]
            override_col = st.selectbox("Column to override", ["(select)"] + col_names, key="ov_col")
            ALLOWED_TYPES = ["auto", "integer", "float", "categorical", "text", "datetime", "boolean"]
            if override_col != "(select)":
                cur_type = cur_overrides.get(override_col, "auto")
                new_type = st.selectbox("New type", ALLOWED_TYPES,
                                        index=ALLOWED_TYPES.index(cur_type) if cur_type in ALLOWED_TYPES else 0,
                                        key="ov_type",
                                        help="'auto' reverts to the inferred type.")
                if st.button("Apply type override", key="ov_apply"):
                    try:
                        from services.api_client import ApiError
                        result = c._request("PATCH",
                            f"/datasets/{ds['id']}/versions/{ver}/type-overrides",
                            json={override_col: new_type})
                        st.success(f"'{override_col}' will be treated as {new_type} from the next analysis run. "
                                   "(Parquet cache cleared — next load re-parses the file.)")
                        st.rerun()
                    except Exception as exc:
                        ui.show_error(exc)
            if cur_overrides:
                st.caption("Active overrides: " + ", ".join(f"{k} → {v}" for k, v in cur_overrides.items()))

        profile = state.api(c.profile, ds["id"], ver)
        with t_cols:
            cols = profile["columns"]
            ui.show_df(pd.DataFrame([{
                "column": x["column_name"], "type": x["inferred_type"], "missing %": x["missing_pct"],
                "unique %": x["unique_pct"], "invalid values": x["invalid_count"],
                "identifier": x["is_potential_identifier"], "constant": x["is_constant"]} for x in cols]))
            pick = st.selectbox("Inspect a column", [x["column_name"] for x in cols], key="ex_col")
            col = next(x for x in cols if x["column_name"] == pick)
            ui.kpi_cards([{"label": "Type", "value": col["inferred_type"], "sub": col["pandas_dtype"]},
                          {"label": "Missing", "value": ui.fmt_pct(col["missing_pct"]), "sub": f"{col['missing_count']:,} values"},
                          {"label": "Unique", "value": ui.fmt_pct(col["unique_pct"]), "sub": f"{col['unique_count']:,} distinct"}])
            for w in col["warnings"]:
                ui.callout("warn", w)
            stats = {k: v for k, v in col["stats"].items() if k not in ("top_values", "invalid_examples")}
            left, right = st.columns(2)
            if stats:
                left.markdown("**Statistics**")
                left.dataframe(pd.DataFrame({"statistic": list(stats), "value": [str(v) for v in stats.values()]}),
                               hide_index=True)
            top = col["stats"].get("top_values")
            if top:
                with right:
                    ui.show_chart(charts.category_chart(pick, top, []), key="ex_cat")
        with t_score:
            m = profile["metrics"]
            ui.kpi_cards([
                {"label": "Profile score", "value": m["quality_score"], "sub": "intrinsic heuristic, no rules needed"},
                {"label": "Rows × columns", "value": f"{m['row_count']:,} × {m['column_count']}"},
                {"label": "Duplicate rows", "value": f"{m['duplicate_rows']:,}", "sub": ui.fmt_pct(m["duplicate_pct"])},
                {"label": "Missing cells", "value": f"{m['missing_cells']:,}", "sub": ui.fmt_pct(m["missing_pct"])}])
            comp = m["quality_breakdown"]["components"]
            ui.show_df(pd.DataFrame([{"component": k, "weight": v["weight"], "ratio": v["ratio"], "points": v["points"]}
                                     for k, v in comp.items()]))
            st.caption(m["quality_breakdown"]["note"] + " For a rules-driven score use the Data Quality page.")


# ------------------------------------------------------------------ upload
with tab_upload:
    mode = st.radio("What do you want to do?", ["Register a new dataset", "Upload a new version of the active dataset"],
                    horizontal=True, disabled=not datasets, key="up_mode") if datasets else "Register a new dataset"
    file = st.file_uploader("CSV or Excel file", type=["csv", "tsv", "txt", "xlsx"], key="up_file")
    if file is not None:
        content = file.getvalue()
        sheet = None
        try:
            info = c.inspect_file(file.name, content)
            if len(info.get("sheet_names") or []) > 1:
                sheet = st.selectbox("Excel sheet", info["sheet_names"], key="up_sheet")
                info = c.inspect_file(file.name, content, sheet)
        except ApiError as exc:
            ui.show_error(exc)
            st.stop()
        st.success(f"Parsed {info['row_count']:,} rows × {info['column_count']} columns.")
        for w in info["warnings"]:
            ui.callout("warn", w)
        ui.show_df(pd.DataFrame(info["preview"]))
        with st.expander("Inferred column types"):
            ui.show_df(pd.DataFrame(info["columns"]))
        synthetic = st.checkbox("This is synthetic / demonstration data", key="up_syn")
        notes = st.text_input("Notes (optional)", key="up_notes")
        if mode.startswith("Register"):
            name = st.text_input("Dataset name", value=pathlib.Path(file.name).stem, key="up_name")
            description = st.text_area("Description (optional)", key="up_desc")
            c1, c2 = st.columns(2)
            owner = c1.text_input("Owner (optional)", key="up_owner")
            project = c2.text_input("Project (optional)", key="up_proj")
            if st.button("Register dataset", type="primary", disabled=not name.strip()):
                try:
                    created = c.create_dataset(name=name.strip(), filename=file.name, content=content,
                                               description=description, owner=owner, project=project,
                                               sheet_name=sheet, is_synthetic=synthetic, notes=notes)
                except ApiError as exc:
                    ui.show_error(exc)
                else:
                    state.select_dataset_by_name(created["name"])
                    st.rerun()
        else:
            ds = state.current_dataset()
            st.info(f"The file will become version {ds['latest_version_number'] + 1} of **{ds['name']}**.")
            if st.button("Upload version", type="primary"):
                try:
                    v = c.add_version(ds["id"], filename=file.name, content=content, sheet_name=sheet, notes=notes,
                                      is_synthetic=synthetic or None)
                except ApiError as exc:
                    ui.show_error(exc)
                else:
                    st.success(f"Uploaded as version {v['version_number']}.")
                    st.rerun()


# -------------------------------------------------------------------- demo
with tab_demo:
    st.markdown("Everything here is **synthetic**, generated deterministically for demonstration "
                "(see `data/sample/manifest.json`).")
    if not SAMPLE_DIR.exists():
        ui.callout("warn", f"Sample files not found at {SAMPLE_DIR}. Run `python scripts/generate_sample_data.py`.")
        st.stop()
    existing = {d["name"]: d for d in datasets}
    retail = existing.get("Retail Transactions")
    has_v2 = bool(retail) and any(v["original_filename"] == "retail_transactions_v2.csv" for v in
                                  state.api(c.get_dataset, retail["id"])["versions"])
    ui.kpi_cards([
        {"label": "Retail Transactions", "value": "loaded" if retail else "missing", "color": "#22c55e" if retail else "#64748b"},
        {"label": "Customer Master", "value": "loaded" if "Customer Master" in existing else "missing",
         "color": "#22c55e" if "Customer Master" in existing else "#64748b"},
        {"label": "Modified v2", "value": "loaded" if has_v2 else "not added", "color": "#22c55e" if has_v2 else "#64748b"}])

    st.markdown("**1 · Load the clean datasets**")
    if st.button("Load Retail Transactions v1 + Customer Master", disabled=bool(retail and "Customer Master" in existing)):
        try:
            for name, fname, proj in (("Retail Transactions", "retail_transactions_v1.csv", "Retail Analytics"),
                                      ("Customer Master", "customers_clean.csv", "Retail Analytics")):
                if name not in existing:
                    c.create_dataset(name=name, filename=fname, content=(SAMPLE_DIR / fname).read_bytes(), project=proj,
                                     owner="demo", is_synthetic=True, description=f"SYNTHETIC {name} for the DataSentinel demo.")
        except ApiError as exc:
            ui.show_error(exc)
        else:
            state.select_dataset_by_name("Retail Transactions")
            st.rerun()

    st.markdown("**2 · Add rules and an approved baseline** (needs step 1)")
    st.caption("Learns starter rules from the clean v1, adds a revenue-formula rule and a customer-exists check, "
               "and approves v1 as the baseline.")
    if st.button("Configure demo rules + baseline", disabled=not retail):
        try:
            rid = retail["id"]
            suggested = c.suggest_rules(rid, 1, save=True)
            have = {r["name"] for r in c.list_rules(rid, include_global=False)}
            added = sum(1 for r in state.DEMO_RULES if r["name"] not in have and c.create_rule({"dataset_id": rid, **r}))
            if state.active_baseline_version(rid) is None:
                c.set_baseline(rid, 1, "Clean Q1 2025 synthetic baseline (demo)", "demo")
        except ApiError as exc:
            ui.show_error(exc)
        else:
            st.success(f"Saved {len(suggested['saved_rules'])} suggested rules and {added} demo rules; baseline is v1.")

    st.markdown("**3 · Add the modified version (known injected issues)**")
    if st.button("Add Retail Transactions v2", disabled=not retail or has_v2):
        try:
            c.add_version(retail["id"], filename="retail_transactions_v2.csv",
                          content=(SAMPLE_DIR / "retail_transactions_v2.csv").read_bytes(),
                          notes="Modified Q2 2025 file with injected issues.", is_synthetic=True)
        except ApiError as exc:
            ui.show_error(exc)
        else:
            st.rerun()
    st.caption("To start over completely, run `python scripts/reset_demo.py --yes` and refresh this page.")
