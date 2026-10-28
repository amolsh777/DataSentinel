from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from components import prefs  # noqa: E402


def test_prefs_roundtrip_and_resilience():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "sub" / "p.json"
        assert prefs.get("target", 90, p) == 90
        assert prefs.set("target", 95, p) and prefs.get("target", 90, p) == 95
        p.write_text("{not json")
        assert prefs.load(p) == {} and prefs.get("x", "d", p) == "d"
        assert prefs.set("x", 1, p) and prefs.get("x", None, p) == 1
        assert prefs.set("k", 1, pathlib.Path("/proc/nope/p.json")) is False
