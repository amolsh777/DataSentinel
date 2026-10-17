"""Reset the demo: regenerate samples, wipe the database and stored files, re-register datasets.

Usage (repo root):
    python scripts/reset_demo.py --yes            # registers Retail Transactions v1 + Customer Master
    python scripts/reset_demo.py --yes --with-v2  # also uploads the modified v2
"""

import argparse
import sys

import _bootstrap  # noqa: F401

from app.core.config import get_settings
from app.demo.seed import reset_demo

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="Confirm deleting all DataSentinel data.")
    parser.add_argument("--with-v2", action="store_true", help="Also upload retail_transactions_v2.csv.")
    args = parser.parse_args()

    settings = get_settings()
    if not args.yes:
        print("This deletes ALL DataSentinel metadata and stored files:")
        print(f"  database: {settings.database_url}")
        print(f"  storage : {settings.storage_dir}")
        print("Re-run with --yes to confirm.")
        sys.exit(1)

    result = reset_demo(settings, with_v2=args.with_v2)
    print(f"Demo reset complete. Registered: {', '.join(result['registered'])}"
          f"{' (+ Retail Transactions v2)' if result['with_v2'] else ''}; "
          f"{result['rules']} validation rules configured for Retail Transactions.")
