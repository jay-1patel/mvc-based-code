"""B2B Support Ticket Flow (Task 4).

Implements the ticket FSM from the plan:

    DIST_TICKET_START -> DIST_TICKET_CATEGORY -> DIST_TICKET_DETAILS
        -> DIST_TICKET_CREATED

Design:
    * The category is chosen first (from a list menu).
    * The user then provides a subject and details.
    * A ticket (TICK-XXXXX) is created in the complaints table with the new
      columns (priority, subject, assigned_to, updated_at).
    * Higher tiers get a higher priority ticket automatically.

This module is pure B2B logic and is the only place that owns the ticket FSM.
"""

import logging

logger = logging.getLogger("b2b_ticket_flow")

# Category -> (subject default, priority for premium tiers)
_CATEGORY_IDS = {
    "delivery": "Delivery / Shipping",
    "billing": "Billing / Invoice",
    "product": "Product / Quality",
    "account": "Account / Access",
    "other": "Other",
}


def _sender():
    from services.whatsapp_sender import send_text_message, send_list_menu
    return send_text_message, send_list_menu


def _tools():
    from . import tools
    return tools


def _states():
    from . import config
    return config


async def handle_ticket_message(wa_id: str, message: str, state: str, context: dict, tier: str) -> dict:
    """Route a message through the ticket FSM.

    Args:
        wa_id: Distributor WhatsApp ID.
        message: Incoming text.
        state: Current FSM state (one of the DIST_TICKET_* states).
        context: Current context dict.
        tier: Distributor tier (premium tiers get priority tickets).

    Returns:
        dict: {"new_state", "context", ...} to persist.
    """
    send_text_message, send_list_menu = _sender()
    tools = _tools()
    cfg = _states()

    message = (message or "").strip()
    context = context or {}

    # -- DIST_TICKET_START: pick category -----------------------------------
    if state == cfg.DIST_TICKET_START:
        await send_text_message(wa_id, "Let's raise a support ticket. What is your issue about?")
        await send_list_menu(wa_id, **_category_menu())
        return {"new_state": cfg.DIST_TICKET_CATEGORY, "context": context}

    # -- DIST_TICKET_CATEGORY: capture category, ask subject ----------------
    if state == cfg.DIST_TICKET_CATEGORY:
        category = _resolve_category(message)
        if not category:
            await send_text_message(wa_id, "Please choose a category from the list, or type one of: delivery, billing, product, account, other.")
            return {"new_state": cfg.DIST_TICKET_CATEGORY, "context": context}
        context["ticket_category"] = category
        await send_text_message(wa_id, f"Category: {category}.\nPlease describe your issue in a short subject line.")
        return {"new_state": cfg.DIST_TICKET_DETAILS, "context": context}

    # -- DIST_TICKET_DETAILS: capture subject + details, create ticket ------
    if state == cfg.DIST_TICKET_DETAILS:
        subject = message or "Support request"
        # Ask for more detail after the subject is captured.
        # To keep the flow simple, the subject line is used as the subject and
        # a detail prompt collects the body. Here we treat the first message as
        # the subject and the next as details (two-step).
        if not context.get("ticket_subject"):
            context["ticket_subject"] = subject
            await send_text_message(wa_id, "Thanks. Now please describe the issue in detail.")
            return {"new_state": cfg.DIST_TICKET_DETAILS, "context": context}

        details = message
        ticket_id = _create_ticket(wa_id, context, tier, details)
        context.pop("ticket_category", None)
        context.pop("ticket_subject", None)
        await send_text_message(
            wa_id,
            f"Your support ticket has been created!\nTicket ID: {ticket_id}\n"
            "Our team will reach out shortly.",
        )
        return {"new_state": cfg.DIST_TICKET_CREATED, "context": context}

    # -- DIST_TICKET_CREATED: already created -> return to main menu ---------
    if state == cfg.DIST_TICKET_CREATED:
        await send_text_message(wa_id, "Your ticket has already been submitted. Anything else?")
        return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}

    # Unknown state: start fresh.
    await send_text_message(wa_id, "Let's start a new support ticket.")
    return {"new_state": cfg.DIST_TICKET_START, "context": context}


# -- Helpers ----------------------------------------------------------------

def _category_menu():
    from . import menus
    return menus.get_ticket_category_menu()


def _resolve_category(message: str) -> str:
    """Resolve the user's message to a ticket category name."""
    lowered = (message or "").strip().lower()
    # Match by id (tcat_*) or by name keyword.
    if lowered.startswith("tcat_"):
        key = lowered.split("_", 1)[1]
        return _CATEGORY_IDS.get(key)
    for key, name in _CATEGORY_IDS.items():
        if key in lowered or name.lower() in lowered:
            return name
    return None


def _create_ticket(wa_id: str, context: dict, tier: str, details: str) -> str:
    """Create the ticket with priority based on tier."""
    tools = _tools()
    from .config import DIST_PRIORITY_TIERS

    category = context.get("ticket_category") or "Other"
    subject = context.get("ticket_subject") or "Support request"
    priority = "high" if (tier or "").strip().title() in DIST_PRIORITY_TIERS else "normal"
    return tools.create_ticket(
        wa_id=wa_id,
        category=category,
        subject=subject,
        details=details or "",
        priority=priority,
        assigned_to=None,
    )
