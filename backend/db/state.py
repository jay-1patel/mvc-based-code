"""User conversation state and human-handover flags data access — extracted from backend/database.py."""
import json
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
                "SELECT tenant_id FROM user_states WHERE wa_id = ? "
                "AND tenant_id IS NOT NULL AND TRIM(tenant_id) != '' "
                "ORDER BY updated_at DESC LIMIT 1",
                (wa_id,),
            ).fetchone()
            if row and row["tenant_id"]:
                return str(row["tenant_id"])
    except Exception:
        # deliberate: resolver chain — fall through to the next source.
        logger.debug("STATE_TENANT_RESOLVE_STEP_SKIPPED | source=user_states wa=%s", wa_id, exc_info=True)
    try:
        from shared.tenancy.resolver import resolve_default_tenant
        return resolve_default_tenant()
    except Exception:
        import os
        return os.environ.get("DEFAULT_TENANT_ID", "default")


def get_user_state(wa_id: str, tenant_id: Optional[str] = None) -> dict:
    tid = _resolve_tenant_id(wa_id, tenant_id)
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM user_states WHERE tenant_id = ? AND wa_id = ?",
            (tid, wa_id),
        ).fetchone()
        if row is None:
            # Legacy fallback: wa_id-only row before composite migration completed
            row = conn.execute(
                "SELECT * FROM user_states WHERE wa_id = ? LIMIT 1",
                (wa_id,),
            ).fetchone()
    if row:
        result = dict(row)
        try:
            result["context_json"] = json.loads(result.get("context_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            result["context_json"] = {}
        return result
    return {
        "wa_id": wa_id,
        "tenant_id": tid,
        "state": "MAIN_MENU",
        "context_json": {},
        "lang": "en",
    }


def set_user_state(
    wa_id: str,
    state: str,
    context: dict = None,
    lang: str = None,
    tenant_id: Optional[str] = None,
) -> bool:
    tid = _resolve_tenant_id(wa_id, tenant_id)
    with get_db_context() as conn:
        context_json = json.dumps(context or {})
        if lang:
            conn.execute(
                """INSERT INTO user_states (tenant_id, wa_id, state, context_json, lang)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
                     state = excluded.state,
                     context_json = excluded.context_json,
                     lang = excluded.lang,
                     updated_at = CURRENT_TIMESTAMP""",
                (tid, wa_id, state, context_json, lang),
            )
        else:
            conn.execute(
                """INSERT INTO user_states (tenant_id, wa_id, state, context_json)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
                     state = excluded.state,
                     context_json = excluded.context_json,
                     updated_at = CURRENT_TIMESTAMP""",
                (tid, wa_id, state, context_json),
            )
    return True


def update_user_context(wa_id: str, context: dict, tenant_id: Optional[str] = None) -> bool:
    current = get_user_state(wa_id, tenant_id=tenant_id)
    merged = {**current.get("context_json", {}), **context}
    return set_user_state(
        wa_id,
        current.get("state", "MAIN_MENU"),
        merged,
        tenant_id=tenant_id or current.get("tenant_id"),
    )


def clear_user_context(wa_id: str, tenant_id: Optional[str] = None) -> bool:
    return set_user_state(wa_id, "MAIN_MENU", {}, tenant_id=tenant_id)


def set_human_handover(wa_id: str, active: bool, tenant_id: Optional[str] = None) -> bool:
    """Set or clear the human-handover state for a conversation.

    When ``active`` is True the conversation enters the live human inbox and any
    previous "resolved" marker is cleared. When False the human session is
    terminated, control returns to the bot, and ``handover_resolved_at`` is
    stamped so the live inbox stops showing the conversation.
    """
    tid = _resolve_tenant_id(wa_id, tenant_id)
    try:
        with get_db_context() as conn:
            if active:
                conn.execute(
                    """
                    INSERT INTO user_states (tenant_id, wa_id, human_handover, updated_at, handover_resolved_at)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP, NULL)
                    ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
                        human_handover = 1,
                        updated_at = CURRENT_TIMESTAMP,
                        handover_resolved_at = NULL
                    """,
                    (tid, wa_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_states (tenant_id, wa_id, human_handover, updated_at, handover_resolved_at)
                    VALUES (?, ?, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
                        human_handover = 0,
                        updated_at = CURRENT_TIMESTAMP,
                        handover_resolved_at = CURRENT_TIMESTAMP
                    """,
                    (tid, wa_id),
                )
        return True
    except Exception as e:
        logger.error(f"set_human_handover failed for {wa_id}: {e}")
        return False


def reopen_conversation_on_inbound(wa_id: str, tenant_id: Optional[str] = None) -> bool:
    """Clear a stale ``handover_resolved_at`` so a new inbound message puts the
    conversation back in the live inbox.
    """
    tid = _resolve_tenant_id(wa_id, tenant_id)
    try:
        with get_db_context() as conn:
            conn.execute(
                "UPDATE user_states SET handover_resolved_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE tenant_id = ? AND wa_id = ? AND handover_resolved_at IS NOT NULL",
                (tid, wa_id),
            )
            # Also clear any legacy wa_id-only rows
            conn.execute(
                "UPDATE user_states SET handover_resolved_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE wa_id = ? AND handover_resolved_at IS NOT NULL",
                (wa_id,),
            )
        return True
    except Exception as e:
        logger.error(f"reopen_conversation_on_inbound failed for {wa_id}: {e}")
        return False
