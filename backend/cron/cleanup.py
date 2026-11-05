"""
Cron jobs for B2C cart/checkout maintenance.

Run periodically via a background thread or external cron.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("cron")

# Sliding 7-day cart TTL (in days)
CART_TTL_DAYS = 7
CHECKOUT_TTL_DAYS = 1


def cleanup_dedup_table():
    """Remove dedup records older than 24 hours."""
    try:
        from database import get_db_context
        with get_db_context() as conn:
            cur = conn.execute(
                "DELETE FROM webhook_dedup WHERE processed_at < datetime('now', '-1 day')"
            )
            if cur.rowcount > 0:
                logger.info(f"Cleaned {cur.rowcount} dedup records")
    except Exception as e:
        logger.error(f"Dedup cleanup failed: {e}")


def cleanup_outbox():
    """Mark old failed outbox entries as dead-letter."""
    try:
        from database import get_db_context
        with get_db_context() as conn:
            cur = conn.execute(
                "UPDATE whatsapp_outbox SET status = 'dead_letter' "
                "WHERE status = 'failed' AND attempts >= 3 "
                "AND created_at < datetime('now', '-7 day')"
            )
            if cur.rowcount > 0:
                logger.info(f"Dead-lettered {cur.rowcount} outbox entries")
    except Exception as e:
        logger.error(f"Outbox cleanup failed: {e}")


def cleanup_expired_carts():
    """Deactivate carts whose active session has exceeded the sliding TTL.

    Uses updated_at as the sliding window anchor. Carts deactivated here are
    counted in metrics.cart_expired_total when prometheus is available.
    """
    try:
        from database import get_db_context
        cutoff = (datetime.now() - timedelta(days=CART_TTL_DAYS)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        with get_db_context() as conn:
            rows = conn.execute(
                """SELECT id, wa_id FROM carts
                   WHERE status = 'active' AND updated_at < ?""",
                (cutoff,),
            ).fetchall()
            if not rows:
                return 0
            for row in rows:
                conn.execute(
                    "UPDATE carts SET status = 'expired', updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (row["id"],),
                )
            try:
                from services.metrics import cart_expired_total
                cart_expired_total.labels(user_type="b2c").inc(len(rows))
            except Exception:
                # deliberate: metrics are observability only — cleanup continues.
                logger.debug("METRICS_UPDATE_SKIPPED | cart_expired_total", exc_info=True)
            logger.info(f"Expired {len(rows)} idle carts")
            return len(rows)
    except Exception as e:
        logger.error(f"Cart TTL cleanup failed: {e}")
        return 0


def cleanup_expired_checkouts():
    """Mark expired checkout sessions as abandoned."""
    try:
        from database import get_db_context
        with get_db_context() as conn:
            cur = conn.execute(
                """UPDATE checkout_sessions SET state = 'abandoned', updated_at = CURRENT_TIMESTAMP
                   WHERE state NOT IN ('completed', 'abandoned', 'failed')
                     AND expires_at < datetime('now')"""
            )
            if cur.rowcount > 0:
                logger.info(f"Abandoned {cur.rowcount} expired checkout sessions")
            return cur.rowcount
    except Exception as e:
        logger.error(f"Checkout TTL cleanup failed: {e}")
        return 0


def run_all():
    cleanup_dedup_table()
    cleanup_outbox()
    cleanup_expired_carts()
    cleanup_expired_checkouts()


if __name__ == "__main__":
    run_all()