"""Admin API for persisted API/runtime errors (Backend Roadmap Phase 1).

Rows are written by services.error_reporter.report_error — the central
exception handler in backend/main.py reports every unhandled 500 here, so a
developer can find the exact failing API from this list (or from the
X-Request-Id / request_id quoted by the user) instead of grepping logs.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from routes.auth import require_permission
from services.error_reporter import get_error, list_recent_errors

router = APIRouter(tags=["api-errors"])


@router.get("/api/admin/errors")
def list_api_errors(
    limit: int = Query(50, ge=1, le=500),
    provider: Optional[str] = Query(None, description="Filter e.g. api, send2, groq"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant id"),
    current_admin: dict = Depends(require_permission("manage_operations")),
):
    """Recent API/runtime errors, newest first."""
    rows = list_recent_errors(limit=limit, provider=provider or None,
                              tenant_id=tenant_id or None)
    return {"count": len(rows), "errors": rows}


@router.get("/api/admin/errors/{error_id}")
def get_api_error(
    error_id: int,
    current_admin: dict = Depends(require_permission("manage_operations")),
):
    row = get_error(error_id)
    if not row:
        raise HTTPException(status_code=404, detail="Error log not found")
    return row
