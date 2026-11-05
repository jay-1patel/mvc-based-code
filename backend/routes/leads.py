"""
Leads inbox (Phase 6) — the CRM hand-off surface for flow-runner leads.

Rows are written by backend/services/flow_runner.py (save_lead step). This
module gives the admin panel what it needs to work them:

    GET  /api/leads                 filter by tenant/status/assignee/search
    GET  /api/leads/export.csv     the same query as CSV
    GET  /api/leads/{id}           one lead with its collected data
    PUT  /api/leads/{id}           update status / assignee

Statuses match the leads table CHECK constraint exactly; the DB rejects
anything else. Every query is tenant-scoped when tenant_id is passed — a
lead from one brand must never surface in another brand's inbox.
"""
import csv
import io
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from database import get_db
from routes.auth import require_permission

logger = logging.getLogger("leads")

router = APIRouter(prefix="/api/leads", tags=["leads"])

LEAD_STATUSES = ("new", "contacted", "qualified", "won", "lost", "closed")


class LeadUpdate(BaseModel):
    status: Optional[str] = None
    assignee: Optional[str] = None


def _row_to_lead(row) -> dict:
    d = dict(row)
    try:
        d["collected"] = json.loads(d.get("collected") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["collected"] = {}
    return d


def _apply_filters(where: str, params: list, tenant_id: Optional[str],
                   status: Optional[str], assignee: Optional[str],
                   q: Optional[str]) -> tuple[str, list]:
    """Shared WHERE-fragment builder so list and CSV export can never drift."""
    if tenant_id:
        where += " AND tenant_id = ?"
        params.append(tenant_id.strip())
    if status:
        if status not in LEAD_STATUSES:
            raise HTTPException(status_code=400, detail=f"Unknown status '{status}'")
        where += " AND status = ?"
        params.append(status)
    if assignee is not None:
        where += " AND assignee = ?"
        params.append(assignee.strip())
    if q:
        like = f"%{q.strip()}%"
        where += (" AND (wa_id LIKE ? OR user_name LIKE ? OR phone LIKE ? "
                  "OR email LIKE ? OR collected LIKE ?)")
        params.extend([like, like, like, like, like])
    return where, params


# ── list ───────────────────────────────────────────────────────────────────

@router.get("")
def list_leads(
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_admin: dict = Depends(require_permission("manage_operations")),
):
    conn = get_db()
    try:
        where, params = _apply_filters(
            " WHERE 1=1", [], tenant_id, status, assignee, q)
        total = conn.execute(
            "SELECT COUNT(*) FROM leads" + where, params).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM leads" + where +
            " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]).fetchall()
        return {"total": total, "count": len(rows), "items": [_row_to_lead(r) for r in rows]}
    finally:
        conn.close()


@router.get("/export.csv")
def export_leads(
    tenant_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    current_admin: dict = Depends(require_permission("manage_operations")),
):
    conn = get_db()
    try:
        where, params = _apply_filters(
            " WHERE 1=1", [], tenant_id, status, assignee, None)
        rows = conn.execute(
            "SELECT id, tenant_id, wa_id, user_name, phone, email, collected, "
            "status, assignee, source, flow, created_at, updated_at "
            "FROM leads" + where + " ORDER BY created_at DESC, id DESC",
            params).fetchall()
    finally:
        conn.close()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "tenant_id", "wa_id", "user_name", "phone", "email",
                     "collected", "status", "assignee", "source", "flow",
                     "created_at", "updated_at"])
    for r in rows:
        writer.writerow(list(r))
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


@router.get("/{lead_id}")
def get_lead(
    lead_id: int,
    current_admin: dict = Depends(require_permission("manage_operations")),
):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return _row_to_lead(row)


@router.put("/{lead_id}")
def update_lead(
    lead_id: int,
    body: LeadUpdate,
    current_admin: dict = Depends(require_permission("manage_operations")),
):
    if body.status and body.status not in LEAD_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status '{body.status}'. Valid: {list(LEAD_STATUSES)}")
    if not body.status and body.assignee is None:
        raise HTTPException(status_code=400, detail="Nothing to update")

    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Lead not found")
        if body.status:
            conn.execute("UPDATE leads SET status = ? WHERE id = ?", (body.status, lead_id))
        if body.assignee is not None:
            conn.execute("UPDATE leads SET assignee = ? WHERE id = ?", (body.assignee.strip(), lead_id))
        conn.execute(
            "UPDATE leads SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (lead_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        return _row_to_lead(row)
    finally:
        conn.close()
