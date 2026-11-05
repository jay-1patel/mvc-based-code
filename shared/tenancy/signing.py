"""HMAC signing for outbound tenant webhooks (decision D2 / Phase 0.5).

Per-tenant secret, resolved from the tenants table, falling back to the
published profile's webhook channel. Headers follow the common convention:

    X-Signature-256: sha256=<hex hmac of the raw body>
    X-Tenant-Id:    <tenant_id>
    X-Timestamp:    <unix seconds>
    X-Event:        <event name>

A receiver verifies by recomputing the HMAC over the raw body. If no secret is
configured we refuse to send rather than send unsigned — a CRM silently
receiving forged lead payloads is worse than a logged failure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("tenancy.signing")

HEADER_SIGNATURE = "X-Signature-256"
HEADER_TENANT = "X-Tenant-Id"
HEADER_TIMESTAMP = "X-Timestamp"
HEADER_EVENT = "X-Event"
MAX_SKEW_SECONDS = 300


def sign_body(secret: str, body: bytes, timestamp: Optional[int] = None) -> str:
    ts = int(timestamp if timestamp is not None else time.time())
    payload = f"{ts}.".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_body(secret: str, body: bytes, signature: str, timestamp: Optional[int] = None) -> bool:
    if not secret or not signature:
        return False
    expected = sign_body(secret, body, timestamp)
    return hmac.compare_digest(expected, signature.strip())


def build_signed_request(tenant_id: str, url: str, event: str,
                         payload: Dict[str, Any], timeout: float = 10.0) -> Optional[dict]:
    """POST a signed JSON payload. Returns a result dict, or None if skipped."""
    from . import store

    secret = store.get_tenant_webhook_secret(tenant_id)
    if not secret:
        logger.warning("WEBHOOK_UNSIGNED | tenant=%s | event=%s | no secret configured, skipping",
                       tenant_id, event)
        return None

    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        HEADER_SIGNATURE: sign_body(secret, body, ts),
        HEADER_TENANT: tenant_id,
        HEADER_TIMESTAMP: str(ts),
        HEADER_EVENT: event,
        "User-Agent": "wa-support-platform/1.0",
    }

    try:
        import requests

        resp = requests.post(url, data=body, headers=headers, timeout=timeout)
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("WEBHOOK_FAILED | tenant=%s | event=%s | status=%s | %s",
                           tenant_id, event, resp.status_code, resp.text[:200])
        return {"ok": ok, "status_code": resp.status_code, "event": event,
                "tenant_id": tenant_id, "url": url}
    except Exception as exc:
        logger.error("WEBHOOK_ERROR | tenant=%s | event=%s | %s", tenant_id, event, exc)
        return {"ok": False, "error": str(exc), "event": event, "tenant_id": tenant_id, "url": url}


def notify(tenant_id: str, event: str, payload: Dict[str, Any],
           channels: Optional[list] = None) -> list:
    """Fan a notification out to a tenant's channels.

    ``channels`` is a list of :class:`NotificationChannel`. When omitted, every
    active channel in the profile is used. Returns one result dict per channel.
    """
    from .loader import get_tenant_profile

    profile = get_tenant_profile(tenant_id)
    targets = channels if channels is not None else profile.notifications.active_channels()
    results: list = []
    for ch in targets:
        if ch.type == "email":
            results.append(_send_email(profile, ch.to, event, payload, secret=ch.secret))
        elif ch.type == "webhook":
            results.append(build_signed_request(tenant_id, ch.to, event, payload))
        else:
            logger.warning("Unknown notification channel type: %r", ch.type)
    return [r for r in results if r is not None]


def _send_email(profile, to: str, event: str, payload: Dict[str, Any], secret: str = "") -> dict:
    from_email = profile.notifications.sales_email or profile.brand.support_email
    subject = f"[{profile.brand.display_name() or profile.tenant_id}] {event}"
    lines = [f"{k}: {v}" for k, v in payload.items() if not isinstance(v, (dict, list))]
    body = "\n".join(lines) or "(no fields)"
    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = from_email
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as srv:
            srv.starttls()
            import os

            user = os.getenv("SMTP_USERNAME", "")
            pwd = os.getenv("SMTP_PASSWORD", "")
            if user and pwd:
                srv.login(user, pwd)
            srv.send_message(msg)
        logger.info("EMAIL_SENT | tenant=%s | event=%s | to=%s", profile.tenant_id, event, to)
        return {"ok": True, "channel": "email", "event": event, "to": to}
    except Exception as exc:
        logger.error("EMAIL_FAILED | tenant=%s | event=%s | to=%s | %s",
                     profile.tenant_id, event, to, exc)
        return {"ok": False, "channel": "email", "event": event, "to": to, "error": str(exc)}


def sha256_hex(data: str) -> Tuple[str, str]:
    """Convenience for storing API tokens: returns (hex_digest, ...)."""
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    return digest, digest[:12]
