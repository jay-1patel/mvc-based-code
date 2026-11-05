"""Key/value app settings and the active test tenant data access — extracted from backend/database.py."""
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


def get_app_setting(key: str, default: str = "") -> str:
    try:
        with get_db_context() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS app_settings (
                    key        TEXT PRIMARY KEY,
                    value      TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row and row["value"] is not None else default
    except Exception as e:
        logger.debug(f"get_app_setting error for key {key}: {e}")
        return default


def set_app_setting(key: str, value: str) -> None:
    with get_db_context() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (key, str(value or "")),
        )


def get_active_test_tenant() -> Optional[str]:
    val = get_app_setting("active_test_tenant", "").strip()
    return val or None


def set_active_test_tenant(tenant_id: Optional[str]) -> None:
    val = str(tenant_id or "").strip()
    if val:
        try:
            from shared.tenancy import store
            record = store.get_tenant(val)
            if not record:
                raise ValueError(f"Unknown tenant for WhatsApp test override: {val}")
            val = str(record.get("slug") or record.get("id") or val)
        except ValueError:
            raise
        except Exception:
            # deliberate: an unreadable tenant record must not block the
            # test-tenant override — the raw value is used as-is.
            logger.debug("TEST_TENANT_VALIDATE_SKIPPED | val=%s", val, exc_info=True)
    set_app_setting("active_test_tenant", val)
    # Clear conversation user states and carts so tester starts completely fresh with greeting/menu
    try:
        with get_db_context() as conn:
            conn.execute("DELETE FROM user_states")
            conn.execute("DELETE FROM carts")
            conn.execute("DELETE FROM cart_items")
    except Exception as e:
        logger.debug(f"Could not clear user_states: {e}")
    try:
        from shared.tenancy import cache as tenancy_cache
        tenancy_cache.clear()
    except Exception:
        # deliberate: cache clear is best-effort session hygiene.
        logger.debug("TENANCY_CACHE_CLEAR_SKIPPED", exc_info=True)
    # Fresh routing session: drop in-memory dedup + poller unrouted warnings so
    # the next WhatsApp "hi" is answered by the newly selected tenant.
    try:
        from services.dedup import clear_ephemeral_caches
        clear_ephemeral_caches()
    except Exception:
        try:
            from backend.services.dedup import clear_ephemeral_caches
            clear_ephemeral_caches()
        except Exception:
            # deliberate: cache clear is best-effort session hygiene.
            logger.debug("DEDUP_CACHE_CLEAR_SKIPPED", exc_info=True)
    try:
        from services import incoming_listener as _il
        _il._unrouted_warned_ids.clear()
    except Exception:
        try:
            from backend.services import incoming_listener as _il
            _il._unrouted_warned_ids.clear()
        except Exception:
            # deliberate: warned-id reset is best-effort session hygiene.
            logger.debug("POLLER_WARNED_IDS_CLEAR_SKIPPED", exc_info=True)
    # Content-fingerprint rows (c:…) block a repeated "hi" for ~25s across
    # webhook+poller. Clear them on switch so the new tenant can greet.
    try:
        with get_db_context() as conn:
            conn.execute("DELETE FROM webhook_dedup WHERE message_id LIKE 'c:%'")
    except Exception as e:
        logger.debug(f"Could not clear content dedup rows: {e}")
    if val:
        logger.info("ACTIVE_TEST_TENANT_READY | tenant=%s | WhatsApp will route here", val)
    else:
        logger.info("ACTIVE_TEST_TENANT_CLEARED | phone binding / default routing restored")
