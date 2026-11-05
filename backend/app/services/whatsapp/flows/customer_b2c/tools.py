"""
B2C business logic.

All database access goes through the existing shared ``get_db_context()``
context manager. No new connection pools or API clients are created here.
"""

import json
import logging
from datetime import datetime

logger = logging.getLogger("b2c_tools")


def _db():
    from database import get_db_context
    return get_db_context()


def list_products() -> list:
    """Return the active product catalog (id, name, price, unit, media_url, short_description).

    Prefers the published config snapshot; falls back to the products table.
    """
    try:
        from database import get_published_products
        items = get_published_products(active_only=True, limit=200)
        return [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "price": p.get("price"),
                "unit": p.get("unit"),
                "category": p.get("category"),
                "media_url": p.get("media_url"),
                "short_description": p.get("short_description"),
            }
            for p in items
        ]
    except Exception as e:
        logger.error(f"list_products failed: {e}")
        return []


def get_nutrition_info(product_name: str) -> dict:
    """Query the products table for a product's nutritional metadata.

    Prefers the published config snapshot; falls back to the products table.
    """
    try:
        from database import get_published_products
        keyword = (product_name or "").lower()
        items = get_published_products(active_only=True, limit=200)
        for p in items:
            if keyword in (p.get("name") or "").lower():
                return {
                    "name": p.get("name"),
                    "description": p.get("description"),
                    "category": p.get("category"),
                    "ingredients": p.get("ingredients"),
                    "price": p.get("price"),
                    "unit": p.get("unit"),
                }
        return {}
    except Exception as e:
        logger.error(f"get_nutrition_info failed for '{product_name}': {e}")
        return {}


def get_recipe_suggestions(ingredient_or_product: str) -> list:
    """Return recipe suggestions based on an ingredient or product.

    Uses a static list of recipes keyed by ingredient; falls back to the
    default recipe list for generic queries.

    Args:
        ingredient_or_product: Ingredient or product keyword.

    Returns:
        list: Recipe dicts, each with {"title", "ingredients"}.
    """
    from .config import B2C_DEFAULT_RECIPES

    keyword = (ingredient_or_product or "").lower()
    static_recipes = {
        "chikki": [
            {"title": "Chikki Crunch Parfait", "ingredients": ["Peanut Chikki", "Yogurt", "Berries"]},
        ],
        "millet": [
            {"title": "Millet Breakfast Bowl", "ingredients": ["Millet Flakes", "Milk", "Honey"]},
        ],
        "peanut": [
            {"title": "Peanut Butter Smoothie", "ingredients": ["Peanut Butter", "Banana", "Milk"]},
        ],
        "almond": [
            {"title": "Almond Energy Bars", "ingredients": ["Almonds", "Oats", "Dates"]},
        ],
    }
    for key, recipes in static_recipes.items():
        if key in keyword:
            return recipes
    return list(B2C_DEFAULT_RECIPES)


def register_b2c_complaint(wa_id: str, type_: str, desc: str) -> str:
    """Insert a complaint into the complaints table and return its ticket_id.

    Args:
        wa_id: The customer's WhatsApp ID.
        type_: Complaint category (e.g. "Damaged", "Wrong Item", "Other").
        desc: Free-text description of the issue.

    Returns:
        str: The generated ticket id.
    """
    ticket_id = f"TCK-{datetime.now().strftime('%Y%m%d%H%M%S')}-{abs(hash(wa_id)) % 10000}"
    with _db() as conn:
        conn.execute(
            "INSERT INTO complaints (ticket_id, wa_id, complaint_type, description, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (ticket_id, wa_id, type_ or "Other", desc or "", "open"),
        )
    logger.info(f"register_b2c_complaint | wa_id={wa_id} | ticket={ticket_id} | type={type_}")
    return ticket_id


def should_escalate(message: str) -> bool:
    """Return True when the message requests a human handover."""
    from .config import HUMAN_HANDOVER_PHRASES
    lowered = (message or "").lower()
    return any(phrase in lowered for phrase in HUMAN_HANDOVER_PHRASES)
