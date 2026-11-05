"""B2B Finance flow: Payment Confirmation + Invoice Request.

These are stub flows for v1 (no real gateway / no PDF generation). They:

- Payment Confirmation: capture amount + UTR/txn id from the distributor and
  record a row in payment_confirmations so finance can reconcile later.
- Invoice Request: validate the order belongs to the distributor, insert a
  row into invoice_requests, and acknowledge. Real PDF generation will be
  triggered from the admin panel in a follow-up phase.

States handled:
    DIST_AWAITING_PAYMENT_PROOF       -> collecting amount / UTR
    DIST_AWAITING_INVOICE_ORDER_ID    -> collecting order number
"""

import logging

logger = logging.getLogger("b2b_finance_flow")

PAYMENT_STEP_AMOUNT = "amount"
PAYMENT_STEP_UTR = "utr"


def _sender():
    from services.whatsapp_sender import send_text_message, send_button_message
    return send_text_message, send_button_message


def _tools():
    from . import tools
    return tools


def _cfg():
    from . import config
    return config


def _parse_amount(text: str) -> float:
    """Best-effort parse a rupee amount from a free-text message."""
    if not text:
        return 0.0
    cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".,")
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return 0.0


async def handle_finance_message(
    wa_id: str,
    message: str,
    state: str,
    context: dict,
    tier: str,
) -> dict:
    """Route a message through the payment/invoice FSM."""
    send_text_message, send_button_message = _sender()
    tools = _tools()
    cfg = _cfg()

    message = (message or "").strip()
    context = context or {}

    # -- Payment confirmation: two-step capture ------------------------------
    if state == cfg.B2B_AWAITING_PAYMENT_PROOF:
        step = context.get("step") or PAYMENT_STEP_AMOUNT

        if step == PAYMENT_STEP_AMOUNT:
            amount = _parse_amount(message)
            if amount <= 0:
                await send_text_message(
                    wa_id,
                    "Please reply with the payment amount in rupees (e.g. 12500).",
                )
                return {"new_state": state, "context": context}

            context["amount"] = amount
            context["step"] = PAYMENT_STEP_UTR
            await send_text_message(
                wa_id,
                f"Got it: Rs {amount:.2f}. Now please send your UTR / transaction id.",
            )
            return {"new_state": state, "context": context}

        # step == UTR
        utr = (message or "").strip()
        if len(utr) < 3:
            await send_text_message(
                wa_id,
                "That doesn't look like a valid UTR / transaction id. Please try again.",
            )
            return {"new_state": state, "context": context}

        amount = float(context.get("amount") or 0)
        new_id = tools.record_payment_confirmation(
            wa_id=wa_id,
            amount=amount,
            utr_txn_id=utr,
        )

        # Clear transient context so a re-entry starts fresh.
        context.pop("amount", None)
        context.pop("step", None)

        if new_id > 0:
            ref = f"PAY-{new_id:05d}"
            await send_text_message(
                wa_id,
                f"Payment of Rs {amount:.2f} recorded (UTR {utr}). "
                f"Your reference is {ref}. Our finance team will confirm shortly.",
            )
        else:
            await send_text_message(
                wa_id,
                "Sorry, I couldn't save the payment record. Please try again or contact your sales rep.",
            )
        return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}

    # -- Invoice request: capture order number -------------------------------
    if state == cfg.B2B_AWAITING_INVOICE_ORDER_ID:
        order_number = (message or "").strip()
        if not order_number:
            await send_text_message(
                wa_id,
                "Please reply with your Order ID (e.g. ORD-12345).",
            )
            return {"new_state": state, "context": context}

        new_id = tools.record_invoice_request(wa_id=wa_id, order_number=order_number)
        if new_id > 0:
            ref = f"INV-{new_id:05d}"
            await send_text_message(
                wa_id,
                f"Invoice request received for order {order_number}. "
                f"Your reference is {ref}. It will be issued by our team shortly.",
            )
        elif new_id == -1:
            await send_text_message(
                wa_id,
                f"Order {order_number} doesn't appear to belong to your account. "
                "Please double-check the order number.",
            )
        else:
            await send_text_message(
                wa_id,
                f"Sorry, I couldn't find order {order_number}. Please verify and try again.",
            )
        return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}

    # -- Unknown finance state: fall back to main menu -----------------------
    from . import menus
    from services.whatsapp_sender import send_list_menu

    await send_list_menu(wa_id, **menus.get_dist_main_menu())
    return {"new_state": cfg.B2B_MAIN_MENU_STATE, "context": context}