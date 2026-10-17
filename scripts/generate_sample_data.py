"""Generate the deterministic SYNTHETIC sample datasets into data/sample/.

Usage (repo root):  python scripts/generate_sample_data.py
"""

import _bootstrap  # noqa: F401

from app.demo.seed import SAMPLE_DIR
from app.demo.sample_data import generate_all

if __name__ == "__main__":
    manifest = generate_all(SAMPLE_DIR)
    for name, meta in manifest["files"].items():
        print(f"  {name:<40} rows={meta['rows']:<6} issues={len(meta['injected_issues'])}")
    print(f"Wrote synthetic samples to {SAMPLE_DIR}")
