"""Onboarding Kits & Document Bifurcation API Routes.

Provides endpoints to:
  1. List and download pre-defined Onboarding Kits (DOCX & PDF) for all 8 verticals.
  2. Stage any document (complete kit → section parsers + gap-fill; incomplete/unstructured → universal extract).
  3. Publish reviewed drafts into tenant profile, intents, offerings, FAQ and KB.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

from routes.auth import get_current_admin, require_tenant_access, security

logger = logging.getLogger("onboarding_kits")
router = APIRouter(prefix="/api/admin", tags=["onboarding-kits"])

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "static" / "templates" / "onboarding"

VERTICAL_INFO = [
    {"slug": "real_estate", "title": "Real Estate & Property Development", "icon": "🏢"},
    {"slug": "ecommerce", "title": "E-Commerce & Retail Brands", "icon": "🛍️"},
    {"slug": "it_software", "title": "Software & IT Services", "icon": "💻"},
    {"slug": "tours_travel", "title": "Tours & Travel Agency", "icon": "✈️"},
    {"slug": "healthcare", "title": "Healthcare & Specialist Clinics", "icon": "🩺"},
    {"slug": "banking", "title": "Banking & Insurance Services", "icon": "💳"},
    {"slug": "finance", "title": "Finance, Accounting & Tax Advisory", "icon": "📊"},
    {"slug": "generic", "title": "General Business & Enterprise Services", "icon": "📦"},
]


# ──────────────────────────────────────────────────────────────────────────────
# 1. Template Kits Listing & Download Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/onboarding-kits/list")
def list_onboarding_kits():
    """List all available industry onboarding templates with download links."""
    kits = []
    for v in VERTICAL_INFO:
        slug = v["slug"]
        docx_name = f"{slug}_onboarding_kit.docx"
        pdf_name = f"{slug}_onboarding_kit.pdf"
        docx_exists = (TEMPLATES_DIR / docx_name).exists()
        pdf_exists = (TEMPLATES_DIR / pdf_name).exists()

        kits.append({
            "vertical": slug,
            "title": v["title"],
            "icon": v["icon"],
            "docx_url": f"/api/admin/onboarding-kits/{slug}/docx" if docx_exists else None,
            "pdf_url": f"/api/admin/onboarding-kits/{slug}/pdf" if pdf_exists else None,
            "has_docx": docx_exists,
            "has_pdf": pdf_exists,
        })
    return {"templates": kits}


@router.get("/onboarding-kits/{vertical}/{fmt}")
def download_onboarding_kit(vertical: str, fmt: str):
    """Download a specific vertical onboarding kit in docx or pdf format."""
    fmt = fmt.lower().strip(".")
    if fmt not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail="Supported formats are 'docx' or 'pdf'")

    valid_slugs = {v["slug"] for v in VERTICAL_INFO}
    if vertical not in valid_slugs:
        raise HTTPException(status_code=404, detail=f"Vertical '{vertical}' not found")

    filename = f"{vertical}_onboarding_kit.{fmt}"
    file_path = TEMPLATES_DIR / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Template file '{filename}' not found on server")

    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if fmt == "docx"
        else "application/pdf"
    )
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=media_type,
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2. Document Staging Endpoints (Parse & Preview before Publish)
# ──────────────────────────────────────────────────────────────────────────────

async def _parse_stage_upload_payload(
    request: Request,
    file: Optional[UploadFile],
) -> tuple[list, Optional[str], str]:
    """Parse multipart/JSON upload body used by both global and tenant stage routes.

    Returns ``(files_payload, optional_tenant_id_from_body, lane)``.
    ``lane`` is ``kit`` for Option B (filled industry kit) or ``any`` for Option A.
    """
    files_payload: List[Dict[str, Any]] = []
    body_tenant: Optional[str] = None
    lane = "any"
    content_type = request.headers.get("content-type", "")

    if file:
        raw = await file.read()
        files_payload.append({
            "filename": file.filename or "onboarding_doc.docx",
            "content": raw,
        })
    elif "multipart/form-data" in content_type:
        form = await request.form()
        maybe_tid = form.get("tenant_id")
        if maybe_tid and isinstance(maybe_tid, str):
            body_tenant = maybe_tid.strip()
        maybe_lane = form.get("lane")
        if maybe_lane and isinstance(maybe_lane, str) and maybe_lane.strip().lower() in ("kit", "any"):
            lane = maybe_lane.strip().lower()
        for field_name in ("file", "files", "document"):
            uploaded = form.getlist(field_name)
            for uf in uploaded:
                if uf and hasattr(uf, "read"):
                    raw = await uf.read()
                    files_payload.append({
                        "filename": uf.filename or "onboarding_doc.docx",
                        "content": raw,
                    })
    elif "application/json" in content_type:
        body = await request.json()
        if isinstance(body.get("tenant_id"), str) and body.get("tenant_id").strip():
            body_tenant = body["tenant_id"].strip()
        raw_lane = str(body.get("lane") or "any").strip().lower()
        if raw_lane in ("kit", "any"):
            lane = raw_lane
        raw_files = body.get("files") or []
        for rf in raw_files:
            files_payload.append({
                "filename": rf.get("filename", "document.docx"),
                "content": rf.get("content", ""),
            })

    return files_payload, body_tenant, lane


@router.post("/onboarding/stage")
async def stage_onboarding_document_global(
    request: Request,
    file: Optional[UploadFile] = File(None),
):
    """Parse any document globally (registration wizard) or for an optional tenant_id.

    Works for kits and unstructured uploads (MD/PDF/DOCX/scrapes). When
    ``tenant_id`` is provided in the JSON/multipart body, the staged draft is
    tagged for that tenant so publish can target any company.
    """
    files_payload, body_tenant, lane = await _parse_stage_upload_payload(request, file)
    if not files_payload:
        raise HTTPException(status_code=400, detail="No document provided to stage")

    target_tenant = (body_tenant or "draft_tenant").strip() or "draft_tenant"
    from services.auto_bifurcator import AutoBifurcator
    bifurcator = AutoBifurcator(tenant_id=target_tenant)
    try:
        # Ollama multi-pass — must not block the asyncio event loop (port 9000 hang).
        staged = await run_in_threadpool(
            lambda: bifurcator.stage_files(files_payload, lane=lane),
        )
        staged["tenant_id"] = target_tenant
        staged["lane"] = lane
        return staged
    except Exception as exc:
        logger.error("Global onboarding document staging failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Document parsing failed: {exc}")


@router.post("/tenants/{tenant_id}/onboarding/stage")
async def stage_onboarding_document_tenant(
    tenant_id: str,
    request: Request,
    file: Optional[UploadFile] = File(None),
    principal: dict = Depends(require_tenant_access()),
):
    """Parse any document for a specific tenant (works for every tenant slug/id)."""
    files_payload, _, lane = await _parse_stage_upload_payload(request, file)
    if not files_payload:
        raise HTTPException(status_code=400, detail="No document provided to stage")

    tid = (tenant_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    from services.auto_bifurcator import AutoBifurcator
    actor = principal.get("actor") or {"username": "admin", "role": "super_admin", "tenant_id": tid}
    bifurcator = AutoBifurcator(tenant_id=tid, actor=actor)
    try:
        staged = await run_in_threadpool(
            lambda: bifurcator.stage_files(files_payload, lane=lane),
        )
        staged["tenant_id"] = tid
        staged["lane"] = lane
        return staged
    except Exception as exc:
        logger.error("Tenant onboarding document staging failed for %s: %s", tid, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Document parsing failed: {exc}")


# ──────────────────────────────────────────────────────────────────────────────
# 3. Confirmation & Publishing Endpoint
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/tenants/{tenant_id}/onboarding/publish")
async def publish_onboarding_bifurcation(
    tenant_id: str,
    request: Request,
    principal: dict = Depends(require_tenant_access()),
):
    """Commit and publish reviewed bifurcated configuration for this tenant (any company)."""
    tid = (tenant_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    body = await request.json()
    bifurcation_draft = body.get("draft") or body
    if not bifurcation_draft:
        raise HTTPException(status_code=400, detail="No bifurcation draft provided to publish")

    try:
        from services.auto_bifurcator import AutoBifurcator, _sanitize_business_hours
    except ImportError:
        from backend.services.auto_bifurcator import AutoBifurcator, _sanitize_business_hours

    actor = principal.get("actor") or {
        "username": principal.get("username") or principal.get("label") or "admin",
        "role": principal.get("role") or "super_admin",
        "tenant_id": tid,
    }
    bifurcator = AutoBifurcator(tenant_id=tid, actor=actor)

    draft = dict(bifurcation_draft)
    # Always publish into the path tenant — never a leftover draft_tenant tag
    draft["tenant_id"] = tid
    if isinstance(draft.get("business_hours"), dict):
        draft["business_hours"] = _sanitize_business_hours(draft["business_hours"])

    try:
        summary = await run_in_threadpool(
            lambda: bifurcator.apply_bifurcation(
                bifurcation=draft,
                raw_files=[],
                auto_publish=True,
                careful_brand_merge=False,
            )
        )
        summary["tenant_id"] = tid
        return {
            "status": "published",
            "tenant_id": tid,
            "summary": summary,
        }
    except Exception as exc:
        logger.error("Publishing bifurcated draft failed for %s: %s", tid, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Publishing failed: {exc}")



