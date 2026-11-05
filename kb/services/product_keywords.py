"""
Dynamic product keyword provider.

Replaces hardcoded product-name lists (e.g. ["chikki", "millet", "peanut", ...])
in orchestrator.py and fuzzy_matcher.py. Keywords are derived from the live
`products` table (name + category words), so intent classification and fuzzy
"is this a product query" detection automatically adapt when products change
-- no code edit needed when the catalogue changes.

A short TTL cache avoids a DB hit on every incoming message.
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300  # refresh every 5 minutes
_cache = {"keywords": set(), "loaded_at": 0.0}

# Generic English stopwords that should never count as a "product indicator"
# even if they appear in a product name (e.g. "Classic Peanut Mix").
_STOPWORDS = {
    "the", "and", "with", "for", "pack", "box", "pcs", "piece", "pieces",
    "gm", "gms", "g", "kg", "ml", "new", "of", "a", "an", "in", "on",
    "premium", "special", "combo", "set", "assorted", "mix", "flavour",
    "flavor", "classic", "original",
}


def _load_from_db() -> set:
    """Fetch distinct significant words from product names and categories."""
    try:
        from ..database import get_db_context
    except ImportError:
        from database import get_db_context

    words = set()
    try:
        with get_db_context() as conn:
            rows = conn.execute(
                "SELECT DISTINCT name, category FROM products WHERE is_active = 1"
            ).fetchall()
        for row in rows:
            name = (row["name"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]) or ""
            category = (row["category"] if isinstance(row, dict) or hasattr(row, "keys") else row[1]) or ""
            for token in re.findall(r"[a-zA-Z]{3,}", f"{name} {category}".lower()):
                if token not in _STOPWORDS:
                    words.add(token)
    except Exception:
        logger.exception("PRODUCT_KEYWORDS_LOAD_FAILED | returning %d words", len(words))
    return words


def get_product_keywords(force_refresh: bool = False) -> set:
    """Return the current set of product/category keywords, refreshing from
    the database when the cache has expired or is empty."""
    now = time.time()
    if force_refresh or not _cache["keywords"] or (now - _cache["loaded_at"]) > _CACHE_TTL_SECONDS:
        fresh = _load_from_db()
        if fresh:
            _cache["keywords"] = fresh
            _cache["loaded_at"] = now
        elif not _cache["keywords"]:
            # DB empty/unreachable on first load: safe minimal fallback so
            # callers still function (kept intentionally generic, not tied
            # to any one product line).
            _cache["keywords"] = {"product", "item"}
            _cache["loaded_at"] = now
    return _cache["keywords"]


def contains_product_keyword(text: str) -> bool:
    """True if the given text mentions any known product/category word."""
    if not text:
        return False
    text_lower = text.lower()
    return any(kw in text_lower for kw in get_product_keywords())
