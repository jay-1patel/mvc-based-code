"""Unanswered-query (knowledge gap) capture data access — extracted from backend/database.py."""
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
from .chat import save_chunks


def record_unanswered_query(
    tenant_id: str,
    query: str,
    wa_id: str = None,
    reason: str = "low_confidence",
    confidence_score: float = 0.0,
) -> int | None:
    """Record an unanswered customer query for Knowledge Gap analytics.

    Skips greetings, empty strings, or duplicate queries received within a 10-minute window.
    """
    if not query or not query.strip():
        return None
    cleaned = query.strip()
    if len(cleaned) < 3:
        return None
    lower = cleaned.lower()
    # Skip common greetings, admin testing commands, and generic navigation
    if lower in {"hi", "hello", "hey", "menu", "start", "opt out", "stop", "yes", "no", "ok", "okay"}:
        return None
    if lower.startswith("#") or lower in {"#tenant", "#tenants", "#switch", "switch", "tenant", "whoami"}:
        return None

    effective_tenant = _resolve_tenant(tenant_id) if tenant_id else (get_request_tenant() or "default")

    with get_db_context() as conn:
        # Cross-tenant check: do not record another brand's name/slug as a
        # knowledge gap for the active tenant (e.g. typing "sbi" while on MMT).
        try:
            other_rows = conn.execute(
                "SELECT id, slug, display_name FROM tenants "
                "WHERE CAST(id AS TEXT) != ? AND IFNULL(slug, '') != ?",
                (str(effective_tenant), str(effective_tenant)),
            ).fetchall()
            for ot in other_rows:
                names = {
                    str(ot["slug"] or "").lower().strip(),
                    str(ot["display_name"] or "").lower().strip(),
                    str(ot["slug"] or "").replace("-", " ").lower().strip(),
                }
                names.discard("")
                if lower in names or any(
                    len(n) >= 2 and (lower == n or lower == f"#{n}") for n in names
                ):
                    logger.info(
                        "Ignoring cross-tenant keyword %r for tenant %s",
                        cleaned, effective_tenant,
                    )
                    return None
        except Exception as filter_err:
            logger.debug("Cross-tenant filter check exception: %s", filter_err)

        # Throttle duplicate query within 10 minutes for this tenant
        recent = conn.execute(
            """SELECT id FROM unanswered_queries
               WHERE tenant_id = ? AND LOWER(TRIM(query)) = LOWER(?)
                 AND created_at >= DATETIME('now', '-10 minutes')
               LIMIT 1""",
            (effective_tenant, cleaned),
        ).fetchone()
        if recent:
            return recent[0]

        cur = conn.execute(
            """INSERT INTO unanswered_queries (tenant_id, wa_id, query, reason, confidence_score)
               VALUES (?, ?, ?, ?, ?)""",
            (effective_tenant, wa_id, cleaned, reason, float(confidence_score or 0.0)),
        )
        return cur.lastrowid


def list_unanswered_queries(
    tenant_id: str = None,
    resolved: bool = False,
    limit: int = 50,
) -> list[dict]:
    """Retrieve unanswered customer queries for a tenant."""
    effective_tenant = _resolve_tenant(tenant_id) if tenant_id else get_request_tenant()
    with get_db_context() as conn:
        params = []
        where_clauses = []
        if effective_tenant:
            where_clauses.append("tenant_id = ?")
            params.append(effective_tenant)
        if resolved is not None:
            where_clauses.append("resolved = ?")
            params.append(1 if resolved else 0)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"SELECT * FROM unanswered_queries {where_sql} ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def resolve_unanswered_query(
    gap_id: int,
    tenant_id: str = None,
    resolved_by: str = None,
    notes: str = None,
) -> bool:
    """Mark a knowledge gap as resolved."""
    effective_tenant = _resolve_tenant(tenant_id) if tenant_id else get_request_tenant()
    with get_db_context() as conn:
        params = [resolved_by or "admin", notes or "", gap_id]
        tenant_clause = ""
        if effective_tenant:
            tenant_clause = " AND tenant_id = ?"
            params.append(effective_tenant)

        cur = conn.execute(
            f"""UPDATE unanswered_queries
                SET resolved = 1, resolved_at = CURRENT_TIMESTAMP, resolved_by = ?, resolution_notes = ?
                WHERE id = ?{tenant_clause}""",
            params,
        )
        return cur.rowcount > 0


def get_knowledge_gaps_summary(tenant_id: str = None) -> dict:
    """Return top recurring unanswered queries and unresolved count for dashboard."""
    effective_tenant = _resolve_tenant(tenant_id) if tenant_id else get_request_tenant()
    with get_db_context() as conn:
        params = [effective_tenant] if effective_tenant else []
        tenant_sql = "AND tenant_id = ?" if effective_tenant else ""
        
        # Total unresolved
        count_row = conn.execute(
            f"SELECT COUNT(*) FROM unanswered_queries WHERE resolved = 0 {tenant_sql}",
            params,
        ).fetchone()
        unresolved_count = count_row[0] if count_row else 0

        # Group by query text to find top frequency questions
        top_rows = conn.execute(
            f"""SELECT query, COUNT(*) as count, MAX(created_at) as last_asked,
                       AVG(confidence_score) as avg_score, reason, MAX(id) as sample_id
                FROM unanswered_queries
                WHERE resolved = 0 {tenant_sql}
                GROUP BY LOWER(TRIM(query))
                ORDER BY count DESC, last_asked DESC
                LIMIT 10""",
            params,
        ).fetchall()

        top_queries = [
            {
                "query": r["query"],
                "count": r["count"],
                "last_asked": r["last_asked"],
                "avg_score": round(float(r["avg_score"] or 0.0), 3),
                "reason": r["reason"],
                "sample_id": r["sample_id"],
            }
            for r in top_rows
        ]

        return {
            "total_unresolved": unresolved_count,
            "top_queries": top_queries,
        }


def add_faq_from_gap(
    tenant_id: str,
    gap_id: int,
    question: str,
    answer: str,
    resolved_by: str = None,
) -> dict:
    """Convert an unanswered query into a live FAQ entry and rebuild the index."""
    effective_tenant = _resolve_tenant(tenant_id) if tenant_id else (get_request_tenant() or "default")
    q = (question or "").strip()
    a = (answer or "").strip()
    if not q or not a:
        raise ValueError("Question and answer are required.")

    formatted_content = f"Question: {q}\nAnswer: {a}"
    chunk = {
        "source_file": "Knowledge Gap FAQ",
        "content_type": "faq",
        "content": formatted_content,
        "page_number": 1,
    }

    # Save to FAQ dataset
    save_chunks([chunk], module="faq", tenant_id=effective_tenant)

    # Save to knowledge_base table as well for hybrid RAG completeness
    with get_db_context() as conn:
        conn.execute(
            """INSERT INTO knowledge_base (title, category, content, tags, source, chunks_json, embedding_blob, created_at, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                f"FAQ: {q[:50]}",
                "faq",
                formatted_content,
                json.dumps(["faq", "knowledge_gap"]),
                "Knowledge Gap FAQ",
                json.dumps([formatted_content]),
                None,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                effective_tenant,
            ),
        )

    # Rebuild FAQ index
    try:
        from faq.service import faq_index
        faq_index.build(force=True)
    except Exception as exc:
        logging.getLogger("database").warning(f"FAQ index auto-sync failed after add_faq_from_gap: {exc}")

    # Mark the gap as resolved
    if gap_id:
        resolve_unanswered_query(
            gap_id=gap_id,
            tenant_id=effective_tenant,
            resolved_by=resolved_by or "admin",
            notes=f"Answered via Knowledge Gap FAQ: {q[:50]}",
        )

    return {"status": "ok", "question": q, "answer": a, "tenant_id": effective_tenant}
