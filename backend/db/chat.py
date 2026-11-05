"""Chat history, embeddings and KB chunk storage data access — extracted from backend/database.py."""
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

from .products import _resolve_tenant


def save_chat(wa_id, sender_name, message, response, route="faq", tenant_id=None):
    tid = tenant_id or _current_tenant.get() or _resolve_tenant()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stored_message = f"[{route}] {message}"
    with get_db_context() as conn:
        conn.execute(
            "INSERT INTO chat_history (wa_id, sender_name, message, response, route, tenant_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wa_id, sender_name, stored_message, response, route, tid, created_at),
        )
    # Mirror every exchange into a per-tenant, per-day JSON audit file,
    # alongside (never instead of) the DB row above.
    try:
        try:
            from json_history import append_chat as _json_append
        except ImportError:
            from backend.json_history import append_chat as _json_append
        _json_append(wa_id, sender_name, stored_message, response, route, tid, created_at)
    except Exception as e:
        logger.warning(f"json_history mirror failed for {wa_id}: {e}")


def get_recent_history(
    wa_id: str,
    limit: int = 5,
    ttl_minutes: int = 30,
    tenant_id: Optional[str] = None,
):
    tid = tenant_id or _current_tenant.get() or _resolve_tenant()
    with get_db_context() as conn:
        if tid:
            rows = conn.execute(
                "SELECT message, response, created_at FROM chat_history "
                "WHERE tenant_id = ? AND wa_id = ? ORDER BY created_at DESC LIMIT ?",
                (tid, wa_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT message, response, created_at FROM chat_history "
                "WHERE wa_id = ? ORDER BY created_at DESC LIMIT ?",
                (wa_id, limit),
            ).fetchall()
    if not rows:
        return []
    rows.reverse()
    last = rows[-1]
    try:
        now = datetime.now(timezone.utc)
        last_time = datetime.strptime(last["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if (now - last_time).total_seconds() > ttl_minutes * 60:
            return []
    except Exception:
        # deliberate: an unparseable timestamp must not hide history —
        # the rows are returned without the TTL cut.
        logger.debug("HISTORY_TTL_CHECK_SKIPPED", exc_info=True)
    out = []
    for row in rows:
        raw_user = row["message"] or ""
        # Strip internal route prefixes like [send2], [webhook], [faq]
        clean_user = re.sub(r"^\[[\w\-/]+\]\s*", "", raw_user).strip()
        out.append({"user": clean_user, "assistant": row["response"] or ""})
    return out


def save_embeddings(embedding_data):
    with get_db_context() as conn:
        for faq_id, embedding_bytes in embedding_data:
            conn.execute(
                "UPDATE faq_dataset SET embedding = ? WHERE id = ?",
                (embedding_bytes, faq_id),
            )


def save_chunks(chunks, module="faq", media_url=None, media_type=None, tenant_id=None, replace_existing=False):
    """Insert FAQ/document chunks. When replace_existing=True, delete prior rows
    for each distinct source_file (+ tenant) before inserting — prevents duplicate
    vectors on re-upload.
    """
    with get_db_context() as conn:
        if replace_existing:
            sources = {chunk.get("source_file") for chunk in chunks if chunk.get("source_file")}
            for src in sources:
                if tenant_id is not None:
                    conn.execute(
                        "DELETE FROM faq_dataset WHERE source_file = ? AND tenant_id = ?",
                        (src, tenant_id),
                    )
                else:
                    conn.execute("DELETE FROM faq_dataset WHERE source_file = ?", (src,))
        for chunk in chunks:
            conn.execute(
                "INSERT INTO faq_dataset (source_file, content_type, content, page_number, module, media_url, media_type, tenant_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chunk["source_file"], chunk["content_type"], chunk["content"], chunk["page_number"], module, media_url, media_type, tenant_id),
            )


def save_knowledge_base_chunks(chunks, source_file, media_url=None, media_type=None, tenant_id=None, replace_existing=True):
    """Insert KB rows. Defaults to replace_existing so re-uploads do not leave
    stale FAISS/SQL duplicates for the same source + tenant.
    """
    from collections import defaultdict
    grouped = defaultdict(list)
    for chunk in chunks:
        grouped[chunk.get("source_file", source_file)].append(chunk)

    with get_db_context() as conn:
        for src, group in grouped.items():
            if replace_existing:
                if tenant_id is not None:
                    conn.execute(
                        "DELETE FROM knowledge_base WHERE source = ? AND tenant_id = ?",
                        (src, tenant_id),
                    )
                else:
                    conn.execute("DELETE FROM knowledge_base WHERE source = ?", (src,))
            texts = [c.get("content", "") for c in group if c.get("content")]
            full_text = "\n".join(texts)
            conn.execute(
                """INSERT INTO knowledge_base (title, category, content, tags, source, chunks_json, embedding_blob, created_at, media_url, media_type, tenant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    src,
                    "uploaded",
                    full_text,
                    json.dumps([]),
                    src,
                    json.dumps(texts),
                    None,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    media_url,
                    media_type,
                    tenant_id,
                ),
            )


def get_files_summary():
    with get_db_context() as conn:
        rows = conn.execute(
            "SELECT source_file, module, COUNT(*) as chunk_count FROM faq_dataset GROUP BY source_file ORDER BY source_file"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_file_chunks(source_file, tenant_id=None):
    with get_db_context() as conn:
        if tenant_id:
            result = conn.execute(
                "DELETE FROM faq_dataset WHERE source_file = ? AND tenant_id = ?",
                (source_file, tenant_id),
            )
        else:
            result = conn.execute("DELETE FROM faq_dataset WHERE source_file = ?", (source_file,))
        return result.rowcount


def delete_knowledge_base_file(source_file, tenant_id=None):
    with get_db_context() as conn:
        if tenant_id:
            result = conn.execute(
                "DELETE FROM knowledge_base WHERE source = ? AND tenant_id = ?",
                (source_file, tenant_id),
            )
        else:
            result = conn.execute("DELETE FROM knowledge_base WHERE source = ?", (source_file,))
        return result.rowcount
