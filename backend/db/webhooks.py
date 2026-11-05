"""Webhook request/response logging and inbound dedupe data access — extracted from backend/database.py."""
import os
import json
import re
import sqlite3
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from .core import (
    DB_PATH, BASE_DIR, PROJECT_ROOT, logger, get_db, get_db_context,
    _ensure_columns, _loads_json, _current_tenant, set_request_tenant,
    get_request_tenant
)


WEBHOOK_LOG_MAX_ROWS = 5000


def log_webhook(
    direction,
    endpoint,
    payload,
    status_code=None,
    notes=None,
    response_time_ms=None,
    wa_id=None,
    tenant_id=None,
    message_id=None,
):
    # SECURITY FIX: Store payload hash instead of raw payload to prevent sensitive data leakage
    try:
        payload_str = json.dumps(payload) if not isinstance(payload, str) else payload
        # Compute hash of the payload for secure storage
        payload_hash = hashlib.sha256(payload_str.encode('utf-8', errors='replace')).hexdigest()
        payload_size = len(payload_str)
        with get_db_context() as conn:
            conn.execute(
                "INSERT INTO webhook_logs "
                "(direction, endpoint, payload_hash, payload_size, status_code, notes, "
                "response_time_ms, wa_id, tenant_id, message_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    direction,
                    endpoint,
                    payload_hash,
                    payload_size,
                    status_code,
                    notes,
                    response_time_ms,
                    wa_id,
                    tenant_id,
                    message_id,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            _trim_webhook_logs(conn)
    except Exception as e:
        logger.error(f"Failed to log webhook: {e}")


def _trim_webhook_logs(conn, max_rows: int = WEBHOOK_LOG_MAX_ROWS):
    try:
        count = conn.execute("SELECT COUNT(*) FROM webhook_logs").fetchone()[0]
        if count > max_rows:
            excess = count - max_rows
            conn.execute(
                "DELETE FROM webhook_logs WHERE id IN "
                "(SELECT id FROM webhook_logs ORDER BY created_at ASC, id ASC LIMIT ?)",
                (excess,),
            )
    except Exception as e:
        logger.error(f"Failed to trim webhook_logs: {e}")


def get_webhook_stats():
    with get_db_context() as conn:
        total = conn.execute("SELECT COUNT(*) as cnt FROM webhook_logs").fetchone()["cnt"]
        incoming = conn.execute("SELECT COUNT(*) as cnt FROM webhook_logs WHERE direction = 'incoming'").fetchone()["cnt"]
        outgoing = conn.execute("SELECT COUNT(*) as cnt FROM webhook_logs WHERE direction = 'outgoing'").fetchone()["cnt"]
        last_in = conn.execute("SELECT created_at FROM webhook_logs WHERE direction = 'incoming' ORDER BY created_at DESC LIMIT 1").fetchone()
        last_out = conn.execute("SELECT created_at FROM webhook_logs WHERE direction = 'outgoing' ORDER BY created_at DESC LIMIT 1").fetchone()
        return {
            "total": total,
            "incoming": incoming,
            "outgoing": outgoing,
            "last_incoming": last_in["created_at"] if last_in else None,
            "last_outgoing": last_out["created_at"] if last_out else None,
        }


def was_inbound_processed(message_id: str, channel: str = "send2") -> bool:
    """True if this channel or the webhook already processed the message (survives restarts).

    Used by the send2 poller and webhook receiver so a restart or dual-path
    delivery does not double-answer the message.
    """
    if not message_id:
        return False
    try:
        with get_db_context() as conn:
            row1 = conn.execute(
                "SELECT 1 FROM processed_inbound_messages WHERE message_id=?",
                (message_id,),
            ).fetchone()
            if row1 is not None:
                return True
            row2 = conn.execute(
                "SELECT 1 FROM webhook_dedup WHERE message_id=?",
                (message_id,),
            ).fetchone()
            return row2 is not None
    except Exception as e:
        # Fail closed: treat as already processed so a locked DB cannot
        # re-answer the poller's 15-minute window.
        logger.error(
            "DEDUP_FAIL_CLOSED | was_inbound_processed check failed for %s: %s",
            message_id, e,
        )
        return True


def mark_inbound_processed(message_id: str, wa_id: str, channel: str = "send2") -> None:
    if not message_id:
        return
    try:
        with get_db_context() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_inbound_messages (message_id, wa_id, channel) VALUES (?, ?, ?)",
                (message_id, wa_id, channel),
            )
            conn.execute(
                "INSERT OR IGNORE INTO webhook_dedup (message_id, wa_id, processed_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (message_id, wa_id),
            )
    except Exception as e:
        logger.error(f"mark_inbound_processed failed for {message_id}: {e}")
