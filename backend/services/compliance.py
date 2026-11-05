"""
DPDP compliance: consent logging.
"""
import logging
from database import get_db_context

logger = logging.getLogger("compliance")


def log_consent(wa_id: str, consent_type: str, consent_text: str = None) -> None:
    """Idempotent consent log."""
    try:
        with get_db_context() as conn:
            existing = conn.execute(
                "SELECT id, opted_in FROM user_consents "
                "WHERE wa_id = ? AND consent_type = ?",
                (wa_id, consent_type),
            ).fetchone()
            if existing:
                if existing[1]:
                    return  # already opted in
                conn.execute(
                    "UPDATE user_consents SET opted_in = 1, opted_in_at = CURRENT_TIMESTAMP, "
                    "opted_out_at = NULL WHERE id = ?",
                    (existing[0],),
                )
            else:
                conn.execute(
                    "INSERT INTO user_consents "
                    "(wa_id, consent_type, opted_in, channel, consent_text) "
                    "VALUES (?, ?, 1, 'whatsapp', ?)",
                    (wa_id, consent_type,
                     consent_text or f"Implicit via {consent_type} on WhatsApp"),
                )
    except Exception as e:
        logger.error(f"log_consent failed {wa_id}: {e}")
