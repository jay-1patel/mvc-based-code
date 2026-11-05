"""Scheduled website-crawl config data access — extracted from backend/database.py."""
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


def _crawl_schedule_row(row) -> dict:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "url": row["url"],
        "interval_hours": row["interval_hours"],
        "max_pages": row["max_pages"],
        "auto_publish": bool(row["auto_publish"]),
        "is_enabled": bool(row["is_enabled"]),
        "last_crawled_at": row["last_crawled_at"],
        "last_status": row["last_status"],
        "last_error": row["last_error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_crawl_schedule(tenant_id: str) -> dict | None:
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_crawl_schedules WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
    return _crawl_schedule_row(row) if row else None


def upsert_crawl_schedule(
    tenant_id: str,
    url: str,
    interval_hours: int = 24,
    max_pages: int = 8,
    auto_publish: bool = True,
    is_enabled: bool = True,
) -> dict:
    interval_hours = max(1, int(interval_hours or 24))
    max_pages = max(1, min(int(max_pages or 8), 25))
    with get_db_context() as conn:
        conn.execute(
            """INSERT INTO tenant_crawl_schedules
                   (tenant_id, url, interval_hours, max_pages, auto_publish, is_enabled)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id) DO UPDATE SET
                   url = excluded.url,
                   interval_hours = excluded.interval_hours,
                   max_pages = excluded.max_pages,
                   auto_publish = excluded.auto_publish,
                   is_enabled = excluded.is_enabled,
                   updated_at = CURRENT_TIMESTAMP""",
            (tenant_id, url, interval_hours, max_pages, 1 if auto_publish else 0, 1 if is_enabled else 0),
        )
        row = conn.execute(
            "SELECT * FROM tenant_crawl_schedules WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
    return _crawl_schedule_row(row)


def delete_crawl_schedule(tenant_id: str) -> bool:
    with get_db_context() as conn:
        cur = conn.execute(
            "DELETE FROM tenant_crawl_schedules WHERE tenant_id = ?",
            (tenant_id,),
        )
    return cur.rowcount > 0


def list_due_crawl_schedules() -> list:
    """Enabled schedules whose last crawl is older than their interval (or never crawled)."""
    with get_db_context() as conn:
        rows = conn.execute(
            """SELECT * FROM tenant_crawl_schedules
               WHERE is_enabled = 1
                 AND (last_crawled_at IS NULL
                      OR last_crawled_at <= DATETIME('now', '-' || interval_hours || ' hours'))
               ORDER BY COALESCE(last_crawled_at, '1970-01-01') ASC""",
        ).fetchall()
    return [_crawl_schedule_row(r) for r in rows]


def mark_crawl_started(tenant_id: str) -> None:
    """Stamp the run time up-front so a slow crawl is not picked up twice."""
    with get_db_context() as conn:
        conn.execute(
            """UPDATE tenant_crawl_schedules
               SET last_crawled_at = CURRENT_TIMESTAMP,
                   last_status = 'running',
                   last_error = NULL,
                   updated_at = CURRENT_TIMESTAMP
               WHERE tenant_id = ?""",
            (tenant_id,),
        )


def mark_crawl_finished(tenant_id: str, status: str, error: str = None) -> None:
    with get_db_context() as conn:
        conn.execute(
            """UPDATE tenant_crawl_schedules
               SET last_status = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
               WHERE tenant_id = ?""",
            (status, error, tenant_id),
        )
