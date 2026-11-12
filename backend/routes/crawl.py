"""Crawl v2 API — staging-gated website crawl (no auto-publish).

Endpoints live under ``/api/admin/tenants/{tenant_id}/crawl*``.
Auth mirrors tenants.py: ``require_tenant_access()`` (admin + manage_operations,
tenant-scoped). Unknown / cross-tenant run ids → 404.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
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


def default_max_pages() -> int:
    """Page cap used when a crawl request does not set one.

    Override with CRAWL_DEFAULT_MAX_PAGES. An explicit positive max_pages on
    the request still wins.
    """
    raw = (os.getenv("CRAWL_DEFAULT_MAX_PAGES") or "50").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 50
    return max(1, min(value, 500))


class CrawlStartRequest(BaseModel):
    url: str = Field(..., min_length=1)
    # Omitted means the configurable default (50), not an unbounded crawl.
    max_pages: Optional[int] = Field(default=None, ge=1, le=500)
    max_depth: int = Field(default=4, ge=0, le=10)


def _bg_run_crawl(
    tenant_id: str,
    url: str,
    run_id: int,
    max_pages: Optional[int],
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

    # M2 (SSRF): reject non-public crawl targets before any run is created.
    from services.site_crawler import BlockedUrlError, assert_public_url
    from services.url_scraper import normalize_url
    try:
        assert_public_url(normalize_url(url) or url)
    except BlockedUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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
                details={"url": url[:200], "max_pages": body.max_pages or default_max_pages()},
            )
    except Exception:
        logger.exception("AUDIT_EVENT_RECORD_FAILED | action=crawl_run_queued")

    background_tasks.add_task(
        _bg_run_crawl,
        tenant_id,
        url,
        run["id"],
        body.max_pages or default_max_pages(),
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


def _crawl_export_data(tenant_id: str, run_id: int, run: dict, staged: dict) -> dict:
    """Normalise a staged crawl payload into the downloadable knowledge doc.

    Only knowledge content is exported — KB articles, FAQs, product
    knowledge and the crawled page list. Website-guessed menu / brand /
    hours are NOT part of the export (Rule 1: those come from the
    tenant's PDF, never from the crawl).
    """
    payload = staged.get("payload") or {}
    bifurcation = payload.get("bifurcation") or {}
    crawl_meta = payload.get("crawl") or {}

    knowledge_base = []
    for art in bifurcation.get("knowledge_articles") or []:
        if not isinstance(art, dict):
            continue
        knowledge_base.append({
            "title": art.get("title") or "",
            "category": art.get("category") or "",
            "content": art.get("markdown_content") or art.get("content") or "",
        })

    faqs = []
    for faq in bifurcation.get("faqs") or []:
        if not isinstance(faq, dict):
            continue
        faqs.append({
            "question": faq.get("question") or "",
            "answer": faq.get("answer") or "",
            "category": faq.get("category") or "",
        })

    products = []
    for off in bifurcation.get("offerings") or []:
        if not isinstance(off, dict):
            continue
        products.append({
            "name": off.get("name") or "",
            "category": off.get("category") or "",
            "price": "" if off.get("price") is None else str(off.get("price")),
            "short_description": off.get("short_description") or "",
            "description": off.get("description") or "",
            "media_url": off.get("media_url") or "",
            "detail_url": off.get("detail_url") or "",
        })

    pages = []
    for page in crawl_meta.get("pages") or []:
        if not isinstance(page, dict):
            continue
        pages.append({
            "url": page.get("url") or "",
            "title": page.get("title") or "",
            "meta_description": page.get("meta_description") or "",
            "word_count": page.get("word_count"),
        })

    return {
        "tenant_id": tenant_id,
        "run_id": run_id,
        "status": staged.get("status"),
        "source_url": run.get("source_url") or crawl_meta.get("root_url") or "",
        "pages_found": crawl_meta.get("pages_found"),
        "pages_scraped": crawl_meta.get("pages_scraped"),
        "counts": {
            "knowledge_base": len(knowledge_base),
            "faqs": len(faqs),
            "products": len(products),
            "pages": len(pages),
        },
        "knowledge_base": knowledge_base,
        "faqs": faqs,
        "products": products,
        "pages": pages,
    }


def _crawl_export_csv(data: dict) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["section", "title_or_question", "category", "text_or_answer", "price", "url"])
    for art in data["knowledge_base"]:
        writer.writerow(["knowledge_base", art["title"], art["category"], art["content"], "", ""])
    for faq in data["faqs"]:
        writer.writerow(["faq", faq["question"], faq["category"], faq["answer"], "", ""])
    for prod in data["products"]:
        writer.writerow([
            "product",
            prod["name"],
            prod["category"],
            prod["description"] or prod["short_description"],
            prod["price"],
            prod["detail_url"] or prod["media_url"],
        ])
    for page in data["pages"]:
        writer.writerow(["page", page["title"], "", page["meta_description"], "", page["url"]])
    return output.getvalue()


def _render_crawl_export_pdf(data: dict) -> bytes:
    """Render the crawl knowledge doc as a simple paginated PDF via PyMuPDF.

    Same approach as the audit export (routes/audit.py): no new dependency.
    Body text is word-wrapped to a fixed character width so long KB
    article text stays readable.
    """
    import textwrap

    import fitz

    lines: list[tuple[str, str]] = []  # (kind, text)

    def add(kind: str, text: str) -> None:
        lines.append((kind, text or ""))

    add("title", f"Website crawl — run {data['run_id']}")
    add("meta", f"Tenant: {data['tenant_id']}  |  Source: {data['source_url']}  |  Status: {data['status']}")
    counts = data["counts"]
    add("meta", (
        f"Knowledge base: {counts['knowledge_base']}  |  FAQs: {counts['faqs']}  |  "
        f"Products: {counts['products']}  |  Pages: {counts['pages']}"
    ))
    add("blank", "")

    add("h", "Knowledge Base")
    if not data["knowledge_base"]:
        add("body", "(none)")
    for art in data["knowledge_base"]:
        add("item", art["title"] or "(untitled)")
        if art["category"]:
            add("body", f"Category: {art['category']}")
        for wrapped in textwrap.wrap(art["content"], width=108) or [""]:
            add("body", wrapped)
        add("blank", "")

    add("h", "FAQs")
    if not data["faqs"]:
        add("body", "(none)")
    for faq in data["faqs"]:
        add("item", f"Q: {faq['question']}")
        for wrapped in textwrap.wrap(f"A: {faq['answer']}", width=108) or [""]:
            add("body", wrapped)
        add("blank", "")

    add("h", "Product Knowledge")
    if not data["products"]:
        add("body", "(none)")
    for prod in data["products"]:
        heading = prod["name"]
        if prod["price"]:
            heading += f" — {prod['price']}"
        add("item", heading)
        if prod["category"]:
            add("body", f"Category: {prod['category']}")
        desc = prod["description"] or prod["short_description"]
        for wrapped in textwrap.wrap(desc, width=108) or [""]:
            add("body", wrapped)
        add("blank", "")

    add("h", "Pages Crawled")
    if not data["pages"]:
        add("body", "(none)")
    for page in data["pages"]:
        add("body", f"{page['url']}  —  {page['title']}".rstrip(" —"))

    doc = fitz.open()
    page = None
    y = 48
    PAGE_BOTTOM = 545

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=842, height=595)  # A4 landscape
        y = 48

    new_page()
    styles = {
        "title": (16, "hebo", 22),
        "meta": (9, "helv", 13),
        "h": (13, "hebo", 20),
        "item": (10, "hebo", 14),
        "body": (9, "helv", 12),
        "blank": (9, "helv", 8),
    }
    for kind, text in lines:
        size, font, advance = styles[kind]
        if y > PAGE_BOTTOM:
            new_page()
        if text:
            page.insert_text((40, y), text[:160], fontsize=size, fontname=font)
        y += advance
    payload = doc.tobytes()
    doc.close()
    return payload


@router.get("/{tenant_id}/crawl-runs/{run_id}/export")
def export_crawl_run(
    tenant_id: str,
    run_id: int,
    format: str = "json",
    principal: dict = Depends(require_tenant_access()),
):
    """Download a run's staged crawled knowledge (KB / FAQs / products).

    Formats: json (default), csv, pdf. Unknown format → 400; unknown or
    cross-tenant run → 404, mirroring the sibling crawl routes.
    """
    fmt = (format or "json").strip().lower()
    if fmt not in ("json", "csv", "pdf"):
        raise HTTPException(
            status_code=400,
            detail="Format must be one of: json, csv, pdf",
        )
    run = get_crawl_run(run_id, tenant_id=tenant_id)
    if not run:
        raise HTTPException(status_code=404, detail="Crawl run not found")
    staged = get_crawl_staged(run_id, tenant_id=tenant_id)
    if not staged:
        raise HTTPException(status_code=404, detail="No staged payload for this run")

    data = _crawl_export_data(tenant_id, run_id, run, staged)
    filename = f"crawl-run-{run_id}-knowledge.{fmt}"
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    if fmt == "json":
        return Response(
            content=json.dumps(data, indent=2, ensure_ascii=False),
            media_type="application/json",
            headers=headers,
        )
    if fmt == "csv":
        return Response(
            content=_crawl_export_csv(data),
            media_type="text/csv",
            headers=headers,
        )
    return Response(
        content=_render_crawl_export_pdf(data),
        media_type="application/pdf",
        headers=headers,
    )


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
