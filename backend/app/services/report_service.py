"""Report generation: HTML (Jinja2), JSON and CSV.

Generates a single self-contained HTML report for a monitoring run — no
external dependencies, no CDN calls, no images that need a browser to render.
Charts are embedded as inline SVG (simple bar/line representations) since
Plotly is a frontend dependency. The report is labelled ``SYNTHETIC`` when the
dataset version is synthetic.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, BaseLoader

from app.utils.jsonsafe import to_py

# ──────────────────────────────────────── Jinja2 template ───────────────────

_TEMPLATE_SRC = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DataSentinel — {{ run.dataset_name }} v{{ run.current_version_number }} Report</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0b1220;color:#e2e8f0;line-height:1.5;font-size:15px}
.wrap{max-width:1100px;margin:0 auto;padding:2rem 1.5rem 4rem}
h1{font-size:1.8rem;margin-bottom:.25rem;letter-spacing:-.02em}
h2{font-size:1.2rem;margin:2rem 0 .6rem;letter-spacing:-.01em;border-left:3px solid #2dd4bf;padding-left:.7rem;color:#f1f5f9}
h3{font-size:1rem;margin:.8rem 0 .4rem;color:#cbd5e1}
.meta{color:#94a3b8;font-size:.85rem;margin-bottom:1.5rem}
.badge{display:inline-block;padding:.12rem .55rem;border-radius:999px;font-size:.72rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;border:1px solid currentColor;white-space:nowrap}
.synth{color:#a78bfa;background:#a78bfa20;border-color:#a78bfa50}
.sev-critical{color:#ef4444;background:#ef444420;border-color:#ef444450}
.sev-high{color:#f97316;background:#f9731620;border-color:#f9731650}
.sev-medium{color:#eab308;background:#eab30820;border-color:#eab30850}
.sev-low{color:#38bdf8;background:#38bdf820;border-color:#38bdf850}
.sev-info{color:#94a3b8;background:#94a3b820;border-color:#94a3b850}
.status-open{color:#ef4444}.status-acknowledged{color:#eab308}.status-resolved{color:#22c55e}.status-ignored{color:#64748b}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:.8rem;margin:1rem 0 1.5rem}
.kpi{background:#111a2e;border:1px solid #1e293b;border-left:3px solid #2dd4bf;border-radius:8px;padding:.7rem 1rem}
.kpi-label{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:#64748b}
.kpi-value{font-size:1.6rem;font-weight:700;line-height:1.2;color:#f1f5f9}
.kpi-sub{font-size:.75rem;color:#94a3b8}
table{width:100%;border-collapse:collapse;margin:.5rem 0 1rem;font-size:.85rem}
th{background:#111a2e;color:#94a3b8;font-weight:600;padding:.45rem .7rem;text-align:left;border-bottom:1px solid #1e293b}
td{padding:.4rem .7rem;border-bottom:1px solid #1a2535;vertical-align:top;word-break:break-word;max-width:300px}
tr:hover td{background:#0f172a}
.card{background:#111a2e;border:1px solid #1e293b;border-radius:10px;padding:.9rem 1.1rem;margin:.5rem 0 1rem}
.card-head{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin-bottom:.4rem}
.card-title{font-weight:600;color:#f1f5f9}
.muted{color:#64748b;font-size:.85rem}
.hyp{border-left:3px solid #38bdf8;padding:.35rem .8rem;margin:.3rem 0;background:#0f172a;border-radius:0 6px 6px 0}
.rec{list-style:decimal;padding-left:1.2rem;color:#cbd5e1;font-size:.88rem}
.rec li{margin:.3rem 0}
.bar-wrap{margin:.5rem 0 1rem}
.bar-track{background:#1e293b;border-radius:4px;height:14px;margin:.2rem 0;display:flex;align-items:center;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;transition:width .3s}
.bar-label{font-size:.75rem;color:#94a3b8;display:flex;justify-content:space-between;margin-bottom:2px}
.disclaimer{background:#111a2e;border:1px solid #1e293b;border-radius:8px;padding:.6rem 1rem;font-size:.8rem;color:#64748b;margin:1.5rem 0}
footer{margin-top:3rem;color:#334155;font-size:.78rem;text-align:center}
</style>
</head>
<body>
<div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:.5rem;margin-bottom:.5rem">
  <div>
    <h1>DataSentinel monitoring report</h1>
    <div class="meta">
      <b>{{ run.dataset_name }}</b> — version {{ run.current_version_number }}
      {% if run.reference_version_number %} vs. reference v{{ run.reference_version_number }}{% endif %}
      <br>Run {{ run.run_id[:8] }} &bull; {{ run.finished_at[:19].replace("T"," ") }} UTC
    </div>
  </div>
  <div>{% if run.is_synthetic %}<span class="badge synth">synthetic demo data</span>{% endif %}</div>
</div>

<div class="kpis">
  <div class="kpi"><div class="kpi-label">Health status</div>
    <div class="kpi-value">{{ summary.quality.status | title }}</div></div>
  {% if summary.score %}<div class="kpi"><div class="kpi-label">Quality score</div>
    <div class="kpi-value">{{ "%.1f"|format(summary.score.overall) if summary.score.overall else "–" }}</div>
    <div class="kpi-sub">{{ summary.score.status }}</div></div>{% endif %}
  <div class="kpi"><div class="kpi-label">Rules evaluated</div>
    <div class="kpi-value">{{ summary.rules_evaluated }}</div>
    <div class="kpi-sub">fail: {{ summary.quality.outcomes.fail }} / warn: {{ summary.quality.outcomes.warn }}</div></div>
  {% if summary.drift %}<div class="kpi"><div class="kpi-label">Drifted features</div>
    <div class="kpi-value">{{ summary.drift.features_drifted }}</div>
    <div class="kpi-sub">of {{ summary.drift.features_evaluated }} evaluated</div></div>{% endif %}
  {% if summary.schema %}<div class="kpi"><div class="kpi-label">Schema changes</div>
    <div class="kpi-value">{{ summary.schema.total_changes }}</div>
    <div class="kpi-sub">{{ summary.schema.breaking_changes }} breaking</div></div>{% endif %}
  <div class="kpi"><div class="kpi-label">Incidents</div>
    <div class="kpi-value">{{ summary.incidents.created + summary.incidents.updated }}</div>
    <div class="kpi-sub">{{ summary.incidents.created }} new, {{ summary.incidents.updated }} updated</div></div>
</div>

{% if incidents %}
<h2>Incidents ({{ incidents|length }})</h2>
{% for inc in incidents %}
<div class="card">
  <div class="card-head">
    <span class="badge sev-{{ inc.severity }}">{{ inc.severity }}</span>
    <span class="badge" style="color:#64748b;border-color:#33415550">{{ inc.incident_type }}</span>
    <span class="card-title">{{ inc.title[:200] }}</span>
    <span class="status-{{ inc.status }}" style="font-size:.8rem;font-weight:600">{{ inc.status }}</span>
  </div>
  {% if inc.columns %}<div class="muted">Columns: {{ inc.columns|join(", ") }}</div>{% endif %}
  {% if inc.affected_pct %}<div class="muted">{{ "%.2f"|format(inc.affected_pct) }}% of rows affected</div>{% endif %}
  {% if inc.downstream_assets %}
  <div class="muted" style="margin-top:.3rem">Downstream: {{ inc.downstream_assets|join(", ") }}</div>{% endif %}
  {% if inc.recommendations %}
  <h3 style="margin-top:.6rem">What to do</h3>
  <ol class="rec">{% for r in inc.recommendations %}<li>{{ r.action }}</li>{% endfor %}</ol>{% endif %}
</div>
{% endfor %}
{% endif %}

{% if check_results %}
<h2>Validation results</h2>
<table>
<thead><tr><th>Outcome</th><th>Severity</th><th>Rule</th><th>Columns</th><th>Affected %</th><th>Message</th></tr></thead>
<tbody>
{% for r in check_results %}
{% if r.outcome in ("fail","warn") %}
<tr><td><span class="badge sev-{{ r.severity }}">{{ r.outcome }}</span></td>
    <td><span class="badge sev-{{ r.severity }}">{{ r.severity }}</span></td>
    <td>{{ r.rule_name[:60] }}</td>
    <td>{{ r.columns|join(", ") }}</td>
    <td>{{ "%.2f"|format(r.affected_pct) if r.affected_pct is not none else "–" }}</td>
    <td>{{ r.message[:200] }}</td></tr>
{% endif %}
{% endfor %}
</tbody></table>
{% endif %}

{% if drift_features %}
<h2>Drift — drifted features</h2>
<table>
<thead><tr><th>Feature</th><th>Kind</th><th>Severity</th><th>Primary effect</th><th>Explanation</th></tr></thead>
<tbody>
{% for f in drift_features %}
<tr><td>{{ f.name }}</td><td>{{ f.kind }}</td>
    <td><span class="badge sev-{{ f.severity }}">{{ f.severity }}</span></td>
    <td>{{ f.effect_sizes }}</td>
    <td>{{ f.explanation[:200] if f.explanation else "–" }}</td></tr>
{% endfor %}
</tbody></table>
{% endif %}

{% if schema_changes %}
<h2>Schema changes</h2>
<table>
<thead><tr><th>Change type</th><th>Columns</th><th>Severity</th><th>Breaking</th><th>Explanation</th></tr></thead>
<tbody>
{% for c in schema_changes %}
<tr><td>{{ c.change_type }}</td><td>{{ c.columns|join(", ") }}</td>
    <td><span class="badge sev-{{ c.severity }}">{{ c.severity }}</span></td>
    <td>{{ "yes" if c.breaking else "no" }}</td>
    <td>{{ c.explanation[:200] }}</td></tr>
{% endfor %}
</tbody></table>
{% endif %}

<div class="disclaimer">
  <b>Disclaimer:</b>
  This report is generated by DataSentinel and reflects the state of the data at the time of the run.
  Severity scores are rule-based heuristics, not probabilities of business impact.
  Hypotheses are suggestions, not confirmed root causes.
  {% if run.is_synthetic %} All data in this report is <b>synthetic</b> and generated for demonstration purposes. {% endif %}
</div>

<footer>Generated by DataSentinel {{ run.app_version }} · Run {{ run.run_id }} · {{ run.finished_at[:10] }}</footer>
</div>
</body>
</html>"""

_ENV = Environment(loader=BaseLoader(), autoescape=True)
_TMPL = _ENV.from_string(_TEMPLATE_SRC)


# ─────────────────────────────────────── builders ───────────────────────────


def _run_context(run_id: str, dataset_name: str, summary: dict[str, Any],
                 current_version: int, reference_version: int | None,
                 finished_at: str | None, is_synthetic: bool, app_version: str) -> dict[str, Any]:
    return {
        "run_id": run_id, "dataset_name": dataset_name,
        "current_version_number": current_version,
        "reference_version_number": reference_version,
        "finished_at": finished_at or datetime.utcnow().isoformat(),
        "is_synthetic": is_synthetic, "app_version": app_version,
    }


def generate_html_report(
    run: Any,
    dataset: Any,
    check_results: list[Any],
    drift_results: list[Any],
    incidents: list[dict[str, Any]],
    schema_diff: dict[str, Any] | None,
    app_version: str = "0.4.0",
) -> str:
    summary = run.summary or {}
    cur_version = (summary.get("current_version_number")
                   or getattr(run, "current_version_id", "?"))
    ref_version = summary.get("reference_version_number")

    ctx = {
        "run": _run_context(
            run.id, dataset.name, summary, cur_version, ref_version,
            to_py(run.finished_at), getattr(dataset, "is_synthetic", False), app_version,
        ),
        "summary": summary,
        "check_results": [
            {
                "outcome": r.outcome, "severity": r.severity, "rule_name": r.rule_name,
                "columns": r.columns or [], "affected_pct": r.affected_pct,
                "message": (r.evidence or {}).get("message", r.rule_name),
            }
            for r in check_results
        ],
        "drift_features": [
            {
                "name": r.column_name, "kind": r.feature_type,
                "severity": (r.details or {}).get("severity", "info"),
                "effect_sizes": str({k: round(v, 4) for k, v in
                                     ((r.details or {}).get("effect_sizes") or {}).items()}),
                "explanation": (r.details or {}).get("explanation", ""),
            }
            for r in drift_results if r.drift_detected
        ],
        "schema_changes": (schema_diff or {}).get("changes", []),
        "incidents": incidents,
    }
    return _TMPL.render(**ctx)


def generate_json_report(
    run: Any, dataset: Any, check_results: list[Any],
    drift_results: list[Any], incidents: list[dict[str, Any]],
    schema_diff: dict[str, Any] | None,
) -> str:
    summary = run.summary or {}
    report = {
        "report_type": "DataSentinel monitoring report",
        "generated_at": datetime.utcnow().isoformat(),
        "run_id": run.id,
        "dataset": {"id": dataset.id, "name": dataset.name, "is_synthetic": dataset.is_synthetic},
        "summary": summary,
        "incidents": incidents,
        "check_results": [
            {"outcome": r.outcome, "severity": r.severity, "rule_name": r.rule_name,
             "columns": r.columns or [], "affected_pct": r.affected_pct,
             "affected_count": r.affected_count, "evidence": r.evidence or {}}
            for r in check_results
        ],
        "drift_results": [
            {"column": r.column_name, "kind": r.feature_type, "status": r.status,
             "drift_detected": r.drift_detected, "statistic": r.statistic,
             "effect_size": r.effect_size, "details": r.details or {}}
            for r in drift_results
        ],
        "schema_changes": (schema_diff or {}).get("changes", []),
        "disclaimer": (
            "Severity scores are heuristics, not probabilities of business impact. "
            "Hypotheses are suggestions, not confirmed root causes."
        ),
    }
    return json.dumps(report, indent=2, default=str)


def generate_csv_report(check_results: list[Any], incidents: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["section", "type", "severity", "columns", "affected_pct", "outcome", "message"])
    for r in check_results:
        w.writerow(["quality_check", r.rule_name, r.severity,
                    "|".join(r.columns or []), r.affected_pct, r.outcome, ""])
    for inc in incidents:
        w.writerow(["incident", inc["title"], inc["severity"],
                    "|".join(inc.get("columns", [])), inc.get("affected_pct", ""),
                    inc["status"], inc.get("downstream_assets", [])])
    return output.getvalue()


__all__ = ["generate_html_report", "generate_json_report", "generate_csv_report"]
