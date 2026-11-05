"""Central API / runtime error reporter (Backend Roadmap Phase 1).

Adapted from the `database` branch's error_reporter design (Nov 2026) to the
Phase-1 signature. Every unhandled API exception (wired in backend/main.py's
central handler) and any external-API failure (Send2, LLM, payments, uploads…)
should call :func:`report_error`. That:

1. Emits ONE greppable log line:
   ``API_ERROR | provider=… | api=… | req=… | tenant=… | …``
2. Persists a row in ``api_error_logs`` (created lazily; readable via
   ``GET /api/admin/errors``).
3. Optionally alerts — **OFF by default**. Alerting only happens when
   ``ERROR_ALERTS_ENABLED=1`` AND ``ALERT_WEBHOOK_URL`` is set; even then it is
   rate-limited so a stuck poller cannot spam. No WhatsApp sending here.

``report_error`` never raises: a reporter failure must never break the request
that was being reported.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("error_reporter")

_lock = threading.Lock()
_last_alert_at: dict = {}

_CREATE_TABLE_SQL = """CREATE TABLE IF NOT EXISTS api_error_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    api TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'error',
    message TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    http_status INTEGER,
    tenant_id TEXT,
    wa_id TEXT,
    request_id TEXT
)"""


def _get_db_context():
    try:
        from database import get_db_context
    except Exception:
        from backend.database import get_db_context  # type: ignore
    return get_db_context


def _alerts_enabled() -> bool:
    return (
        os.getenv("ERROR_ALERTS_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
        and bool((os.getenv("ALERT_WEBHOOK_URL") or "").strip())
    )


def _cooldown_seconds() -> float:
    try:
        return float(os.getenv("ERROR_ALERT_COOLDOWN_SECONDS", "60"))
    except ValueError:
        return 60.0


def _should_notify(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        last = _last_alert_at.get(key, 0.0)
        if now - last < _cooldown_seconds():
            return False
        _last_alert_at[key] = now
        return True


def _persist(*, provider, api, severity, message, detail, http_status,
             tenant_id, wa_id, request_id) -> None:
    try:
        get_db_context = _get_db_context()
        with get_db_context() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_api_error_logs_created "
                "ON api_error_logs(created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_api_error_logs_provider "
                "ON api_error_logs(provider, created_at DESC)"
            )
            conn.execute(
                """INSERT INTO api_error_logs
                   (created_at, provider, api, severity, message, detail,
                    http_status, tenant_id, wa_id, request_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    (provider or "unknown")[:80],
                    (api or "")[:200],
                    (severity or "error")[:20],
                    (message or "")[:500],
                    (detail or "")[:4000],
                    http_status,
                    tenant_id or None,
                    wa_id or None,
                    request_id or None,
                ),
            )
    except Exception as exc:  # never break the caller
        logger.debug("api_error_logs persist failed: %s", exc)


def _push_webhook(text: str) -> None:
    """Best-effort webhook alert; only called when alerts are enabled."""
    try:
        url = (os.getenv("ALERT_WEBHOOK_URL") or "").strip()
        if not url:
            return
        req = urllib.request.Request(
            url,
            data=("{\"text\": " + _json_quote(text[:1500]) + "}").encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as exc:
        logger.debug("webhook alert failed: %s", exc)


def _json_quote(s: str) -> str:
    import json

    return json.dumps(s)


def report_error(
    provider: str,
    api: str,
    error,
    *,
    tenant_id: Optional[str] = None,
    request_id: Optional[str] = None,
    detail: Optional[str] = None,
    severity: str = "error",
    http_status: Optional[int] = None,
    wa_id: Optional[str] = None,
) -> None:
    """Log + persist an API/runtime failure. Never raises.

    ``error`` may be a string message or an exception instance.
    """
    try:
        if isinstance(error, BaseException):
            message = str(error) or error.__class__.__name__
            if not detail:
                detail = "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )[-3500:]
        else:
            message = str(error or "")

        line = (
            f"API_ERROR | provider={provider or 'unknown'} | api={api or '-'} "
            f"| req={request_id or '-'} | tenant={tenant_id or '-'} "
            f"| severity={severity} "
            f"| http={http_status if http_status is not None else '-'} "
            f"| {message[:300]}"
        )
        if severity == "warning":
            logger.warning(line)
        else:
            logger.error(line)

        _persist(
            provider=provider,
            api=api,
            severity=severity,
            message=message,
            detail=detail or "",
            http_status=http_status if http_status is not None else (500 if isinstance(error, BaseException) else None),
            tenant_id=tenant_id,
            wa_id=wa_id,
            request_id=request_id,
        )

        if not _alerts_enabled():
            return
        key = f"{provider}|{api}|{severity}|{message[:80]}"
        if _should_notify(key):
            _push_webhook(f"[{severity.upper()}] {provider} {api}: {message[:300]}")
    except Exception as exc:  # absolute last line of defence
        logger.debug("report_error failed: %s", exc)


def report_exception(provider, api, exc, **kwargs) -> None:
    """Convenience wrapper mirroring the database-branch helper."""
    report_error(provider, api, exc, **kwargs)


def _ensure_table(conn) -> None:
    conn.execute(_CREATE_TABLE_SQL)


def list_recent_errors(limit: int = 50, provider: Optional[str] = None,
                       tenant_id: Optional[str] = None) -> list:
    """Newest-first error rows for the admin API. Returns [] on a fresh DB."""
    get_db_context = _get_db_context()
    limit = max(1, min(int(limit or 50), 500))
    clauses, params = [], []
    if provider:
        clauses.append("provider = ?")
        params.append(provider)
    if tenant_id:
        clauses.append("tenant_id = ?")
        params.append(tenant_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with get_db_context() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            f"SELECT * FROM api_error_logs{where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_error(error_id: int) -> Optional[dict]:
    get_db_context = _get_db_context()
    with get_db_context() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM api_error_logs WHERE id = ?", (int(error_id),)
        ).fetchone()
        return dict(row) if row else None
