"""Build the demo portfolio: 10 large synthetic datasets (5 versions each), rules, baselines, lineage and ~30 days
of monitoring history with incidents.

Usage (repo root):
    python scripts/seed_portfolio.py --yes              # wipes the DB, loads everything (several minutes at full size)
    python scripts/seed_portfolio.py --yes --quick      # ~20% of the rows, 6 runs per dataset (about a minute)
    python scripts/seed_portfolio.py --files-only       # only write the CSV files to data/sample/portfolio/
"""

import argparse
import sys

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.database.session import build_engine, build_session_factory
from app.demo import portfolio
from app.demo.portfolio_seed import seed_portfolio
from app.demo.seed import SAMPLE_DIR, reset_demo

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="Confirm that ALL existing DataSentinel data is deleted.")
    ap.add_argument("--quick", action="store_true", help="Smaller files and fewer runs.")
    ap.add_argument("--scale", type=float, default=None, help="Row-count multiplier (default 1.0, 0.2 with --quick).")
    ap.add_argument("--runs", type=int, default=None, help="Max backdated runs per dataset.")
    ap.add_argument("--days", type=int, default=30, help="History length in days.")
    ap.add_argument("--only", nargs="*", help="Dataset slugs, e.g. orders banking.")
    ap.add_argument("--files-only", action="store_true", help="Write CSV files only; do not touch the database.")
    a = ap.parse_args()
    scale = a.scale if a.scale is not None else (0.2 if a.quick else 1.0)
    runs = a.runs if a.runs is not None else (6 if a.quick else None)

    if a.files_only:
        m = portfolio.write_files(SAMPLE_DIR / "portfolio", scale=scale, slugs=a.only)
        print(f"Wrote {sum(len(v['files']) for v in m['datasets'].values())} files to {SAMPLE_DIR / 'portfolio'}")
        sys.exit(0)
    settings = get_settings()
    if not a.yes:
        print(f"This DELETES all DataSentinel data in {settings.database_url} and {settings.storage_dir}.\nRe-run with --yes.")
        sys.exit(1)
    reset_demo(settings, with_v2=True)
    engine = build_engine(settings.database_url)
    session = build_session_factory(engine)()
    try:
        print(f"Seeding portfolio (scale={scale}) ...")
        res = seed_portfolio(session, settings, SAMPLE_DIR, scale=scale, days=a.days, runs=runs, slugs=a.only)
    finally:
        session.close()
        engine.dispose()
    print(f"Done: {res['datasets']} datasets, {res['runs']} monitoring runs. Start the app and open Command Center.")
