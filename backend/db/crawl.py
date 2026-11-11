"""Scheduled website-crawl config + crawl-v2 run/staging/image data access.

Extracted from backend/database.py; crawl-v2 tables are additive and do not
change legacy UrlAutoOnboarder publish behaviour.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from .core import get_db_context

logger = logging.getLogger("chiki_webhook")


def _crawl_schedule_row(row) -> dict:
    keys = set(row.keys()) if hasattr(row, "keys") else set()
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
        "last_pages_found": row["last_pages_found"] if "last_pages_found" in keys else None,
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
    interval_hours: int = 168,
    max_pages: int = None,
    auto_publish: bool = False,
    is_enabled: bool = True,
) -> dict:
    """Upsert schedule. auto_publish is forced off — gated pipeline only.

    max_pages is retained on the row for backward compatibility but is no
    longer applied: the crawler's page-count limit was removed (2026-11-11)
    and scheduled runs ignore any stored value (see crawl_pipeline).
    None is stored as NULL.
    """
    interval_hours = max(1, int(interval_hours or 168))
    max_pages = max(1, int(max_pages)) if max_pages else None
    auto_publish = False  # scheduled crawls never auto-publish
    with get_db_context() as conn:
        conn.execute(
            """INSERT INTO tenant_crawl_schedules
                   (tenant_id, url, interval_hours, max_pages, auto_publish, is_enabled)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id) DO UPDATE SET
                   url = excluded.url,
                   interval_hours = excluded.interval_hours,
                   max_pages = excluded.max_pages,
                   auto_publish = 0,
                   is_enabled = excluded.is_enabled,
                   updated_at = CURRENT_TIMESTAMP""",
            (tenant_id, url, interval_hours, max_pages, 0, 1 if is_enabled else 0),
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


def mark_crawl_finished(
    tenant_id: str,
    status: str,
    error: str = None,
    pages_found: int = None,
) -> None:
    with get_db_context() as conn:
        if pages_found is not None:
            conn.execute(
                """UPDATE tenant_crawl_schedules
                   SET last_status = ?, last_error = ?, last_pages_found = ?,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE tenant_id = ?""",
                (status, error, int(pages_found), tenant_id),
            )
        else:
            conn.execute(
                """UPDATE tenant_crawl_schedules
                   SET last_status = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE tenant_id = ?""",
                (status, error, tenant_id),
            )


# ── Crawl v2 runs / pages / staging / images ──────────────────────────────


def _run_row(row) -> dict:
    return {
        "id": row["id"],
        "tenant_id": row["tenant_id"],
        "source_url": row["source_url"],
        "trigger": row["trigger"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "pages_found": row["pages_found"] or 0,
        "pages_scraped": row["pages_scraped"] or 0,
        "error": row["error"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def create_crawl_run(
    tenant_id: str,
    source_url: str,
    trigger: str = "manual",
    created_by: str = None,
) -> dict:
    with get_db_context() as conn:
        cur = conn.execute(
            """INSERT INTO tenant_crawl_runs
                   (tenant_id, source_url, trigger, status, created_by)
               VALUES (?, ?, ?, 'queued', ?)""",
            (tenant_id, source_url, trigger, created_by),
        )
        run_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM tenant_crawl_runs WHERE id = ?", (run_id,)
        ).fetchone()
    return _run_row(row)


def get_crawl_run(run_id: int, tenant_id: str = None) -> Optional[dict]:
    with get_db_context() as conn:
        if tenant_id:
            row = conn.execute(
                "SELECT * FROM tenant_crawl_runs WHERE id = ? AND tenant_id = ?",
                (run_id, tenant_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM tenant_crawl_runs WHERE id = ?", (run_id,)
            ).fetchone()
    return _run_row(row) if row else None


def list_crawl_runs(tenant_id: str, limit: int = 20) -> List[dict]:
    with get_db_context() as conn:
        rows = conn.execute(
            """SELECT * FROM tenant_crawl_runs
               WHERE tenant_id = ?
               ORDER BY id DESC LIMIT ?""",
            (tenant_id, max(1, min(int(limit or 20), 100))),
        ).fetchall()
    return [_run_row(r) for r in rows]


def get_running_crawl(tenant_id: str) -> Optional[dict]:
    with get_db_context() as conn:
        row = conn.execute(
            """SELECT * FROM tenant_crawl_runs
               WHERE tenant_id = ? AND status IN ('queued', 'running')
               ORDER BY id DESC LIMIT 1""",
            (tenant_id,),
        ).fetchone()
    return _run_row(row) if row else None


def update_crawl_run(
    run_id: int,
    *,
    status: str = None,
    pages_found: int = None,
    pages_scraped: int = None,
    error: str = None,
    mark_started: bool = False,
    mark_finished: bool = False,
) -> None:
    sets = []
    params: list = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if pages_found is not None:
        sets.append("pages_found = ?")
        params.append(int(pages_found))
    if pages_scraped is not None:
        sets.append("pages_scraped = ?")
        params.append(int(pages_scraped))
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if mark_started:
        sets.append("started_at = CURRENT_TIMESTAMP")
    if mark_finished:
        sets.append("finished_at = CURRENT_TIMESTAMP")
    if not sets:
        return
    params.append(run_id)
    with get_db_context() as conn:
        conn.execute(
            f"UPDATE tenant_crawl_runs SET {', '.join(sets)} WHERE id = ?",
            params,
        )


def insert_crawl_page(
    run_id: int,
    url: str,
    source: str = "link",
    status: str = None,
    http_status: int = None,
    word_count: int = 0,
    error: str = None,
) -> int:
    with get_db_context() as conn:
        cur = conn.execute(
            """INSERT INTO tenant_crawl_pages
                   (run_id, url, source, status, http_status, word_count, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, url, source, status, http_status, word_count or 0, error),
        )
        return cur.lastrowid


def list_crawl_pages(run_id: int) -> List[dict]:
    with get_db_context() as conn:
        rows = conn.execute(
            "SELECT * FROM tenant_crawl_pages WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "run_id": r["run_id"],
            "url": r["url"],
            "source": r["source"],
            "status": r["status"],
            "http_status": r["http_status"],
            "word_count": r["word_count"] or 0,
            "error": r["error"],
        }
        for r in rows
    ]


def save_crawl_staged(run_id: int, tenant_id: str, payload: Dict[str, Any]) -> dict:
    payload_json = json.dumps(payload, ensure_ascii=False)
    with get_db_context() as conn:
        conn.execute(
            """INSERT INTO tenant_crawl_staged (run_id, tenant_id, payload_json, status)
               VALUES (?, ?, ?, 'staged')
               ON CONFLICT(run_id) DO UPDATE SET
                   payload_json = excluded.payload_json,
                   status = 'staged',
                   decided_by = NULL,
                   decided_at = NULL""",
            (run_id, tenant_id, payload_json),
        )
        row = conn.execute(
            "SELECT * FROM tenant_crawl_staged WHERE run_id = ?", (run_id,)
        ).fetchone()
    return _staged_row(row)


def get_crawl_staged(run_id: int, tenant_id: str = None) -> Optional[dict]:
    with get_db_context() as conn:
        if tenant_id:
            row = conn.execute(
                "SELECT * FROM tenant_crawl_staged WHERE run_id = ? AND tenant_id = ?",
                (run_id, tenant_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM tenant_crawl_staged WHERE run_id = ?", (run_id,)
            ).fetchone()
    return _staged_row(row) if row else None


def decide_crawl_staged(
    run_id: int,
    tenant_id: str,
    decision: str,
    decided_by: str = None,
) -> Optional[dict]:
    """Mark staged row published/rejected. Returns None if missing; raises ValueError if already decided."""
    with get_db_context() as conn:
        row = conn.execute(
            "SELECT * FROM tenant_crawl_staged WHERE run_id = ? AND tenant_id = ?",
            (run_id, tenant_id),
        ).fetchone()
        if not row:
            return None
        current = row["status"]
        if current != "staged":
            raise ValueError(f"already_{current}")
        conn.execute(
            """UPDATE tenant_crawl_staged
               SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP
               WHERE run_id = ? AND tenant_id = ?""",
            (decision, decided_by, run_id, tenant_id),
        )
        row = conn.execute(
            "SELECT * FROM tenant_crawl_staged WHERE run_id = ?", (run_id,)
        ).fetchone()
    return _staged_row(row)


def _staged_row(row) -> dict:
    payload = {}
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "tenant_id": row["tenant_id"],
        "payload": payload,
        "status": row["status"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "created_at": row["created_at"],
    }


def insert_crawl_image(row: Dict[str, Any]) -> int:
    with get_db_context() as conn:
        cur = conn.execute(
            """INSERT INTO tenant_crawl_images
                   (run_id, tenant_id, source_url, local_path, served_url, content_type,
                    sha256, byte_size, width, height, alt_text, source_page_url,
                    offering_name, kind, is_duplicate, duplicate_of, skipped, skip_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.get("run_id"),
                row.get("tenant_id"),
                row.get("source_url"),
                row.get("local_path"),
                row.get("served_url"),
                row.get("content_type"),
                row.get("sha256"),
                row.get("byte_size"),
                row.get("width"),
                row.get("height"),
                row.get("alt_text"),
                row.get("source_page_url"),
                row.get("offering_name"),
                row.get("kind") or "image",
                1 if row.get("is_duplicate") else 0,
                row.get("duplicate_of"),
                1 if row.get("skipped") else 0,
                row.get("skip_reason"),
            ),
        )
        return cur.lastrowid


def list_crawl_images(run_id: int, tenant_id: str = None) -> List[dict]:
    with get_db_context() as conn:
        if tenant_id:
            rows = conn.execute(
                "SELECT * FROM tenant_crawl_images WHERE run_id = ? AND tenant_id = ? ORDER BY id ASC",
                (run_id, tenant_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tenant_crawl_images WHERE run_id = ? ORDER BY id ASC",
                (run_id,),
            ).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "run_id": r["run_id"],
            "tenant_id": r["tenant_id"],
            "source_url": r["source_url"],
            "local_path": r["local_path"],
            "served_url": r["served_url"],
            "content_type": r["content_type"],
            "sha256": r["sha256"],
            "byte_size": r["byte_size"],
            "width": r["width"],
            "height": r["height"],
            "alt_text": r["alt_text"],
            "source_page_url": r["source_page_url"],
            "offering_name": r["offering_name"],
            "kind": r["kind"],
            "is_duplicate": bool(r["is_duplicate"]),
            "duplicate_of": r["duplicate_of"],
            "skipped": bool(r["skipped"]),
            "skip_reason": r["skip_reason"],
            "created_at": r["created_at"],
        })
    return out


def find_crawl_image_by_sha(run_id: int, sha256: str) -> Optional[dict]:
    with get_db_context() as conn:
        row = conn.execute(
            """SELECT * FROM tenant_crawl_images
               WHERE run_id = ? AND sha256 = ? AND skipped = 0 AND is_duplicate = 0
               ORDER BY id ASC LIMIT 1""",
            (run_id, sha256),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "local_path": row["local_path"],
        "served_url": row["served_url"],
        "source_url": row["source_url"],
    }


def find_crawl_image_by_source_url(run_id: int, source_url: str) -> Optional[dict]:
    with get_db_context() as conn:
        row = conn.execute(
            """SELECT * FROM tenant_crawl_images
               WHERE run_id = ? AND source_url = ?
               ORDER BY id ASC LIMIT 1""",
            (run_id, source_url),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "local_path": row["local_path"],
        "served_url": row["served_url"],
        "sha256": row["sha256"],
        "skipped": bool(row["skipped"]),
        "is_duplicate": bool(row["is_duplicate"]),
    }
