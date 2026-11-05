"""Menu items and menu settings data access — extracted from backend/database.py."""
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


def invalidate_menu_cache():
    """Clear the in-process menu cache (called after admin edits)."""
    try:
        from services.menu_catalog import invalidate_menu_cache as _inv
        _inv()
    except Exception:
        logger.exception("MENU_CACHE_INVALIDATE_FAILED")


def get_menu_items(menu_key: str, active_only: bool = True) -> list:
    """Rows for one menu, ordered. Empty list when menu not in DB."""
    sql = ("SELECT menu_key, item_id, title, description, section, icon, "
           "sort_order, is_active FROM menus WHERE menu_key = ?")
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY sort_order ASC, id ASC"
    with get_db_context() as conn:
        rows = conn.execute(sql, (menu_key,)).fetchall()
    return [
        {"menu_key": r[0], "item_id": r[1], "title": r[2], "description": r[3],
         "section": r[4], "icon": r[5], "sort_order": r[6], "is_active": r[7]}
        for r in rows
    ]


def upsert_menu_item(menu_key: str, item_id: str, **fields) -> bool:
    """Insert or update one menu item. item_id/menu_key are immutable keys."""
    allowed = {"title", "description", "section", "icon", "sort_order", "is_active"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return False
    sets = ", ".join(f"{k} = ?" for k in data)
    with get_db_context() as conn:
        conn.execute(
            f"""INSERT INTO menus (menu_key, item_id, {", ".join(data)})
                VALUES (?, ?, {", ".join("?" for _ in data)})
                ON CONFLICT(menu_key, item_id) DO UPDATE SET
                {sets}, updated_at = CURRENT_TIMESTAMP""",
            (menu_key, item_id, *data.values(), *data.values()),
        )
    return True


def reset_menu(menu_key: str) -> int:
    """Delete DB overrides for one menu; loader falls back to code defaults."""
    with get_db_context() as conn:
        cur = conn.execute("DELETE FROM menus WHERE menu_key = ?", (menu_key,))
    invalidate_menu_cache()
    return cur.rowcount


def delete_menu_item(menu_key: str, item_id: str) -> bool:
    """Delete a single menu item row. Returns True if a row was removed."""
    with get_db_context() as conn:
        cur = conn.execute(
            "DELETE FROM menus WHERE menu_key = ? AND item_id = ?",
            (menu_key, item_id),
        )
    invalidate_menu_cache()
    return cur.rowcount > 0


def get_menu_settings(menu_key: str) -> dict:
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT header, body, footer, button_text FROM menu_settings WHERE menu_key = ?",
            (menu_key,),
        ).fetchone()
    if not row:
        return {}
    return {"header": row[0] or "", "body": row[1] or "",
            "footer": row[2] or "", "button_text": row[3] or ""}


def set_menu_settings(menu_key: str, **fields) -> bool:
    allowed = {"header", "body", "footer", "button_text"}
    data = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not data:
        return False
    cols = ", ".join(data)
    sets = ", ".join(f"{k} = ?" for k in data)
    with get_db_context() as conn:
        conn.execute(
            f"""INSERT INTO menu_settings (menu_key, {cols})
                VALUES (?, {", ".join("?" for _ in data)})
                ON CONFLICT(menu_key) DO UPDATE SET
                {sets}, updated_at = CURRENT_TIMESTAMP""",
            (menu_key, *data.values(), *data.values()),
        )
    return True
