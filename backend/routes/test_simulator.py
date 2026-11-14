"""
Local WhatsApp Tester simulator.

Provides a ``POST /api/test-simulator`` endpoint used by the Admin Panel's
"WhatsApp Tester" page (src/components/tester/whatsapp-tester.tsx).

This endpoint drives the SAME message pipeline as a real WhatsApp connection:
it calls ``services.orchestrator.process_incoming_message`` which routes the
message to the correct B2B / B2C persona workflow, runs the state machine, and
persists the FSM state to ``user_states``.

The only difference from production is that outgoing messages are captured by
``services.whatsapp_sender`` (capture mode) instead of being sent over the
send2.digital API — so the tester can render exactly what a real WhatsApp
connection would send.

Request:
    {"wa_id": "test-user-001", "message": "Hello", "user_type": "b2c"}

    user_type is optional and may be "b2b", "b2c", or "auto" (default auto:
    routes as a distributor if the wa_id exists in the distributors table).

Response:
    {
        "messages": [
            {"type": "text", "text": "..."},
            {"type": "buttons", "text": "...", "buttons": [{"id","title"}]},
            {"type": "list", "header": "...", "body": "...",
             "button_text": "...", "sections": [...]}
        ],
        "whatsapp": [
            {"kind": "list", "header": "...", "body": "...", ...}
        ],
        "new_state": "B2C_MAIN_MENU",
        "human_handover": false,
        "user_type": "b2c",
        "status": "processed"
    }

``messages`` is the raw captured payload list (debugging view). ``whatsapp``
is the same reply after the production delivery layer's shaping
(``services.whatsapp_render.render_phone_messages``): WhatsApp formatting
normalization, interactive limits, and body splits applied — the exact
sequence the customer's phone displays. The Tester page renders ``whatsapp``.
"""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from routes.auth import get_current_admin, scoped_tenant_id

logger = logging.getLogger("test_simulator")

router = APIRouter(prefix="/api/test-simulator", tags=["test-simulator"])


class SimulatorRequest(BaseModel):
    """Request body for the WhatsApp tester simulator."""
    wa_id: str = "test-user-001"
    message: str = ""
    user_type: Optional[Literal["b2b", "b2c", "auto"]] = "auto"
    tenant_id: Optional[str] = None


class MenuPreviewRequest(BaseModel):
    """Request body for rendering a menu preview."""
    wa_id: str = "test-user-001"
    user_type: Optional[Literal["b2b", "b2c"]] = "b2c"
    tier: Optional[str] = None
    outstanding: Optional[float] = None
    tenant_id: Optional[str] = None


def _ensure_user_type(wa_id: str, user_type: str) -> str:
    """Resolve 'auto' to 'b2b'/'b2c' and ensure a matching DB record exists.

    If the tester explicitly picks a persona, ensure the corresponding row
    exists (a distributor row for b2b) so routing matches a real connection.
    """
    from database import get_db_context

    with get_db_context() as conn:
        is_dist = conn.execute(
            "SELECT 1 FROM distributors WHERE wa_id=?", (wa_id,)
        ).fetchone()

    resolved = user_type
    if resolved == "auto":
        resolved = "b2b" if is_dist else "b2c"

    if resolved == "b2b" and not is_dist:
        # Simulate a real distributor registration so B2B routing works.
        with get_db_context() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO distributors (wa_id, name, outstanding_payments) "
                "VALUES (?, ?, 0)",
                (wa_id, "Test Distributor"),
            )
    return resolved


@router.post("")
async def simulate_message(
    request: SimulatorRequest,
    current_admin: dict = Depends(get_current_admin),
):
    """Process a message through the real orchestrator and return what it sends."""
    from fastapi import HTTPException
    from services.orchestrator import process_incoming_message

    scope = scoped_tenant_id(current_admin)
    if scope and request.tenant_id and request.tenant_id != scope:
        raise HTTPException(status_code=403, detail=f"Admin is scoped to tenant '{scope}'")

    wa_id = (request.wa_id or "").strip() or "test-user-001"
    message = (request.message or "").strip()
    logger.info(f"TEST_SIMULATOR | wa_id={wa_id} | tenant={request.tenant_id} | type={request.user_type} | message={message!r}")

    if request.tenant_id:
        try:
            from database import set_request_tenant, get_db_context
            from backend.services import state_manager as sm
            set_request_tenant(request.tenant_id)
            sm.set_tenant(wa_id, request.tenant_id)
            with get_db_context() as conn:
                try:
                    conn.execute("UPDATE user_states SET tenant_id=? WHERE wa_id=?", (request.tenant_id, wa_id))
                except Exception:
                    # deliberate: tenant stamp is best-effort; the outer handler logs failures
                    logger.debug("SIMULATOR_TENANT_STAMP_FAILED | wa_id=%s", wa_id, exc_info=True)
        except Exception as exc:
            logger.debug(f"Could not stamp simulator tenant: {exc}")

    resolved_type = _ensure_user_type(wa_id, request.user_type)

    # Capture-only: same pipeline as production, but do NOT hit send2.digital.
    result = await process_incoming_message(
        wa_id, message, capture_only=True, tenant_id=request.tenant_id
    )

    result["user_type"] = resolved_type
    # The rendered WhatsApp view: the captured payloads after the production
    # delivery layer's shaping (formatting, limits, splits) — i.e. the exact
    # sequence of messages the customer's phone would show. The panel's
    # replica console renders THIS; "messages" stays the raw capture for
    # debugging.
    try:
        from services.whatsapp_render import render_phone_messages

        result["whatsapp"] = render_phone_messages(result.get("messages") or [])
    except Exception:
        # deliberate: rendering is a view over the capture — if it ever
        # fails, the raw captured payloads still return so the tester works.
        logger.exception("SIMULATOR_RENDER_FAILED | wa_id=%s", wa_id)
        result["whatsapp"] = []
    return result


@router.post("/reset")
async def reset_conversation(
    request: SimulatorRequest,
    current_admin: dict = Depends(get_current_admin),
):
    """Reset a tester user's conversation to a brand-new state.

    Clears the persisted FSM state and the human-handover flag (and any
    registered distributor row created by the tester), so the next message
    behaves like the very first message of a fresh WhatsApp chat.
    """
    from database import get_db_context

    wa_id = (request.wa_id or "").strip() or "test-user-001"
    logger.info(f"TEST_SIMULATOR_RESET | wa_id={wa_id}")

    try:
        try:
            from services import flow_runner
        except ImportError:
            from backend.services import flow_runner
        flow_runner.clear_flow(wa_id)
    except Exception:
        # deliberate: reset continues even if no live flow state exists to clear
        logger.debug("SIMULATOR_FLOW_CLEAR_FAILED | wa_id=%s", wa_id, exc_info=True)

    with get_db_context() as conn:
        conn.execute("DELETE FROM user_states WHERE wa_id=?", (wa_id,))
        conn.execute("DELETE FROM distributors WHERE wa_id=?", (wa_id,))

    return {
        "wa_id": wa_id,
        "reset": True,
        "new_state": None,
        "human_handover": False,
        "status": "reset",
    }


def _ensure_distributor_for_preview(wa_id: str, tier: Optional[str], outstanding: Optional[float]):
    """Create or update a distributor row so the B2B menu preview is realistic."""
    from database import get_db_context

    name = "Test Distributor"
    tier = tier or "Bronze"
    outstanding = outstanding if outstanding is not None else 0.0

    with get_db_context() as conn:
        conn.execute(
            """INSERT INTO distributors (wa_id, name, tier, outstanding_payments)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(wa_id) DO UPDATE SET
                 name = excluded.name,
                 tier = excluded.tier,
                 outstanding_payments = excluded.outstanding_payments""",
            (wa_id, name, tier, outstanding),
        )


def _count_list_rows(menu: dict) -> int:
    """Count total rows across all sections in a list menu."""
    return sum(len(s.get("rows", [])) for s in menu.get("sections", []))


def _collect_ids(menu: dict) -> list:
    """Collect all row/button IDs from a rendered menu."""
    ids = []
    if menu.get("type") == "list":
        for section in menu.get("sections", []):
            for row in section.get("rows", []):
                ids.append(row.get("id"))
    elif menu.get("type") == "buttons":
        for btn in menu.get("buttons", []):
            # Buttons arrive nested ({"reply": {"id"}}) from the workflows
            # or flat ({"id", "title"}) from the menu Smart Switch.
            reply = btn.get("reply") or {}
            ids.append(reply.get("id") or btn.get("id"))
    return ids


@router.post("/menu-preview")
async def preview_menu(
    request: MenuPreviewRequest,
    current_admin: dict = Depends(get_current_admin),
):
    """Render the unified main menu for a mock B2C or B2B user.

    Useful for verifying menu layout, IDs, and WhatsApp limits without running
    the full conversation pipeline.
    """
    scope = scoped_tenant_id(current_admin)
    if scope and request.tenant_id and request.tenant_id != scope:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail=f"Admin is scoped to tenant '{scope}'")

    wa_id = (request.wa_id or "").strip() or "test-user-001"
    user_type = request.user_type or "b2c"

    if request.tenant_id:
        try:
            from database import set_request_tenant

            set_request_tenant(request.tenant_id)
        except Exception:
            # deliberate: tenant stamp is best-effort for a read-only preview
            logger.debug("MENU_PREVIEW_TENANT_STAMP_FAILED | wa_id=%s", wa_id, exc_info=True)

    # Build the menu with the SAME builders the live pipeline uses, so the
    # preview can never drift from what a customer receives: the B2C Smart
    # Switch menu (quick-reply buttons <= 3 options, list menu > 3) and the
    # distributor main menu. (The previous implementation imported
    # get_b2c_main_menu / get_b2b_main_menu from services.menu_catalog —
    # functions that do not exist there — so this endpoint always 500'd.)
    if user_type == "b2b":
        _ensure_distributor_for_preview(wa_id, request.tier, request.outstanding)
        from bots.distributor_b2b import menus as b2b_menus

        menu = {"type": "list", **b2b_menus.get_dist_main_menu(wa_id)}
    else:
        from bots.customer_b2c import menus as b2c_menus

        menu = b2c_menus.get_main_menu(wa_id, tenant_id=request.tenant_id)

    # Validation / assertions
    section_count = len(menu.get("sections", []))
    if menu.get("type") == "buttons":
        row_count = len(menu.get("buttons", []))
    else:
        row_count = _count_list_rows(menu)
    ids = _collect_ids(menu)

    issues = []
    if section_count > 3:
        issues.append(f"Too many sections: {section_count}")
    if row_count > 10:
        issues.append(f"Too many rows: {row_count}")
    if menu.get("type") == "buttons" and row_count > 3:
        issues.append(f"Too many buttons: {row_count}")

    # The rendered WhatsApp view of this menu (delivery shaping applied),
    # same as the simulate endpoint returns for a live conversation turn.
    whatsapp = []
    try:
        from services.whatsapp_render import render_phone_messages

        whatsapp = render_phone_messages([menu])
    except Exception:
        # deliberate: preview still returns the menu if rendering fails
        logger.exception("MENU_PREVIEW_RENDER_FAILED | wa_id=%s", wa_id)

    return {
        "wa_id": wa_id,
        "user_type": user_type,
        "menu": menu,
        "whatsapp": whatsapp,
        "meta": {
            "sections": section_count,
            "rows": row_count,
            "ids": ids,
            "issues": issues,
        },
    }
