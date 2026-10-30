"""Design system: CSS tokens, badges, KPI cards, score rings, sparklines, callouts, cards.

All user-derived text is HTML-escaped. Every function from earlier versions keeps its signature;
v8 adds `card`, `section`, `empty_state`, `score_ring`, `sparkline`, `progress_bar`, `chip_row` and
richer `kpi_cards` (delta + sparkline).
"""

from __future__ import annotations

import html
from typing import Any, Sequence

import streamlit as st

from components import prefs, theme

SEVERITY_COLORS = {"critical": "#ef4444", "high": "#f97316", "medium": "#eab308", "low": "#38bdf8", "info": "#94a3b8"}
OUTCOME_COLORS = {"fail": "#ef4444", "warn": "#eab308", "pass": "#22c55e", "skipped": "#64748b", "info": "#94a3b8"}
STATUS_COLORS = {
    "healthy": "#22c55e", "stable": "#22c55e", "unchanged": "#22c55e", "degraded": "#eab308", "warn": "#eab308",
    "changed": "#eab308", "failing": "#ef4444", "removed": "#ef4444", "drifted": "#f97316", "added": "#38bdf8",
    "unknown": "#64748b", "not_evaluated": "#64748b", "insufficient_data": "#64748b",
    "open": "#f97316", "acknowledged": "#eab308", "resolved": "#22c55e", "ignored": "#64748b",
    "completed": "#22c55e", "running": "#38bdf8", "queued": "#94a3b8", "failed": "#ef4444",
}
LIKELIHOOD_COLORS = {"likely": "#f97316", "possible": "#38bdf8", "unlikely": "#64748b"}
ACCENT = "#2dd4bf"
VIOLET = "#a78bfa"

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
/*__VARS__*/
html, body, .stApp, .stMarkdown, button, input, textarea, select {font-family:'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif;}
/* icons are ligature fonts: they must keep their own family or the icon *names* show up as text over the labels */
[data-testid="stIconMaterial"], [class*="material-symbols"], .material-icons {font-family:'Material Symbols Rounded','Material Symbols Outlined' !important;
  font-feature-settings:'liga'; letter-spacing:normal; text-transform:none;}
.block-container {padding-top: 2rem; padding-bottom: 4rem; max-width: 1320px;}
h1, h2, h3 {letter-spacing: -0.015em;}
[data-testid="stAppViewContainer"] {background:
  radial-gradient(1100px 420px at 85% -120px, var(--ds-glow-a), transparent 60%),
  radial-gradient(900px 380px at -10% -80px, var(--ds-glow-b), transparent 55%), var(--ds-bg);}
[data-testid="stSidebarNav"] a, [data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] {border-radius:10px;}
[data-testid="stHeader"] {background:transparent;}

/* brand */
.ds-brand {display:flex; align-items:center; gap:.65rem; font-weight:800; font-size:1.3rem; padding:.2rem 0 .1rem 0; color:var(--ds-strong);}
.ds-brand svg {flex:none; filter:drop-shadow(0 0 10px rgba(45,212,191,.45));}
.ds-brand small {display:block; font-weight:500; font-size:.7rem; letter-spacing:.08em; color:var(--ds-faint); text-transform:uppercase; margin-top:-.15rem;}
.ds-side-alert {display:flex; align-items:center; gap:.55rem; padding:.55rem .75rem; border-radius:10px; font-size:.82rem; margin:.4rem 0 .2rem 0;
  border:1px solid color-mix(in srgb, var(--c) 45%, transparent); background:color-mix(in srgb, var(--c) 12%, transparent); color:var(--ds-text);}
.ds-side-alert b {color:var(--ds-strong);}
.ds-pulse {width:9px; height:9px; border-radius:50%; background:var(--c); box-shadow:0 0 0 0 var(--c); animation:ds-pulse 1.8s infinite; flex:none;}
@keyframes ds-pulse {0%{box-shadow:0 0 0 0 color-mix(in srgb, var(--c) 60%, transparent);} 70%{box-shadow:0 0 0 9px transparent;} 100%{box-shadow:0 0 0 0 transparent;}}

/* hero */
.ds-hero {position:relative; overflow:hidden; margin:0 0 1.2rem 0; padding:1.25rem 1.5rem; border-radius:18px; border:1px solid var(--ds-border);
  background:linear-gradient(135deg, rgba(45,212,191,.10) 0%, var(--ds-hero-mid) 38%, rgba(167,139,250,.09) 100%);}
.ds-hero::after {content:""; position:absolute; inset:0; background:radial-gradient(500px 160px at 100% 0%, rgba(45,212,191,.12), transparent 70%); pointer-events:none;}
.ds-hero h1 {margin:0; font-size:1.85rem; font-weight:800; color:var(--ds-strong); position:relative;}
.ds-hero p {margin:.3rem 0 0 0; color:var(--ds-muted); font-size:.98rem; position:relative; max-width:70ch;}
.ds-hero .ds-row {position:relative;}

/* sections */
.ds-section {display:flex; align-items:baseline; justify-content:space-between; gap:1rem; margin:1.6rem 0 .6rem 0;}
.ds-section h3 {margin:0; font-size:1.05rem; font-weight:700; color:var(--ds-strong);}
.ds-section span {color:var(--ds-muted); font-size:.85rem;}

/* KPI cards */
.ds-kpis {display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:.85rem; margin:.4rem 0 1.2rem 0;}
.ds-kpi {position:relative; overflow:hidden; background:linear-gradient(180deg, var(--ds-surface) 0%, var(--ds-surface-2) 100%);
  border:1px solid var(--ds-border); border-radius:var(--ds-radius); padding:.95rem 1.1rem; box-shadow:var(--ds-shadow);
  transition:border-color .15s ease, transform .15s ease;}
.ds-kpi:hover {border-color:color-mix(in srgb, var(--c,#2dd4bf) 55%, var(--ds-border)); transform:translateY(-1px);}
.ds-kpi::before {content:""; position:absolute; left:0; top:0; bottom:0; width:3px; background:var(--c,#2dd4bf);}
.ds-kpi-label {color:var(--ds-muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.07em; font-weight:600;}
.ds-kpi-value {font-size:1.9rem; font-weight:800; line-height:1.2; color:var(--ds-strong); letter-spacing:-.02em; margin-top:.15rem; display:flex; align-items:baseline; gap:.5rem;}
.ds-kpi-sub {color:var(--ds-muted); font-size:.8rem; margin-top:.1rem;}
.ds-delta {font-size:.78rem; font-weight:700; padding:.08rem .45rem; border-radius:999px; letter-spacing:0;
  color:color-mix(in srgb, var(--d) 70%, var(--ds-strong)); background:color-mix(in srgb, var(--d) 14%, transparent);}
.ds-kpi svg.ds-spark {position:absolute; right:.8rem; bottom:.7rem; opacity:.9;}

/* badges, chips, callouts */
.ds-badge {display:inline-block; padding:.12rem .55rem; border-radius:999px; font-size:.7rem; font-weight:700;
  letter-spacing:.04em; text-transform:uppercase; color:color-mix(in srgb, var(--c) 70%, var(--ds-strong)); background:color-mix(in srgb, var(--c) 16%, transparent);
  border:1px solid color-mix(in srgb, var(--c) 45%, transparent); white-space:nowrap;}
.ds-chip {display:inline-flex; align-items:center; gap:.4rem; padding:.2rem .65rem; border-radius:999px; font-size:.78rem;
  background:var(--ds-surface-2); border:1px solid var(--ds-border); color:var(--ds-muted); margin:.15rem .3rem .15rem 0;}
.ds-chip b {color:var(--ds-strong);} .ds-chip i {width:8px; height:8px; border-radius:50%; background:var(--c); display:inline-block;}
.ds-callout {border-radius:12px; padding:.75rem 1rem; margin:.5rem 0 1rem 0; font-size:.92rem; border:1px solid color-mix(in srgb, var(--c) 55%, transparent);
  background:color-mix(in srgb, var(--c) 9%, transparent); color:var(--ds-text); border-left:4px solid var(--c);}

/* cards */
.ds-card {background:var(--ds-surface); border:1px solid var(--ds-border); border-radius:var(--ds-radius); padding:1rem 1.15rem; margin-bottom:.8rem; box-shadow:var(--ds-shadow);}
.ds-card h4 {margin:0 0 .35rem 0; font-size:1rem; color:var(--ds-strong);}
.ds-muted {color:var(--ds-muted); font-size:.88rem;}
.ds-row {display:flex; flex-wrap:wrap; gap:.4rem; align-items:center; margin:.25rem 0;}
.ds-hyp {border-left:3px solid var(--c); padding:.4rem .8rem; margin:.45rem 0; background:var(--ds-surface-2); border-radius:0 8px 8px 0;}
.ds-hyp .why {color:var(--ds-muted); font-size:.85rem; margin-top:.2rem;}
div[class*="st-key-card_"] {background:var(--ds-surface); border:1px solid var(--ds-border) !important; border-radius:var(--ds-radius) !important;
  padding:.35rem .5rem .1rem .5rem; box-shadow:var(--ds-shadow);}
.ds-ctitle {font-size:.95rem; font-weight:700; color:var(--ds-strong); margin:.25rem 0 0 0;}
.ds-csub {font-size:.8rem; color:var(--ds-muted); margin:0 0 .2rem 0;}

/* dataset tiles (command centre) */
.ds-tiles {display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:.9rem;}
.ds-tile {position:relative; background:linear-gradient(180deg, var(--ds-surface) 0%, var(--ds-surface-2) 100%); border:1px solid var(--ds-border);
  border-radius:16px; padding:1rem 1.1rem; box-shadow:var(--ds-shadow); display:flex; gap:1rem; align-items:center;}
.ds-tile::before {content:""; position:absolute; left:0; top:14px; bottom:14px; width:3px; border-radius:0 3px 3px 0; background:var(--c);}
.ds-tile h4 {margin:0; font-size:1.02rem; color:var(--ds-strong);}
.ds-tile .meta {color:var(--ds-muted); font-size:.78rem; margin-top:.15rem;}
.ds-tile .stats {display:flex; gap:.9rem; flex-wrap:wrap; margin-top:.5rem; font-size:.78rem; color:var(--ds-muted);}
.ds-tile .stats b {color:var(--ds-strong); font-size:.95rem; display:block; line-height:1.1;}
.ds-ring text {fill:var(--ds-strong); font-weight:800;}

/* rows with bars */
.ds-bars {display:flex; flex-direction:column; gap:.35rem;}
.ds-bar-row {display:grid; grid-template-columns:minmax(120px,1.4fr) 3fr auto; align-items:center; gap:.8rem; padding:.45rem .7rem; border-radius:10px; background:var(--ds-surface-2);}
.ds-bar-row .n {font-size:.86rem; color:var(--ds-text); font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
.ds-bar-row .t {height:6px; background:var(--ds-border); border-radius:3px; overflow:hidden;}
.ds-bar-row .t i {display:block; height:100%; border-radius:3px; background:var(--c);}
.ds-bar-row .v {font-size:.85rem; font-weight:700; color:var(--ds-strong); min-width:2.5rem; text-align:right;}
.ds-progress {height:8px; background:var(--ds-border); border-radius:999px; overflow:hidden;}
.ds-progress i {display:block; height:100%; border-radius:999px; background:linear-gradient(90deg, var(--c), color-mix(in srgb, var(--c) 60%, white));}

/* empty state */
.ds-empty {text-align:center; padding:2.2rem 1rem; border:1px dashed var(--ds-border-hi); border-radius:16px; background:var(--ds-tint); margin:.8rem 0 1rem 0;}
.ds-empty .big {font-size:2rem; line-height:1; margin-bottom:.4rem;}
.ds-empty h4 {margin:.2rem 0; color:var(--ds-strong);} .ds-empty p {margin:0 auto; color:var(--ds-muted); max-width:56ch; font-size:.92rem;}
.ds-steps {display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:.8rem; margin:1rem 0;}
.ds-step {background:var(--ds-surface); border:1px solid var(--ds-border); border-radius:14px; padding:.9rem 1rem;}
.ds-step .num {display:inline-flex; width:26px; height:26px; align-items:center; justify-content:center; border-radius:50%; font-weight:800; font-size:.8rem;
  background:color-mix(in srgb, var(--c,#2dd4bf) 20%, transparent); color:var(--c,#2dd4bf); margin-bottom:.4rem;}
.ds-step.done .num {background:var(--c,#22c55e); color:#06231f;}
.ds-step b {color:var(--ds-strong); display:block;} .ds-step span {color:var(--ds-muted); font-size:.85rem;}

/* widgets */
div[data-testid="stTabs"] button {font-weight:600;}
div[data-baseweb="tab-list"] {gap:.25rem;}
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {border-radius:10px; font-weight:600; transition:transform .1s ease, box-shadow .15s ease;}
.stButton > button:hover, .stDownloadButton > button:hover {transform:translateY(-1px);}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {background:linear-gradient(135deg,#14b8a6,#2dd4bf); color:#04201c; border:none; box-shadow:0 6px 18px -8px rgba(45,212,191,.7);}
[data-testid="stDataFrame"] {border:1px solid var(--ds-border); border-radius:12px; overflow:hidden;}
[data-testid="stExpander"] {border:1px solid var(--ds-border) !important; border-radius:12px !important; background:var(--ds-tint);}
[data-testid="stMetric"] {background:var(--ds-surface); border:1px solid var(--ds-border); border-radius:12px; padding:.6rem .9rem;}
hr {border-color:var(--ds-border) !important;}
@media (max-width: 900px) {.ds-hero h1 {font-size:1.45rem;} .ds-bar-row {grid-template-columns:1fr 2fr auto;} .ds-tile {flex-direction:column; align-items:flex-start;}}
"""

LOGO_SVG = (
    '<svg width="34" height="34" viewBox="0 0 32 32" fill="none" aria-hidden="true">'
    '<defs><linearGradient id="dsg" x1="4" y1="2" x2="28" y2="30"><stop stop-color="#5eead4"/><stop offset="1" stop-color="#a78bfa"/></linearGradient></defs>'
    '<path d="M16 2.5 4.5 7v8.2c0 7 4.8 12.4 11.5 14.3 6.7-1.9 11.5-7.3 11.5-14.3V7L16 2.5Z" fill="url(#dsg)" fill-opacity=".16" stroke="url(#dsg)" stroke-width="1.8"/>'
    '<path d="M9.5 17.5h3l2-5 3 8 2-5h3" stroke="url(#dsg)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _color(value: str) -> str:
    """Only allow plain CSS colours inside style attributes (no quotes/semicolons/parens beyond rgb())."""
    v = str(value)
    return v if all(ch.isalnum() or ch in "#(),.% " for ch in v) else "#64748b"


def theme_mode() -> str:
    """'dark' or 'light' - what the sidebar switch is set to."""
    return theme.mode_from_state(st.session_state)


def inject_css() -> None:
    st.markdown(f"<style>{theme.build_css(theme_mode(), CSS, theme.native_theme())}</style>", unsafe_allow_html=True)


def theme_switch() -> None:
    """Dark grey / white switch. The choice is remembered (components/prefs.py) and restyles the whole app."""
    if theme.SESSION_KEY not in st.session_state:
        st.session_state[theme.SESSION_KEY] = prefs.get(theme.PREF_KEY, theme.DEFAULT) == "light"
    st.toggle("Light theme", key=theme.SESSION_KEY, help="Switch the whole app between dark grey and white.")
    prefs.set(theme.PREF_KEY, theme_mode())


# --------------------------------------------------------------------------- atoms
def badge(text: Any, color: str) -> str:
    return f'<span class="ds-badge" style="--c:{_color(color)}">{esc(text)}</span>'


def severity_badge(sev: str) -> str:
    return badge(sev, SEVERITY_COLORS.get(sev, "#94a3b8"))


def outcome_badge(outcome: str) -> str:
    return badge(outcome, OUTCOME_COLORS.get(outcome, "#94a3b8"))


def status_badge(status: str) -> str:
    return badge(str(status).replace("_", " "), STATUS_COLORS.get(status, "#94a3b8"))


def likelihood_badge(level: str) -> str:
    return badge(level, LIKELIHOOD_COLORS.get(level, "#94a3b8"))


def chip(label: str, value: Any, color: str = "#64748b") -> str:
    return f'<span class="ds-chip" style="--c:{_color(color)}"><i></i><b>{esc(value)}</b> {esc(label)}</span>'


def chip_row(chips: Sequence[tuple[str, Any, str]]) -> None:
    """Row of small pills: [(label, value, color), ...]."""
    st.markdown('<div style="margin:.3rem 0 .8rem 0">' + "".join(chip(*c) for c in chips) + "</div>", unsafe_allow_html=True)


def sparkline(values: Sequence[float], color: str = ACCENT, width: int = 96, height: int = 30, fill: bool = True) -> str:
    """Inline SVG sparkline. Returns '' when there are fewer than two points."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    pad = 3
    pts = [(pad + i * (width - 2 * pad) / (len(vals) - 1), pad + (height - 2 * pad) * (1 - (v - lo) / span)) for i, v in enumerate(vals)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f'<polygon points="{pad},{height - pad} {line} {width - pad},{height - pad}" fill="{_color(color)}" fill-opacity=".14"/>' if fill else ""
    lx, ly = pts[-1]
    return (f'<svg class="ds-spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-hidden="true">{area}'
            f'<polyline points="{line}" fill="none" stroke="{_color(color)}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.6" fill="{_color(color)}"/></svg>')


def score_ring(score: float | None, size: int = 84, color: str | None = None, label: str = "") -> str:
    """Circular score gauge (0-100) as inline SVG."""
    r = size / 2 - 7
    circ = 2 * 3.14159265 * r
    pct = 0.0 if score is None else max(0.0, min(100.0, float(score)))
    col = _color(color or ("#64748b" if score is None else "#22c55e" if pct >= 90 else "#eab308" if pct >= 70 else "#ef4444"))
    text = "–" if score is None else f"{pct:.0f}"
    c = size / 2
    return (f'<svg class="ds-ring" width="{size}" height="{size}" viewBox="0 0 {size} {size}" role="img" aria-label="score {text} {esc(label)}">'
            f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" style="stroke:var(--ds-border)" stroke-width="7"/>'
            f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{col}" stroke-width="7" stroke-linecap="round" '
            f'stroke-dasharray="{circ * pct / 100:.1f} {circ:.1f}" transform="rotate(-90 {c} {c})"/>'
            f'<text x="{c}" y="{c + size * 0.09:.1f}" text-anchor="middle" font-size="{size * 0.3:.0f}">{text}</text></svg>')


def progress_bar(pct: float, color: str = ACCENT) -> str:
    return f'<div class="ds-progress" style="--c:{_color(color)}"><i style="width:{max(0.0, min(100.0, pct)):.0f}%"></i></div>'


def bar_rows(rows: Sequence[tuple[str, float, str]], max_value: float | None = None, fmt: str = "{:,.0f}") -> None:
    """Horizontal labelled bars: [(label, value, color), ...] rendered as one HTML block."""
    if not rows:
        return
    top = max_value or max((v for _, v, _ in rows), default=1) or 1
    body = "".join(
        f'<div class="ds-bar-row" style="--c:{_color(col)}"><div class="n" title="{esc(name)}">{esc(name)}</div>'
        f'<div class="t"><i style="width:{max(2.0, 100 * v / top):.0f}%"></i></div><div class="v">{esc(fmt.format(v))}</div></div>'
        for name, v, col in rows)
    st.markdown(f'<div class="ds-bars">{body}</div>', unsafe_allow_html=True)


def delta_html(value: float | None, *, good_when: str = "up", digits: int = 1, suffix: str = "") -> str:
    """Coloured +/- pill. `good_when` is 'up' (score) or 'down' (failures)."""
    if value is None:
        return ""
    if abs(value) < 10 ** -digits / 2:
        return f'<span class="ds-delta" style="--d:#94a3b8">±0{suffix}</span>'
    good = (value > 0) == (good_when == "up")
    return f'<span class="ds-delta" style="--d:{"#22c55e" if good else "#ef4444"}">{"▲" if value > 0 else "▼"} {abs(value):.{digits}f}{suffix}</span>'


# ----------------------------------------------------------------------- composites
def page_header(title: str, subtitle: str = "", dataset: dict[str, Any] | None = None) -> None:
    chips = ""
    if dataset:
        chips = '<div class="ds-row" style="margin-top:.6rem">' + badge(dataset["name"], ACCENT)
        if dataset.get("is_synthetic"):
            chips += badge("synthetic demo data", VIOLET)
        if dataset.get("project"):
            chips += badge(dataset["project"], "#64748b")
        chips += "</div>"
    st.markdown(f'<div class="ds-hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p>{chips}</div>', unsafe_allow_html=True)


def section(title: str, hint: str = "") -> None:
    st.markdown(f'<div class="ds-section"><h3>{esc(title)}</h3><span>{esc(hint)}</span></div>', unsafe_allow_html=True)


def kpi_cards(items: list[dict[str, Any]]) -> None:
    """KPI grid. Item keys: label, value, sub, color, delta (HTML from delta_html), spark (list of numbers)."""
    cards = []
    for it in items:
        color = it.get("color", ACCENT)
        spark = sparkline(it["spark"], color) if it.get("spark") else ""
        cards.append(
            f'<div class="ds-kpi" style="--c:{_color(color)}"><div class="ds-kpi-label">{esc(it["label"])}</div>'
            f'<div class="ds-kpi-value">{esc(it["value"])}{it.get("delta", "")}</div>'
            f'<div class="ds-kpi-sub">{esc(it.get("sub", ""))}</div>{spark}</div>')
    st.markdown(f'<div class="ds-kpis">{"".join(cards)}</div>', unsafe_allow_html=True)


def callout(kind: str, text: str) -> None:
    color = {"info": "#38bdf8", "warn": "#eab308", "danger": "#ef4444", "success": "#22c55e"}.get(kind, "#38bdf8")
    st.markdown(f'<div class="ds-callout" style="--c:{color}">{esc(text)}</div>', unsafe_allow_html=True)


def card(key: str):
    """A bordered container styled as a surface card. `key` must be unique on the page."""
    try:
        return st.container(border=True, key=f"card_{key}")
    except TypeError:  # older Streamlit without container(key=...)
        return st.container(border=True)


def card_title(title: str, subtitle: str = "") -> None:
    st.markdown(f'<div class="ds-ctitle">{esc(title)}</div>' + (f'<div class="ds-csub">{esc(subtitle)}</div>' if subtitle else ""),
                unsafe_allow_html=True)


def empty_state(icon: str, title: str, text: str) -> None:
    st.markdown(f'<div class="ds-empty"><div class="big">{esc(icon)}</div><h4>{esc(title)}</h4><p>{esc(text)}</p></div>',
                unsafe_allow_html=True)


def steps(items: Sequence[tuple[str, str, bool]]) -> None:
    """Onboarding checklist: [(title, description, done), ...]."""
    body = "".join(
        f'<div class="ds-step{" done" if done else ""}" style="--c:{"#22c55e" if done else "#2dd4bf"}"><div class="num">{"✓" if done else i}</div>'
        f'<b>{esc(t)}</b><span>{esc(d)}</span></div>' for i, (t, d, done) in enumerate(items, 1))
    st.markdown(f'<div class="ds-steps">{body}</div>', unsafe_allow_html=True)


def show_error(err: Any) -> None:
    code = getattr(err, "code", "ERROR")
    if code == "API_UNREACHABLE":
        st.error(f"{err}")
        st.code("cd backend\nuvicorn app.asgi:app --port 8000", language="powershell")
    elif code == "UNAUTHORIZED":
        st.error(f"{err}  \nEnter the key in the sidebar under **Connection → API key**, or set `DS_API_KEY` for the frontend.")
    elif code == "FORBIDDEN":
        st.error(f"{err}  \nThis key is read-only; use a full-access key to make changes.")
    elif code == "RATE_LIMITED":
        st.warning(f"{err}")
    else:
        st.error(f"{err}  \n`{code}`")


def toast(message: str, icon: str | None = None) -> None:
    try:
        st.toast(message, icon=icon)
    except Exception:  # very old Streamlit / headless contexts
        pass


# ------------------------------------------------------------------------ formatting
def fmt_int(n: Any) -> str:
    return f"{int(n):,}" if n is not None else "–"


def fmt_pct(n: Any, digits: int = 2) -> str:
    return f"{float(n):.{digits}f}%" if n is not None else "–"


def fmt_score(n: Any) -> str:
    return "–" if n is None else f"{float(n):.1f}"


def show_chart(fig, key: str | None = None) -> None:
    try:
        st.plotly_chart(fig, width="stretch", key=key, theme=None)
    except TypeError:  # older Streamlit
        st.plotly_chart(fig, use_container_width=True, key=key, theme=None)


def show_df(data, **kwargs) -> None:
    try:
        st.dataframe(data, width="stretch", hide_index=True, **kwargs)
    except TypeError:
        st.dataframe(data, use_container_width=True, hide_index=True, **kwargs)
