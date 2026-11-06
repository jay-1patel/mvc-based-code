"""Crawl v2 API — staging-gated website crawl (no auto-publish).

Endpoints live under ``/api/admin/tenants/{tenant_id}/crawl*``.
Auth mirrors tenants.py: ``require_tenant_access()`` (admin + manage_operations,
tenant-scoped). Unknown / cross-tenant run ids → 404.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from database import (
    create_crawl_run,
    get_crawl_run,
    get_crawl_schedule,
    get_crawl_staged,
    get_running_crawl,
    list_crawl_images,
    list_crawl_pages,
    list_crawl_runs,
    record_admin_audit_event,
    get_db_context,
)
from routes.auth import require_tenant_access

logger = logging.getLogger("crawl")
router = APIRouter(prefix="/api/admin/tenants", tags=["crawl"])


class CrawlStartRequest(BaseModel):
    url: str = Field(..., min_length=1)
    max_pages: int = Field(default=50, ge=1, le=100)
    max_depth: int = Field(default=4, ge=0, le=10)


def _bg_run_crawl(
    tenant_id: str,
    url: str,
    run_id: int,
    max_pages: int,
    max_depth: int,
    actor: dict,
) -> None:
    try:
        from services.crawl_pipeline import run_crawl_pipeline
        run_crawl_pipeline(
            tenant_id=tenant_id,
            url=url,
            run_id=run_id,
            trigger="manual",
            max_pages=max_pages,
            max_depth=max_depth,
            actor=actor,
        )
    except Exception as exc:
        logger.error("Background crawl failed run=%s: %s", run_id, exc, exc_info=True)


@router.post("/{tenant_id}/crawl", status_code=202)
def start_crawl(
    tenant_id: str,
    body: CrawlStartRequest,
    background_tasks: BackgroundTasks,
    principal: dict = Depends(require_tenant_access()),
):
    url = (body.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Website URL is required")

    running = get_running_crawl(tenant_id)
    if running:
        raise HTTPException(
            status_code=409,
            detail=f"A crawl is already running for this tenant (run_id={running['id']})",
        )

    actor_name = (
        principal.get("username")
        or principal.get("label")
        or principal.get("tenant_id")
        or "admin"
    )
    run = create_crawl_run(
        tenant_id=tenant_id,
        source_url=url,
        trigger="manual",
        created_by=actor_name,
    )

    try:
        with get_db_context() as conn:
            record_admin_audit_event(
                conn,
                action="crawl_run_queued",
                actor=principal,
                resource_type="crawl_run",
                resource_id=str(run["id"]),
                tenant_id=tenant_id,
                details={"url": url[:200], "max_pages": body.max_pages},
            )
    except Exception:
        logger.exception("AUDIT_EVENT_RECORD_FAILED | action=crawl_run_queued")

    background_tasks.add_task(
        _bg_run_crawl,
        tenant_id,
        url,
        run["id"],
        body.max_pages,
        body.max_depth,
        principal,
    )
    return {
        "ok": True,
        "status": "queued",
        "run_id": run["id"],
        "run": run,
    }


@router.get("/{tenant_id}/crawl-runs")
def get_crawl_runs(
    tenant_id: str,
    limit: int = 20,
    principal: dict = Depends(require_tenant_access()),
):
    runs = list_crawl_runs(tenant_id, limit=limit)
    latest = runs[0] if runs else None
    schedule = get_crawl_schedule(tenant_id)
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "latest": latest,
        "history": runs,
        "schedule": schedule,
    }


@router.get("/{tenant_id}/crawl-runs/{run_id}")
def get_crawl_run_detail(
    tenant_id: str,
    run_id: int,
    principal: dict = Depends(require_tenant_access()),
):
    run = get_crawl_run(run_id, tenant_id=tenant_id)
    if not run:
        raise HTTPException(status_code=404, detail="Crawl run not found")
    pages = list_crawl_pages(run_id)
    images = list_crawl_images(run_id, tenant_id=tenant_id)
    staged = get_crawl_staged(run_id, tenant_id=tenant_id)
    return {
        "ok": True,
        "run": run,
        "pages": pages,
        "images": images,
        "staged_status": staged["status"] if staged else None,
    }


@router.get("/{tenant_id}/crawl-runs/{run_id}/staged")
def get_crawl_run_staged(
    tenant_id: str,
    run_id: int,
    principal: dict = Depends(require_tenant_access()),
):
    run = get_crawl_run(run_id, tenant_id=tenant_id)
    if not run:
        raise HTTPException(status_code=404, detail="Crawl run not found")
    staged = get_crawl_staged(run_id, tenant_id=tenant_id)
    if not staged:
        raise HTTPException(status_code=404, detail="No staged payload for this run")

    payload = staged.get("payload") or {}
    bifurcation = payload.get("bifurcation") or {}
    crawl_meta = payload.get("crawl") or {}
    return {
        "ok": True,
        "run_id": run_id,
        "tenant_id": tenant_id,
        "status": staged["status"],
        "decided_by": staged.get("decided_by"),
        "decided_at": staged.get("decided_at"),
        "summary": {
            "brand": (bifurcation.get("brand") or {}).get("name"),
            "vertical": bifurcation.get("vertical"),
            "offerings_count": len(bifurcation.get("offerings") or []),
            "faqs_count": len(bifurcation.get("faqs") or []),
            "pages_found": crawl_meta.get("pages_found"),
            "pages_scraped": crawl_meta.get("pages_scraped"),
            "images": payload.get("images_summary"),
            "offerings_sample": [
                {"name": o.get("name"), "price": o.get("price"), "media_url": o.get("media_url")}
                for o in (bifurcation.get("offerings") or [])[:10]
            ],
            "faqs_sample": (bifurcation.get("faqs") or [])[:5],
            "pages": crawl_meta.get("pages") or [],
        },
        "payload": payload,
    }


@router.post("/{tenant_id}/crawl-runs/{run_id}/approve")
def approve_crawl(
    tenant_id: str,
    run_id: int,
    principal: dict = Depends(require_tenant_access()),
):
    from services.crawl_pipeline import approve_crawl_run

    try:
        result = approve_crawl_run(
            tenant_id=tenant_id, run_id=run_id, actor=principal
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Crawl run not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.error("Approve crawl failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Approve failed: {exc}")
    return {"ok": True, "result": result}


@router.post("/{tenant_id}/crawl-runs/{run_id}/reject")
def reject_crawl(
    tenant_id: str,
    run_id: int,
    principal: dict = Depends(require_tenant_access()),
):
    from services.crawl_pipeline import reject_crawl_run

    try:
        result = reject_crawl_run(
            tenant_id=tenant_id, run_id=run_id, actor=principal
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Crawl run not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.error("Reject crawl failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Reject failed: {exc}")
    return {"ok": True, "result": result}
