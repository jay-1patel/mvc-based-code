"""Admin API for persisted API/runtime errors (Backend Roadmap Phase 1).

Rows are written by services.error_reporter.report_error — the central
exception handler in backend/main.py reports every unhandled 500 here, so a
developer can find the exact failing API from this list (or from the
X-Request-Id / request_id quoted by the user) instead of grepping logs.

Access: super admins see every tenant's errors. A tenant admin sees ONLY
their own tenant's errors — the tenant filter is forced to their tenant
and asking for another tenant is refused with 403.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from routes.auth import get_current_admin
from services.error_reporter import get_error, list_recent_errors

router = APIRouter(tags=["api-errors"])


def _scoped_tenant_id(current_admin: dict, requested_tenant_id: Optional[str]) -> Optional[str]:
    role = current_admin.get("role")
    if role == "super_admin":
        return requested_tenant_id
    if role == "admin":
        own = str(current_admin.get("tenant_id") or "").strip()
        if not own:
            raise HTTPException(status_code=403, detail="Your admin account is not assigned to a tenant")
        requested = str(requested_tenant_id or "").strip()
        if requested and requested != own:
            raise HTTPException(status_code=403, detail="Admins can only view their own tenant's errors")
        return own
    raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/api/admin/errors")
def list_api_errors(
    limit: int = Query(50, ge=1, le=500),
    provider: Optional[str] = Query(None, description="Filter e.g. api, send2, groq"),
    tenant_id: Optional[str] = Query(None, description="Filter by tenant id"),
    current_admin: dict = Depends(get_current_admin),
):
    """Recent API/runtime errors, newest first."""
    tenant_id = _scoped_tenant_id(current_admin, tenant_id)
    rows = list_recent_errors(limit=limit, provider=provider or None,
                              tenant_id=tenant_id or None)
    return {"count": len(rows), "errors": rows}


@router.get("/api/admin/errors/{error_id}")
def get_api_error(
    error_id: int,
    current_admin: dict = Depends(get_current_admin),
):
    row = get_error(error_id)
    if not row:
        raise HTTPException(status_code=404, detail="Error log not found")
    if current_admin.get("role") == "admin":
        own = _scoped_tenant_id(current_admin, None)
        if str(row.get("tenant_id") or "") != str(own or ""):
            raise HTTPException(status_code=403, detail="Admins can only view their own tenant's errors")
    elif current_admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return row
