"""Deterministic SYNTHETIC demonstration datasets.

Every file produced here is synthetic and labelled as such in ``manifest.json``.
Each issue injected into version 2 is listed with the column it affects and the
module expected to detect it, so tests and the live demo can rely on it.

Files written to ``data/sample/``:
- retail_transactions_v1.csv          clean baseline (Q1 2025)
- retail_transactions_v1b_stable.csv  next quarter, same distributions (should show no meaningful drift)
- retail_transactions_v2.csv          next quarter with intentional issues
- customers_clean.csv                 clean customer master (referential-integrity target)
- retail_transactions_sample.xlsx     2-sheet workbook (README + Transactions) for sheet selection
- lineage_demo.json                   Transactions -> Daily Revenue -> Monthly Revenue KPI -> Executive Dashboard
- manifest.json                       machine-readable description of all of the above
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

N_CUSTOMERS = 800
N_ROWS = 5000

CATEGORIES = ["Electronics", "Clothing", "Home", "Beauty", "Sports", "Books"]
CATEGORY_P = [0.22, 0.25, 0.18, 0.12, 0.13, 0.10]
# log-normal (mu, sigma) of unit price in INR per category
PRICE_PARAMS = {
    "Electronics": (8.0, 0.5),
    "Clothing": (6.8, 0.5),
    "Home": (7.2, 0.5),
    "Beauty": (6.2, 0.5),
    "Sports": (7.0, 0.5),
    "Books": (5.8, 0.4),
    "Toys": (6.5, 0.5),
    "Grocery": (5.5, 0.4),
}
PAYMENTS = ["UPI", "Credit Card", "Debit Card", "Cash", "Net Banking"]
PAYMENT_P = [0.38, 0.22, 0.18, 0.12, 0.10]
REGIONS = ["North", "South", "East", "West"]
REGION_P = [0.27, 0.30, 0.20, 0.23]
DISCOUNTS = [0, 5, 10, 15, 20]
DISCOUNT_P = [0.50, 0.20, 0.15, 0.10, 0.05]

V2_INJECTED_ISSUES: list[dict[str, Any]] = [
    {"id": "ISS-01", "type": "unexpected_column", "columns": ["loyalty_tier"],
     "description": "New column not present in the baseline schema.",
     "expected_detection": "schema drift: added column"},
    {"id": "ISS-02", "type": "removed_column", "columns": ["discount_pct"],
     "description": "Column present in the baseline has been removed.",
     "expected_detection": "schema drift: removed column (breaks Daily Revenue lineage)"},
    {"id": "ISS-03", "type": "type_change", "columns": ["quantity"],
     "description": "About 2% of quantity values are the string 'unknown'; pandas dtype becomes object.",
     "expected_detection": "schema drift: dtype change; quality: type mismatch / invalid values"},
    {"id": "ISS-04", "type": "missingness_increase", "columns": ["customer_age", "payment_method"],
     "description": "customer_age missing rate 1% -> 18%; payment_method 0% -> 8%.",
     "expected_detection": "quality: missing-value threshold; drift: missingness change"},
    {"id": "ISS-05", "type": "numeric_shift", "columns": ["unit_price", "revenue", "customer_age"],
     "description": ("unit_price x1.30 within every category (revenue follows); customer_age mean "
                     "35 -> 43. The overall unit_price median rises less (~14%) because the new "
                     "categories in ISS-06 are cheaper, so compare per category when explaining it."),
     "expected_detection": "statistical drift: PSI / KS / Wasserstein"},
    {"id": "ISS-06", "type": "unseen_categories", "columns": ["product_category", "region"],
     "description": "New categories 'Toys' (~8%), 'Grocery' (~7%) and region 'Central' (~6%).",
     "expected_detection": "drift: unseen categories, JS divergence; quality: invalid categorical values"},
    {"id": "ISS-07", "type": "duplicate_rows", "columns": ["*"],
     "description": "150 exact duplicate rows appended (duplicate transaction_id values).",
     "expected_detection": "quality: duplicate rows; unique-key violation on transaction_id"},
    {"id": "ISS-08", "type": "business_rule_violation", "columns": ["revenue"],
     "description": "30 rows have negative revenue.",
     "expected_detection": "quality: numeric range / business rule revenue >= 0"},
    {"id": "ISS-09", "type": "referential_integrity", "columns": ["customer_id"],
     "description": "About 3% of rows reference customer IDs absent from customers_clean.csv.",
     "expected_detection": "quality: referential integrity vs customers_clean.customer_id"},
]


def _customer_ids(rng: np.random.Generator, n: int, unknown_share: float = 0.0) -> np.ndarray:
    known = np.array([f"CUST-{i:04d}" for i in range(1, N_CUSTOMERS + 1)])
    ids = rng.choice(known, size=n)
    if unknown_share:
        mask = rng.random(n) < unknown_share
        ids[mask] = [f"CUST-{9000 + int(k):04d}" for k in rng.integers(0, 500, mask.sum())]
    return ids


def _transactions(
    seed: int,
    *,
    start: str,
    id_offset: int,
    categories: list[str] = CATEGORIES,
    category_p: list[float] = CATEGORY_P,
    regions: list[str] = REGIONS,
    region_p: list[float] = REGION_P,
    price_multiplier: float = 1.0,
    age_mean: float = 35.0,
    age_sd: float = 10.0,
    age_missing: float = 0.01,
    unknown_customer_share: float = 0.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = N_ROWS
    dates = pd.Timestamp(start) + pd.to_timedelta(rng.integers(0, 90, n), unit="D")
    category = rng.choice(categories, size=n, p=category_p)
    mu = np.array([PRICE_PARAMS[c][0] for c in category])
    sigma = np.array([PRICE_PARAMS[c][1] for c in category])
    unit_price = np.round(rng.lognormal(mu, sigma) * price_multiplier, 2)
    quantity = np.clip(rng.poisson(1.2, n) + 1, 1, 10)
    discount = rng.choice(DISCOUNTS, size=n, p=DISCOUNT_P)
    revenue = np.round(quantity * unit_price * (1 - discount / 100), 2)
    age = np.clip(np.round(rng.normal(age_mean, age_sd, n)), 18, 80)
    age = np.where(rng.random(n) < age_missing, np.nan, age)

    df = pd.DataFrame(
        {
            "transaction_date": dates,
            "customer_id": _customer_ids(rng, n, unknown_customer_share),
            "product_category": category,
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_pct": discount,
            "revenue": revenue,
            "payment_method": rng.choice(PAYMENTS, size=n, p=PAYMENT_P),
            "region": rng.choice(regions, size=n, p=region_p),
            "customer_age": age,
            "is_returned": rng.random(n) < 0.05,
        }
    )
    df = df.sort_values("transaction_date", kind="stable").reset_index(drop=True)
    df.insert(0, "transaction_id", [f"TXN-{id_offset + i:06d}" for i in range(1, n + 1)])
    df["transaction_date"] = df["transaction_date"].dt.strftime("%Y-%m-%d")
    df["customer_age"] = df["customer_age"].astype("Int64")
    return df


def build_v1() -> pd.DataFrame:
    return _transactions(42, start="2025-01-01", id_offset=0)


def build_v1b_stable() -> pd.DataFrame:
    return _transactions(44, start="2025-04-01", id_offset=200_000)


def build_v2() -> pd.DataFrame:
    """Version 2 with every issue in ``V2_INJECTED_ISSUES`` injected deterministically."""
    cats = CATEGORIES + ["Toys", "Grocery"]
    base_p = np.array(CATEGORY_P) * 0.85
    cat_p = list(base_p / base_p.sum() * 0.85) + [0.08, 0.07]
    regions = REGIONS + ["Central"]
    reg_p = list(np.array(REGION_P) * 0.94) + [0.06]
    df = _transactions(
        43,
        start="2025-04-01",
        id_offset=100_000,
        categories=cats,
        category_p=cat_p,
        regions=regions,
        region_p=reg_p,
        price_multiplier=1.30,                          # ISS-05
        age_mean=43.0, age_sd=11.0, age_missing=0.18,   # ISS-04 / ISS-05
        unknown_customer_share=0.03,                    # ISS-09
    )
    rng = np.random.default_rng(4300)
    n = len(df)

    pay = df["payment_method"].astype(object)
    pay[rng.random(n) < 0.08] = np.nan                  # ISS-04
    df["payment_method"] = pay

    qty = df["quantity"].astype(object)
    qty[rng.random(n) < 0.02] = "unknown"               # ISS-03
    df["quantity"] = qty

    neg_idx = rng.choice(n, size=30, replace=False)     # ISS-08
    df.loc[neg_idx, "revenue"] = -df.loc[neg_idx, "revenue"].abs()

    df["loyalty_tier"] = rng.choice(                    # ISS-01
        ["Bronze", "Silver", "Gold", "Platinum"], size=n, p=[0.5, 0.3, 0.15, 0.05]
    )
    df = df.drop(columns=["discount_pct"])              # ISS-02

    dupes = df.iloc[rng.choice(n, size=150, replace=False)]  # ISS-07
    df = pd.concat([df, dupes], ignore_index=True)
    df = df.sort_values("transaction_date", kind="stable").reset_index(drop=True)
    return df


def build_customers() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = N_CUSTOMERS
    signup = pd.Timestamp("2022-01-01") + pd.to_timedelta(rng.integers(0, 1000, n), unit="D")
    return pd.DataFrame(
        {
            "customer_id": [f"CUST-{i:04d}" for i in range(1, n + 1)],
            "signup_date": signup.strftime("%Y-%m-%d"),
            "city": rng.choice(
                ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune", "Kolkata"], size=n
            ),
            "segment": rng.choice(["Consumer", "Small Business", "Corporate"], size=n, p=[0.7, 0.2, 0.1]),
            "lifetime_orders": rng.poisson(6, n) + 1,
        }
    )


LINEAGE_DEMO: dict[str, Any] = {
    "synthetic": True,
    "assets": [
        {"name": "Retail Transactions", "asset_type": "dataset", "dataset_name": "Retail Transactions",
         "columns": ["transaction_id", "transaction_date", "quantity", "unit_price", "discount_pct",
                     "revenue", "product_category", "region"]},
        {"name": "Customer Master", "asset_type": "dataset", "dataset_name": "Customer Master",
         "columns": ["customer_id", "segment"]},
        {"name": "Daily Revenue", "asset_type": "transformation",
         "description": "SUM(revenue) and AVG(discount_pct) by transaction_date and region."},
        {"name": "Monthly Revenue KPI", "asset_type": "metric",
         "description": "Monthly revenue total and growth vs previous month."},
        {"name": "Executive Dashboard", "asset_type": "report",
         "description": "Leadership dashboard with revenue and category mix tiles."},
        {"name": "Category Mix Report", "asset_type": "report",
         "description": "Share of revenue per product_category."},
        {"name": "Churn Model Features", "asset_type": "ml_feature",
         "description": "customer_age, segment and order frequency used by a churn model."},
    ],
    "edges": [
        {"source": "Retail Transactions", "target": "Daily Revenue",
         "source_columns": ["transaction_date", "revenue", "discount_pct", "region"], "kind": "known"},
        {"source": "Daily Revenue", "target": "Monthly Revenue KPI", "source_columns": [], "kind": "known"},
        {"source": "Monthly Revenue KPI", "target": "Executive Dashboard", "source_columns": [], "kind": "known"},
        {"source": "Retail Transactions", "target": "Category Mix Report",
         "source_columns": ["product_category", "revenue"], "kind": "known"},
        {"source": "Category Mix Report", "target": "Executive Dashboard", "source_columns": [], "kind": "inferred"},
        {"source": "Retail Transactions", "target": "Churn Model Features",
         "source_columns": ["customer_id", "customer_age"], "kind": "known"},
        {"source": "Customer Master", "target": "Churn Model Features",
         "source_columns": ["customer_id", "segment"], "kind": "known"},
    ],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate_all(output_dir: Path) -> dict[str, Any]:
    """Write all synthetic files and the manifest; return the manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        "retail_transactions_v1.csv": (build_v1(), "Clean baseline, Q1 2025.", []),
        "retail_transactions_v1b_stable.csv": (
            build_v1b_stable(),
            "Q2 2025 drawn from the same distributions as v1; expect no meaningful drift "
            "(transaction_date range and transaction_id values differ by design).",
            [],
        ),
        "retail_transactions_v2.csv": (
            build_v2(), "Q2 2025 with intentionally injected issues.", V2_INJECTED_ISSUES
        ),
        "customers_clean.csv": (build_customers(), "Clean customer master.", []),
    }
    manifest: dict[str, Any] = {
        "synthetic": True,
        "notice": "All files in this directory are SYNTHETIC demonstration data.",
        "files": {},
    }
    for filename, (df, description, issues) in frames.items():
        path = output_dir / filename
        df.to_csv(path, index=False, lineterminator="\n")
        manifest["files"][filename] = {
            "synthetic": True,
            "description": description,
            "rows": int(len(df)),
            "columns": [str(c) for c in df.columns],
            "sha256": _sha256(path),
            "injected_issues": issues,
        }

    xlsx = output_dir / "retail_transactions_sample.xlsx"
    readme = pd.DataFrame(
        {"field": ["notice", "source", "rows"],
         "value": ["SYNTHETIC demonstration data", "retail_transactions_v1.csv", "first 500 rows"]}
    )
    with pd.ExcelWriter(xlsx, engine="openpyxl") as writer:
        readme.to_excel(writer, sheet_name="README", index=False)
        frames["retail_transactions_v1.csv"][0].head(500).to_excel(
            writer, sheet_name="Transactions", index=False
        )
    manifest["files"][xlsx.name] = {
        "synthetic": True,
        "description": "Workbook with README and Transactions sheets (select 'Transactions').",
        "sheets": ["README", "Transactions"],
        "rows": 500,
        "injected_issues": [],
    }

    (output_dir / "lineage_demo.json").write_text(json.dumps(LINEAGE_DEMO, indent=2), encoding="utf-8")
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
