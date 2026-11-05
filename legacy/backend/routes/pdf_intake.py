"""PDF intake — upload a client PDF, review where everything will land, approve.

    POST /api/admin/tenants/{tenant_id}/pdf-intake/preview   — parse, no writes
    POST /api/admin/tenants/{tenant_id}/pdf-intake/publish   — place the draft
    GET  /api/admin/tenants/{tenant_id}/pdf-intake/placements — the placement map

``preview`` never writes to a tenant. It returns the draft, the placement
breakdown and any warnings, so the console can show what will land where before
anything is committed. ``publish`` is the only call that writes, and it merges
into the tenant's existing config rather than replacing it.

PDF only, deliberately: the placement rules here are defined for documents that
carry company details, a catalogue and policies. The ordinary knowledge-base
upload path keeps accepting DOCX/XLSX/CSV.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     Request, UploadFile)
from fastapi.security import HTTPAuthorizationCredentials

from database import save_admin_file, update_admin_file_status
from routes.admin import MAX_UPLOAD_BYTES, UPLOAD_DIR
from routes.auth import require_tenant_access, security

logger = logging.getLogger("pdf_intake_route")

router = APIRouter(prefix="/api/admin/tenants/{tenant_id}/pdf-intake",
                   tags=["pdf-intake"])

#: Where a tenant's uploaded PDFs are kept, alongside other KB uploads so they
#: appear in the tenant's file library and can be previewed/downloaded there.
PDF_SUBDIR = "kb"


def _service(tenant_id: str, principal: dict):
    """Build the intake service with an actor suitable for the audit trail."""
    try:
        from services.pdf_intake import PdfIntakeService
    except ImportError:
        from backend.services.pdf_intake import PdfIntakeService

    actor = principal.get("actor") or {
        "username": principal.get("username") or principal.get("label") or "admin",
        "role": "super_admin",
        "tenant_id": tenant_id,
    }
    return PdfIntakeService(tenant_id, actor=actor)


def _placement_labels() -> dict:
    try:
        from services.pdf_intake import PLACEMENT_LABELS
    except ImportError:
        from backend.services.pdf_intake import PLACEMENT_LABELS
    return {k: {"label": v[0], "destination": v[1]} for k, v in PLACEMENT_LABELS.items()}


def _reject_non_pdf(filename: str) -> None:
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(
            status_code=400,
            detail=f"Only PDF documents are accepted here (got '{filename}'). "
                   "Use the uploads panel for Word or spreadsheet files.",
        )


def _store_pdf(tenant_id: str, filename: str, content: bytes, actor: dict) -> str:
    """Write the PDF to the tenant's library and return its path on disk."""
    safe_name = Path(filename).name  # never trust a client-supplied path
    module_dir = os.path.join(UPLOAD_DIR, PDF_SUBDIR)
    os.makedirs(module_dir, exist_ok=True)
    filepath = os.path.join(module_dir, safe_name)
    with open(filepath, "wb") as fh:
        fh.write(content)
    save_admin_file(
        safe_name, "pdf", PDF_SUBDIR, len(content),
        file_path=filepath, tenant_id=tenant_id, actor=actor,
        status="processing", chunks_count=0,
    )
    return filepath


@router.get("/placements")
def get_placements(
    tenant_id: str,
    principal: dict = Depends(require_tenant_access()),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """The placement map, so the console labels destinations from one source."""
    return {"placements": _placement_labels()}


@router.post("/preview")
async def preview_pdf_intake(
    request: Request,
    tenant_id: str,
    principal: dict = Depends(require_tenant_access()),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Parse uploaded PDFs and report where each item would land. Writes nothing."""
    try:
        from routes.tenant_files import _resolve_uploader
        actor = _resolve_uploader(request, principal, credentials)
    except Exception:
        actor = principal.get("actor") or {"username": "admin", "role": "super_admin",
                                           "tenant_id": tenant_id}

    content_type = request.headers.get("content-type", "")
    uploads: list[tuple[str, bytes]] = []

    if "multipart/form-data" in content_type:
        form = await request.form()
        candidates = list(form.getlist("files")) + [form.get("file")]
        for uf in candidates:
            if uf and hasattr(uf, "read"):
                raw = await uf.read()
                if raw:
                    uploads.append((uf.filename or "document.pdf", raw))
    elif "application/json" in content_type:
        import base64
        body = await request.json()
        for item in body.get("files") or []:
            raw = item.get("content") or ""
            if raw:
                uploads.append((item.get("filename") or "document.pdf",
                                base64.b64decode(raw)))
    else:
        raise HTTPException(status_code=400, detail="Unsupported content type")

    if not uploads:
        raise HTTPException(status_code=400, detail="No PDF provided")

    for filename, content in uploads:
        _reject_non_pdf(filename)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"'{filename}' is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            )

    service = _service(tenant_id, actor)
    docs, paths = [], []
    for filename, content in uploads:
        filepath = _store_pdf(tenant_id, filename, content, actor)
        paths.append(filepath)
        docs.append(service.extract(filepath))

    draft = service.bifurcate(docs)
    profile, warnings = service.validate(draft)

    return {
        "draft": draft,
        "warnings": warnings,
        "placements": _placement_labels(),
        "placement": draft.get("placement", {}),
        "profile_valid": profile is not None,
        "files": [Path(p).name for p in paths],
    }


@router.post("/publish")
async def publish_pdf_intake(
    request: Request,
    tenant_id: str,
    background_tasks: BackgroundTasks,
    principal: dict = Depends(require_tenant_access()),
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Place an approved draft: profile, products, services, FAQs and knowledge."""
    try:
        from shared.tenancy import store as tenancy_store
    except ImportError:
        from backend.shared.tenancy import store as tenancy_store  # type: ignore

    if not tenancy_store.get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")

    body = await request.json()
    draft = body.get("draft") or {}
    if not draft or not (draft.get("brand") or draft.get("offerings") or draft.get("faqs")):
        raise HTTPException(status_code=400, detail="Nothing to publish — the draft is empty")

    try:
        from routes.tenant_files import _resolve_uploader
        actor = _resolve_uploader(request, principal, credentials)
    except Exception:
        actor = principal.get("actor") or {"username": "admin", "role": "super_admin",
                                           "tenant_id": tenant_id}

    service = _service(tenant_id, actor)
    try:
        summary = service.publish(draft, background_tasks=background_tasks)
    except Exception as exc:
        logger.error("PDF intake publish failed for %s: %s", tenant_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Publishing failed: {exc}")

    for name in draft.get("source_files") or []:
        try:
            update_admin_file_status(
                Path(str(name)).name, status="ready",
                chunks_count=summary.get("kb_chunks_indexed", 0), tenant_id=tenant_id,
            )
        except Exception:
            logger.exception("INTAKE_FILE_STATUS_UPDATE_FAILED | file=%s tenant=%s", name, tenant_id)

    return {"status": "published", "summary": summary}
