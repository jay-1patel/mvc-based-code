"""B2B Catalog / Price-list / Schemes flow (Distributor Chatbot spec).

States handled:
    DIST_VIEW_CATALOG       -> send the catalogue PDF (interactive document)
    DIST_VIEW_PRICE_LIST    -> render tier-priced price list text
    DIST_VIEW_SCHEMES       -> render active schemes/offers text

All flows return to the main menu on completion.
"""

import logging

logger = logging.getLogger("b2b_catalog_flow")


def _sender():
    from services.whatsapp_sender import send_text_message
    return send_text_message


def _tools():
    from . import tools
    return tools


def _cfg():
    from . import config
    return config


def _format_price_list(rows: list, tier: str) -> str:
    """Format the price list applying the distributor's tier discount."""
    from .config import DIST_TIER_DISCOUNTS

    if not rows:
        return "The price list is currently empty. Please check back later."

    tier = (tier or "Bronze").strip().title() or "Bronze"
    discount_pct = float(DIST_TIER_DISCOUNTS.get(tier, 0.0)) * 100.0

    header = f"Latest Price List ({tier} - {discount_pct:.0f}% discount applied)"
    lines = [header, "-" * len(header)]
    for r in rows:
        name = r.get("name", "-")
        unit = r.get("unit") or ""
        list_price = float(r.get("price") or 0)
        your_price = round(list_price * (1 - discount_pct / 100.0), 2)
        unit_str = f" / {unit}" if unit else ""
        lines.append(f"- {name}: Rs {list_price:.2f}{unit_str}  |  Yours: Rs {your_price:.2f}")
    if discount_pct == 0:
        lines.append("")
        lines.append("Upgrade your tier to unlock a discount on every order.")
    return "\n".join(lines)


def _format_schemes(schemes: list) -> str:
    if not schemes:
        return "No active schemes or offers right now. Watch this space!"
    lines = ["Current Schemes & Offers", "--------------------------"]
    for s in schemes:
        title = s.get("title") or f"Scheme #{s.get('id')}"
        desc = s.get("description") or ""
        valid_to = s.get("valid_to") or "open"
        lines.append(f"- {title}")
        if desc:
            for chunk in desc.splitlines():
                if chunk.strip():
                    lines.append(f"    {chunk.strip()}")
        lines.append(f"    Valid until: {valid_to}")
    return "\n".join(lines)


async def handle_catalog_message(
    wa_id: str,
    message: str,
    state: str,
    context: dict,
    tier: str,
) -> dict:
    """Route a message through the catalog/price-list/schemes mini-FSM."""
    send_text_message = _sender()
    tools = _tools()
    cfg = _cfg()

    message = (message or "").strip()

    # -- DIST_VIEW_CATALOG: send the PDF catalogue ----------------------------
    if state == cfg.B2B_VIEW_CATALOG:
        from services.whatsapp_sender import send_catalogue_message
        sent = await send_catalogue_message(wa_id)
        if not sent:
            await send_text_message(
                wa_id,
                "The catalogue could not be delivered right now. "
                "Please try again shortly.",
            )
        return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}

    # -- B2B_VIEW_PRICE_LIST: render text table ------------------------------
    if state == cfg.B2B_VIEW_PRICE_LIST:
        rows = tools.get_latest_price_list()
        await send_text_message(wa_id, _format_price_list(rows, tier))
        return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}

    # -- B2B_VIEW_SCHEMES: list active schemes --------------------------------
    if state == cfg.B2B_VIEW_SCHEMES:
        schemes = tools.get_active_schemes()
        await send_text_message(wa_id, _format_schemes(schemes))
        return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}

    # -- Unknown state: fall back to main menu --------------------------------
    from . import menus

    from services.whatsapp_sender import send_list_menu

    await send_list_menu(wa_id, **menus.get_dist_main_menu())
    return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}