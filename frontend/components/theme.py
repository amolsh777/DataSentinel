"""Theme support: dark grey and white palettes, applied to the whole app (page, sidebar, widgets, charts).

Streamlit cannot switch its own theme at run time, so the app styles every surface itself through CSS
variables (``--ds-*``) and overrides Streamlit's widgets with them. ``.streamlit/config.toml`` keeps the
native base theme at *dark*; in light mode the few canvas/pre-styled widgets that Streamlit draws itself
(dataframes, code blocks, JSON viewer) are colour-inverted so they match.

The palette data and CSS builders are pure functions so they can be unit-tested without Streamlit.
"""

from __future__ import annotations

from typing import Any

DARK = {
    "bg": "#1b1c1f", "side": "#17181b", "surface": "#25262a", "surface-2": "#202125", "input": "#2b2c31",
    "border": "#36373d", "border-hi": "#484951", "text": "#e4e4e7", "strong": "#fafafa", "muted": "#a1a1aa",
    "faint": "#7c7c86", "accent": "#2dd4bf", "accent-text": "#2dd4bf", "hover": "rgba(255,255,255,.06)",
    "active": "rgba(45,212,191,.16)", "tint": "rgba(37,38,42,.65)", "hero-mid": "rgba(37,38,42,.92)",
    "shadow": "0 1px 2px rgba(0,0,0,.35), 0 8px 24px -12px rgba(0,0,0,.55)", "heat0": "#2a2b30", "grid": "#33343a",
    "glow-a": "rgba(45,212,191,.06)", "glow-b": "rgba(167,139,250,.05)",
}
LIGHT = {
    "bg": "#ffffff", "side": "#f6f7f9", "surface": "#ffffff", "surface-2": "#f6f7f9", "input": "#ffffff",
    "border": "#e1e4ea", "border-hi": "#c9ced8", "text": "#24272d", "strong": "#0d1117", "muted": "#5b6370",
    "faint": "#8b929e", "accent": "#14b8a6", "accent-text": "#0f766e", "hover": "rgba(15,23,42,.05)",
    "active": "rgba(13,148,136,.12)", "tint": "rgba(246,247,249,.9)", "hero-mid": "rgba(255,255,255,.92)",
    "shadow": "0 1px 2px rgba(15,23,42,.06), 0 8px 24px -14px rgba(15,23,42,.18)", "heat0": "#e6f3f3", "grid": "#e6e8ec",
    "glow-a": "rgba(20,184,166,.07)", "glow-b": "rgba(139,92,246,.05)",
}
PALETTES = {"dark": DARK, "light": LIGHT}
DEFAULT = "dark"
SESSION_KEY = "ds_light"          # session_state key of the toggle (True = light)
PREF_KEY = "theme"                # key in the persisted UI preferences ("light" / "dark")


def normalize(mode: Any) -> str:
    return "light" if str(mode).lower() == "light" else "dark"


def palette(mode: Any) -> dict[str, str]:
    return PALETTES[normalize(mode)]


def mode_from_state(state: Any) -> str:
    """Current mode from a session_state-like mapping (anything unreadable falls back to dark)."""
    try:
        return "light" if state.get(SESSION_KEY) else "dark"
    except Exception:  # noqa: BLE001 - no session (tests, scripts)
        return DEFAULT


def tok(name: str, mode: Any = None) -> str:
    """One palette value for the active theme (used by the Plotly figures)."""
    if mode is None:
        try:
            import streamlit as st
            mode = mode_from_state(st.session_state)
        except Exception:  # noqa: BLE001
            mode = DEFAULT
    return palette(mode)[name]


def root_css(mode: Any) -> str:
    pal = palette(mode)
    body = " ".join(f"--ds-{k}:{v};" for k, v in pal.items())
    return f":root {{{body} --ds-violet:#a78bfa; --ds-radius:14px; color-scheme:{normalize(mode)};}}"


# Streamlit widgets restyled with the variables above (works in both themes, so it does not depend on the
# base theme Streamlit was started with).
NATIVE_CSS = """
.stApp {background:var(--ds-bg); color:var(--ds-text);}
.stApp :is(h1,h2,h3,h4,h5,h6,p,li,label,summary,dt,dd) {color:var(--ds-text);}
.stApp :is(h1,h2,h3) {color:var(--ds-strong);}
[data-testid="stCaptionContainer"], .stApp small {color:var(--ds-muted);}
[data-testid="stHeader"] {background:transparent;} [data-testid="stToolbar"], [data-testid="stHeader"] button {color:var(--ds-muted);}
[data-testid="stSidebar"] {background:var(--ds-side); border-right:1px solid var(--ds-border);}
[data-testid="stSidebarHeader"] button, [data-testid="stSidebarCollapseButton"] button {color:var(--ds-muted);}
/* sidebar page list */
[data-testid="stSidebarNavLink"], [data-testid="stSidebarNavLink"] span {color:var(--ds-text) !important;}
[data-testid="stSidebarNavLink"]:hover {background:var(--ds-hover) !important;}
[data-testid="stSidebarNavLink"][aria-current="page"] {background:var(--ds-active) !important;}
[data-testid="stSidebarNavLink"][aria-current="page"], [data-testid="stSidebarNavLink"][aria-current="page"] span {color:var(--ds-strong) !important; font-weight:600;}
[data-testid="stNavSectionHeader"], [data-testid="stNavSectionHeader"] span {color:var(--ds-faint) !important;}
[data-testid="stSidebarNav"] button, [data-testid="stSidebarNavSeparator"] button {color:var(--ds-muted) !important;}
/* inputs */
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"], [data-baseweb="select"] > div,
[data-testid="stNumberInput"] button, [data-testid="stDateInput"] [data-baseweb="input"] {background:var(--ds-input) !important; border-color:var(--ds-border) !important;}
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea, [data-baseweb="select"] input {color:var(--ds-text) !important; -webkit-text-fill-color:var(--ds-text) !important; caret-color:var(--ds-text);}
[data-baseweb="select"] div, [data-baseweb="select"] span, [data-testid="stNumberInput"] button {color:var(--ds-text);}
[data-baseweb="select"] svg, [data-baseweb="input"] svg, [data-testid="stNumberInput"] svg {fill:var(--ds-muted);}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within, [data-baseweb="select"] > div:focus-within {border-color:var(--ds-accent) !important;}
::placeholder {color:var(--ds-faint) !important; -webkit-text-fill-color:var(--ds-faint) !important; opacity:1;}
[data-baseweb="tag"] {background:var(--ds-active) !important;} [data-baseweb="tag"] span {color:var(--ds-strong) !important;}
/* menus, popovers, tooltips (rendered outside the app container) */
[data-baseweb="popover"] > div, [data-baseweb="menu"], [data-baseweb="popover"] ul, [role="listbox"] {background:var(--ds-surface) !important; color:var(--ds-text) !important; border-color:var(--ds-border);}
[data-baseweb="popover"] li, [data-baseweb="menu"] li, [role="option"] {color:var(--ds-text) !important; background:transparent !important;}
[data-baseweb="popover"] li:hover, [role="option"]:hover, [role="option"][aria-selected="true"] {background:var(--ds-hover) !important;}
[data-testid="stTooltipContent"] {background:var(--ds-surface); color:var(--ds-text); border:1px solid var(--ds-border);}
[data-baseweb="calendar"], [data-baseweb="calendar"] * {background-color:var(--ds-surface); color:var(--ds-text);}
/* tabs */
button[data-baseweb="tab"] {color:var(--ds-muted) !important; background:transparent !important;}
button[data-baseweb="tab"][aria-selected="true"] {color:var(--ds-accent-text) !important;}
[data-baseweb="tab-border"] {background:var(--ds-border) !important;} [data-baseweb="tab-highlight"] {background:var(--ds-accent) !important;}
/* buttons */
.stButton > button[kind="secondary"], .stDownloadButton > button, .stFormSubmitButton > button[kind="secondary"],
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"], [data-testid="stFileUploader"] button {
  background:var(--ds-surface) !important; color:var(--ds-text) !important; border:1px solid var(--ds-border-hi) !important;}
.stButton > button[kind="secondary"]:hover, .stDownloadButton > button:hover, [data-testid="stBaseButton-secondary"]:hover {
  border-color:var(--ds-accent) !important; color:var(--ds-accent-text) !important;}
/* misc widgets */
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary * {color:var(--ds-text);}
[data-testid="stAlert"] {background:var(--ds-surface-2) !important; border:1px solid var(--ds-border); color:var(--ds-text);}
[data-testid="stAlert"] * {color:var(--ds-text);}
[data-testid="stFileUploaderDropzone"] {background:var(--ds-input) !important; border:1px dashed var(--ds-border-hi);}
[data-testid="stFileUploaderDropzone"] * {color:var(--ds-muted);}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] * {color:var(--ds-muted) !important;}
[data-testid="stMetricValue"], [data-testid="stMetricValue"] * {color:var(--ds-strong) !important;}
[data-testid="stSliderThumbValue"], [data-testid="stTickBarMin"], [data-testid="stTickBarMax"] {color:var(--ds-text) !important;}
[data-baseweb="checkbox"] > span:first-of-type, [data-baseweb="radio"] > div:first-of-type {border-color:var(--ds-border-hi) !important;}
[data-testid="stCode"] pre, [data-testid="stCode"] code {background:var(--ds-surface-2) !important;}
hr {border-color:var(--ds-border) !important;}
"""


def invert_css(mode: Any, native: str = DEFAULT) -> str:
    """Colour-invert widgets Streamlit draws itself (canvas dataframes, code, JSON) when the chosen theme
    differs from Streamlit's own base theme, so they never show dark-on-light or light-on-dark."""
    if normalize(mode) == normalize(native):
        return ""
    return ('[data-testid="stDataFrame"] canvas, [data-testid="stDataFrame"] [data-testid="stDataFrameResizable"], '
            '[data-testid="stJson"], [data-testid="stCode"] {filter:invert(1) hue-rotate(180deg);}')


def native_theme() -> str:
    """Streamlit's own base theme ('dark' from .streamlit/config.toml; detected from the browser when available)."""
    try:
        import streamlit as st
        t = getattr(getattr(st, "context", None), "theme", None)
        kind = getattr(t, "type", None)
        if kind in ("light", "dark"):
            return kind
        opt = st.get_option("theme.base")
        if opt in ("light", "dark"):
            return opt
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT


def build_css(mode: Any, component_css: str, native: str = DEFAULT) -> str:
    """Final stylesheet: the component CSS with its ``/*__VARS__*/`` marker replaced by the palette variables and
    the native-widget overrides (kept after the ``@import``), plus inversion rules for the active theme."""
    return component_css.replace("/*__VARS__*/", root_css(mode) + NATIVE_CSS) + invert_css(mode, native)
