"""
Per-user shopping cart — now a thin delegate over the unified cart service
(backend/services/cart_service.py, SQLite-backed: survives restarts, multi-worker safe).

Migration (Phase 2): writes go to the unified store only. Reads check the
unified store first, then fall back to the legacy in-process dict for carts
created before this deploy. The legacy dict drains out within its old 24h TTL;
every fallback read is logged so the gate (zero fallback reads for 48h) is
measurable. TODO(phase-2-gate): delete _legacy_carts once the gate passes.
"""
import logging
import time

logger = logging.getLogger("My Business_bot")

# ── Legacy in-process store (read-only fallback during migration drain) ──────
_CART_TTL_SECONDS = 24 * 60 * 60
_legacy_carts: dict = {}


def _fallback_get(wa_id: str) -> list:
    """Legacy memory read, TTL-checked. Logged for the phase-2 gate."""
    cart = _legacy_carts.get(wa_id)
    if not cart:
        return []
    updated_at = cart.get("updated_at", 0)
    if not updated_at or (time.time() - updated_at) >= _CART_TTL_SECONDS:
        _legacy_carts.pop(wa_id, None)
        return []
    logger.info(f"CART_LEGACY_FALLBACK_READ | wa_id={wa_id}")
    return cart.get("items", [])


def _svc():
    """Lazy import of the unified cart service (avoids import cycles)."""
    import sys, os
    _root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if _root not in sys.path:
        sys.path.insert(0, _root)
    from backend.services import cart_service
    return cart_service


# ── Public API (unchanged signatures for kb_handler) ─────────────────────────

def add_to_cart(wa_id: str, product: dict, quantity: int = 1) -> dict:
    """Add a product to the user's cart. Returns {'items': [...]} for compat."""
    svc = _svc()
    result = svc.add_to_cart(wa_id, product, quantity)
    # Drop any stale legacy copy so the unified store wins from now on.
    _legacy_carts.pop(wa_id, None)
    return {"items": get_cart(wa_id), "ok": result.get("ok", False)}


def get_cart(wa_id: str) -> list:
    """Unified items; legacy memory fallback during the drain window.

    Items look like {product_id, name, qty, price_at_add}; a 'price'/'quantity'
    view is provided so existing kb_handler code keeps working.
    """
    svc = _svc()
    items = svc.get_cart(wa_id)
    if items:
        return [
            {
                "product_id": it["product_id"],
                "name": it["name"],
                "qty": it["qty"],
                "quantity": it["qty"],      # legacy key compat
                "price": it["price_at_add"],  # legacy key compat
                "price_at_add": it["price_at_add"],
            }
            for it in items
        ]
    return _fallback_get(wa_id)


def cart_total(wa_id: str):
    items = get_cart(wa_id)
    total = 0
    for item in items:
        price = item.get("price", item.get("price_at_add"))
        try:
            total += float(price) * int(item.get("qty", item.get("quantity", 1)))
        except (TypeError, ValueError):
            continue
    return round(total, 2)


def cart_count(wa_id: str) -> int:
    items = get_cart(wa_id)
    return sum(int(it.get("qty", it.get("quantity", 1))) for it in items)


def clear_cart(wa_id: str) -> None:
    _svc().clear_cart(wa_id)
    _legacy_carts.pop(wa_id, None)


def format_cart_text(wa_id: str) -> str:
    return _svc().format_cart_text(wa_id)
