"""Post-upload auto-bifurcation router.

Called from the background document ingestion path after a tenant (or admin)
file is saved. Extracts products, FAQs, and KB content via AutoBifurcator and
publishes them live for **any** tenant. Brand/menu merges are careful when the
tenant already has a real profile so placeholder LLM defaults do not wipe it;
empty / new tenants get a full write.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("upload_auto_router")


def _full_bifurcate_enabled() -> bool:
    return os.environ.get("AUTO_BIFURCATE_FULL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _tenant_needs_full_brand_write(tenant_id: str) -> bool:
    """True when the tenant has no meaningful brand yet (safe to fully overwrite)."""
    try:
        from shared.tenancy import store as tenancy_store
    except ImportError:
        try:
            from shared.tenancy import store as tenancy_store  # type: ignore
        except Exception:
            return True
    try:
        draft = tenancy_store.get_draft(tenant_id) or {}
        published = {}
        try:
            published = tenancy_store.get_published_mirror(tenant_id) or {}
        except Exception:
            published = {}
        brand = dict((published.get("brand") or draft.get("brand") or {}))
        if not brand:
            return True
        name = str(brand.get("name") or "").strip().lower()
        email = str(brand.get("support_email") or "").strip().lower()
        phone = str(brand.get("support_phone") or "").strip()
        tid = (tenant_id or "").strip().lower()
        placeholder_name = not name or name in {tid, tid.replace("-", " "), "draft tenant"}
        placeholder_email = (not email) or email.endswith(f"@{tid}.com") or email == f"support@{tid}.com"
        placeholder_phone = (not phone) or phone in {"+91 9800000000", "+919800000000", "9800000000", "+91 9876543210"}
        return placeholder_name and placeholder_email and placeholder_phone
    except Exception as exc:
        logger.debug("tenant profile empty-check failed for %s: %s", tenant_id, exc)
        return True


def route_uploaded_document(
    tenant_id: Optional[str],
    filename: str,
    content: bytes,
    actor: Optional[dict] = None,
) -> Dict[str, Any]:
    """Run full universal bifurcation for one uploaded document on any tenant.

    Returns a compact stats dict suitable for storing on ``admin_files``.
    Empty/disabled results still return a structured dict (never None).
    """
    empty = {
        "offerings_created": 0,
        "offerings_sample": [],
        "faqs_saved": 0,
        "kb_chunks_indexed": 0,
        "menu_buttons_count": 0,
        "intents_count": 0,
        "brand_updated": False,
        "brand": {},
        "vertical": None,
        "status": "skipped",
        "files_processed": [filename],
    }

    if not _full_bifurcate_enabled():
        empty["status"] = "disabled"
        return empty

    if not tenant_id:
        empty["status"] = "no_tenant"
        logger.info("UPLOAD_AUTO_ROUTER_SKIP | reason=no_tenant | file=%s", filename)
        return empty

    if not content:
        empty["status"] = "empty_file"
        return empty

    tid = str(tenant_id).strip()
    try:
        try:
            from services.auto_bifurcator import AutoBifurcator
        except ImportError:
            from backend.services.auto_bifurcator import AutoBifurcator

        careful = not _tenant_needs_full_brand_write(tid)
        bifurcator = AutoBifurcator(tenant_id=tid, actor=actor)
        # Stage offerings by default — wrong PDFs must not go live on WhatsApp.
        result = bifurcator.process_document_bytes(
            filename=filename,
            content=content,
            auto_publish=False,
            careful_brand_merge=careful,
        )
        result["status"] = "configured"
        result["careful_brand_merge"] = careful
        logger.info(
            "UPLOAD_AUTO_ROUTER_OK | tenant=%s | file=%s | careful=%s | offerings=%s | faqs=%s | kb=%s",
            tid,
            filename,
            careful,
            result.get("offerings_created"),
            result.get("faqs_saved"),
            result.get("kb_chunks_indexed"),
        )
        return result
    except Exception as exc:
        logger.warning(
            "UPLOAD_AUTO_ROUTER_FAILED | tenant=%s | file=%s | err=%s",
            tid,
            filename,
            exc,
            exc_info=True,
        )
        empty["status"] = "error"
        empty["error"] = str(exc)
        return empty
