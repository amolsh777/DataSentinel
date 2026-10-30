"""Plotly figures with a consistent dark theme."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

from components.theme import tok
from components.ui import ACCENT, SEVERITY_COLORS

MUTED = "#64748b"


def _style(fig: go.Figure, height: int = 340, title: str | None = None) -> go.Figure:
    fig.update_layout(
        template="plotly_dark" if tok("bg") != "#ffffff" else "plotly_white", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=height,
        margin=dict(l=10, r=10, t=44 if title else 16, b=10), title=dict(text=title, x=0.01, font=dict(size=15, color=tok("strong"))),
        font=dict(family="Inter, system-ui, sans-serif", color=tok("text"), size=12),
        legend=dict(orientation="h", y=1.12, x=0, font=dict(size=11)),
        hoverlabel=dict(bgcolor=tok("surface"), bordercolor=tok("border-hi"), font_size=12))
    fig.update_xaxes(gridcolor=tok("grid"), zerolinecolor=tok("grid"), linecolor=tok("grid"))
    fig.update_yaxes(gridcolor=tok("grid"), zerolinecolor=tok("grid"), linecolor=tok("grid"))
    return fig


def _score_color(score: float) -> str:
    return "#22c55e" if score >= 90 else "#eab308" if score >= 75 else "#f97316" if score >= 60 else "#ef4444"


def score_gauge(score: float | None, threshold: float | None = None) -> go.Figure:
    value = 0 if score is None else score
    gauge: dict[str, Any] = {
        "axis": {"range": [0, 100], "tickcolor": MUTED}, "bar": {"color": _score_color(value), "thickness": 0.28},
        "bgcolor": tok("surface"), "borderwidth": 0}
    if threshold is not None:
        gauge["threshold"] = {"line": {"color": tok("strong"), "width": 3}, "thickness": 0.8, "value": threshold}
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
                             marker=dict(symbol="line-ns", size=26, line=dict(width=3, color=tok("strong")))))
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
    fig.update_layout(polar=dict(radialaxis=dict(range=[0, 100], gridcolor=tok("grid")), bgcolor="rgba(0,0,0,0)",
                                 angularaxis=dict(gridcolor=tok("grid"))))
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
    fig.add_vline(x=1, line_dash="dash", line_color=tok("strong"), annotation_text="warn threshold", annotation_position="top")
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
            marker=dict(size=sizes, color=colors, line=dict(color=tok("bg"), width=1.5)),
            hovertemplate="<b>%{text}</b><br>type: " + atype + "<extra></extra>"))

    fig = go.Figure(traces)
    fig.update_layout(showlegend=True, hovermode="closest")
    fig.update_xaxes(showticklabels=False, showgrid=False, zeroline=False)
    fig.update_yaxes(showticklabels=False, showgrid=False, zeroline=False)
    return _style(fig, 500)


# ───────────────────────────── v8: trend / fleet / comparison charts ──────────────────────────────
PURPLE = "#a78bfa"


def run_label(row: dict) -> str:
    return str(row.get("started_at") or "")[5:16].replace("T", " ")


def score_trend_with_target(rows: list[dict], target: float, anomalies: list[int] | None = None) -> go.Figure:
    """Score per run (oldest -> newest) with the SLA target line, a shaded healthy band and flagged outliers."""
    xs = [run_label(r) for r in rows]
    ys = [r.get("score") for r in rows]
    fig = go.Figure()
    fig.add_hrect(y0=target, y1=100, fillcolor="rgba(34,197,94,.07)", line_width=0)
    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines+markers", name="quality score", line=dict(color=ACCENT, width=2.5, shape="spline", smoothing=0.5),
        marker=dict(size=7, color=[_score_color(v) if v is not None else MUTED for v in ys], line=dict(color=tok("bg"), width=1.5)),
        fill="tozeroy", fillcolor="rgba(45,212,191,.07)", connectgaps=True,
        customdata=[[r.get("current_version"), r.get("failures", 0), r.get("drifted_features", 0)] for r in rows],
        hovertemplate="<b>%{x}</b><br>score %{y:.1f}<br>v%{customdata[0]} · %{customdata[1]} failures · %{customdata[2]} drifted<extra></extra>"))
    if anomalies:
        fig.add_trace(go.Scatter(
            x=[xs[i] for i in anomalies], y=[ys[i] for i in anomalies], mode="markers", name="unusual run",
            marker=dict(size=15, color="rgba(0,0,0,0)", line=dict(color="#f97316", width=2.5), symbol="circle-open"),
            hovertemplate="<b>%{x}</b><br>unusual vs. history: %{y:.1f}<extra></extra>"))
    fig.add_hline(y=target, line_dash="dash", line_color="#22c55e", line_width=1.2,
                  annotation_text=f"target {target:g}", annotation_position="bottom right", annotation_font=dict(color="#22c55e", size=11))
    fig.update_yaxes(range=[max(0, min([v for v in ys if v is not None] + [target]) - 15), 101], title="score")
    fig.update_xaxes(type="category", tickangle=-30 if len(xs) > 8 else 0)
    return _style(fig, 330)


def outcomes_per_run(rows: list[dict]) -> go.Figure:
    """Stacked failures / warnings per run plus drifted features as a line."""
    xs = [run_label(r) for r in rows]
    fig = go.Figure()
    fig.add_bar(x=xs, y=[r.get("failures", 0) for r in rows], name="failed checks", marker_color="#ef4444")
    fig.add_bar(x=xs, y=[r.get("warnings", 0) for r in rows], name="warnings", marker_color="#eab308")
    fig.add_trace(go.Scatter(x=xs, y=[r.get("drifted_features", 0) for r in rows], name="drifted features", mode="lines+markers",
                             line=dict(color=PURPLE, width=2), marker=dict(size=6)))
    fig.update_layout(barmode="stack", bargap=0.35)
    fig.update_xaxes(type="category", tickangle=-30 if len(xs) > 8 else 0)
    return _style(fig, 300)


def issue_type_bars(breakdown: dict[str, dict]) -> go.Figure:
    """Rows affected per rule type in one run (checks may overlap on the same rows, so this is an upper bound)."""
    names = list(breakdown)[::-1]
    fig = go.Figure(go.Bar(
        y=[n.replace("_", " ") for n in names], x=[breakdown[n]["affected_rows"] for n in names], orientation="h",
        marker=dict(color=[breakdown[n]["max_affected_pct"] for n in names], colorscale=[[0, "#38bdf8"], [0.5, "#eab308"], [1, "#ef4444"]],
                    cmin=0, cmax=max([breakdown[n]["max_affected_pct"] for n in names] + [10])),
        text=[f"{breakdown[n]['checks']} check(s)" for n in names], textposition="auto",
        hovertemplate="<b>%{y}</b><br>%{x:,} rows affected (sum over checks)<extra></extra>"))
    fig.update_xaxes(title="rows affected")
    return _style(fig, max(220, 46 * len(names) + 60))


def outcome_donut(counts: dict[str, int], centre: str = "") -> go.Figure:
    order = [k for k in ("pass", "warn", "fail", "skipped") if counts.get(k)]
    fig = go.Figure(go.Pie(labels=order, values=[counts[k] for k in order], hole=0.66, sort=False,
                           marker=dict(colors=[OUTCOME_COLORS_MAP[k] for k in order], line=dict(color=tok("bg"), width=2)),
                           textinfo="value", hovertemplate="<b>%{label}</b>: %{value} checks (%{percent})<extra></extra>"))
    if centre:
        fig.add_annotation(text=centre, x=0.5, y=0.5, showarrow=False, font=dict(size=22, color=tok("strong")))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.05, x=0.1))
    return _style(fig, 260)


OUTCOME_COLORS_MAP = {"pass": "#22c55e", "warn": "#eab308", "fail": "#ef4444", "skipped": "#64748b"}


def rule_type_heatmap(xs: list[str], ys: list[str], z: list[list]) -> go.Figure:
    """Rule type x run, coloured by the worst affected % (blank where the type was not evaluated)."""
    fig = go.Figure(go.Heatmap(
        z=z, x=xs, y=[y.replace("_", " ") for y in ys], xgap=3, ygap=3, hoverongaps=False,
        colorscale=[[0, tok("heat0")], [0.02, "#164e63"], [0.35, "#eab308"], [1, "#ef4444"]], zmin=0,
        colorbar=dict(title="% rows", thickness=10, len=0.9),
        hovertemplate="<b>%{y}</b><br>%{x}<br>worst check affects %{z:.2f}% of rows<extra></extra>"))
    fig.update_xaxes(type="category", tickangle=-30 if len(xs) > 8 else 0)
    return _style(fig, max(220, 38 * len(ys) + 90))


def severity_bars(counts: dict[str, int]) -> go.Figure:
    order = ["critical", "high", "medium", "low", "info"]
    fig = go.Figure(go.Bar(x=order, y=[counts.get(s, 0) for s in order], marker_color=[SEVERITY_COLORS[s] for s in order],
                           text=[counts.get(s, 0) for s in order], textposition="outside", cliponaxis=False,
                           hovertemplate="<b>%{x}</b>: %{y} incidents<extra></extra>"))
    fig.update_yaxes(range=[0, max(max(counts.values(), default=0) * 1.3, 4)], title="incidents")
    return _style(fig, 260)


def incident_timeline(incidents: list[dict]) -> go.Figure:
    """When incidents first appeared. Bubble size = occurrences, colour = severity."""
    fig = go.Figure()
    for sev in ("critical", "high", "medium", "low", "info"):
        sub = [i for i in incidents if i.get("severity") == sev and i.get("first_seen_at")]
        if not sub:
            continue
        fig.add_trace(go.Scatter(
            x=[i["first_seen_at"][:19] for i in sub], y=[i.get("incident_type", "?") for i in sub], mode="markers", name=sev,
            marker=dict(size=[min(34, 10 + 3 * (i.get("occurrence_count") or 1)) for i in sub], color=SEVERITY_COLORS[sev],
                        opacity=0.85, line=dict(color=tok("bg"), width=1)),
            text=[i.get("title", "")[:70] for i in sub],
            hovertemplate="<b>%{text}</b><br>first seen %{x}<extra>" + sev + "</extra>"))
    fig.update_yaxes(title="")
    return _style(fig, 250)


def run_compare_bars(diff: dict) -> go.Figure:
    labels = ["new problems", "worsened", "still failing", "improved", "resolved"]
    keys = ["new_failures", "worsened", "unchanged_failing", "improved", "resolved"]
    colors = ["#ef4444", "#f97316", "#eab308", "#38bdf8", "#22c55e"]
    fig = go.Figure(go.Bar(x=labels, y=[len(diff[k]) for k in keys], marker_color=colors,
                           text=[len(diff[k]) for k in keys], textposition="outside", cliponaxis=False))
    fig.update_yaxes(range=[0, max([len(diff[k]) for k in keys] + [3]) * 1.3], title="rules")
    return _style(fig, 250)


def fleet_score_bars(rows: list[dict]) -> go.Figure:
    """Latest score per dataset, sorted, with the 90 / 70 reference lines."""
    rows = sorted([r for r in rows if r.get("score") is not None], key=lambda r: r["score"])
    fig = go.Figure(go.Bar(y=[r["name"] for r in rows], x=[r["score"] for r in rows], orientation="h",
                           marker_color=[_score_color(r["score"]) for r in rows], text=[f"{r['score']:.1f}" for r in rows],
                           textposition="outside", cliponaxis=False, hovertemplate="<b>%{y}</b>: %{x:.1f}<extra></extra>"))
    fig.add_vline(x=90, line_dash="dot", line_color="#22c55e", line_width=1)
    fig.add_vline(x=70, line_dash="dot", line_color="#eab308", line_width=1)
    fig.update_xaxes(range=[0, 108], title="latest monitoring score")
    return _style(fig, max(200, 52 * len(rows) + 70))
