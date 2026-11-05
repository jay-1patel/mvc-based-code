"""LIKE-style FAQ/KB keyword search data access — extracted from backend/database.py."""
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

from .products import _resolve_tenant, _row_to_dict


def search_faq_db(query: str, limit: int = 5, tenant_id: str = None) -> list:
    import re as _re
    words = [w for w in _re.findall(r"[a-zA-Z]{2,}", query.lower())]
    if not words:
        return []

    effective_tid = tenant_id or _resolve_tenant()
    t_id_str = None
    t_slug = None
    if effective_tid:
        try:
            from shared.tenancy import store
            rec = store.get_tenant(str(effective_tid))
            if rec:
                t_id_str = str(rec.get("id"))
                t_slug = str(rec.get("slug"))
            else:
                t_id_str = str(effective_tid)
                t_slug = str(effective_tid)
        except Exception:
            t_id_str = str(effective_tid)
            t_slug = str(effective_tid)

    if t_id_str and t_slug and t_id_str != t_slug:
        tenant_clause = " AND (tenant_id = ? OR tenant_id = ? OR tenant_id = ?)"
        tenant_params = [t_id_str, int(t_id_str) if t_id_str.isdigit() else t_id_str, t_slug]
    elif t_id_str:
        tenant_clause = " AND (tenant_id = ? OR tenant_id = ?)"
        tenant_params = [t_id_str, int(t_id_str) if t_id_str.isdigit() else t_id_str]
    else:
        tenant_clause = ""
        tenant_params = []

    with get_db_context() as conn:
        rows = conn.execute(
            f"""SELECT * FROM faq_dataset
               WHERE content LIKE ?{tenant_clause}
               ORDER BY id DESC LIMIT ?""",
            ([f"%{query}%"] + tenant_params + [limit]),
        ).fetchall()
        if rows:
            return [_row_to_dict(r) for r in rows]
        all_rows = conn.execute("SELECT * FROM faq_dataset").fetchall()
        scored = []
        allowed_tids = {t_id_str, t_slug, str(effective_tid)} if effective_tid else set()
        for r in all_rows:
            d = _row_to_dict(r)
            row_tid = str(d.get("tenant_id") or "")
            if allowed_tids and row_tid not in allowed_tids:
                continue
            searchable = " ".join([
                (d.get("source_file") or "").lower(),
                (d.get("content") or "").lower(),
            ])
            score = sum(2 for w in words if w in searchable)
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:limit]]


def search_knowledge_base_db(query: str, limit: int = 5, tenant_id: str = None) -> list:
    import re as _re
    words = [w for w in _re.findall(r"[a-zA-Z]{2,}", query.lower())]
    if not words:
        return []

    effective_tid = tenant_id or _resolve_tenant()
    t_id_str = None
    t_slug = None
    if effective_tid:
        try:
            from shared.tenancy import store
            rec = store.get_tenant(str(effective_tid))
            if rec:
                t_id_str = str(rec.get("id"))
                t_slug = str(rec.get("slug"))
            else:
                t_id_str = str(effective_tid)
                t_slug = str(effective_tid)
        except Exception:
            t_id_str = str(effective_tid)
            t_slug = str(effective_tid)

    if t_id_str and t_slug and t_id_str != t_slug:
        tenant_clause = " AND (tenant_id = ? OR tenant_id = ? OR tenant_id = ?)"
        tenant_params = [t_id_str, int(t_id_str) if t_id_str.isdigit() else t_id_str, t_slug]
    elif t_id_str:
        tenant_clause = " AND (tenant_id = ? OR tenant_id = ?)"
        tenant_params = [t_id_str, int(t_id_str) if t_id_str.isdigit() else t_id_str]
    else:
        tenant_clause = ""
        tenant_params = []

    with get_db_context() as conn:
        rows = conn.execute(
            f"""SELECT * FROM knowledge_base
               WHERE (title LIKE ? OR content LIKE ? OR tags LIKE ?){tenant_clause}
               ORDER BY id DESC LIMIT ?""",
            ([f"%{query}%", f"%{query}%", f"%{query}%"] + tenant_params + [limit]),
        ).fetchall()
        if rows:
            return [_row_to_dict(r) for r in rows]
        all_rows = conn.execute("SELECT * FROM knowledge_base").fetchall()
        scored = []
        allowed_tids = {t_id_str, t_slug, str(effective_tid)} if effective_tid else set()
        for r in all_rows:
            d = _row_to_dict(r)
            row_tid = str(d.get("tenant_id") or "")
            if allowed_tids and row_tid not in allowed_tids:
                continue
            searchable = " ".join([
                (d.get("title") or "").lower(),
                (d.get("content") or "").lower(),
                (d.get("tags") or "").lower(),
            ])
            score = sum(2 for w in words if w in searchable)
            if score > 0:
                scored.append((score, d))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:limit]]
