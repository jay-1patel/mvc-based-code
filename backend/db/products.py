"""Tenant resolvers and the products/offerings catalog data access — extracted from backend/database.py."""
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

from .config_drafts import get_published_config
from .audit import record_admin_audit_event


def _resolve_tenant(tenant_id: str = None) -> str:
    """Resolve the effective tenant using the same chain as WhatsApp routing.

    Resolution order:
      1. Frontend Active Test Tenant (when set — overrides empty callers)
      2. Explicit ``tenant_id`` argument (admin pages editing one brand)
      3. Single active tenant or DEFAULT_TENANT_ID — never a synthetic "default"

    Returns an empty string when no tenant can be resolved.
    """
    try:
        from shared.tenancy.resolver import active_test_tenant_id
        active = active_test_tenant_id()
        if active and not str(tenant_id or "").strip():
            return str(active).strip()
    except Exception:
        # deliberate: resolver chain — use the requested tenant id.
        logger.debug("ACTIVE_TEST_TENANT_LOOKUP_SKIPPED | _resolve_tenant", exc_info=True)
    if str(tenant_id or "").strip():
        return str(tenant_id).strip()
    try:
        from shared.tenancy.resolver import resolve_default_tenant
        val = resolve_default_tenant()
        return str(val).strip() if val else ""
    except Exception:
        return os.environ.get("DEFAULT_TENANT_ID", "") or ""


def _resolve_tenant_ids(tenant_id: str = None) -> list:
    """Return matching tenant identifiers (numeric id, slug, raw string) so that
    queries match rows regardless of whether tenant_id was stored as integer or text.

    Resolves the effective tenant through the central resolver instead of
    calling :func:`~.settings.get_active_test_tenant` directly, keeping this
    module in sync with WhatsApp routing logic.
    """
    raw = str(tenant_id or "").strip()
    if not raw:
        try:
            from shared.tenancy.resolver import active_test_tenant_id, resolve_default_tenant
            active = active_test_tenant_id()
            if active:
                raw = str(active).strip()
            else:
                val = resolve_default_tenant()
                if val:
                    raw = str(val).strip()
        except Exception:
            raw = os.environ.get("DEFAULT_TENANT_ID", "") or ""

    candidates = {raw}
    if raw.isdigit():
        candidates.add(int(raw))

    try:
        from shared.tenancy import store
        rec = store.get_tenant(raw)
        if rec:
            if rec.get("id") is not None:
                candidates.add(rec["id"])
                candidates.add(str(rec["id"]))
            if rec.get("slug"):
                candidates.add(rec["slug"])
                candidates.add(rec["slug"].lower())
    except Exception:
        try:
            with get_db_context() as conn:
                row = conn.execute(
                    "SELECT id, slug FROM tenants WHERE id = ? OR slug = ? OR LOWER(slug) = LOWER(?)",
                    (raw, raw, raw),
                ).fetchone()
                if row:
                    if row["id"] is not None:
                        candidates.add(row["id"])
                        candidates.add(str(row["id"]))
                    if row["slug"]:
                        candidates.add(row["slug"])
                        candidates.add(row["slug"].lower())
        except Exception:
            # deliberate: one candidate source failed — the other sources
            # still contribute candidates.
            logger.debug("TENANT_ID_CANDIDATE_QUERY_SKIPPED", exc_info=True)

    return list(candidates)


def _ensure_product_media_schema(conn) -> None:
    """Guarantee media columns exist before insert/update.

    Older faq.db copies (and DBs created before offerings migration) have
    ``media_url`` but not ``image_url``. Bifurcation then fails on INSERT and
    WhatsApp never gets a package/catalogue image.
    """
    from .core import _ensure_columns
    _ensure_columns(conn, "products", [
        ("media_url", "TEXT"),
        ("media_type", "TEXT DEFAULT 'image'"),
        ("image_url", "TEXT"),
        ("attrs_json", "TEXT DEFAULT '{}'"),
        ("tenant_id", "TEXT"),
    ])


def save_product(name: str, slug: str, category: str = "general",
                 description: str = "", short_description: str = "",
                 price: str = "", mrp: str = "", unit: str = "piece",
                 moq: str = "", media_url: str = None, media_type: str = "image",
                  ingredients: list = None, sort_order: int = 0,
                 variants_json: list = None,
                  stock_quantity: int = None, nutritional_facts: str = None,
                 bulk_discount_tiers: list = None,
                 attrs_json: dict = None, short_label: str = None,
                 detail_url: str = None, tenant_id: str = None, 
                 is_active: int = 1,
                 actor: dict = None,
                 image_url: str = None) -> int:
    eff_media_url = media_url or image_url
    eff_image_url = image_url or media_url
    attrs = dict(attrs_json or {})
    if eff_media_url and "media_url" not in attrs:
        attrs["media_url"] = eff_media_url
    if eff_image_url and "image_url" not in attrs:
        attrs["image_url"] = eff_image_url
    with get_db_context() as conn:
        _ensure_product_media_schema(conn)
        cur = conn.execute(
            """INSERT INTO products (name, slug, category, description, short_description,
               price, mrp, unit, moq, media_url, image_url, media_type, ingredients, sort_order, variants_json,
               stock_quantity, nutritional_facts, bulk_discount_tiers, attrs_json, short_label,
               detail_url, is_active, tenant_id, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (name, slug, category, description, short_description,
              price, mrp, unit, moq, eff_media_url, eff_image_url, media_type, json.dumps(ingredients or []), sort_order,
             json.dumps(variants_json or []),
              stock_quantity, nutritional_facts, json.dumps(bulk_discount_tiers or []),
             json.dumps(attrs), short_label, detail_url, int(is_active), _resolve_tenant(tenant_id)),
        )
        product_id = cur.lastrowid
        
        try:
            from backend.services.audit_context import actor_from_context
        except ImportError:
            try:
                from services.audit_context import actor_from_context
            except ImportError:
                actor_from_context = lambda explicit=None: explicit
        actor = actor_from_context(actor)
        # Record audit event for product creation
        if actor:
            record_admin_audit_event(
                conn,
                action="product_created",
                actor=actor,
                resource_type="product",
                resource_id=product_id,
                tenant_id=_resolve_tenant(tenant_id),
                details={
                    "changed_fields": ["name", "slug", "category", "price"],
                    "name": name[:120],
                    "slug": slug[:120],
                    "category": category[:80],
                    "has_price": bool(price),
                }
            )
        
        return product_id


def get_product(product_id: int, tenant_id: str = None) -> dict:
    t_ids = _resolve_tenant_ids(tenant_id)
    placeholders = ", ".join(["?"] * len(t_ids))
    with get_db_context() as conn:
        row = conn.execute(
            f"SELECT * FROM products WHERE id = ? AND tenant_id IN ({placeholders})",
            (product_id, *t_ids),
        ).fetchone()
    return _deserialize_product(dict(row)) if row else None


def get_product_by_slug(slug: str, tenant_id: str = None) -> dict:
    t_ids = _resolve_tenant_ids(tenant_id)
    placeholders = ", ".join(["?"] * len(t_ids))
    with get_db_context() as conn:
        row = conn.execute(
            f"SELECT * FROM products WHERE slug = ? AND tenant_id IN ({placeholders})",
            (slug, *t_ids),
        ).fetchone()
    return _deserialize_product(dict(row)) if row else None


def product_slug_taken(slug: str, tenant_id: str = None) -> bool:
    """True when this tenant's catalog already holds the slug.

    Slugs are UNIQUE per tenant (since the _migrate_products_per_tenant_slug
    rebuild); the default tenant's id-space overlap is handled by
    _resolve_tenant_ids like every other product lookup.
    """
    t_ids = _resolve_tenant_ids(tenant_id)
    placeholders = ", ".join(["?"] * len(t_ids))
    with get_db_context() as conn:
        row = conn.execute(
            f"SELECT 1 FROM products WHERE slug = ? AND tenant_id IN ({placeholders}) LIMIT 1",
            (slug, *t_ids),
        ).fetchone()
    return row is not None


def list_products(category: str = None, active_only: bool = True, limit: int = 100,
                  tenant_id: str = None) -> list:
    t_ids = _resolve_tenant_ids(tenant_id)
    placeholders = ", ".join(["?"] * len(t_ids))
    query = f"SELECT * FROM products WHERE tenant_id IN ({placeholders})"
    params = list(t_ids)
    if active_only:
        query += " AND is_active = 1"
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY sort_order ASC, created_at DESC LIMIT ?"
    params.append(limit)
    with get_db_context() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_deserialize_product(dict(r)) for r in rows]


def count_products(category: str = None, active_only: bool = True, tenant_id: str = None) -> int:
    t_ids = _resolve_tenant_ids(tenant_id)
    placeholders = ", ".join(["?"] * len(t_ids))
    query = f"SELECT COUNT(*) FROM products WHERE tenant_id IN ({placeholders})"
    params = list(t_ids)
    if active_only:
        query += " AND is_active = 1"
    if category:
        query += " AND category = ?"
        params.append(category)
    with get_db_context() as conn:
        row = conn.execute(query, params).fetchone()
        return int(row[0]) if row else 0


def list_product_categories(active_only: bool = True, tenant_id: str = None) -> list[str]:
    t_ids = _resolve_tenant_ids(tenant_id)
    placeholders = ", ".join(["?"] * len(t_ids))
    query = f"SELECT DISTINCT category FROM products WHERE tenant_id IN ({placeholders}) AND category IS NOT NULL"
    params = list(t_ids)
    if active_only:
        query += " AND is_active = 1"
    query += " ORDER BY category ASC"
    with get_db_context() as conn:
        rows = conn.execute(query, params).fetchall()
        return [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]


def list_staged_products(tenant_id: str = None) -> list:
    """List draft / unapproved products for a tenant (is_active = 0)."""
    t_ids = _resolve_tenant_ids(tenant_id)
    placeholders = ", ".join(["?"] * len(t_ids))
    query = f"SELECT * FROM products WHERE tenant_id IN ({placeholders}) AND is_active = 0 ORDER BY id DESC"
    with get_db_context() as conn:
        rows = conn.execute(query, list(t_ids)).fetchall()
    return [_deserialize_product(dict(r)) for r in rows]


def approve_staged_products(tenant_id: str = None, product_ids: list[int] = None) -> int:
    """Promote draft products to live catalog (is_active = 1)."""
    t_ids = _resolve_tenant_ids(tenant_id)
    t_placeholders = ", ".join(["?"] * len(t_ids))
    with get_db_context() as conn:
        if product_ids:
            p_placeholders = ", ".join(["?"] * len(product_ids))
            cur = conn.execute(
                f"""UPDATE products 
                   SET is_active = 1, updated_at = CURRENT_TIMESTAMP 
                   WHERE tenant_id IN ({t_placeholders}) AND id IN ({p_placeholders})""",
                [*list(t_ids), *product_ids],
            )
        else:
            cur = conn.execute(
                f"""UPDATE products 
                   SET is_active = 1, updated_at = CURRENT_TIMESTAMP 
                   WHERE tenant_id IN ({t_placeholders}) AND is_active = 0""",
                list(t_ids),
            )
        return cur.rowcount


def delete_staged_products(tenant_id: str = None, product_ids: list[int] = None) -> int:
    """Delete draft / rejected products."""
    t_ids = _resolve_tenant_ids(tenant_id)
    t_placeholders = ", ".join(["?"] * len(t_ids))
    with get_db_context() as conn:
        if product_ids:
            p_placeholders = ", ".join(["?"] * len(product_ids))
            cur = conn.execute(
                f"""DELETE FROM products 
                   WHERE tenant_id IN ({t_placeholders}) AND id IN ({p_placeholders}) AND is_active = 0""",
                [*list(t_ids), *product_ids],
            )
        else:
            cur = conn.execute(
                f"""DELETE FROM products 
                   WHERE tenant_id IN ({t_placeholders}) AND is_active = 0""",
                list(t_ids),
            )
        return cur.rowcount


def _row_to_dict(row) -> dict:
    """sqlite3.Row -> dict, dropping BLOB columns.

    Rows selected with `SELECT *` carry the `embedding` / `embedding_blob`
    float32 arrays. FastAPI's jsonable_encoder raises UnicodeDecodeError on raw
    bytes, which turns any endpoint returning these rows into a 500. Consumers
    that need the vector (the FAISS rebuilds) select it explicitly.
    """
    d = dict(row)
    for blob_col in ("embedding", "embedding_blob"):
        d.pop(blob_col, None)
    return d


def _deserialize_product(row: dict) -> dict:
    """JSON-decode list fields on a product row."""
    row["ingredients"] = _loads_json(row.get("ingredients"), [])
    row["variants_json"] = _loads_json(row.get("variants_json"), [])
    row["bulk_discount_tiers"] = _loads_json(row.get("bulk_discount_tiers"), [])
    row["attrs_json"] = _loads_json(row.get("attrs_json"), {})
    if row.get("media_url") and not row.get("image_url"):
        row["image_url"] = row["media_url"]
    elif row.get("image_url") and not row.get("media_url"):
        row["media_url"] = row["image_url"]
    # Drop the BLOB so this row can be JSON-serialized (see _row_to_dict).
    row.pop("embedding", None)
    return row


def update_product(product_id: int, tenant_id: str = None, **kwargs) -> bool:
    allowed = {"name", "slug", "category", "description", "short_description",
               "price", "mrp", "unit", "moq", "media_url", "image_url", "media_type",
               "ingredients", "is_active", "sort_order", "variants_json",
               "stock_quantity", "nutritional_facts", "bulk_discount_tiers",
               "attrs_json", "short_label", "detail_url"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    if "media_url" in updates and "image_url" not in updates:
        updates["image_url"] = updates["media_url"]
    elif "image_url" in updates and "media_url" not in updates:
        updates["media_url"] = updates["image_url"]
    for key in ("ingredients", "variants_json", "bulk_discount_tiers", "attrs_json"):
        if key in updates and isinstance(updates[key], (list, dict)):
            updates[key] = json.dumps(updates[key])
    updates["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [product_id, _resolve_tenant(tenant_id)]
    
    actor = kwargs.pop("actor", None)
    try:
        from backend.services.audit_context import actor_from_context
    except ImportError:
        try:
            from services.audit_context import actor_from_context
        except ImportError:
            actor_from_context = lambda explicit=None: explicit
    actor = actor_from_context(actor)

    with get_db_context() as conn:
        _ensure_product_media_schema(conn)
        before = conn.execute(
            "SELECT name, slug, category, price, is_active, sort_order FROM products WHERE id = ? AND tenant_id = ?",
            (product_id, _resolve_tenant(tenant_id)),
        ).fetchone()
        cursor = conn.execute(
            f"UPDATE products SET {set_clause} WHERE id = ? AND tenant_id = ?", values
        )

        # Record field names and a tightly allowlisted before/after summary.
        if actor and cursor.rowcount:
            after = conn.execute(
                "SELECT name, slug, category, price, is_active, sort_order FROM products WHERE id = ? AND tenant_id = ?",
                (product_id, _resolve_tenant(tenant_id)),
            ).fetchone()
            changed = {}
            if before and after:
                for key in ("name", "slug", "category", "price", "is_active", "sort_order"):
                    if before[key] != after[key]:
                        changed[key] = {"before": before[key], "after": after[key]}
            record_admin_audit_event(
                conn,
                action="product_updated",
                actor=actor,
                resource_type="product",
                resource_id=product_id,
                tenant_id=_resolve_tenant(tenant_id),
                details={"changed_fields": sorted(changed), "changes": changed},
            )
        
    return True


def delete_product(product_id: int, hard: bool = False, tenant_id: str = None, actor: dict = None) -> bool:
    try:
        from backend.services.audit_context import actor_from_context
    except ImportError:
        try:
            from services.audit_context import actor_from_context
        except ImportError:
            actor_from_context = lambda explicit=None: explicit
    actor = actor_from_context(actor)
    with get_db_context() as conn:
        # Get product details before deletion for audit logging
        product_row = conn.execute(
            "SELECT name, slug, category FROM products WHERE id = ? AND tenant_id = ?",
            (product_id, _resolve_tenant(tenant_id)),
        ).fetchone()
        
        if hard:
            conn.execute(
                "DELETE FROM products WHERE id = ? AND tenant_id = ?",
                (product_id, _resolve_tenant(tenant_id)),
            )
        else:
            conn.execute(
                "UPDATE products SET is_active = 0 WHERE id = ? AND tenant_id = ?",
                (product_id, _resolve_tenant(tenant_id)),
            )
        
        # Record audit event for product deletion
        if actor:
            action_type = "product_deleted_hard" if hard else "product_deleted"
            product_details = dict(product_row) if product_row else {}
            record_admin_audit_event(
                conn,
                action=action_type,
                actor=actor,
                resource_type="product",
                resource_id=product_id,
                tenant_id=_resolve_tenant(tenant_id),
                details={
                    "product_name": product_details.get("name", "")[:120],
                    "product_slug": product_details.get("slug", "")[:120],
                    "product_category": product_details.get("category", "")[:80],
                    "hard_delete": hard,
                }
            )
        
    return True


def search_products(query: str, limit: int = 20, tenant_id: str = None) -> list:
    import re as _re

    def _words_match(a, b):
        if a == b:
            return True
        if len(a) >= 4 and len(b) >= 4:
            if a.startswith(b) or b.startswith(a):
                return True
            if a in b or b in a:
                return True
        return False

    # Every product lookup is scoped to one tenant. None resolves to the
    # registered default tenant (shared.tenancy), never to "all tenants".
    try:
        from shared.tenancy.resolver import resolve_default_tenant
    except Exception:
        resolve_default_tenant = lambda: "default"  # noqa: E731
    tid = str(tenant_id or "").strip() or resolve_default_tenant()

    words = [w for w in _re.findall(r"[a-zA-Z]{2,}", query.lower())]
    if not words:
        return []

    # Try FAISS vector search first
    try:
        from kb.services.rag import search_products_faiss
        faiss_results = search_products_faiss(query, limit=limit, tenant_id=tid)
        if faiss_results:
            ids = [r["product_id"] for r in faiss_results]
            placeholders = ",".join("?" * len(ids))
            with get_db_context() as conn:
                rows = conn.execute(
                    f"SELECT * FROM products WHERE id IN ({placeholders}) AND is_active = 1 AND tenant_id = ?",
                    ids + [tid],
                ).fetchall()
            if rows:
                row_map = {p["id"]: p for p in
                           (_deserialize_product(dict(x)) for x in rows)}
                return [row_map[pid] for pid in ids if pid in row_map]
    except Exception:
        # deliberate: the id-ordered fetch is an optimisation — the
        # regular search below still runs.
        logger.debug("PRODUCT_ID_ORDER_FETCH_SKIPPED | tenant=%s", tid, exc_info=True)

    # Fallback: keyword search
    with get_db_context() as conn:
        rows = conn.execute(
            """SELECT * FROM products
                WHERE is_active = 1 AND tenant_id = ?
                  AND (name LIKE ? OR description LIKE ? OR ingredients LIKE ? OR category LIKE ?)
               ORDER BY sort_order ASC LIMIT ?""",
            (tid, f"%{query}%", f"%{query}%", f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        if rows:
            return [_deserialize_product(dict(r)) for r in rows]
        all_rows = conn.execute(
            "SELECT * FROM products WHERE is_active = 1 AND tenant_id = ?",
            (tid,),
        ).fetchall()
        scored = []
        for r in all_rows:
            d = _deserialize_product(dict(r))
            pname = (d.get("name") or "").lower()
            _ingredients = d.get("ingredients") or ""
            if isinstance(_ingredients, list):
                _ingredients = " ".join(_ingredients)
            searchable = " ".join([
                pname,
                (d.get("description") or "").lower(),
                _ingredients.lower(),
                (d.get("category") or "").lower(),
            ])
            product_words = [w for w in pname.split() if len(w) > 2]
            score = 0
            for w in words:
                if w in searchable:
                    score += 2
                else:
                    for pw in product_words:
                        if _words_match(w, pw):
                            score += 1
                            break
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:limit]]


def get_products_by_category(category: str, limit: int = 50, tenant_id: str = None) -> list:
    with get_db_context() as conn:
        rows = conn.execute(
            """SELECT * FROM products
               WHERE is_active = 1 AND tenant_id = ? AND category = ?
               ORDER BY sort_order ASC LIMIT ?""",
            (_resolve_tenant(tenant_id), category, limit),
        ).fetchall()
    return [_deserialize_product(dict(r)) for r in rows]


def get_published_products(category: str = None, active_only: bool = True, limit: int = 100,
                           tenant_id: str = None) -> list:
    """Return products from the published config snapshot, falling back to DB."""
    published = get_published_config("products")
    if published and isinstance(published, dict) and "items" in published:
        items = published["items"]
        if active_only:
            items = [p for p in items if p.get("is_active", True)]
        if category:
            items = [p for p in items if p.get("category") == category]
        items = sorted(
            items,
            key=lambda p: (p.get("sort_order", 0), p.get("created_at", "")),
        )
        return items[:limit]
    # Fallback: read directly from DB if nothing is published yet.
    return list_products(category=category, active_only=active_only, limit=limit,
                         tenant_id=tenant_id)


def build_products_snapshot(category: str = None, active_only: bool = True,
                            tenant_id: str = None) -> dict:
    """Build the canonical products snapshot from DB rows."""
    items = list_products(category=category, active_only=active_only, limit=500,
                          tenant_id=tenant_id)
    return {"items": items, "count": len(items), "generated_at": datetime.now().isoformat()}
