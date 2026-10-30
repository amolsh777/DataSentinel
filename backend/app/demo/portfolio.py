"""Deterministic SYNTHETIC demo portfolio: 10 large datasets, each with a clean reference version and
four later versions with increasing / different problems (missing values, outliers, duplicates, unseen
categories, shifted distributions, invalid dates, schema changes).

Everything is generated from fixed seeds, so every run produces identical files. Nothing here is real data.

* :func:`generate_version` - one DataFrame for ``(dataset, version number)``.
* :func:`write_files`      - write every version to ``data/sample/portfolio/<slug>/vN_<label>.csv``.
* :data:`VERSION_PLAN`     - severity and label of each version (v1 = clean reference).
* :data:`HISTORY`          - which version each backdated monitoring run uses (see ``portfolio_seed.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

INTENSITY = 3.0

# version -> (label, severity 0..1).  v1 is the approved reference/baseline.
VERSION_PLAN: dict[int, tuple[str, float]] = {
    1: ("reference_clean", 0.0),
    2: ("mild_drift", 0.15),
    3: ("degraded", 0.45),
    4: ("severe_incident", 0.85),
    5: ("recovering", 0.22),
}

# Versions used by the backdated monitoring runs, oldest first. Different shapes make the trend charts
# interesting: improving, degrading, flaky, stable, recovering after an incident.
HISTORY: dict[str, list[int]] = {
    "improving": [4, 4, 3, 3, 5, 2, 5, 2, 1, 2, 1, 1],
    "degrading": [1, 1, 2, 1, 2, 2, 3, 2, 3, 3, 4, 4],
    "flaky": [1, 3, 1, 2, 4, 1, 5, 1, 3, 1, 2, 1],
    "stable": [1, 2, 1, 1, 2, 1, 1, 2, 1, 1, 2, 1],
    "incident_recovery": [1, 1, 2, 1, 4, 4, 3, 5, 5, 2, 1, 1],
}


@dataclass
class Spec:
    name: str
    slug: str
    project: str
    owner: str
    description: str
    rows: int
    make: Callable[[np.random.Generator, int], pd.DataFrame]
    ids: list[str]
    nums: list[str]
    cats: list[str]
    dates: list[str]
    rules: list[dict[str, Any]] = field(default_factory=list)
    history: str = "stable"
    downstream: list[tuple[str, str]] = field(default_factory=list)  # (asset name, asset type)


# ----------------------------------------------------------------------------------------- generators
def _dates(rng, n, start="2025-01-01", days=180):
    return (pd.Timestamp(start) + pd.to_timedelta(rng.integers(0, days, n), unit="D")).strftime("%Y-%m-%d")


def _ts(rng, n, start="2025-01-01", days=30):
    return (pd.Timestamp(start) + pd.to_timedelta(rng.integers(0, days * 86400, n), unit="s")).strftime("%Y-%m-%d %H:%M:%S")


def _pick(rng, n, values, p=None):
    return rng.choice(values, n, p=p)


def _orders(rng, n):
    q = rng.integers(1, 8, n)
    price = np.round(rng.lognormal(3.3, 0.55, n), 2)
    disc = _pick(rng, n, [0, 5, 10, 15, 20], [.55, .15, .15, .1, .05])
    return pd.DataFrame({
        "order_id": [f"ORD-{i:07d}" for i in range(n)], "customer_id": [f"C{rng.integers(1, 9000):05d}" for _ in range(n)],
        "order_date": _dates(rng, n), "country": _pick(rng, n, ["US", "GB", "DE", "FR", "IN", "BR", "JP"], [.35, .12, .1, .1, .15, .1, .08]),
        "category": _pick(rng, n, ["electronics", "fashion", "home", "grocery", "toys", "books"], [.2, .25, .2, .15, .1, .1]),
        "quantity": q, "unit_price": price, "discount_pct": disc, "total": np.round(q * price * (1 - disc / 100), 2),
        "status": _pick(rng, n, ["paid", "shipped", "refunded", "cancelled"], [.62, .3, .05, .03]),
        "payment_method": _pick(rng, n, ["card", "paypal", "upi", "bank_transfer"], [.55, .2, .15, .1])})


def _bank(rng, n):
    amt = np.round(rng.lognormal(3.6, 1.0, n), 2)
    return pd.DataFrame({
        "txn_id": [f"TX{i:09d}" for i in range(n)], "account_id": [f"AC{rng.integers(1, 12000):06d}" for _ in range(n)],
        "txn_ts": _ts(rng, n), "amount": amt, "currency": _pick(rng, n, ["USD", "EUR", "GBP", "INR"], [.5, .25, .15, .1]),
        "channel": _pick(rng, n, ["card", "online", "atm", "branch", "mobile"], [.35, .25, .15, .05, .2]),
        "merchant_category": _pick(rng, n, ["retail", "food", "travel", "utilities", "entertainment"], [.3, .25, .15, .2, .1]),
        "is_fraud": (rng.random(n) < 0.006).astype(int), "balance_after": np.round(rng.normal(5200, 2400, n).clip(5), 2)})


def _health(rng, n):
    age = rng.normal(46, 18, n).clip(0, 98).round().astype(int)
    stay = rng.gamma(2.0, 1.8, n).round(1).clip(0.2, 40)
    return pd.DataFrame({
        "visit_id": [f"V{i:08d}" for i in range(n)], "patient_id": [f"P{rng.integers(1, 15000):06d}" for _ in range(n)],
        "age": age, "gender": _pick(rng, n, ["F", "M", "X"], [.51, .48, .01]),
        "department": _pick(rng, n, ["cardiology", "orthopedics", "emergency", "pediatrics", "oncology"], [.2, .2, .3, .15, .15]),
        "admit_date": _dates(rng, n), "length_of_stay_days": stay,
        "diagnosis_code": _pick(rng, n, ["I10", "E11", "J45", "M54", "K21", "N39"]),
        "charges": np.round(stay * rng.normal(1800, 300, n).clip(400), 2),
        "insurance": _pick(rng, n, ["private", "public", "self_pay"], [.5, .4, .1])})


def _iot(rng, n):
    return pd.DataFrame({
        "reading_id": np.arange(n), "device_id": [f"D{rng.integers(1, 800):04d}" for _ in range(n)], "ts": _ts(rng, n, days=14),
        "temperature_c": np.round(rng.normal(22, 4, n), 2), "humidity_pct": np.round(rng.normal(48, 12, n).clip(5, 99), 1),
        "pressure_hpa": np.round(rng.normal(1013, 7, n), 1), "battery_pct": np.round(rng.uniform(8, 100, n), 1),
        "status": _pick(rng, n, ["ok", "low_battery", "offline"], [.93, .05, .02]),
        "firmware": _pick(rng, n, ["2.1.0", "2.1.1", "2.2.0"], [.2, .5, .3])})


def _hr(rng, n):
    grade = _pick(rng, n, ["G1", "G2", "G3", "G4", "G5"], [.3, .3, .2, .15, .05])
    base = pd.Series(grade).map({"G1": 38000, "G2": 52000, "G3": 70000, "G4": 95000, "G5": 140000}).to_numpy()
    return pd.DataFrame({
        "employee_id": [f"E{i:06d}" for i in range(n)], "department": _pick(rng, n, ["eng", "sales", "ops", "finance", "hr", "support"]),
        "hire_date": _dates(rng, n, "2015-01-01", 3600), "salary": np.round(base * rng.normal(1, .08, n), 0),
        "age": rng.normal(37, 9, n).clip(20, 66).round().astype(int), "grade": grade,
        "email": [f"user{i}@example.com" for i in range(n)], "remote": _pick(rng, n, ["yes", "no", "hybrid"], [.3, .3, .4])})


def _telecom(rng, n):
    tenure = rng.integers(1, 72, n)
    plan = _pick(rng, n, ["basic", "plus", "premium"], [.5, .35, .15])
    fee = pd.Series(plan).map({"basic": 25, "plus": 45, "premium": 80}).to_numpy() * rng.normal(1, .04, n)
    return pd.DataFrame({
        "customer_id": [f"T{i:07d}" for i in range(n)], "tenure_months": tenure, "plan": plan, "monthly_charge": np.round(fee, 2),
        "data_gb": np.round(rng.gamma(2.5, 4, n), 1), "support_calls": rng.poisson(1.2, n),
        "churned": (rng.random(n) < 0.14).astype(int), "region": _pick(rng, n, ["north", "south", "east", "west"])})


def _logistics(rng, n):
    w = np.round(rng.lognormal(2.2, .8, n), 2)
    ship = pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 150, n), unit="D")
    return pd.DataFrame({
        "shipment_id": [f"SH{i:08d}" for i in range(n)], "origin": _pick(rng, n, ["Mumbai", "Rotterdam", "Shenzhen", "Chicago", "Dubai"]),
        "destination": _pick(rng, n, ["London", "Berlin", "Austin", "Tokyo", "Lagos", "Sydney"]),
        "ship_date": ship.strftime("%Y-%m-%d"), "delivery_days": rng.gamma(4, 1.6, n).round(1).clip(1, 40),
        "weight_kg": w, "carrier": _pick(rng, n, ["DHL", "FedEx", "Maersk", "UPS"], [.3, .25, .25, .2]),
        "status": _pick(rng, n, ["delivered", "in_transit", "delayed", "lost"], [.8, .12, .075, .005]),
        "cost": np.round(w * rng.normal(3.2, .5, n).clip(1) + 12, 2)})


def _clicks(rng, n):
    return pd.DataFrame({
        "event_id": np.arange(n), "session_id": [f"S{rng.integers(1, 40000):06d}" for _ in range(n)],
        "user_id": [f"U{rng.integers(1, 25000):06d}" for _ in range(n)], "ts": _ts(rng, n, days=7),
        "page": _pick(rng, n, ["home", "search", "product", "cart", "checkout", "help"], [.25, .25, .25, .1, .05, .1]),
        "device": _pick(rng, n, ["mobile", "desktop", "tablet"], [.58, .37, .05]),
        "browser": _pick(rng, n, ["chrome", "safari", "firefox", "edge"], [.6, .2, .1, .1]),
        "duration_s": np.round(rng.gamma(1.8, 40, n), 1), "country": _pick(rng, n, ["US", "IN", "DE", "BR", "GB"], [.35, .25, .15, .1, .15]),
        "referrer": _pick(rng, n, ["organic", "paid", "email", "direct", "social"], [.35, .2, .1, .25, .1])})


def _claims(rng, n):
    amt = np.round(rng.lognormal(7.6, .9, n), 2)
    return pd.DataFrame({
        "claim_id": [f"CL{i:08d}" for i in range(n)], "policy_id": [f"POL{rng.integers(1, 20000):06d}" for _ in range(n)],
        "claim_date": _dates(rng, n), "claim_amount": amt,
        "claim_type": _pick(rng, n, ["auto", "home", "health", "travel"], [.4, .25, .25, .1]),
        "status": _pick(rng, n, ["approved", "pending", "rejected"], [.6, .27, .13]),
        "adjuster": [f"ADJ{rng.integers(1, 60):02d}" for _ in range(n)], "region": _pick(rng, n, ["NA", "EU", "APAC", "LATAM"]),
        "approved_amount": np.round(amt * rng.uniform(.5, 1, n), 2)})


def _energy(rng, n):
    return pd.DataFrame({
        "reading_id": np.arange(n), "meter_id": [f"M{rng.integers(1, 5000):05d}" for _ in range(n)], "reading_date": _dates(rng, n, days=120),
        "kwh": np.round(rng.gamma(3, 4.2, n), 2), "tariff": _pick(rng, n, ["peak", "off_peak", "standard"], [.3, .3, .4]),
        "region": _pick(rng, n, ["urban", "suburban", "rural"], [.5, .35, .15]), "voltage": np.round(rng.normal(230, 4, n), 1),
        "outage_flag": (rng.random(n) < 0.012).astype(int)})


def _rule(name, rule_type, severity, column=None, **params):
    return {"name": name, "rule_type": rule_type, "severity": severity, **({"column": column} if column else {}), "params": params}


SPECS: list[Spec] = [
    Spec("Orders Warehouse", "orders", "E-commerce", "growth-team", "SYNTHETIC e-commerce orders.", 60000, _orders,
         ["order_id"], ["unit_price", "total", "quantity"], ["country", "category", "status"], ["order_date"],
         [_rule("Order id unique", "unique", "high", "order_id"),
          _rule("Quantity in range", "numeric_range", "high", "quantity", min=1, max=50),
          _rule("Total matches price x qty", "business_rule", "high",
                expression="abs(total - quantity * unit_price * (1 - discount_pct / 100)) <= 0.01"),
          _rule("Status values", "allowed_values", "medium", "status", values=["paid", "shipped", "refunded", "cancelled"])],
         "degrading", [("Revenue Dashboard", "report"), ("Daily GMV", "metric")]),
    Spec("Banking Transactions", "banking", "Finance", "risk-team", "SYNTHETIC card and account transactions.", 80000, _bank,
         ["txn_id"], ["amount", "balance_after"], ["currency", "channel", "merchant_category"], ["txn_ts"],
         [_rule("Txn id unique", "unique", "critical", "txn_id"),
          _rule("Amount positive", "numeric_range", "critical", "amount", min=0.01, max=200000),
          _rule("Currency values", "allowed_values", "high", "currency", values=["USD", "EUR", "GBP", "INR"]),
          _rule("Balance not negative", "business_rule", "medium", expression="balance_after >= 0")],
         "incident_recovery", [("Fraud Model Features", "ml_feature"), ("Risk Report", "report")]),
    Spec("Patient Visits", "health", "Healthcare", "analytics-team", "SYNTHETIC hospital visits.", 40000, _health,
         ["visit_id"], ["age", "length_of_stay_days", "charges"], ["gender", "department", "insurance"], ["admit_date"],
         [_rule("Visit id unique", "unique", "high", "visit_id"),
          _rule("Age plausible", "numeric_range", "high", "age", min=0, max=110),
          _rule("Gender values", "allowed_values", "medium", "gender", values=["F", "M", "X"]),
          _rule("Stay is positive", "business_rule", "medium", expression="length_of_stay_days > 0")],
         "stable", [("Occupancy Report", "report")]),
    Spec("IoT Sensor Readings", "iot", "Operations", "platform-team", "SYNTHETIC sensor telemetry.", 100000, _iot,
         ["reading_id"], ["temperature_c", "humidity_pct", "pressure_hpa", "battery_pct"], ["status", "firmware"], ["ts"],
         [_rule("Reading id unique", "unique", "high", "reading_id"),
          _rule("Humidity 0-100", "numeric_range", "high", "humidity_pct", min=0, max=100),
          _rule("Temperature range", "numeric_range", "medium", "temperature_c", min=-30, max=60),
          _rule("Status values", "allowed_values", "medium", "status", values=["ok", "low_battery", "offline"])],
         "flaky", [("Device Health Monitor", "report"), ("Anomaly Features", "ml_feature")]),
    Spec("HR Employees", "hr", "People", "hr-ops", "SYNTHETIC employee master.", 20000, _hr,
         ["employee_id"], ["salary", "age"], ["department", "grade", "remote"], ["hire_date"],
         [_rule("Employee id unique", "unique", "critical", "employee_id"),
          _rule("Salary positive", "numeric_range", "high", "salary", min=1000, max=500000),
          _rule("Grade values", "allowed_values", "medium", "grade", values=["G1", "G2", "G3", "G4", "G5"])],
         "stable", [("Headcount Report", "report")]),
    Spec("Telecom Customers", "telecom", "Telecom", "crm-team", "SYNTHETIC subscriber base.", 50000, _telecom,
         ["customer_id"], ["monthly_charge", "data_gb", "tenure_months"], ["plan", "region"], [],
         [_rule("Customer id unique", "unique", "high", "customer_id"),
          _rule("Charge positive", "numeric_range", "high", "monthly_charge", min=1, max=300),
          _rule("Plan values", "allowed_values", "medium", "plan", values=["basic", "plus", "premium"])],
         "improving", [("Churn Model Features", "ml_feature"), ("ARPU Metric", "metric")]),
    Spec("Logistics Shipments", "logistics", "Supply Chain", "ops-team", "SYNTHETIC freight shipments.", 45000, _logistics,
         ["shipment_id"], ["weight_kg", "cost", "delivery_days"], ["carrier", "status", "origin"], ["ship_date"],
         [_rule("Shipment id unique", "unique", "high", "shipment_id"),
          _rule("Weight positive", "numeric_range", "high", "weight_kg", min=0.01, max=5000),
          _rule("Carrier values", "allowed_values", "medium", "carrier", values=["DHL", "FedEx", "Maersk", "UPS"]),
          _rule("Cost positive", "business_rule", "medium", expression="cost > 0")],
         "flaky", [("On-time Delivery KPI", "metric")]),
    Spec("Web Clickstream", "clicks", "Marketing", "web-team", "SYNTHETIC web events.", 120000, _clicks,
         ["event_id"], ["duration_s"], ["page", "device", "browser", "referrer"], ["ts"],
         [_rule("Event id unique", "unique", "high", "event_id"),
          _rule("Device values", "allowed_values", "medium", "device", values=["mobile", "desktop", "tablet"]),
          _rule("Duration not negative", "numeric_range", "medium", "duration_s", min=0, max=7200)],
         "improving", [("Funnel Report", "report"), ("Sessions Metric", "metric")]),
    Spec("Insurance Claims", "claims", "Insurance", "claims-team", "SYNTHETIC insurance claims.", 35000, _claims,
         ["claim_id"], ["claim_amount", "approved_amount"], ["claim_type", "status", "region"], ["claim_date"],
         [_rule("Claim id unique", "unique", "critical", "claim_id"),
          _rule("Amount positive", "numeric_range", "high", "claim_amount", min=1, max=2000000),
          _rule("Approved <= claimed", "business_rule", "high", expression="approved_amount <= claim_amount"),
          _rule("Type values", "allowed_values", "medium", "claim_type", values=["auto", "home", "health", "travel"])],
         "degrading", [("Loss Ratio Report", "report")]),
    Spec("Energy Meter Usage", "energy", "Utilities", "grid-team", "SYNTHETIC smart-meter readings.", 70000, _energy,
         ["reading_id"], ["kwh", "voltage"], ["tariff", "region"], ["reading_date"],
         [_rule("Reading id unique", "unique", "high", "reading_id"),
          _rule("kWh not negative", "numeric_range", "high", "kwh", min=0, max=500),
          _rule("Voltage in band", "numeric_range", "medium", "voltage", min=200, max=260),
          _rule("Tariff values", "allowed_values", "medium", "tariff", values=["peak", "off_peak", "standard"])],
         "incident_recovery", [("Load Forecast Features", "ml_feature"), ("Billing Report", "report")]),
]


# ---------------------------------------------------------------------------------------------- degrade
def _degrade(df: pd.DataFrame, spec: Spec, sev: float, rng: np.random.Generator) -> pd.DataFrame:
    """Inject problems proportional to ``sev`` (0 = untouched)."""
    if sev <= 0:
        return df
    df = df.copy()
    n = len(df)

    def idx(frac: float) -> np.ndarray:  # INTENSITY makes the problems large enough to move the quality score visibly
        return rng.choice(n, max(1, int(n * min(0.6, frac * INTENSITY))), replace=False)

    nums = [c for c in spec.nums if c in df]
    cats = [c for c in spec.cats if c in df]
    for k, col in enumerate(nums):                     # shifted distribution, outliers, nulls, invalid text
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
        df[col] = df[col] * (1 + sev * (0.45 if k == 0 else 0.12))
        df.loc[idx(sev * 0.02), col] *= 12
        df.loc[idx(sev * (0.18 if k == 0 else 0.06)), col] = np.nan
    if len(nums) > 1:
        df[nums[1]] = df[nums[1]].astype(object)
        df.loc[idx(sev * 0.015), nums[1]] = "N/A"
        df.loc[idx(sev * 0.02), nums[1]] = -abs(float(pd.to_numeric(df[nums[1]], errors="coerce").median() or 1))
    for k, col in enumerate(cats):                     # unseen labels, skew, case variants
        df[col] = df[col].astype(object)
        df.loc[idx(sev * (0.10 if k == 0 else 0.04)), col] = "UNKNOWN"
        top = df[col].mode().iat[0]
        df.loc[idx(sev * (0.30 if k == 0 else 0.1)), col] = top
        df.loc[idx(sev * 0.03), col] = df[col].astype(str).str.upper()
        df.loc[idx(sev * 0.02), col] = np.nan
    for col in spec.dates:                             # drift forward, future dates, invalid strings
        d = pd.to_datetime(df[col], errors="coerce") + pd.to_timedelta(int(sev * 45), unit="D")
        df[col] = d.dt.strftime("%Y-%m-%d" if "date" in col else "%Y-%m-%d %H:%M:%S")
        df.loc[idx(sev * 0.01), col] = "2031-01-01"
        df.loc[idx(sev * 0.015), col] = "not-a-date"
    if spec.ids:                                       # duplicated keys + duplicated rows
        dup = df.iloc[idx(sev * 0.03)]
        df = pd.concat([df, dup], ignore_index=True)
    if sev >= 0.6 and len(nums) > 2:                   # schema change on severe versions
        df = df.drop(columns=[nums[-1]])
        df["legacy_flag"] = rng.integers(0, 2, len(df))
    return df.sample(frac=1.0, random_state=int(sev * 1000) + 7).reset_index(drop=True)


def generate_version(spec: Spec, version: int, *, scale: float = 1.0) -> pd.DataFrame:
    """The DataFrame for one version. v1 is clean; later versions apply :func:`_degrade` with growing severity.

    Each version draws a *fresh* sample from the same process (so it is not just v1 with errors) and then
    degrades it. ``scale`` shrinks the row count for quick tests.
    """
    label, sev = VERSION_PLAN[version]
    seed = sum(ord(c) for c in spec.slug) * 101 + version  # stable (str hash() is randomised per process)
    rng = np.random.default_rng(seed)
    n = max(200, int(spec.rows * scale * (1 + 0.04 * (version - 1))))
    return _degrade(spec.make(rng, n), spec, sev, rng)


def write_files(out_dir: Path, *, scale: float = 1.0, slugs: list[str] | None = None) -> dict[str, Any]:
    """Write every version as CSV plus a ``manifest.json``. Returns the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"synthetic": True, "scale": scale, "datasets": {}}
    for spec in SPECS:
        if slugs and spec.slug not in slugs:
            continue
        folder = out_dir / spec.slug
        folder.mkdir(exist_ok=True)
        files = {}
        for v, (label, sev) in VERSION_PLAN.items():
            df = generate_version(spec, v, scale=scale)
            fname = f"v{v}_{label}.csv"
            df.to_csv(folder / fname, index=False)
            files[fname] = {"version": v, "severity": sev, "rows": len(df), "columns": len(df.columns)}
        manifest["datasets"][spec.name] = {"slug": spec.slug, "project": spec.project, "files": files}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
