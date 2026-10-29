"""Alerting: webhook (Slack/Teams/generic) and SMTP email stub.

The webhook format auto-detects Slack (``{"blocks": …}``) vs generic JSON.
Email uses Python's ``smtplib``; SMTP settings come from environment variables
so no credentials are ever stored in the database.

Alerting is best-effort and non-blocking: a delivery failure is logged and
stored in the summary but does not fail the monitoring run.
"""

from __future__ import annotations

import json
import logging
import smtplib
import socket
from email.mime.text import MIMEText
from typing import Any

import requests

from app.core.config import Settings
from app.core.security import validate_webhook_url

logger = logging.getLogger(__name__)

_TIMEOUT = 10
_policy = {"allow_private": False}


def configure(settings: Settings) -> None:
    """Apply the webhook policy from settings (called once by create_app)."""
    _policy["allow_private"] = bool(getattr(settings, "allow_private_webhooks", False))


def _post(url: str, payload: dict[str, Any]) -> requests.Response:
    """POST after the SSRF check; redirects are not followed (a redirect could point at an internal address)."""
    validate_webhook_url(url, allow_private=_policy["allow_private"])
    return requests.post(url, json=payload, timeout=_TIMEOUT, allow_redirects=False)


# ──────────────────────────────────── payload builders ──────────────────────


def _slack_payload(run_summary: dict[str, Any], dataset_name: str, run_id: str,
                   dashboard_url: str) -> dict[str, Any]:
    q = run_summary.get("quality", {})
    s = run_summary.get("score") or {}
    drift = run_summary.get("drift") or {}
    status = q.get("status", "unknown")
    color = {"healthy": "#22c55e", "degraded": "#eab308", "failing": "#ef4444"}.get(status, "#64748b")
    score_text = f"{s.get('overall', 0):.1f}" if s.get("overall") is not None else "–"
    text = (
        f"*DataSentinel run completed* — `{dataset_name}`\n"
        f"Health: *{status.title()}*  |  Score: *{score_text}*  |  "
        f"Drifted features: *{drift.get('features_drifted', '–')}*\n"
        f"<{dashboard_url}|Open Dashboard>"
    )
    return {"text": text, "blocks": [{"type": "section",
                                       "text": {"type": "mrkdwn", "text": text}}]}


def _teams_payload(run_summary: dict[str, Any], dataset_name: str, run_id: str,
                   dashboard_url: str) -> dict[str, Any]:
    q = run_summary.get("quality", {})
    status = q.get("status", "unknown")
    return {
        "@type": "MessageCard", "@context": "http://schema.org/extensions",
        "themeColor": {"healthy": "22c55e", "degraded": "eab308", "failing": "ef4444"}.get(status, "64748b"),
        "summary": f"DataSentinel — {dataset_name} monitoring run",
        "sections": [{"activityTitle": f"DataSentinel: {dataset_name}",
                      "activitySubtitle": f"Health: {status.title()}",
                      "text": json.dumps(run_summary, indent=2)[:1000]}],
        "potentialAction": [{"@type": "OpenUri", "name": "Open dashboard",
                             "targets": [{"os": "default", "uri": dashboard_url}]}],
    }


def _generic_payload(run_summary: dict[str, Any], dataset_name: str, run_id: str,
                     dashboard_url: str) -> dict[str, Any]:
    return {"event": "monitor_run_completed", "dataset": dataset_name, "run_id": run_id,
            "summary": run_summary, "dashboard_url": dashboard_url}


# ──────────────────────────────────────── delivery ──────────────────────────


def _detect_format(url: str) -> str:
    if "hooks.slack.com" in url:
        return "slack"
    if "webhook.office.com" in url or "outlook.office.com" in url:
        return "teams"
    return "generic"


def send_webhook(
    url: str,
    run_summary: dict[str, Any],
    dataset_name: str,
    run_id: str,
    dashboard_url: str = "http://localhost:8501",
) -> dict[str, Any]:
    """POST a webhook. Returns ``{ok, status_code, error}``."""
    fmt = _detect_format(url)
    builders = {"slack": _slack_payload, "teams": _teams_payload, "generic": _generic_payload}
    payload = builders[fmt](run_summary, dataset_name, run_id, dashboard_url)
    try:
        resp = _post(url, payload)
        resp.raise_for_status()
        logger.info("Alert webhook %s delivered (HTTP %s)", fmt, resp.status_code)
        return {"ok": True, "status_code": resp.status_code, "error": None}
    except ValueError as exc:  # blocked by the SSRF policy
        logger.warning("Alert webhook blocked: %s", exc)
        return {"ok": False, "status_code": None, "error": str(exc)}
    except requests.RequestException as exc:
        # Do not log the URL: Slack/Teams webhook URLs are secrets.
        msg = type(exc).__name__
        logger.warning("Alert webhook failed: %s", msg)
        return {"ok": False, "status_code": None, "error": f"Delivery failed ({msg})."}


def send_email_alert(
    to: str,
    run_summary: dict[str, Any],
    dataset_name: str,
    settings: Settings,
) -> dict[str, Any]:
    """Send a plain-text email. Returns ``{ok, error}``."""
    smtp_host = getattr(settings, "smtp_host", None)
    smtp_port = getattr(settings, "smtp_port", 587)
    smtp_user = getattr(settings, "smtp_user", None)
    smtp_pass = getattr(settings, "smtp_password", None)
    smtp_from = getattr(settings, "smtp_from", smtp_user or "datasentinel@localhost")
    if not smtp_host:
        return {"ok": False, "error": "SMTP not configured (DS_SMTP_HOST)."}
    q = run_summary.get("quality", {})
    body = (
        f"DataSentinel monitoring run completed for '{dataset_name}'.\n\n"
        f"Health status: {q.get('status', 'unknown')}\n"
        f"Summary:\n{json.dumps(run_summary, indent=2)[:2000]}"
    )
    msg = MIMEText(body)
    msg["Subject"] = f"DataSentinel alert: {dataset_name} — {q.get('status', 'unknown').title()}"
    msg["From"] = smtp_from
    msg["To"] = to
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=_TIMEOUT) as s:
            s.ehlo()
            if smtp_port in (587, 465):
                s.starttls()
            if smtp_user and smtp_pass:
                s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, [to], msg.as_string())
        return {"ok": True, "error": None}
    except (smtplib.SMTPException, socket.error, OSError) as exc:
        msg_str = str(exc)
        logger.warning("Email alert failed: %s", msg_str)
        return {"ok": False, "error": msg_str}


def send_test_alert(url: str) -> dict[str, Any]:
    """POST a recognisably fake payload so users can verify the webhook without a real run."""
    fake_summary = {
        "quality": {"status": "healthy", "outcomes": {"pass": 30, "fail": 0, "warn": 0}},
        "score": {"overall": 100.0, "status": "healthy"},
        "drift": {"features_drifted": 0, "features_evaluated": 10},
        "incidents": {"created": 0, "updated": 0},
    }
    payload = _generic_payload(fake_summary, "DataSentinel test", "test-run-id",
                               "http://localhost:8501")
    payload["event"] = "test_alert"
    payload["message"] = "This is a test alert from DataSentinel."
    try:
        resp = _post(url, payload)
        return {"ok": resp.status_code < 400, "status_code": resp.status_code, "error": None}
    except ValueError as exc:
        return {"ok": False, "status_code": None, "error": str(exc)}
    except requests.RequestException as exc:
        return {"ok": False, "status_code": None, "error": f"Delivery failed ({type(exc).__name__})."}


__all__ = ["configure", "send_webhook", "send_email_alert", "send_test_alert"]
