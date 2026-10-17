"""Design system: CSS, badges, KPI cards, callouts. All user-derived text is HTML-escaped."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

SEVERITY_COLORS = {"critical": "#ef4444", "high": "#f97316", "medium": "#eab308", "low": "#38bdf8", "info": "#94a3b8"}
OUTCOME_COLORS = {"fail": "#ef4444", "warn": "#eab308", "pass": "#22c55e", "skipped": "#64748b", "info": "#94a3b8"}
STATUS_COLORS = {
    "healthy": "#22c55e", "stable": "#22c55e", "unchanged": "#22c55e", "degraded": "#eab308", "warn": "#eab308",
    "changed": "#eab308", "failing": "#ef4444", "removed": "#ef4444", "drifted": "#f97316", "added": "#38bdf8",
    "unknown": "#64748b", "not_evaluated": "#64748b", "insufficient_data": "#64748b",
}
LIKELIHOOD_COLORS = {"likely": "#f97316", "possible": "#38bdf8", "unlikely": "#64748b"}
ACCENT = "#2dd4bf"

CSS = """
.block-container {padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1280px;}
h1, h2, h3 {letter-spacing: -0.01em;}
[data-testid="stSidebar"] {border-right: 1px solid #1e293b;}
.ds-brand {display:flex; align-items:center; gap:.6rem; font-weight:700; font-size:1.25rem; padding:.2rem 0 .4rem 0;}
.ds-brand .dot {width:12px; height:12px; border-radius:50%; background:#2dd4bf; box-shadow:0 0 12px #2dd4bf88;}
.ds-hero {margin-bottom:1.1rem;}
.ds-hero h1 {margin:0; font-size:1.9rem;}
.ds-hero p {margin:.25rem 0 0 0; color:#94a3b8; font-size:.98rem;}
.ds-kpis {display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:.8rem; margin:.4rem 0 1.1rem 0;}
.ds-kpi {background:#111a2e; border:1px solid #1e293b; border-left:3px solid var(--c,#2dd4bf); border-radius:10px; padding:.8rem 1rem;}
.ds-kpi-label {color:#94a3b8; font-size:.75rem; text-transform:uppercase; letter-spacing:.06em;}
.ds-kpi-value {font-size:1.75rem; font-weight:700; line-height:1.25; color:#f1f5f9;}
.ds-kpi-sub {color:#94a3b8; font-size:.8rem;}
.ds-badge {display:inline-block; padding:.12rem .55rem; border-radius:999px; font-size:.72rem; font-weight:600;
  letter-spacing:.03em; text-transform:uppercase; color:var(--c); background:color-mix(in srgb, var(--c) 16%, transparent);
  border:1px solid color-mix(in srgb, var(--c) 45%, transparent); white-space:nowrap;}
.ds-callout {border-radius:10px; padding:.75rem 1rem; margin:.5rem 0 1rem 0; font-size:.92rem; border:1px solid var(--c);
  background:color-mix(in srgb, var(--c) 10%, transparent); color:#e2e8f0;}
.ds-card {background:#111a2e; border:1px solid #1e293b; border-radius:12px; padding:1rem 1.15rem; margin-bottom:.8rem;}
.ds-card h4 {margin:0 0 .35rem 0; font-size:1rem;}
.ds-muted {color:#94a3b8; font-size:.88rem;}
.ds-row {display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; margin:.25rem 0;}
.ds-hyp {border-left:3px solid var(--c); padding:.4rem .8rem; margin:.45rem 0; background:#0f1729; border-radius:0 8px 8px 0;}
.ds-hyp .why {color:#94a3b8; font-size:.85rem; margin-top:.2rem;}
div[data-testid="stTabs"] button {font-weight:600;}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def inject_css() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def badge(text: Any, color: str) -> str:
    return f'<span class="ds-badge" style="--c:{color}">{esc(text)}</span>'


def severity_badge(sev: str) -> str:
    return badge(sev, SEVERITY_COLORS.get(sev, "#94a3b8"))


def outcome_badge(outcome: str) -> str:
    return badge(outcome, OUTCOME_COLORS.get(outcome, "#94a3b8"))


def status_badge(status: str) -> str:
    return badge(str(status).replace("_", " "), STATUS_COLORS.get(status, "#94a3b8"))


def likelihood_badge(level: str) -> str:
    return badge(level, LIKELIHOOD_COLORS.get(level, "#94a3b8"))


def page_header(title: str, subtitle: str = "", dataset: dict[str, Any] | None = None) -> None:
    chips = ""
    if dataset:
        chips = '<div class="ds-row" style="margin-top:.5rem">' + badge(dataset["name"], ACCENT)
        if dataset.get("is_synthetic"):
            chips += badge("synthetic demo data", "#a78bfa")
        chips += "</div>"
    st.markdown(f'<div class="ds-hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p>{chips}</div>',
                unsafe_allow_html=True)


def kpi_cards(items: list[dict[str, Any]]) -> None:
    cards = "".join(
        f'<div class="ds-kpi" style="--c:{it.get("color", ACCENT)}"><div class="ds-kpi-label">{esc(it["label"])}</div>'
        f'<div class="ds-kpi-value">{esc(it["value"])}</div><div class="ds-kpi-sub">{esc(it.get("sub", ""))}</div></div>'
        for it in items)
    st.markdown(f'<div class="ds-kpis">{cards}</div>', unsafe_allow_html=True)


def callout(kind: str, text: str) -> None:
    color = {"info": "#38bdf8", "warn": "#eab308", "danger": "#ef4444", "success": "#22c55e"}.get(kind, "#38bdf8")
    st.markdown(f'<div class="ds-callout" style="--c:{color}">{esc(text)}</div>', unsafe_allow_html=True)


def show_error(err: Any) -> None:
    code = getattr(err, "code", "ERROR")
    if code == "API_UNREACHABLE":
        st.error(f"{err}")
        st.code("cd backend\nuvicorn app.asgi:app --port 8000", language="powershell")
    else:
        st.error(f"{err}  \n`{code}`")


def fmt_int(n: Any) -> str:
    return f"{int(n):,}" if n is not None else "–"


def fmt_pct(n: Any, digits: int = 2) -> str:
    return f"{float(n):.{digits}f}%" if n is not None else "–"


def show_chart(fig, key: str | None = None) -> None:
    try:
        st.plotly_chart(fig, width="stretch", key=key)
    except TypeError:  # older Streamlit
        st.plotly_chart(fig, use_container_width=True, key=key)


def show_df(data, **kwargs) -> None:
    try:
        st.dataframe(data, width="stretch", hide_index=True, **kwargs)
    except TypeError:
        st.dataframe(data, use_container_width=True, hide_index=True, **kwargs)
