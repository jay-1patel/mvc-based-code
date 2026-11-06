"""Crawl v2 pipeline — crawl → images → AI bifurcation → staging only.

Approve replays staged bifurcation through legacy AutoBifurcator commit.
Reject deletes staged image files and touches nothing live.
NO auto_publish anywhere in this module.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("crawl_pipeline")


def _actor_name(actor: Optional[dict]) -> str:
    if not actor:
        return "system"
    return (
        actor.get("username")
        or actor.get("label")
        or actor.get("tenant_id")
        or "system"
    )


def run_crawl_pipeline(
    *,
    tenant_id: str,
    url: str,
    run_id: int,
    trigger: str = "manual",
    max_pages: int = 50,
    max_depth: int = 4,
    actor: Optional[dict] = None,
) -> Dict[str, Any]:
    """Execute a full crawl run and write results to staging only."""
    from database import (
        insert_crawl_page,
        list_crawl_images,
        mark_crawl_finished,
        record_admin_audit_event,
        save_crawl_staged,
        update_crawl_run,
        get_db_context,
    )
    from services.crawl_images import download_and_store_crawl_images
    from services.site_crawler import SiteCrawler
    from services.url_scraper import UrlAutoOnboarder

    tenant_id = (tenant_id or "").strip().lower()
    summary: Dict[str, Any] = {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "status": "running",
    }

    update_crawl_run(run_id, status="running", mark_started=True)

    try:
        crawler = SiteCrawler(
            base_url=url,
            max_pages=max_pages,
            max_depth=max_depth,
        )
        crawl_res = crawler.crawl()

        if crawl_res.get("blocked_by_robots"):
            raise ValueError(f"robots.txt disallows crawling this site: {url}")

        inventory = crawl_res.get("inventory") or []
        pages = crawl_res.get("pages") or []

        for item in inventory:
            insert_crawl_page(
                run_id=run_id,
                url=item.get("url") or item.get("requested_url") or "",
                source=item.get("source") or "link",
                status="success" if item.get("success") else "failure",
                http_status=item.get("http_status"),
                word_count=int(item.get("word_count") or 0),
                error=item.get("error"),
            )

        pages_found = len(inventory)
        pages_scraped = len(pages)
        update_crawl_run(
            run_id,
            pages_found=pages_found,
            pages_scraped=pages_scraped,
        )

        if not pages:
            raise ValueError(f"Could not reach or crawl website: {url}")

        # Download managed assets (staging dir under UPLOAD_DIR)
        image_records = download_and_store_crawl_images(
            tenant_id=tenant_id,
            run_id=run_id,
            pages=pages,
        )

        # Strip heavy HTML before bifurcation / staging payload
        slim_pages = []
        for p in pages:
            slim = dict(p)
            slim.pop("html", None)
            slim_pages.append(slim)
        crawl_res_for_ai = dict(crawl_res)
        crawl_res_for_ai["pages"] = slim_pages

        onboarder = UrlAutoOnboarder(tenant_id=tenant_id, actor=actor)
        bifurcation = onboarder.extract_bifurcation(crawl_res_for_ai)

        # Link stored image URLs into bifurcation media fields where possible
        _apply_image_links(bifurcation, image_records)

        payload = {
            "bifurcation": bifurcation,
            "crawl": {
                "root_url": crawl_res.get("root_url"),
                "domain": crawl_res.get("domain"),
                "pages_found": pages_found,
                "pages_scraped": pages_scraped,
                "pages": [
                    {
                        "url": p.get("url"),
                        "title": p.get("title"),
                        "meta_description": p.get("meta_description"),
                        "discovery_source": p.get("discovery_source"),
                        "word_count": p.get("word_count"),
                        "headings": p.get("headings"),
                    }
                    for p in slim_pages
                ],
            },
            "images_summary": {
                "total_rows": len(image_records),
                "stored": sum(
                    1 for i in image_records
                    if i.get("local_path") and not i.get("is_duplicate") and not i.get("skipped")
                ),
                "duplicates": sum(1 for i in image_records if i.get("is_duplicate")),
                "skipped": sum(1 for i in image_records if i.get("skipped")),
            },
            "trigger": trigger,
        }

        save_crawl_staged(run_id, tenant_id, payload)
        update_crawl_run(run_id, status="done", mark_finished=True)

        if trigger == "schedule":
            mark_crawl_finished(tenant_id, "success", pages_found=pages_found)

        try:
            with get_db_context() as conn:
                record_admin_audit_event(
                    conn,
                    action="crawl_run_staged",
                    actor=actor or {"username": "crawl_pipeline", "role": "system"},
                    resource_type="crawl_run",
                    resource_id=str(run_id),
                    tenant_id=tenant_id,
                    details={
                        "pages_found": pages_found,
                        "pages_scraped": pages_scraped,
                        "offerings": len((bifurcation or {}).get("offerings") or []),
                        "faqs": len((bifurcation or {}).get("faqs") or []),
                        "images_stored": payload["images_summary"]["stored"],
                        "trigger": trigger,
                        "url": url[:200],
                    },
                )
        except Exception:
            logger.exception("AUDIT_EVENT_RECORD_FAILED | action=crawl_run_staged run=%s", run_id)

        summary.update({
            "status": "done",
            "pages_found": pages_found,
            "pages_scraped": pages_scraped,
            "offerings": len((bifurcation or {}).get("offerings") or []),
            "faqs": len((bifurcation or {}).get("faqs") or []),
            "images": payload["images_summary"],
        })
        return summary

    except Exception as exc:
        logger.error(
            "CRAWL_PIPELINE_FAIL | tenant=%s run=%s | %s",
            tenant_id, run_id, exc, exc_info=True,
        )
        update_crawl_run(
            run_id,
            status="failed",
            error=str(exc)[:500],
            mark_finished=True,
        )
        if trigger == "schedule":
            try:
                mark_crawl_finished(tenant_id, "failed", str(exc)[:500])
            except Exception:
                pass
        summary.update({"status": "failed", "error": str(exc)[:500]})
        return summary


def _apply_image_links(
    bifurcation: Dict[str, Any],
    image_records: List[Dict[str, Any]],
) -> None:
    """Prefer staged local URLs for brand logo + offering media when we have them."""
    if not bifurcation:
        return
    by_source = {
        i["source_url"]: i
        for i in image_records
        if i.get("source_url") and i.get("served_url") and not i.get("skipped")
    }
    logos = [
        i for i in image_records
        if i.get("kind") == "logo" and i.get("served_url") and not i.get("skipped")
    ]
    brand = bifurcation.get("brand") or {}
    logo_src = brand.get("logo_url")
    if logo_src and logo_src in by_source:
        brand["logo_url"] = by_source[logo_src]["served_url"]
        brand["logo_source_url"] = logo_src
    elif logos:
        brand["logo_url"] = logos[0]["served_url"]
        brand["logo_source_url"] = logos[0].get("source_url")
    bifurcation["brand"] = brand

    for off in bifurcation.get("offerings") or []:
        media = off.get("media_url")
        if media and media in by_source:
            off["media_source_url"] = media
            off["media_url"] = by_source[media]["served_url"]
            continue
        # Fuzzy match by offering name
        name = (off.get("name") or "").strip().lower()
        if not name:
            continue
        for img in image_records:
            if img.get("skipped") or not img.get("served_url"):
                continue
            oname = (img.get("offering_name") or "").strip().lower()
            if oname and (oname in name or name in oname):
                off["media_source_url"] = img.get("source_url")
                off["media_url"] = img["served_url"]
                break


def approve_crawl_run(
    *,
    tenant_id: str,
    run_id: int,
    actor: Optional[dict] = None,
) -> Dict[str, Any]:
    """Publish staged bifurcation to live tenant; copy images to product_images."""
    from database import (
        decide_crawl_staged,
        get_crawl_run,
        get_crawl_staged,
        get_db_context,
        list_crawl_images,
        list_products,
        record_admin_audit_event,
    )
    from services.crawl_images import promote_images_to_product_dir
    from services.url_scraper import UrlAutoOnboarder
    from shared.tenancy import schemas as tenancy_schemas
    from shared.tenancy import store as tenancy_store

    tenant_id = (tenant_id or "").strip().lower()
    run = get_crawl_run(run_id, tenant_id=tenant_id)
    if not run:
        raise LookupError("not_found")

    staged = get_crawl_staged(run_id, tenant_id=tenant_id)
    if not staged:
        raise LookupError("not_found")
    if staged["status"] != "staged":
        raise PermissionError(f"already_{staged['status']}")

    payload = staged.get("payload") or {}
    bifurcation = dict(payload.get("bifurcation") or {})
    images = list_crawl_images(run_id, tenant_id=tenant_id)

    # Promote staged files → served product-images; rewrite media URLs
    url_map = promote_images_to_product_dir(tenant_id, run_id, images)
    brand = bifurcation.get("brand") or {}
    for key in ("logo_url",):
        val = brand.get(key)
        if val and val in url_map:
            brand[key] = url_map[val]
        src = brand.get("logo_source_url")
        if src and src in url_map:
            brand[key] = url_map[src]
    bifurcation["brand"] = brand

    for off in bifurcation.get("offerings") or []:
        media = off.get("media_url")
        if media and media in url_map:
            off["media_url"] = url_map[media]
        src = off.get("media_source_url")
        if src and src in url_map:
            off["media_url"] = url_map[src]

    vertical = bifurcation.get("vertical", "generic")
    if vertical not in tenancy_schemas.VERTICALS:
        vertical = "generic"

    extracted_name = (brand.get("name") or tenant_id).strip()
    tenancy_store.ensure_tenant(
        tenant_id=tenant_id,
        slug=tenant_id,
        vertical=vertical,
        display_name=extracted_name,
        status="active",
    )

    actor = actor or {
        "username": "crawl_approve",
        "role": "system",
        "tenant_id": tenant_id,
    }
    onboarder = UrlAutoOnboarder(tenant_id=tenant_id, actor=actor)

    raw_files = [
        {"filename": p.get("url"), "text": ""}
        for p in (payload.get("crawl") or {}).get("pages") or []
    ]

    try:
        from services.auto_bifurcator import AutoBifurcator
        bifurcator = AutoBifurcator(tenant_id=tenant_id, actor=actor)
        summary = bifurcator.apply_bifurcation(
            bifurcation=bifurcation,
            raw_files=raw_files,
            auto_publish=True,
            source_file=f"crawl_run_{run_id}.md",
        )
    except Exception as exc:
        logger.error("Approve AutoBifurcator failed: %s — falling back", exc, exc_info=True)
        summary = onboarder._direct_commit(tenant_id, bifurcation, vertical, True)

    onboarder._sync_products_with_images(tenant_id, bifurcation.get("offerings") or [])
    faqs_created = onboarder._ingest_faqs_dataset(tenant_id, bifurcation.get("faqs") or [])
    summary["faqs_created"] = faqs_created

    try:
        from faq.service import faq_index
        faq_index.build(force=True)
    except Exception as faq_err:
        logger.warning("Could not rebuild FAQ index after approve: %s", faq_err)

    decide_crawl_staged(
        run_id, tenant_id, "published", decided_by=_actor_name(actor)
    )

    try:
        with get_db_context() as conn:
            record_admin_audit_event(
                conn,
                action="crawl_run_approved",
                actor=actor,
                resource_type="crawl_run",
                resource_id=str(run_id),
                tenant_id=tenant_id,
                details={
                    "offerings_created": summary.get("offerings_created"),
                    "faqs_created": faqs_created,
                    "images_promoted": len(url_map),
                },
            )
    except Exception:
        logger.exception("AUDIT_EVENT_RECORD_FAILED | action=crawl_run_approved run=%s", run_id)

    products = list_products(tenant_id=tenant_id)
    summary.update({
        "run_id": run_id,
        "tenant_id": tenant_id,
        "status": "published",
        "products_live": len(products),
        "images_promoted": len(url_map),
    })
    return summary


def reject_crawl_run(
    *,
    tenant_id: str,
    run_id: int,
    actor: Optional[dict] = None,
) -> Dict[str, Any]:
    """Reject staged crawl: delete staged image files; touch nothing live."""
    from database import (
        decide_crawl_staged,
        get_crawl_run,
        get_crawl_staged,
        get_db_context,
        record_admin_audit_event,
    )
    from services.crawl_images import delete_staged_image_files

    tenant_id = (tenant_id or "").strip().lower()
    run = get_crawl_run(run_id, tenant_id=tenant_id)
    if not run:
        raise LookupError("not_found")

    staged = get_crawl_staged(run_id, tenant_id=tenant_id)
    if not staged:
        raise LookupError("not_found")
    if staged["status"] != "staged":
        raise PermissionError(f"already_{staged['status']}")

    deleted = delete_staged_image_files(tenant_id, run_id)
    decide_crawl_staged(
        run_id, tenant_id, "rejected",
        decided_by=_actor_name(actor),
    )

    try:
        with get_db_context() as conn:
            record_admin_audit_event(
                conn,
                action="crawl_run_rejected",
                actor=actor or {"username": "crawl_reject", "role": "system"},
                resource_type="crawl_run",
                resource_id=str(run_id),
                tenant_id=tenant_id,
                details={"images_deleted": deleted},
            )
    except Exception:
        logger.exception("AUDIT_EVENT_RECORD_FAILED | action=crawl_run_rejected run=%s", run_id)

    return {
        "run_id": run_id,
        "tenant_id": tenant_id,
        "status": "rejected",
        "images_deleted": deleted,
    }


def enqueue_scheduled_crawl(schedule: dict) -> Optional[int]:
    """Create a queued run for a due schedule and execute the gated pipeline.

    Returns run_id, or None if a crawl is already running for the tenant.
    """
    from database import (
        create_crawl_run,
        get_running_crawl,
        mark_crawl_started,
    )

    tenant_id = schedule["tenant_id"]
    if get_running_crawl(tenant_id):
        logger.info("SCHEDULE_CRAWL_SKIP_RUNNING | tenant=%s", tenant_id)
        return None

    mark_crawl_started(tenant_id)
    run = create_crawl_run(
        tenant_id=tenant_id,
        source_url=schedule["url"],
        trigger="schedule",
        created_by="crawl_scheduler",
    )
    run_crawl_pipeline(
        tenant_id=tenant_id,
        url=schedule["url"],
        run_id=run["id"],
        trigger="schedule",
        max_pages=int(schedule.get("max_pages") or 50),
        actor={
            "username": "crawl_scheduler",
            "role": "system",
            "tenant_id": tenant_id,
        },
    )
    return run["id"]
