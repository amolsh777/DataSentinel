from components import theme


def test_palettes_have_same_keys():
    assert theme.DARK.keys() == theme.LIGHT.keys()


def test_light_is_white_and_dark_is_grey():
    assert theme.LIGHT["bg"] == "#ffffff"
    r, g, b = (int(theme.DARK["bg"][i:i + 2], 16) for i in (1, 3, 5))
    assert abs(r - g) < 8 and abs(g - b) < 8 and r < 60  # neutral dark grey, not blue


def test_mode_from_state():
    assert theme.mode_from_state({theme.SESSION_KEY: True}) == "light"
    assert theme.mode_from_state({}) == "dark"


def test_build_css_swaps_variables_and_keeps_import_first():
    css = "@import url(x);\n/*__VARS__*/\n.a{color:var(--ds-text)}"
    out = theme.build_css("light", css, native="dark")
    assert out.startswith("@import") and "--ds-bg:#ffffff" in out and "invert(1)" in out
    assert "invert(1)" not in theme.build_css("dark", css, native="dark")


def test_icon_font_not_overridden_by_text_font():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "components" / "ui.py").read_text(encoding="utf-8")
    assert 'html, body, [class*="st-"]' not in src and "Material Symbols Rounded" in src
