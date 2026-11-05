"""Outbound WhatsApp send-reason ledger.

Every customer-facing send should call ``log_whatsapp_send`` with a stable
``reason`` so Friday-style "who texted this number?" incidents are answerable
from SQLite instead of guesswork.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("send_log")

# Stable reason codes used across callers and the ops runbook.
REASON_REPLY = "reply"
REASON_HANDOFF_TIMEOUT = "handoff_timeout"
REASON_CAMPAIGN = "campaign"
REASON_INBOX = "inbox"
REASON_ACTIVE_TEST_MENU = "active_test_menu"
REASON_NULL_MENU = "null_menu"
REASON_COMPLAINT = "complaint"
REASON_CALLBACK = "callback"
REASON_OTHER = "other"


def log_whatsapp_send(
    *,
    wa_id: str,
    reason: str,
    tenant_id: Optional[str] = None,
    trigger_message_id: Optional[str] = None,
    snippet: Optional[str] = None,
) -> None:
    """Insert one outbound send audit row. Never raises to callers."""
    if not wa_id:
        return
    try:
        from database import get_db_context

        text = (snippet or "")[:240]
        with get_db_context() as conn:
            conn.execute(
                """
                INSERT INTO whatsapp_send_log
                    (wa_id, tenant_id, reason, trigger_message_id, snippet, created_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    str(wa_id).strip(),
                    (tenant_id or None),
                    (reason or REASON_OTHER)[:64],
                    (trigger_message_id or None),
                    text or None,
                ),
            )
    except Exception as e:
        logger.warning(
            "send_log write failed wa_id=%s reason=%s: %s",
            wa_id, reason, e,
        )
