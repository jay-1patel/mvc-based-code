"""Shared foundation: paths, connection helpers, request-scoped tenant context.

Extracted from backend/database.py (header block). Every other db_* module
imports its globals from here; backend/database.py re-exports from here too.
"""

import os
import sys as _sys
import json
import sqlite3
import logging
import hashlib
import re
import contextvars
from ipaddress import ip_address as _parse_ip_address, ip_network
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional, Any, Dict, List

logger = logging.getLogger("chiki_webhook")

# ── Single database shared by the app, the FAQ bot and the KB bot ─────────
# routing.config resolves DB_PATH from ROUTING_DB_PATH (routing/.env) and the
# FAQ/KB indexes open that file. This module must open the SAME file, otherwise
# the admin UI (chat_history / webhook_logs / customers) reads a different,
# empty faq.db than the bot writes to.
#
# routing/.env is loaded here too because this module can be imported before
# the app calls load_dotenv(). The resolution logic mirrors routing.config
# exactly (relative paths resolve against the repo root).
# Anchored so paths resolve identically to the old backend/database.py.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
PROJECT_ROOT = os.path.dirname(BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, "routing", ".env"))
except Exception:  # pragma: no cover - dotenv missing is not fatal
    logger.debug("DOTENV_LOAD_SKIPPED | continuing without routing/.env", exc_info=True)


def _resolve_db_path() -> str:
    raw = (os.getenv("ROUTING_DB_PATH") or "").strip()
    if raw:
        if not os.path.isabs(raw):
            raw = os.path.join(PROJECT_ROOT, raw)
        return os.path.normpath(raw)
    return os.path.join(PROJECT_ROOT, "faq.db")


DB_PATH = _resolve_db_path()

# ── Helpers ──────────────────────────────────────────────────────────────

def _ensure_columns(conn, table, columns):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for col_name, col_type in columns:
        if col_name not in existing:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added column {col_name} {col_type} to {table}")
            except Exception as e:
                logger.error(f"Failed to add column {col_name} to {table}: {e}")


def _loads_json(value, default=None):
    try:
        return json.loads(value) if value else (default if default is not None else {})
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


# ── Request-scoped tenant ────────────────────────────────────────────────
# The live webhook resolves the tenant from the inbound WABA phone-id and calls
# `set_request_tenant` before handling the message. `save_chat` and friends then
# attribute rows to that tenant without threading an argument through every
# call site. A contextvar (not a global) so concurrent requests cannot bleed.

_current_tenant: contextvars.ContextVar = contextvars.ContextVar("current_tenant", default=None)
_audit_request_context: contextvars.ContextVar = contextvars.ContextVar("audit_request_context", default=None)


def set_audit_request_context(*, ip_address: str | None = None, user_agent: str | None = None) -> None:
    """Attach trusted request metadata to audit writes in this request context."""
    _audit_request_context.set({
        "ip_address": (ip_address or "")[:64] or None,
        "user_agent": (user_agent or "")[:512] or None,
    })


def clear_audit_request_context() -> None:
    _audit_request_context.set(None)


def trusted_client_ip(peer_ip: str | None, forwarded_for: str | None = None) -> str | None:
    """Resolve client IP, trusting forwarded-for only behind configured proxies."""
    try:
        peer = _parse_ip_address(str(peer_ip or "").strip())
    except ValueError:
        return None

    trusted = []
    for entry in os.getenv("TRUSTED_PROXY_IPS", "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            trusted.append(ip_network(entry, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid TRUSTED_PROXY_IPS entry")
    if not any(peer in network for network in trusted):
        return str(peer)

    # Walk from nearest to farthest proxy. The first untrusted address is the
    # client address; ignore malformed forwarded entries.
    try:
        chain = [_parse_ip_address(part.strip()) for part in (forwarded_for or "").split(",") if part.strip()]
    except ValueError:
        return str(peer)
    for candidate in reversed(chain):
        if not any(candidate in network for network in trusted):
            return str(candidate)
    return str(chain[0]) if chain else str(peer)


def set_request_tenant(tenant_id) -> None:
    """Attribute rows written during this request to `tenant_id`."""
    _current_tenant.set(str(tenant_id).strip() if tenant_id else None)


def get_request_tenant():
    return _current_tenant.get()


# ── Connection helpers ───────────────────────────────────────────────────

def active_db_path() -> str:
    """Resolve the database path at call time.

    `DB_PATH` (this module's constant) is the default, but the facade
    (`backend/database.py`) and every test suite point the app at a throwaway
    file by patching `database.DB_PATH` before `init_db()`. Reading the facade's
    attribute here keeps that contract working after the module split.
    """
    for name in ("database", "backend.database"):
        facade = _sys.modules.get(name)
        path = getattr(facade, "DB_PATH", None) if facade else None
        if path:
            return path
    return DB_PATH


def _facade_ref(name, default=None):
    """Resolve `name` through the public facade at call time.

    Before the module split, every query lived in one file and resolved helpers
    via module globals, so `patch.object(database, "get_db", ...)` in tests
    took effect everywhere. This helper preserves that contract: the facade's
    current attribute wins, falling back to this module's own object.
    """
    for modname in ("database", "backend.database"):
        mod = _sys.modules.get(modname)
        if mod is not None and hasattr(mod, name):
            return getattr(mod, name)
    return default


def get_db():
    # Uses the absolute path we defined at the top of the file
    conn = sqlite3.connect(active_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        # deliberate: pragmas are tuning — the connection is still usable
        # with sqlite defaults.
        logger.debug("DB_PRAGMA_SKIPPED", exc_info=True)
    return conn


@contextmanager
def get_db_context():
    # Resolved at call time so a test that patches `database.get_db`
    # (in-memory fixture connections) also steers every context user.
    conn = _facade_ref("get_db", get_db)()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
