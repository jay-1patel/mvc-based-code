"""24h session-open helpers data access — extracted from backend/database.py."""
from datetime import datetime, timezone
from typing import Optional

from .core import (
    logger, get_db_context,
    _current_tenant, get_request_tenant
)


def _resolve_tenant_id(wa_id: str, tenant_id: Optional[str] = None) -> str:
    tid = (tenant_id or get_request_tenant() or _current_tenant.get() or "").strip()
    if tid:
        return tid
    try:
        with get_db_context() as conn:
            row = conn.execute(
                "SELECT tenant_id FROM user_sessions WHERE wa_id = ? "
                "AND tenant_id IS NOT NULL AND TRIM(tenant_id) != '' "
                "LIMIT 1",
                (wa_id,),
            ).fetchone()
            if row and row["tenant_id"]:
                return str(row["tenant_id"])
    except Exception:
        # deliberate: resolver chain — fall through to the next source.
        logger.debug("SESSION_TENANT_RESOLVE_STEP_SKIPPED | source=user_sessions wa=%s", wa_id, exc_info=True)
    try:
        from shared.tenancy.resolver import resolve_default_tenant
        return resolve_default_tenant()
    except Exception:
        import os
        return os.environ.get("DEFAULT_TENANT_ID", "default")


def update_session_inbound(wa_id: str, tenant_id: Optional[str] = None):
    tid = _resolve_tenant_id(wa_id, tenant_id)
    with get_db_context() as conn:
        conn.execute(
            """INSERT INTO user_sessions (tenant_id, wa_id, last_inbound_at, session_open)
               VALUES (?, ?, CURRENT_TIMESTAMP, 1)
               ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
                 last_inbound_at = CURRENT_TIMESTAMP,
                 session_open = 1""",
            (tid, wa_id),
        )


def update_session_outbound(wa_id: str, tenant_id: Optional[str] = None):
    tid = _resolve_tenant_id(wa_id, tenant_id)
    with get_db_context() as conn:
        conn.execute(
            """INSERT INTO user_sessions (tenant_id, wa_id, last_outbound_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
                 last_outbound_at = CURRENT_TIMESTAMP""",
            (tid, wa_id),
        )


def is_session_open(wa_id: str, window_hours: int = 24, tenant_id: Optional[str] = None) -> bool:
    tid = _resolve_tenant_id(wa_id, tenant_id)
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT last_inbound_at FROM user_sessions WHERE tenant_id = ? AND wa_id = ?",
            (tid, wa_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT last_inbound_at FROM user_sessions WHERE wa_id = ? LIMIT 1",
                (wa_id,),
            ).fetchone()
    if not row or not row["last_inbound_at"]:
        return False
    try:
        last = datetime.strptime(row["last_inbound_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - last).total_seconds() < window_hours * 3600
    except Exception:
        return False


def get_session_info(wa_id: str, tenant_id: Optional[str] = None) -> dict:
    tid = _resolve_tenant_id(wa_id, tenant_id)
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM user_sessions WHERE tenant_id = ? AND wa_id = ?",
            (tid, wa_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM user_sessions WHERE wa_id = ? LIMIT 1",
                (wa_id,),
            ).fetchone()
    return dict(row) if row else {}
