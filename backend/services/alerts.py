"""
Alerts service — sends alerts to a webhook (Slack/Discord/etc).
"""
import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger("alerts")


def send_alert(message: str, severity: str = "error") -> None:
    webhook_url = os.getenv("ALERT_WEBHOOK_URL")
    if not webhook_url:
        logger.warning(f"No ALERT_WEBHOOK_URL. Alert: {message}")
        return
    icon = "\U0001f6a8" if severity == "error" else "\u26a0\ufe0f"
    try:
        requests.post(
            webhook_url,
            json={
                "text": f"{icon} *[whatsapp-bot]* {message}",
                "attachments": [
                    {
                        "color": "danger" if severity == "error" else "warning",
                        "fields": [
                            {"title": "Severity", "value": severity, "short": True},
                            {"title": "Time", "value": datetime.utcnow().isoformat(), "short": True},
                        ],
                    }
                ],
            },
            timeout=5,
        )
    except Exception as e:
        logger.error(f"Alert send failed: {e}")
