"""
Periodic cleanup for the unified cart/checkout layer.

Runs alongside the handoff timeout monitor in the app lifespan. Expires:
  - abandoned carts idle > 7 days (sliding) — deactivated
  - checkout sessions older than the 15-minute TTL — status='expired'

Safe to run concurrently with the webhook worker: SQLite handles the writes
serially, and both updates are idempotent.
"""

import asyncio
import logging

logger = logging.getLogger("cart_cleanup")

# How often to run the sweep (seconds)
CHECK_INTERVAL = 300  # 5 minutes

CART_IDLE_DAYS = 7
CHECKOUT_TTL_MINUTES = 15


def _expire_carts() -> int:
    try:
        from backend.services.cart_service import expire_stale_carts
        return expire_stale_carts()
    except Exception as e:
        logger.error(f"expire_stale_carts failed: {e}")
        return 0


def _expire_checkouts() -> int:
    try:
        from backend.services.cart_service import expire_stale_checkouts
        return expire_stale_checkouts()
    except Exception as e:
        logger.error(f"expire_stale_checkouts failed: {e}")
        return 0


async def cart_cleanup_monitor(stop_event: asyncio.Event = None) -> None:
    """Background loop that expires stale carts and checkout sessions."""
    logger.info("Cart cleanup monitor started (interval=%ds)", CHECK_INTERVAL)

    while True:
        if stop_event is not None and stop_event.is_set():
            logger.info("Cart cleanup monitor stopping")
            break

        try:
            expired_carts = _expire_carts()
            expired_checkouts = _expire_checkouts()
            if expired_carts or expired_checkouts:
                logger.info(
                    "CART_CLEANUP | carts=%d checkouts=%d",
                    expired_carts, expired_checkouts,
                )
        except Exception as e:
            logger.error(f"Cart cleanup sweep failed: {e}")

        await asyncio.sleep(CHECK_INTERVAL)