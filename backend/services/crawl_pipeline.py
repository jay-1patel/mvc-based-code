"""Crawl v2 pipeline — crawl → images → AI bifurcation → staging only.

Approve replays staged bifurcation through legacy AutoBifurcator commit.
Reject deletes staged image files and touches nothing live.
NO auto_publish anywhere in this module.
"""
from __future__ import annotations

import copy
import logging
import os
from typing import Any, Dict, List, Optional

from services.bifurcation_guards import placeholder_warnings

logger = logging.getLogger("crawl_pipeline")


def _bifurcation_timeout_seconds() -> float:
    try:
        return float(os.environ.get("CRAWL_BIFURCATION_TIMEOUT_SECONDS", "180"))
    except (TypeError, ValueError):
        return 180.0


def _extract_bifurcation_with_deadline(onboarder, crawl_res_for_ai, run_id: int):
    """Run LLM bifurcation under an overall deadline (D9).

    On timeout the LLM result is discarded and the heuristic extraction is
    used instead, marked ``bifurcation_source = "heuristic_timeout"``.
    """
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    timeout = _bifurcation_timeout_seconds()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="crawl-bifurcation")
    future = executor.submit(onboarder.extract_bifurcation, crawl_res_for_ai)
    try:
        result = future.result(timeout=timeout)
        executor.shutdown(wait=False)
        return result
    except FuturesTimeoutError:
        logger.warning("CRAWL_BIFURCATION_TIMEOUT | run=%s", run_id)
        executor.shutdown(wait=False)
        result = onboarder.extract_bifurcation(crawl_res_for_ai, allow_llm=False)
        result["bifurcation_source"] = "heuristic_timeout"
        return result


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
    max_pages: Optional[int] = None,
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
    from services.crawl_fulltext import build_page_knowledge
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

        # ── Full-text knowledge layer (the extraction fix) ──
        # Real page body text → KB chunks, before the HTML is stripped.
        # Junk pages (privacy/terms/login/search results), too-short
        # pages and duplicate URL spellings are excluded here and from
        # every later stage; each exclusion is recorded with its reason.
        kb = build_page_knowledge(pages)
        # Junk / duplicate-spelling pages are excluded from every stage.
        # Merely short pages stage no chunks but still feed the AI layer
        # and image capture (a contact page is short, not junk).
        excluded_urls = set(kb["excluded_urls"])
        kept_pages = [p for p in pages if (p.get("url") or "") not in excluded_urls]

        # Download managed assets (staging dir under UPLOAD_DIR) — kept
        # pages only, so junk pages never contribute "knowledge" images.
        image_records = download_and_store_crawl_images(
            tenant_id=tenant_id,
            run_id=run_id,
            pages=kept_pages,
        )

        # Knowledge images: stored, de-duplicated content images (logos
        # stay brand assets, handled by _apply_image_links), each linked
        # to the page it was captured from.
        knowledge_images = []
        for rec in image_records:
            if rec.get("skipped") or rec.get("is_duplicate"):
                continue
            if (rec.get("kind") or "") == "logo":
                continue
            knowledge_images.append({
                "alt_text": rec.get("alt_text") or "",
                "image_url": rec.get("served_url") or rec.get("source_url") or "",
                "source_url": rec.get("source_url") or "",
                "source_page_url": rec.get("source_page_url") or "",
                "section": rec.get("section") or "",
                "kind": rec.get("kind") or "image",
            })

        # Strip heavy HTML before bifurcation / staging payload
        slim_pages = []
        for p in kept_pages:
            slim = dict(p)
            slim.pop("html", None)
            slim_pages.append(slim)
        crawl_res_for_ai = dict(crawl_res)
        crawl_res_for_ai["pages"] = slim_pages

        onboarder = UrlAutoOnboarder(tenant_id=tenant_id, actor=actor)
        bifurcation = _extract_bifurcation_with_deadline(onboarder, crawl_res_for_ai, run_id)

        # Link stored image URLs into bifurcation media fields where possible
        _apply_image_links(bifurcation, image_records)

        payload = {
            "bifurcation": bifurcation,
            # E2E D-E2E-7: stage-time flags for schema-placeholder content
            # in the staged extraction; approve rejects these items.
            "placeholder_warnings": placeholder_warnings(bifurcation),
            "bifurcation_source": bifurcation.get("bifurcation_source", "heuristic"),
            "crawl": {
                "root_url": crawl_res.get("root_url"),
                "domain": crawl_res.get("domain"),
                "max_pages": max_pages,  # None = unlimited (no page-count cap)
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
                        "rendered": bool(p.get("rendered")),
                    }
                    for p in slim_pages
                ],
                "pages_rendered": int(crawl_res.get("pages_rendered") or 0),
                "render_unavailable": bool(crawl_res.get("render_unavailable")),
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
            # Grounded full-text layer: real page text chunks + the
            # images captured from those pages, each with its source URL.
            "page_chunks": [
                {
                    "title": c["title"],
                    "category": c["category"],
                    "content": c["content"],
                    "url": c["url"],
                }
                for c in kb["chunks"]
            ],
            "knowledge_images": knowledge_images,
            "yield": {
                "pages_discovered": pages_found,
                "pages_fetched": pages_scraped,
                "pages_staged": len(kb["kept_urls"]),
                "pages_skipped": kb["skipped"],
                "pages_rendered": int(crawl_res.get("pages_rendered") or 0),
                "render_unavailable": bool(crawl_res.get("render_unavailable")),
                "pages_supplemented": len(kb.get("supplements") or {}),
                "supplement_words": sum((kb.get("supplements") or {}).values()),
                "kb_chunks": len(kb["chunks"]),
                "kb_articles": len((bifurcation or {}).get("knowledge_articles") or []),
                "faqs": len((bifurcation or {}).get("faqs") or []),
                "products": len((bifurcation or {}).get("offerings") or []),
                "images_captured": len(knowledge_images),
                "images_deduped": sum(1 for i in image_records if i.get("is_duplicate")),
                "images_skipped": sum(1 for i in image_records if i.get("skipped")),
                "extractors": {
                    name: sum(1 for v in kb["extractors"].values() if v == name)
                    for name in ("trafilatura", "fallback")
                },
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
                        "pages_skipped": len(kb["skipped"]),
                        "kb_chunks": len(kb["chunks"]),
                        "offerings": len((bifurcation or {}).get("offerings") or []),
                        "faqs": len((bifurcation or {}).get("faqs") or []),
                        "images_stored": payload["images_summary"]["stored"],
                        "images_captured": len(knowledge_images),
                        "bifurcation_source": payload["bifurcation_source"],
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
            "pages_skipped": len(kb["skipped"]),
            "pages_rendered": int(crawl_res.get("pages_rendered") or 0),
            "render_unavailable": bool(crawl_res.get("render_unavailable")),
            "kb_chunks": len(kb["chunks"]),
            "offerings": len((bifurcation or {}).get("offerings") or []),
            "faqs": len((bifurcation or {}).get("faqs") or []),
            "images": payload["images_summary"],
            "yield": payload["yield"],
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

    # ── D8: snapshot the tenant's live vertical + profile website ──
    # Approving a crawl must NEVER change either field. The staged guesses
    # are dropped below and recorded in skipped_fields.
    tenant_row = tenancy_store.get_tenant(tenant_id) or {}
    staged_vertical_raw = bifurcation.get("vertical")
    current_vertical = (tenant_row.get("vertical") or "").strip()
    if current_vertical not in tenancy_schemas.VERTICALS:
        # No live vertical on record — fall back to the staged value,
        # normalised exactly as the old code did.
        current_vertical = (
            staged_vertical_raw
            if staged_vertical_raw in tenancy_schemas.VERTICALS
            else "generic"
        )
    try:
        from shared.tenancy.loader import get_tenant_profile
        current_website = (get_tenant_profile(tenant_id).brand.website or "").strip()
    except Exception:
        logger.warning("D8_WEBSITE_SNAPSHOT_FAILED | tenant=%s", tenant_id, exc_info=True)
        current_website = ""

    # ── Rule 1: menu / brand / business hours / intents come ONLY from ──
    # the tenant's own PDF/kit — a website crawl publishes knowledge
    # (KB, FAQs, product knowledge) and must never rewrite them. Snapshot
    # the live profile sections now; the publish below is fed a
    # knowledge-only payload, and any drift is restored afterwards
    # (same belt-and-braces pattern as the D8 lock above).
    RULE1_LOCKED_KEYS = ("menu", "brand", "business_hours", "intents")
    profile_lock: Dict[str, Any] = {}
    try:
        live_payload_before = tenancy_store.get_current_payload(tenant_id)
        if isinstance(live_payload_before, dict):
            for key in RULE1_LOCKED_KEYS:
                if key in live_payload_before:
                    profile_lock[key] = copy.deepcopy(live_payload_before[key])
    except Exception:
        logger.warning("RULE1_SNAPSHOT_FAILED | tenant=%s", tenant_id, exc_info=True)
        profile_lock = {}

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

    # ── D8: force the live vertical + website over the staged guesses ──
    staged_website = (brand.get("website") or "").strip()
    skipped_fields: Dict[str, Any] = {}
    if staged_vertical_raw and staged_vertical_raw != current_vertical:
        skipped_fields["vertical"] = staged_vertical_raw
    if staged_website and staged_website != current_website:
        skipped_fields["website"] = staged_website
    if skipped_fields:
        logger.info(
            "CRAWL_APPROVE_SKIPPED_FIELDS | tenant=%s run=%s | %s",
            tenant_id, run_id, skipped_fields,
        )

    vertical = current_vertical
    brand["website"] = current_website
    bifurcation["brand"] = brand
    bifurcation["vertical"] = vertical

    extracted_name = (brand.get("name") or tenant_id).strip()
    # Rule 1: an existing tenant keeps its own display name — the website's
    # brand guess must not rename it (ensure_tenant overwrites when given
    # a non-empty name). Only a brand-new tenant row takes the staged name.
    existing_display_name = (tenant_row.get("display_name") or "").strip()
    tenancy_store.ensure_tenant(
        tenant_id=tenant_id,
        slug=tenant_id,
        vertical=vertical,
        display_name=existing_display_name or extracted_name,
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

    # ── Rule 1: publish KNOWLEDGE ONLY ──
    # The staged bifurcation also carries website guesses for brand, menu,
    # business hours and intents. None of those may reach the publisher:
    # apply_bifurcation is fed an allow-listed payload (vertical +
    # offerings + FAQs + KB articles), so the PDF-owned profile sections
    # are never even candidates for a write. The snapshot/restore below
    # covers anything the publisher derives on its own (e.g. the
    # offering-linked intents it synthesises from the catalog).
    # The grounded full-text chunks publish as KB articles alongside the
    # LLM summary articles — same knowledge-only channel, real page text.
    page_chunks = payload.get("page_chunks") or []
    chunk_articles = [
        {
            "title": c.get("title") or "",
            "category": c.get("category") or "Website page",
            "markdown_content": c.get("content") or "",
            "source_url": c.get("url") or "",
        }
        for c in page_chunks
        if isinstance(c, dict) and (c.get("content") or "").strip()
    ]
    knowledge_bifurcation: Dict[str, Any] = {
        "vertical": vertical,
        "offerings": bifurcation.get("offerings") or [],
        "faqs": bifurcation.get("faqs") or [],
        "knowledge_articles": (bifurcation.get("knowledge_articles") or []) + chunk_articles,
    }

    apply_failed = False
    try:
        from services.auto_bifurcator import AutoBifurcator
        bifurcator = AutoBifurcator(tenant_id=tenant_id, actor=actor)
        summary = bifurcator.apply_bifurcation(
            bifurcation=knowledge_bifurcation,
            raw_files=raw_files,
            auto_publish=True,
            source_file=f"crawl_run_{run_id}.md",
        )
    except Exception as exc:
        apply_failed = True
        logger.error("Approve AutoBifurcator failed: %s — falling back", exc, exc_info=True)
        summary = onboarder._direct_commit(tenant_id, knowledge_bifurcation, vertical, True)

    onboarder._sync_products_with_images(tenant_id, knowledge_bifurcation.get("offerings") or [])
    # E2E D-E2E-6 (known D-08): apply_bifurcation is the ONE canonical FAQ
    # writer on this path (source crawl_run_<id>.md, with publish-time
    # placeholder rejection + duplicate guard). The legacy
    # _ingest_faqs_dataset call re-wrote every FAQ under a second
    # website_faq_*.md source — that double-write is removed. The legacy
    # writer survives ONLY as the fallback for when apply_bifurcation
    # itself raised (and _direct_commit ran), where no canonical FAQ
    # write happened at all.
    if apply_failed:
        summary["faqs_created"] = onboarder._ingest_faqs_dataset(
            tenant_id, knowledge_bifurcation.get("faqs") or []
        )
    else:
        summary["faqs_created"] = int(summary.get("faqs_saved") or 0)

    try:
        from faq.service import faq_index
        faq_index.build(force=True)
    except Exception as faq_err:
        logger.warning("Could not rebuild FAQ index after approve: %s", faq_err)

    # ── D8 belt-and-braces: verify live vertical + website survived ──
    # publishing (apply_bifurcation can fabricate a website default);
    # restore the snapshot if either drifted.
    try:
        after_row = tenancy_store.get_tenant(tenant_id) or {}
        if (after_row.get("vertical") or "") != current_vertical:
            logger.warning(
                "D8_VERTICAL_RESTORE | tenant=%s | %s -> %s",
                tenant_id, after_row.get("vertical"), current_vertical,
            )
            tenancy_store.ensure_tenant(
                tenant_id=tenant_id,
                slug=tenant_id,
                vertical=current_vertical,
                display_name=(after_row.get("display_name") or ""),
                status=(after_row.get("status") or "active"),
            )
    except Exception:
        logger.warning("D8_VERTICAL_VERIFY_FAILED | tenant=%s", tenant_id, exc_info=True)
    try:
        from shared.tenancy.loader import get_tenant_profile as _get_profile
        after_website = (_get_profile(tenant_id).brand.website or "").strip()
        if after_website != current_website:
            logger.warning(
                "D8_WEBSITE_RESTORE | tenant=%s | %s -> %s",
                tenant_id, after_website, current_website,
            )
            current_payload = tenancy_store.get_current_payload(tenant_id)
            if current_payload is not None:
                p_brand = dict(current_payload.get("brand") or {})
                p_brand["website"] = current_website
                current_payload["brand"] = p_brand
                tenancy_store.save_draft(
                    tenant_id, current_payload, updated_by=_actor_name(actor)
                )
                tenancy_store.publish_version(
                    tenant_id, current_payload, published_by=_actor_name(actor)
                )
                tenancy_store.invalidate(tenant_id)
    except Exception:
        logger.warning("D8_WEBSITE_VERIFY_FAILED | tenant=%s", tenant_id, exc_info=True)

    # ── Rule 1 belt-and-braces: verify the PDF-owned profile sections ──
    # survived publishing byte-identical; restore any that drifted.
    profile_sections_restored: List[str] = []
    if profile_lock:
        try:
            after_payload = tenancy_store.get_current_payload(tenant_id)
            if isinstance(after_payload, dict):
                drifted = [
                    key for key, locked in profile_lock.items()
                    if after_payload.get(key) != locked
                ]
                if drifted:
                    logger.warning(
                        "RULE1_PROFILE_RESTORE | tenant=%s run=%s | %s",
                        tenant_id, run_id, drifted,
                    )
                    for key in drifted:
                        after_payload[key] = copy.deepcopy(profile_lock[key])
                    tenancy_store.save_draft(
                        tenant_id, after_payload, updated_by=_actor_name(actor)
                    )
                    tenancy_store.publish_version(
                        tenant_id, after_payload, published_by=_actor_name(actor)
                    )
                    tenancy_store.invalidate(tenant_id)
                    profile_sections_restored = drifted
        except Exception:
            logger.warning("RULE1_VERIFY_FAILED | tenant=%s", tenant_id, exc_info=True)

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
                    "kb_chunks_published": len(chunk_articles),
                    "images_promoted": len(url_map),
                    "skipped_fields": skipped_fields,
                    "rule1_locked_sections": sorted(profile_lock.keys()),
                    "profile_sections_restored": profile_sections_restored,
                },
            )
    except Exception:
        logger.exception("AUDIT_EVENT_RECORD_FAILED | action=crawl_run_approved run=%s", run_id)

    # Knowledge images: the promoted (served) files, linked to the page
    # each was captured from. NOTE the honest limit: KB chunk rows
    # published above carry text only — the publisher's article path has
    # no per-article media field — so images persist with the crawl
    # publish data (here + tenant_crawl_images), linked by source page
    # URL, rather than inside the KB rows themselves.
    knowledge_images_linked = []
    for img in payload.get("knowledge_images") or []:
        if not isinstance(img, dict):
            continue
        promoted = (
            url_map.get(img.get("source_url") or "")
            or url_map.get(img.get("image_url") or "")
            or img.get("image_url")
            or ""
        )
        knowledge_images_linked.append({
            "alt_text": img.get("alt_text") or "",
            "image_url": promoted,
            "source_page_url": img.get("source_page_url") or "",
            "section": img.get("section") or "",
            "kind": img.get("kind") or "image",
        })

    products = list_products(tenant_id=tenant_id)
    summary.update({
        "run_id": run_id,
        "tenant_id": tenant_id,
        "status": "published",
        "products_live": len(products),
        "images_promoted": len(url_map),
        "kb_chunks_published": len(chunk_articles),
        "knowledge_images": knowledge_images_linked,
        "skipped_fields": skipped_fields,
        "rule1_locked_sections": sorted(profile_lock.keys()),
        "profile_sections_restored": profile_sections_restored,
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
        # Stored schedule max_pages is deliberately IGNORED: the page-count
        # limit was removed (2026-11-11), so legacy schedules (which carry
        # the old default 50) also run unlimited instead of staying capped.
        max_pages=None,
        actor={
            "username": "crawl_scheduler",
            "role": "system",
            "tenant_id": tenant_id,
        },
    )
    return run["id"]
