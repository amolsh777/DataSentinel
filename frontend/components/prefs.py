"""Tiny persistent UI preferences (score target, staleness threshold). Stored as JSON; failures never break a page."""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any


def _path() -> pathlib.Path:
    env = os.environ.get("DS_UI_PREFS")
    return pathlib.Path(env) if env else pathlib.Path(__file__).resolve().parents[2] / "data" / "ui_prefs.json"


def load(path: pathlib.Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads((path or _path()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(key: str, default: Any = None, path: pathlib.Path | None = None) -> Any:
    return load(path).get(key, default)


def set(key: str, value: Any, path: pathlib.Path | None = None) -> bool:  # noqa: A001
    """Persist one preference. Returns False (silently) if the file cannot be written."""
    p = path or _path()
    data = load(p)
    if data.get(key) == value:
        return True
    data[key] = value
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False
