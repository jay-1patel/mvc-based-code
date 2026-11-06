"""Background jobs and the application lifespan.

Moved out of the entry point: the lifespan (startup/shutdown sequence)
and the two periodic monitors it starts (cart/checkout cleanup and the
scheduled website re-crawl). Bodies are unchanged from the entry point.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

try:  # backend/ on sys.path (the normal import mode)
    from database import init_db
    from services.handoff_timeout import handoff_timeout_monitor
except ImportError:  # imported as part of the `backend` package
    from backend.database import init_db
    from backend.services.handoff_timeout import handoff_timeout_monitor

logger = logging.getLogger("router")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[STARTUP] lifespan entered")
    from shared.tenancy.paths import validate_backend_clients_dir
    _tenant_data_dir = validate_backend_clients_dir()
    logger.info("[STARTUP] tenant data dir OK: %s", _tenant_data_dir)
    init_db()
    logger.info("[STARTUP] init_db done")
    try:
        from routing.config import DB_PATH as _routing_db
        from database import DB_PATH as _app_db
        logger.info(
            "[STARTUP] DB paths app=%s routing=%s same=%s "
            "(run ONE server process against this file — dual processes double WhatsApp replies)",
            _app_db, _routing_db, os.path.abspath(str(_app_db or "")) == os.path.abspath(str(_routing_db or "")),
        )
    except Exception as _db_path_exc:
        logger.warning("[STARTUP] could not compare DB paths: %s", _db_path_exc)
    logger.info(
        "[STARTUP] HANDOFF_TIMEOUT_NOTIFY=%s (customer WhatsApp on handoff auto-revert)",
        os.environ.get("HANDOFF_TIMEOUT_NOTIFY", "0"),
    )

    from faq.service import faq_index
    logger.info(f"[STARTUP] faq_index id={id(faq_index)}, is_built={faq_index.is_built}")

    try:
        faq_index.build()
        logger.info(f"[STARTUP] build() returned, is_built={faq_index.is_built}, "
                    f"ntotal={getattr(faq_index.index, 'ntotal', 'no-index')}")
        if faq_index.is_built:
            logger.info(f"FAQ index built: {faq_index.index.ntotal} vectors")
        else:
            logger.warning("FAQ index build returned but index not marked as built")
    except Exception as exc:
        logger.error(f"FAQ index init FAILED: {exc}", exc_info=True)

    try:
        from kb.services.rag import build_chunks_for_kb, rebuild_faiss_index, generate_missing_embeddings
        build_chunks_for_kb()
        generate_missing_embeddings()
        rebuild_faiss_index()
        logger.info("[STARTUP] KB index built")
    except Exception as exc:
        logger.warning(f"KB index init skipped: {exc}", exc_info=True)

    try:
        from kb.services.rag import rebuild_product_faiss_index
        rebuild_product_faiss_index()
        logger.info("[STARTUP] Product FAISS index built")
    except Exception as exc:
        logger.warning(f"Product index init skipped: {exc}", exc_info=True)

    # Start the handoff timeout monitor
    handoff_stop_event = asyncio.Event()
    handoff_task = asyncio.create_task(handoff_timeout_monitor(handoff_stop_event))
    logger.info("[STARTUP] Handoff timeout monitor started (1h auto-revert)")

    # Start the cart/checkout TTL cleanup loop (7-day carts, 15-min checkouts)
    cart_cleanup_stop_event = asyncio.Event()
    cart_cleanup_task = asyncio.create_task(cart_cleanup_monitor(cart_cleanup_stop_event))
    logger.info("[STARTUP] Cart/checkout TTL cleanup monitor started")

    # Start the scheduled website re-crawl loop (checks tenant_crawl_schedules every 30 min)
    crawl_stop_event = asyncio.Event()
    crawl_task = asyncio.create_task(crawl_scheduler_monitor(crawl_stop_event))
    logger.info("[STARTUP] Website re-crawl scheduler started (30-minute poll)")

    # Start the send2.digital inbound polling listener. This is what delivers
    # WhatsApp messages that arrive outside the push webhook; every conversation,
    # including a bare "Hi", is recorded in the customer directory via the
    # orchestrator's touch_customer (first contact creates, later contacts update).
    try:
        from services import incoming_listener as _incoming_listener
        # Honor routing/.env even if this module was imported before dotenv ran.
        _incoming_listener.SEND2_POLLER_ENABLED = _incoming_listener.read_poller_enabled()
        incoming_stop_event = asyncio.Event()
        incoming_task = asyncio.create_task(
            _incoming_listener.poll_incoming_messages(incoming_stop_event)
        )
        if _incoming_listener.SEND2_POLLER_ENABLED:
            logger.info("[STARTUP] send2 inbound message poller started")
        else:
            logger.info("[STARTUP] send2 inbound poller disabled (SEND2_POLLER_ENABLED=0)")
    except Exception as exc:
        logger.warning(f"[STARTUP] send2 inbound poller NOT started: {exc}", exc_info=True)
        incoming_task = None
        incoming_stop_event = None

    try:
        yield
    finally:
        handoff_stop_event.set()
        handoff_task.cancel()
        try:
            await handoff_task
        except asyncio.CancelledError:
            # deliberate: expected — the task was just cancelled above.
            logger.debug("SHUTDOWN_TASK_CANCELLED | task=handoff_monitor")
        cart_cleanup_stop_event.set()
        cart_cleanup_task.cancel()
        try:
            await cart_cleanup_task
        except asyncio.CancelledError:
            # deliberate: expected — the task was just cancelled above.
            logger.debug("SHUTDOWN_TASK_CANCELLED | task=cart_cleanup")
        crawl_stop_event.set()
        crawl_task.cancel()
        try:
            await crawl_task
        except asyncio.CancelledError:
            # deliberate: expected — the task was just cancelled above.
            logger.debug("SHUTDOWN_TASK_CANCELLED | task=crawl")
        if incoming_task is not None:
            incoming_stop_event.set()
            incoming_task.cancel()
            try:
                await incoming_task
            except asyncio.CancelledError:
                # deliberate: expected — the task was just cancelled above.
                logger.debug("SHUTDOWN_TASK_CANCELLED | task=incoming_listener")
        logger.info("[SHUTDOWN] Server stopped")


async def cart_cleanup_monitor(stop_event: asyncio.Event = None) -> None:
    """Hourly cleanup of expired carts and checkout sessions."""
    while not (stop_event and stop_event.is_set()):
        try:
            from services.cart_service import cleanup_expired
            counts = cleanup_expired()
            if counts.get("carts") or counts.get("checkouts"):
                logger.info(f"CART_CLEANUP | expired carts={counts['carts']} checkouts={counts['checkouts']}")
        except Exception as exc:
            logger.error(f"CART_CLEANUP_MONITOR_FAIL: {exc}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3600)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise


async def crawl_scheduler_monitor(stop_event: asyncio.Event = None) -> None:
    """Re-crawl tenant websites whose schedule is due.

    Polls tenant_crawl_schedules every 30 minutes. Each due schedule is
    stamped 'running' before its crawl starts, so a long crawl is not picked
    up twice, and the crawl itself runs in a worker thread because every
    step in execute_onboarding is blocking (httpx, LLM calls, image writes).
    """
    def _run_crawl(schedule: dict) -> Optional[int]:
        # Gated pipeline: stages results for approval — never auto-publishes.
        from services.crawl_pipeline import enqueue_scheduled_crawl
        return enqueue_scheduled_crawl(schedule)

    while not (stop_event and stop_event.is_set()):
        try:
            from database import list_due_crawl_schedules, mark_crawl_finished
            due = await asyncio.to_thread(list_due_crawl_schedules)
            for schedule in due:
                tenant_id = schedule["tenant_id"]
                try:
                    run_id = await asyncio.to_thread(_run_crawl, schedule)
                    logger.info(
                        "CRAWL_SCHEDULE_DONE | tenant=%s | run_id=%s",
                        tenant_id,
                        run_id,
                    )
                except Exception as exc:
                    logger.error("CRAWL_SCHEDULE_FAIL | tenant=%s | %s", tenant_id, exc, exc_info=True)
                    mark_crawl_finished(tenant_id, "failed", str(exc)[:500])
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"CRAWL_SCHEDULER_MONITOR_FAIL: {exc}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1800)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
