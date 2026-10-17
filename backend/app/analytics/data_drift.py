"""Statistical data-drift detection.

Pure functions: pandas Series/DataFrames in, JSON-safe dicts out.

Design rules
------------
* **Effect size gates the verdict, significance never does.** With large samples a
  trivially small shift becomes "statistically significant" (p < 0.05); we only
  mark a feature *drifted* when the chosen effect-size metric exceeds its
  threshold. P-values are reported alongside and are secondary.
* **Minimum sample sizes.** Tests have minima (see ``MIN_SAMPLE_SIZES``); below
  them the result is ``insufficient_data`` with a stated reason, not a verdict.
* **Multiple comparisons.** When KS/chi-square are run across many features their
  p-values go through Benjamini–Hochberg so a 5 % false-discovery rate holds.
* **Missing values are a separate signal.** The share of missing values is tracked
  separately from the distribution comparison; missingness change is its own
  finding, not a drift one.
* **Method selection.** Numeric columns get PSI (always) + KS + Wasserstein;
  categorical columns get Jensen–Shannon distance (always) + chi-square (only when
  every expected cell is ≥ 5 after pooling rare categories).
* **Identifiers, constant columns and user-excluded columns are skipped.**

Limitations (reported alongside results, not hidden)
----------------------------------------------------
* Data drift is not concept drift or model performance degradation.
* Drift does not prove business impact; it is a signal to investigate.
* PSI bins are built on the reference quantiles; a reference with heavy ties
  produces fewer effective bins than requested.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from app.utils.jsonsafe import deep_to_py

# ---------------------------------------------------------------- configuration

MIN_SAMPLE_SIZES = {
    "psi": 30,         # per side; smaller than this makes bin counts unstable
    "ks": 30,          # per side
    "wasserstein": 30,
    "js": 30,
    "chi_square_expected_cell_min": 5,
}

# Effect-size thresholds. Values above the "warn" level mark the feature as drifted;
# values at/above the "high" level escalate the severity.
DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "psi":         {"warn": 0.10,  "high": 0.25},   # industry convention
    "wasserstein": {"warn": 0.10,  "high": 0.30},   # normalised by reference IQR
    "js":          {"warn": 0.10,  "high": 0.20},   # distance in [0, 1], base 2
    "missingness_change_pp": {"warn": 5.0, "high": 20.0},
    "unseen_share_pct":      {"warn": 1.0, "high": 5.0},
    "ks_alpha": 0.05,          # significance level after BH correction
    "chi_square_alpha": 0.05,
    "psi_bins": 10,
    "ks_min_effect": 0.05,     # minimum KS statistic to even consider the test
}

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
_EPS = 1e-6                     # smoothing for empty PSI/JS bins
_IDENTIFIER_TYPES = {"integer", "text", "categorical"}


# -------------------------------------------------------------------- utilities


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 4) if whole else 0.0


def _is_numeric(series: pd.Series) -> bool:
    return pd.api.types.is_bool_dtype(series) is False and pd.api.types.is_numeric_dtype(series)


def _is_datetime(series: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(series)


def _as_numeric(series: pd.Series) -> pd.Series:
    """Return a float Series; strings that are 90%+ parseable become numbers."""
    if _is_numeric(series):
        return series.astype(float)
    if _is_datetime(series):
        return series.astype("int64").astype(float)  # nanoseconds since epoch
    coerced = pd.to_numeric(series, errors="coerce")
    if coerced.notna().mean() < 0.9 and series.dtype == object:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            as_dates = pd.to_datetime(series, errors="coerce", format="mixed")
        if as_dates.notna().mean() >= 0.9:
            return as_dates.astype("int64").astype(float).where(as_dates.notna())
    return coerced.astype(float)


def _as_categories(series: pd.Series) -> pd.Series:
    """Normalise to strings (NaNs preserved) for category comparisons."""
    if pd.api.types.is_bool_dtype(series):
        return series.map({True: "True", False: "False"})
    return series.astype("string").str.strip()


# ---------------------------------------------------------------- effect sizes


def population_stability_index(reference: np.ndarray, current: np.ndarray, *, bins: int = 10) -> dict[str, Any]:
    """PSI with bins defined by reference quantiles and epsilon smoothing.

    Returns ``{"psi", "bins": [{edges, reference_pct, current_pct, contribution}],
    "effective_bins", "note"}``.
    """
    reference = reference[np.isfinite(reference)]
    current = current[np.isfinite(current)]
    if len(reference) < 2 or len(current) == 0:
        return {"psi": None, "bins": [], "effective_bins": 0, "note": "Not enough data for PSI."}

    quantiles = np.linspace(0, 1, bins + 1)
    edges = np.unique(np.quantile(reference, quantiles))
    if len(edges) < 3:
        return {"psi": None, "bins": [], "effective_bins": 0,
                "note": "Reference values are nearly constant; PSI not meaningful."}
    edges[0], edges[-1] = -np.inf, np.inf

    ref_counts, _ = np.histogram(reference, bins=edges)
    cur_counts, _ = np.histogram(current, bins=edges)
    ref_pct = np.clip(ref_counts / ref_counts.sum(), _EPS, None)
    cur_pct = np.clip(cur_counts / cur_counts.sum(), _EPS, None)
    contrib = (cur_pct - ref_pct) * np.log(cur_pct / ref_pct)

    bins_out = []
    for i in range(len(contrib)):
        lo = edges[i] if np.isfinite(edges[i]) else None
        hi = edges[i + 1] if np.isfinite(edges[i + 1]) else None
        bins_out.append({
            "lower": lo, "upper": hi,
            "reference_pct": round(100.0 * ref_pct[i], 4),
            "current_pct": round(100.0 * cur_pct[i], 4),
            "contribution": round(float(contrib[i]), 6),
        })
    note = (f"Requested {bins} bins; reference ties produced {len(edges) - 1}."
            if len(edges) - 1 < bins else None)
    return {"psi": round(float(contrib.sum()), 6), "bins": bins_out,
            "effective_bins": len(edges) - 1, "note": note}


def kolmogorov_smirnov(reference: np.ndarray, current: np.ndarray) -> dict[str, Any]:
    reference = reference[np.isfinite(reference)]
    current = current[np.isfinite(current)]
    if len(reference) < MIN_SAMPLE_SIZES["ks"] or len(current) < MIN_SAMPLE_SIZES["ks"]:
        return {"statistic": None, "p_value": None, "note": "insufficient samples"}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = stats.ks_2samp(reference, current, alternative="two-sided", method="auto")
    return {"statistic": round(float(res.statistic), 6), "p_value": round(float(res.pvalue), 8), "note": None}


def wasserstein_normalized(reference: np.ndarray, current: np.ndarray) -> dict[str, Any]:
    """1-Wasserstein distance divided by the reference IQR (0 if IQR is zero, with a note)."""
    reference = reference[np.isfinite(reference)]
    current = current[np.isfinite(current)]
    if len(reference) < MIN_SAMPLE_SIZES["wasserstein"] or len(current) == 0:
        return {"raw": None, "normalized": None, "note": "insufficient samples"}
    q75, q25 = np.quantile(reference, [0.75, 0.25])
    iqr = float(q75 - q25)
    raw = float(stats.wasserstein_distance(reference, current))
    if iqr == 0:
        return {"raw": round(raw, 6), "normalized": None,
                "note": "reference IQR is zero; normalised Wasserstein undefined."}
    return {"raw": round(raw, 6), "normalized": round(raw / iqr, 6),
            "reference_iqr": round(iqr, 6), "note": None}


def jensen_shannon(reference: pd.Series, current: pd.Series) -> dict[str, Any]:
    """JS distance in [0, 1] (base 2) between the two category distributions.

    Values that only appear in one side are counted; smoothing avoids ``log(0)``.
    """
    ref = _as_categories(reference).dropna().value_counts()
    cur = _as_categories(current).dropna().value_counts()
    if ref.sum() < MIN_SAMPLE_SIZES["js"] or cur.sum() == 0:
        return {"distance": None, "note": "insufficient samples"}
    categories = sorted(set(ref.index) | set(cur.index))
    ref_pct = np.clip(ref.reindex(categories, fill_value=0).to_numpy() / ref.sum(), _EPS, None)
    cur_pct = np.clip(cur.reindex(categories, fill_value=0).to_numpy() / cur.sum(), _EPS, None)
    ref_pct /= ref_pct.sum()
    cur_pct /= cur_pct.sum()
    m = 0.5 * (ref_pct + cur_pct)
    kl = lambda a, b: float(np.sum(a * np.log2(a / b)))
    js_div = 0.5 * kl(ref_pct, m) + 0.5 * kl(cur_pct, m)
    return {"distance": round(float(np.sqrt(max(0.0, js_div))), 6),
            "divergence_bits": round(js_div, 6), "note": None}


def chi_square(reference: pd.Series, current: pd.Series) -> dict[str, Any]:
    """Chi-square two-sample test on category counts.

    Rare categories are pooled into an ``_OTHER_`` bucket until every expected
    cell is >= ``chi_square_expected_cell_min``. If no valid test survives (e.g.
    only one category after pooling), returns ``not_applicable``.
    """
    ref = _as_categories(reference).dropna().value_counts()
    cur = _as_categories(current).dropna().value_counts()
    if ref.sum() == 0 or cur.sum() == 0:
        return {"statistic": None, "p_value": None, "note": "insufficient samples"}

    categories = sorted(set(ref.index) | set(cur.index))
    ref_a = ref.reindex(categories, fill_value=0).to_numpy().astype(float)
    cur_a = cur.reindex(categories, fill_value=0).to_numpy().astype(float)
    r_n, c_n = ref_a.sum(), cur_a.sum()
    totals = ref_a + cur_a
    expected_ref = totals * r_n / (r_n + c_n)
    expected_cur = totals * c_n / (r_n + c_n)

    min_cell = MIN_SAMPLE_SIZES["chi_square_expected_cell_min"]
    rare = (expected_ref < min_cell) | (expected_cur < min_cell)
    if rare.any():
        ref_a = np.r_[ref_a[~rare], [ref_a[rare].sum()]]
        cur_a = np.r_[cur_a[~rare], [cur_a[rare].sum()]]
        totals = ref_a + cur_a
        expected_ref = totals * r_n / (r_n + c_n)
        expected_cur = totals * c_n / (r_n + c_n)
        if (expected_ref < min_cell).any() or (expected_cur < min_cell).any() or len(ref_a) < 2:
            return {"statistic": None, "p_value": None,
                    "note": f"too few samples per category for chi-square (min expected cell {min_cell})."}
    observed = np.vstack([ref_a, cur_a])
    try:
        stat, p, _, _ = stats.chi2_contingency(observed, correction=False)
    except ValueError as exc:
        return {"statistic": None, "p_value": None, "note": f"chi-square not applicable: {exc}"}
    return {"statistic": round(float(stat), 6), "p_value": round(float(p), 8), "note": None}


def benjamini_hochberg(p_values: list[float], alpha: float) -> list[bool]:
    """Return a mask of hypotheses rejected at the given FDR level.

    Entries whose p-value is ``None`` are not tested and always return ``False``.
    """
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    if not indexed:
        return [False] * len(p_values)
    indexed.sort(key=lambda x: x[1])
    m = len(indexed)
    rejected: set[int] = set()
    k_star = 0
    for rank, (_, p) in enumerate(indexed, start=1):
        if p <= rank / m * alpha:
            k_star = rank
    for idx, _ in indexed[:k_star]:
        rejected.add(idx)
    return [i in rejected for i in range(len(p_values))]


# ---------------------------------------------------------------- per-feature


@dataclass
class FeatureSelection:
    name: str
    kind: str                # "numeric" | "categorical" | "datetime"
    reason_skipped: str | None = None


def _select_features(
    reference_profiles: list[dict[str, Any]],
    current_profiles: list[dict[str, Any]],
    exclude: set[str],
) -> list[FeatureSelection]:
    """Pick features eligible for drift. Rules match the module docstring."""
    cur_by_name = {c["column_name"]: c for c in current_profiles}
    out: list[FeatureSelection] = []
    for r in reference_profiles:
        name = r["column_name"]
        if name in exclude:
            out.append(FeatureSelection(name, "", "excluded by request"))
            continue
        if name not in cur_by_name:
            out.append(FeatureSelection(name, "", "column absent in current (reported as schema change)"))
            continue
        c = cur_by_name[name]
        if r["is_potential_identifier"] or c["is_potential_identifier"]:
            out.append(FeatureSelection(name, "", "identifier-like column"))
            continue
        if r["is_constant"] and c["is_constant"]:
            out.append(FeatureSelection(name, "", "constant in both reference and current"))
            continue
        rt, ct = r["inferred_type"], c["inferred_type"]
        if rt in ("integer", "float") and ct in ("integer", "float"):
            out.append(FeatureSelection(name, "numeric"))
        elif rt == "datetime" and ct == "datetime":
            out.append(FeatureSelection(name, "datetime"))
        elif rt in ("categorical", "boolean") or ct in ("categorical", "boolean"):
            out.append(FeatureSelection(name, "categorical"))
        else:
            out.append(FeatureSelection(name, "", f"unsupported type pair ({rt} vs {ct})"))
    return out


def _severity(effect: float | None, thresholds: dict[str, float]) -> str:
    if effect is None:
        return "info"
    if effect >= thresholds["high"]:
        return "high"
    if effect >= thresholds["warn"]:
        return "medium"
    return "info"


def _missingness(reference: pd.Series, current: pd.Series, thresholds: dict[str, float]
                 ) -> dict[str, Any]:
    ref_miss = _pct(int(reference.isna().sum()), len(reference))
    cur_miss = _pct(int(current.isna().sum()), len(current))
    delta = round(cur_miss - ref_miss, 4)
    status = ("ok" if abs(delta) < thresholds["warn"]
              else "high" if abs(delta) >= thresholds["high"] else "warn")
    return {"reference_pct": ref_miss, "current_pct": cur_miss,
            "delta_pp": delta, "status": status}


def _numeric_feature(name: str, ref: pd.Series, cur: pd.Series,
                     cfg: dict[str, Any]) -> dict[str, Any]:
    ref_v = _as_numeric(ref).dropna().to_numpy()
    cur_v = _as_numeric(cur).dropna().to_numpy()
    if len(ref_v) < MIN_SAMPLE_SIZES["psi"] or len(cur_v) < MIN_SAMPLE_SIZES["psi"]:
        return {"name": name, "kind": "numeric", "status": "insufficient_data",
                "severity": "info", "reason": f"need >= {MIN_SAMPLE_SIZES['psi']} non-null values per side; "
                                              f"have {len(ref_v)} and {len(cur_v)}.",
                "reference_n": int(len(ref_v)), "current_n": int(len(cur_v)),
                "effect_sizes": {}, "missingness": _missingness(ref, cur, cfg["missingness_change_pp"])}

    psi = population_stability_index(ref_v, cur_v, bins=int(cfg["psi_bins"]))
    ks = kolmogorov_smirnov(ref_v, cur_v)
    wd = wasserstein_normalized(ref_v, cur_v)
    missing = _missingness(ref, cur, cfg["missingness_change_pp"])

    psi_sev = _severity(psi["psi"], cfg["psi"])
    wd_sev = _severity(wd["normalized"], cfg["wasserstein"])
    severity = max(
        [psi_sev, wd_sev, missing["status"] if missing["status"] in SEVERITY_ORDER else "info"],
        key=SEVERITY_ORDER.index,
    )
    drifted = psi_sev in ("medium", "high") or wd_sev in ("medium", "high")
    primary = "psi" if psi["psi"] is not None else "wasserstein"

    stats_summary = {
        "reference": {"mean": float(np.mean(ref_v)), "median": float(np.median(ref_v)),
                      "std": float(np.std(ref_v, ddof=1)) if len(ref_v) > 1 else 0.0},
        "current":   {"mean": float(np.mean(cur_v)), "median": float(np.median(cur_v)),
                      "std": float(np.std(cur_v, ddof=1)) if len(cur_v) > 1 else 0.0},
    }
    return {
        "name": name, "kind": "numeric",
        "status": "drifted" if drifted else "stable",
        "severity": severity, "primary_method": primary,
        "reference_n": int(len(ref_v)), "current_n": int(len(cur_v)),
        "missingness": missing,
        "effect_sizes": {"psi": psi["psi"], "wasserstein_normalized": wd.get("normalized")},
        "psi": psi, "ks": ks, "wasserstein": wd,
        "stats": stats_summary,
        "thresholds": {"psi": cfg["psi"], "wasserstein": cfg["wasserstein"]},
        "explanation": _explain_numeric(name, psi, wd, missing, ref_v, cur_v),
    }


def _explain_numeric(name: str, psi: dict, wd: dict, missing: dict,
                     ref_v: np.ndarray, cur_v: np.ndarray) -> str:
    parts: list[str] = []
    if psi["psi"] is not None:
        parts.append(f"PSI={psi['psi']:.3f}")
    if wd.get("normalized") is not None:
        parts.append(f"Wasserstein (normalised by reference IQR)={wd['normalized']:.3f}")
    direction = "increased" if np.mean(cur_v) > np.mean(ref_v) else "decreased"
    delta_mean = float(np.mean(cur_v) - np.mean(ref_v))
    parts.append(f"mean {direction} by {abs(delta_mean):.4g} (ref {np.mean(ref_v):.4g} -> cur {np.mean(cur_v):.4g})")
    if missing["status"] != "ok":
        parts.append(f"missing rate {missing['reference_pct']:.2f}% -> {missing['current_pct']:.2f}%")
    return f"'{name}' – " + "; ".join(parts) + "."


def _categorical_feature(name: str, ref: pd.Series, cur: pd.Series,
                         cfg: dict[str, Any]) -> dict[str, Any]:
    ref_cats = _as_categories(ref).dropna()
    cur_cats = _as_categories(cur).dropna()
    if len(ref_cats) < MIN_SAMPLE_SIZES["js"] or len(cur_cats) < MIN_SAMPLE_SIZES["js"]:
        return {"name": name, "kind": "categorical", "status": "insufficient_data",
                "severity": "info", "reason": f"need >= {MIN_SAMPLE_SIZES['js']} non-null values per side; "
                                              f"have {len(ref_cats)} and {len(cur_cats)}.",
                "reference_n": int(len(ref_cats)), "current_n": int(len(cur_cats)),
                "effect_sizes": {}, "missingness": _missingness(ref, cur, cfg["missingness_change_pp"])}

    js = jensen_shannon(ref, cur)
    chi = chi_square(ref, cur)
    missing = _missingness(ref, cur, cfg["missingness_change_pp"])

    ref_counts = ref_cats.value_counts()
    cur_counts = cur_cats.value_counts()
    cur_share = cur_counts / cur_counts.sum() * 100.0
    unseen = sorted(set(cur_counts.index) - set(ref_counts.index))
    unseen_share = float(cur_share.reindex(unseen, fill_value=0).sum())
    vanished = sorted(set(ref_counts.index) - set(cur_counts.index))
    unseen_detail = [
        {"value": v, "current_pct": round(float(cur_share.get(v, 0.0)), 4),
         "current_count": int(cur_counts.get(v, 0))}
        for v in unseen[:20]
    ]
    unseen_sev = _severity(unseen_share, cfg["unseen_share_pct"])

    js_sev = _severity(js["distance"], cfg["js"])
    severity = max(
        [js_sev, unseen_sev, missing["status"] if missing["status"] in SEVERITY_ORDER else "info"],
        key=SEVERITY_ORDER.index,
    )
    drifted = js_sev in ("medium", "high") or unseen_sev in ("medium", "high")
    return {
        "name": name, "kind": "categorical",
        "status": "drifted" if drifted else "stable",
        "severity": severity, "primary_method": "js",
        "reference_n": int(len(ref_cats)), "current_n": int(len(cur_cats)),
        "missingness": missing,
        "effect_sizes": {"js_distance": js["distance"], "unseen_share_pct": round(unseen_share, 4)},
        "js": js, "chi_square": chi,
        "unseen_categories": unseen_detail, "unseen_count": len(unseen),
        "vanished_categories": vanished[:20], "vanished_count": len(vanished),
        "thresholds": {"js": cfg["js"], "unseen_share_pct": cfg["unseen_share_pct"]},
        "explanation": _explain_categorical(name, js, unseen_share, len(unseen), len(vanished), missing),
    }


def _explain_categorical(name: str, js: dict, unseen_share: float,
                         n_unseen: int, n_vanished: int, missing: dict) -> str:
    parts: list[str] = []
    if js["distance"] is not None:
        parts.append(f"JS distance={js['distance']:.3f}")
    if n_unseen:
        parts.append(f"{n_unseen} new category value(s) covering {unseen_share:.2f}% of rows")
    if n_vanished:
        parts.append(f"{n_vanished} baseline category value(s) absent now")
    if missing["status"] != "ok":
        parts.append(f"missing rate {missing['reference_pct']:.2f}% -> {missing['current_pct']:.2f}%")
    return f"'{name}' – " + ("; ".join(parts) if parts else "distributions comparable") + "."


# -------------------------------------------------------------------- main API


def detect_drift(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    *,
    reference_profiles: list[dict[str, Any]],
    current_profiles: list[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
    exclude_columns: list[str] | None = None,
) -> dict[str, Any]:
    """Compare ``current`` vs ``reference`` and return a dataset-level drift report.

    The report has ``summary``, ``features`` (one entry per shared column), ``skipped``
    (columns intentionally excluded or ineligible, with reasons), ``thresholds``,
    ``multiple_testing`` (BH decisions for KS and chi-square), and ``limitations``.
    """
    cfg = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    exclude = set(exclude_columns or [])
    selections = _select_features(reference_profiles, current_profiles, exclude)

    features: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for sel in selections:
        if sel.reason_skipped:
            skipped.append({"column": sel.name, "reason": sel.reason_skipped})
            continue
        if sel.kind in ("numeric", "datetime"):
            features.append(_numeric_feature(sel.name, reference[sel.name], current[sel.name], cfg))
        else:
            features.append(_categorical_feature(sel.name, reference[sel.name], current[sel.name], cfg))

    # BH correction for KS and chi-square p-values across evaluated features.
    ks_p = [f.get("ks", {}).get("p_value") if f.get("kind") == "numeric" else None for f in features]
    chi_p = [f.get("chi_square", {}).get("p_value") if f.get("kind") == "categorical" else None for f in features]
    ks_reject = benjamini_hochberg(ks_p, cfg["ks_alpha"])
    chi_reject = benjamini_hochberg(chi_p, cfg["chi_square_alpha"])
    for f, k_r, c_r in zip(features, ks_reject, chi_reject):
        if f.get("ks"):
            f["ks"]["significant_after_bh"] = bool(k_r)
        if f.get("chi_square"):
            f["chi_square"]["significant_after_bh"] = bool(c_r)

    def _max_sev(items):
        return max((f["severity"] for f in items if "severity" in f), key=SEVERITY_ORDER.index, default="info")

    drifted = [f for f in features if f.get("status") == "drifted"]
    insufficient = [f for f in features if f.get("status") == "insufficient_data"]
    missingness_changes = [
        {"column": f["name"], "reference_pct": f["missingness"]["reference_pct"],
         "current_pct": f["missingness"]["current_pct"], "delta_pp": f["missingness"]["delta_pp"],
         "status": f["missingness"]["status"]}
        for f in features if f.get("missingness", {}).get("status") in ("warn", "high")
    ]
    by_severity = {s: 0 for s in SEVERITY_ORDER}
    for f in features:
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1

    dataset_level = ("drifted" if any(f["severity"] in ("high", "critical") for f in drifted)
                     else "warn" if drifted else "stable")

    features.sort(key=lambda f: (-SEVERITY_ORDER.index(f.get("severity", "info")),
                                 -(f.get("effect_sizes", {}).get("psi") or
                                   f.get("effect_sizes", {}).get("js_distance") or 0.0)))
    return deep_to_py({
        "summary": {
            "status": dataset_level,
            "features_evaluated": len(features) - len(insufficient),
            "features_drifted": len(drifted),
            "features_insufficient_data": len(insufficient),
            "features_skipped": len(skipped),
            "max_severity": _max_sev(features),
            "by_severity": by_severity,
            "missingness_changes": len(missingness_changes),
        },
        "features": features,
        "missingness_changes": missingness_changes,
        "skipped": skipped,
        "multiple_testing": {
            "method": "Benjamini–Hochberg",
            "ks_alpha": cfg["ks_alpha"], "chi_square_alpha": cfg["chi_square_alpha"],
            "ks_rejected": sum(ks_reject), "chi_square_rejected": sum(chi_reject),
        },
        "thresholds": cfg,
        "limitations": [
            "Data drift measured here is distribution change in input data; it is not concept "
            "drift or model-performance degradation.",
            "A drifted feature is a signal to investigate, not proof of business impact.",
            "PSI uses reference quantile bins; a reference with heavy ties yields fewer "
            "effective bins than requested (see each feature's PSI result).",
            "Features with fewer than the minimum per-side sample size are returned as "
            "insufficient_data, not stable.",
        ],
    })


__all__ = [
    "DEFAULT_THRESHOLDS", "MIN_SAMPLE_SIZES", "SEVERITY_ORDER",
    "benjamini_hochberg", "chi_square", "detect_drift", "jensen_shannon",
    "kolmogorov_smirnov", "population_stability_index", "wasserstein_normalized",
]
