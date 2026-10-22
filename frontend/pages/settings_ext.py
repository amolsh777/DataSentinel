"""Alerting & scheduler additions — imported by settings.py via st.tabs extension."""
# This is a reusable block, not a standalone page.
# Call render_alerting_scheduler(client, dataset, state) from settings.py.
from __future__ import annotations

import streamlit as st
from components import ui
from services.api_client import ApiError


def render_alerting(c, ds: dict) -> None:
    """Webhook alerting config panel."""
    st.markdown("### Webhook alerting")
    st.caption("DataSentinel posts a JSON payload when a monitoring run fails or creates critical incidents. "
               "Slack, Teams and generic webhooks are auto-detected.")
    with st.form("alert_form"):
        url = st.text_input("Webhook URL", placeholder="https://hooks.slack.com/services/…")
        if st.form_submit_button("Send test alert"):
            if not url.strip():
                st.error("Enter a webhook URL.")
            else:
                try:
                    result = c.test_alert(ds["id"], url.strip())
                    if result.get("ok"):
                        st.success(f"Test alert delivered (HTTP {result.get('status_code')}).")
                    else:
                        st.error(f"Delivery failed: {result.get('error')}")
                except ApiError as exc:
                    ui.show_error(exc)
    st.caption("The webhook fires automatically when you use the scheduler below, or you can call it "
               "programmatically via `POST /datasets/{id}/alerts/test`.")


def render_scheduler(c, ds: dict) -> None:
    """Schedule automatic monitoring for a dataset."""
    st.markdown("### Automatic monitoring")
    try:
        sched = c.get_schedule(ds["id"])
    except ApiError as exc:
        ui.show_error(exc); return

    if sched["scheduled"]:
        cfg = sched["config"] or {}
        ui.callout("success",
                   f"Scheduled every {cfg.get('interval_hours','?')} hours. "
                   f"Next run: {sched.get('next_run','unknown')}.")
    else:
        ui.callout("info", "No schedule configured for this dataset.")

    with st.form("sched_form"):
        hours = st.number_input("Run every N hours (0 = disable)", min_value=0, max_value=720,
                                value=int((sched.get("config") or {}).get("interval_hours", 0)))
        webhook = st.text_input("Alert webhook URL (optional, fired on failure)",
                                value=(sched.get("config") or {}).get("alert_webhook", ""))
        excl_def = (sched.get("config") or {}).get("exclude_drift_columns", [])
        excl = st.text_input("Exclude from drift (comma-separated)", value=",".join(excl_def))
        if st.form_submit_button("Save schedule"):
            try:
                body = {
                    "interval_hours": int(hours) if hours > 0 else None,
                    "alert_webhook": webhook.strip() or None,
                    "exclude_drift_columns": [x.strip() for x in excl.split(",") if x.strip()],
                }
                result = c.set_schedule(ds["id"], body)
                if result["scheduled"]:
                    st.success(f"Monitoring scheduled every {hours} hours.")
                else:
                    st.success("Schedule removed.")
                st.rerun()
            except ApiError as exc:
                ui.show_error(exc)
