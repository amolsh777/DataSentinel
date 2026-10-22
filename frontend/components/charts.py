"""Plotly figures with a consistent dark theme."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

from components.ui import ACCENT, SEVERITY_COLORS

MUTED = "#64748b"
GRID = "#1e293b"


def _style(fig: go.Figure, height: int = 340, title: str | None = None) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=height,
        margin=dict(l=10, r=10, t=44 if title else 16, b=10), title=dict(text=title, x=0.01, font=dict(size=15)),
        font=dict(family="Inter, system-ui, sans-serif", color="#cbd5e1"),
        legend=dict(orientation="h", y=1.12, x=0), hoverlabel=dict(bgcolor="#0f172a"))
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=GRID)
    return fig


def _score_color(score: float) -> str:
    return "#22c55e" if score >= 90 else "#eab308" if score >= 75 else "#f97316" if score >= 60 else "#ef4444"


def score_gauge(score: float | None, threshold: float | None = None) -> go.Figure:
    value = 0 if score is None else score
    gauge: dict[str, Any] = {
        "axis": {"range": [0, 100], "tickcolor": MUTED}, "bar": {"color": _score_color(value), "thickness": 0.28},
        "bgcolor": "#111a2e", "borderwidth": 0}
    if threshold is not None:
        gauge["threshold"] = {"line": {"color": "#f8fafc", "width": 3}, "thickness": 0.8, "value": threshold}
    fig = go.Figure(go.Indicator(mode="gauge+number", value=value, number={"font": {"size": 44}}, gauge=gauge))
    return _style(fig, 250)


def dimension_bars(dimensions: dict[str, dict[str, Any]]) -> go.Figure:
    rows = [(n, d) for n, d in dimensions.items() if d.get("score") is not None]
    names = [n.title() for n, _ in rows]
    scores = [d["score"] for _, d in rows]
    colors = [_score_color(s) for s in scores]
    fig = go.Figure(go.Bar(x=scores, y=names, orientation="h", marker_color=colors,
                           text=[f"{s:.1f}" for s in scores], textposition="inside", name="score"))
    fig.add_trace(go.Scatter(x=[d.get("threshold") for _, d in rows], y=names, mode="markers", name="minimum",
                             marker=dict(symbol="line-ns", size=26, line=dict(width=3, color="#f8fafc"))))
    fig.update_xaxes(range=[0, 100])
    fig.update_yaxes(autorange="reversed")
    return _style(fig, 60 + 52 * max(len(rows), 1))


def radar(dimensions: dict[str, dict[str, Any]]) -> go.Figure:
    rows = [(n.title(), d) for n, d in dimensions.items() if d.get("score") is not None]
    names = [n for n, _ in rows] + ([rows[0][0]] if rows else [])
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=[d["score"] for _, d in rows] + ([rows[0][1]["score"]] if rows else []),
                                  theta=names, fill="toself", name="score", line_color=ACCENT))
    fig.add_trace(go.Scatterpolar(r=[d.get("threshold") for _, d in rows] + ([rows[0][1].get("threshold")] if rows else []),
                                  theta=names, name="minimum", line=dict(color="#94a3b8", dash="dot")))
    fig.update_layout(polar=dict(radialaxis=dict(range=[0, 100], gridcolor=GRID), bgcolor="rgba(0,0,0,0)",
                                 angularaxis=dict(gridcolor=GRID)))
    return _style(fig, 330)


def score_trend(rows: list[dict[str, Any]]) -> go.Figure:
    labels = [r["label"] for r in rows]
    fig = go.Figure(go.Bar(x=labels, y=[r["score"] for r in rows], marker_color=[_score_color(r["score"]) for r in rows],
                           text=[f"{r['score']:.1f}" for r in rows], textposition="outside", name="overall"))
    fig.update_yaxes(range=[0, 105], title="overall score")
    return _style(fig, 300, "Overall score by version")


def primary_effect(f: dict[str, Any]) -> tuple[float, str] | None:
    """Effect size as a multiple of the warn threshold, so numeric and categorical features compare."""
    es, th = f.get("effect_sizes") or {}, f.get("thresholds") or {}
    cands: list[tuple[float, str]] = []

    def add(value: Any, key: str, label: str) -> None:
        warn = (th.get(key) or {}).get("warn") if isinstance(th.get(key), dict) else th.get(key)
        if value is not None and warn:
            cands.append((float(value) / float(warn), f"{label} {float(value):.3g}"))

    if f["kind"] in ("numeric", "datetime"):
        add(es.get("psi"), "psi", "PSI")
        add(es.get("wasserstein_normalized"), "wasserstein", "W/IQR")
    else:
        add(es.get("js_distance"), "js", "JS")
        add(es.get("unseen_share_pct"), "unseen_share_pct", "new cats %")
    return max(cands, key=lambda c: c[0]) if cands else None


def effect_size_chart(features: list[dict[str, Any]]) -> go.Figure:
    rows = [(f["name"], f["severity"], p) for f in features if f.get("status") in ("drifted", "stable")
            and (p := primary_effect(f))]
    rows.sort(key=lambda r: r[2][0])
    fig = go.Figure(go.Bar(
        x=[r[2][0] for r in rows], y=[r[0] for r in rows], orientation="h",
        marker_color=[SEVERITY_COLORS.get(r[1], MUTED) if r[2][0] >= 1 else MUTED for r in rows],
        text=[r[2][1] for r in rows], textposition="auto", name="effect size"))
    fig.add_vline(x=1, line_dash="dash", line_color="#f8fafc", annotation_text="warn threshold", annotation_position="top")
    fig.update_xaxes(title="effect size ÷ warn threshold (log scale)", type="log")
    return _style(fig, 80 + 34 * max(len(rows), 1), "Drift effect size by feature")


def _edge(v: float | None) -> str:
    if v is None:
        return ""
    if abs(v) > 1e17:  # nanoseconds since epoch (datetime columns)
        return str(pd.to_datetime(v).date())
    return f"{v:.3g}"


def psi_bins_chart(feature: dict[str, Any]) -> go.Figure:
    bins = (feature.get("psi") or {}).get("bins") or []
    labels = []
    for i, b in enumerate(bins):
        lo, hi = _edge(b["lower"]), _edge(b["upper"])
        labels.append(f"< {hi}" if not lo else f"≥ {lo}" if not hi else f"{lo} – {hi}")
    fig = go.Figure()
    fig.add_bar(x=labels, y=[b["reference_pct"] for b in bins], name="reference", marker_color=MUTED)
    fig.add_bar(x=labels, y=[b["current_pct"] for b in bins], name="current", marker_color=ACCENT)
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="% of values")
    fig.update_xaxes(title="bins from reference quantiles")
    return _style(fig, 330, f"{feature['name']}: reference vs current distribution")


def category_chart(name: str, ref_top: list[dict[str, Any]], cur_top: list[dict[str, Any]], top_n: int = 12) -> go.Figure:
    ref = {r["value"]: r["pct"] for r in ref_top}
    cur = {r["value"]: r["pct"] for r in cur_top}
    cats = sorted(set(ref) | set(cur), key=lambda c: -max(ref.get(c, 0), cur.get(c, 0)))[:top_n]
    fig = go.Figure()
    if cur_top:
        fig.add_bar(x=cats, y=[ref.get(c, 0) for c in cats], name="reference", marker_color=MUTED)
        fig.add_bar(x=cats, y=[cur.get(c, 0) for c in cats], name="current", marker_color=ACCENT)
    else:
        fig.add_bar(x=cats, y=[ref.get(c, 0) for c in cats], name="share", marker_color=ACCENT)
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="% of non-null values")
    return _style(fig, 330, f"{name}: category share")


def missingness_chart(features: list[dict[str, Any]]) -> go.Figure | None:
    rows = [(f["name"], f["missingness"]) for f in features if f.get("missingness")]
    rows = [r for r in rows if r[1]["reference_pct"] or r[1]["current_pct"]]
    rows.sort(key=lambda r: -abs(r[1]["delta_pp"]))
    rows = rows[:12]
    if not rows:
        return None
    fig = go.Figure()
    fig.add_bar(x=[r[0] for r in rows], y=[r[1]["reference_pct"] for r in rows], name="reference", marker_color=MUTED)
    fig.add_bar(x=[r[0] for r in rows], y=[r[1]["current_pct"] for r in rows], name="current", marker_color="#f97316")
    fig.update_layout(barmode="group")
    fig.update_yaxes(title="% missing")
    return _style(fig, 300, "Missing values: reference vs current")


def severity_donut(counts: dict[str, int]) -> go.Figure:
    order = [s for s in ("critical", "high", "medium", "low", "info") if counts.get(s)]
    fig = go.Figure(go.Pie(labels=order, values=[counts[s] for s in order], hole=0.62,
                           marker_colors=[SEVERITY_COLORS[s] for s in order], textinfo="value"))
    fig.update_layout(showlegend=True)
    return _style(fig, 240)


def run_history_chart(rows: list[dict]) -> go.Figure:
    """Score and failure count over monitoring runs."""
    labels = [f"v{r['current_version']}\n{r.get('started_at','')[:10]}" for r in rows]
    scores = [r["score"] for r in rows]
    failures = [r["failures"] for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=labels, y=scores, name="quality score", mode="lines+markers",
                             line=dict(color=ACCENT, width=2), marker=dict(size=7)))
    fig.add_trace(go.Bar(x=labels, y=failures, name="rule failures", yaxis="y2",
                         marker_color="#ef444460", opacity=0.6))
    fig.update_layout(
        yaxis=dict(title="quality score", range=[0, 105], side="left"),
        yaxis2=dict(title="rule failures", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.12))
    return _style(fig, 320, "Quality score and failures over time")


def network_plotly(assets: list[dict], edges: list[dict], highlight_ids: set[str] | None = None) -> go.Figure:
    """Simple force-directed lineage graph drawn with Plotly."""
    import math
    if not assets:
        return go.Figure()

    highlight_ids = highlight_ids or set()
    # Circular layout by asset type.
    TYPE_ORDER = ["dataset", "transformation", "metric", "report", "ml_feature"]
    by_type: dict[str, list] = {t: [] for t in TYPE_ORDER}
    for a in assets:
        by_type.setdefault(a["asset_type"], []).append(a)

    pos: dict[str, tuple[float, float]] = {}
    ring = 0
    for atype in TYPE_ORDER:
        group = by_type.get(atype, [])
        for i, a in enumerate(group):
            angle = 2 * math.pi * i / max(len(group), 1)
            r = 1 + ring * 0.7
            pos[a["id"]] = (r * math.cos(angle), r * math.sin(angle))
        if group:
            ring += 1

    TYPE_COLORS = {"dataset": "#2dd4bf", "transformation": "#38bdf8", "metric": "#a78bfa",
                   "report": "#f472b6", "ml_feature": "#fb923c"}
    id_map = {a["id"]: a for a in assets}

    # Edges.
    ex, ey = [], []
    for e in edges:
        if e["source_asset_id"] in pos and e["target_asset_id"] in pos:
            sx, sy = pos[e["source_asset_id"]]
            tx, ty = pos[e["target_asset_id"]]
            ex += [sx, tx, None]; ey += [sy, ty, None]

    traces = [go.Scatter(x=ex, y=ey, mode="lines",
                         line=dict(color="#334155", width=1.5),
                         hoverinfo="skip", name="edges")]

    for atype in TYPE_ORDER:
        group = by_type.get(atype, [])
        if not group:
            continue
        xs = [pos[a["id"]][0] for a in group if a["id"] in pos]
        ys = [pos[a["id"]][1] for a in group if a["id"] in pos]
        texts = [a["name"] for a in group if a["id"] in pos]
        sizes = [16 if a["id"] in highlight_ids else 11 for a in group if a["id"] in pos]
        colors = ["#f97316" if a["id"] in highlight_ids else TYPE_COLORS.get(atype, "#64748b")
                  for a in group if a["id"] in pos]
        traces.append(go.Scatter(
            x=xs, y=ys, mode="markers+text", text=texts,
            textposition="top center", name=atype,
            marker=dict(size=sizes, color=colors, line=dict(color="#0b1220", width=1.5)),
            hovertemplate="<b>%{text}</b><br>type: " + atype + "<extra></extra>"))

    fig = go.Figure(traces)
    fig.update_layout(showlegend=True, hovermode="closest")
    fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
    fig.update_yaxes(showticklabels=False, showgrid=False, zeroline=False)
    return _style(fig, 500)
