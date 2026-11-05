"""
Unified orchestrator.

The main entry point for incoming WhatsApp messages. This module acts as a
router: based on the user type (B2B distributor vs B2C customer) it delegates
to the correct modular persona workflow and persists the resulting FSM state.

Shared infrastructure (WhatsApp sender, database context, RAG) is reused —
no duplication here.

This function captures every outgoing message via services.whatsapp_sender.
Callers can request capture-only mode when they own outbound delivery.
"""

import json
import logging
import re

from database import get_db_context
from services.whatsapp_sender import begin_capture, drain_captured, end_capture

logger = logging.getLogger("orchestrator")


async def process_incoming_message(
    wa_id: str, message: str, *, capture_only: bool = False, tenant_id: str = None
) -> dict:
    """Route a single incoming message through the correct persona workflow.

    Returns a dict describing the outcome:
        {
            "messages": [ {type: text|buttons|list, ...}, ... ],
            "new_state": str | None,
            "human_handover": bool,
            "user_type": "b2b" | "b2c" | None,
            "status": "processed" | "ignored" | "error",
            "error": str | None,
        }
    """
    # Capture every outgoing message produced while processing this message.
    capture_tokens = begin_capture(capture_only=capture_only)

    try:
        return await _process_captured_message(wa_id, message, tenant_id=tenant_id)
    finally:
        end_capture(capture_tokens)


async def _send_b2c_menu(wa_id: str, tenant_id: str = None):
    """Send active tenant's published profile menu using Smart Switch (Buttons <=3, List >3)."""
    from bots.customer_b2c import menus
    from services.whatsapp_sender import send_list_menu, send_button_message

    # Greeting / main menu clears pending offering follow-up context.
    try:
        try:
            from services.offerings_flow import clear_offerings_context
        except ImportError:
            from backend.services.offerings_flow import clear_offerings_context
        clear_offerings_context(wa_id)
    except Exception:
        logger.exception("OFFERINGS_CONTEXT_CLEAR_FAILED | wa=%s", wa_id)

    menu_payload = menus.get_main_menu(wa_id, tenant_id=tenant_id)
    if menu_payload.get("type") == "buttons":
        await send_button_message(
            wa_id,
            body=menu_payload.get("text", "Options"),
            buttons=menu_payload.get("buttons", []),
        )
    else:
        await send_list_menu(
            wa_id,
            header=menu_payload.get("header", ""),
            body=menu_payload.get("body", "What would you like to explore today?"),
            button_text=menu_payload.get("button_text", "Show Options"),
            sections=menu_payload.get("sections", []),
        )


async def _process_captured_message(wa_id: str, message: str, tenant_id: str = None) -> dict:
    # 1. Human handover must run before tenant routing. Otherwise a missing
    # tenant short-circuits with "ignored" and typing *hi* never resumes the bot.
    with get_db_context() as conn:
        user_state = conn.execute(
            "SELECT state, context_json, human_handover FROM user_states WHERE wa_id=?",
            (wa_id,),
        ).fetchone()

    if user_state and user_state[2] == 1:  # human_handover is True
        try:
            from shared.handover import HANDOVER_BOT_RESUME, wants_bot_resume
        except ImportError:  # pragma: no cover
            from handover import HANDOVER_BOT_RESUME, wants_bot_resume  # type: ignore

        if wants_bot_resume(message):
            try:
                from database import set_human_handover
                set_human_handover(wa_id, False)
            except Exception as exc:
                logger.error(f"Failed to clear handover for {wa_id}: {exc}")
            try:
                from services.whatsapp_sender import send_text_message
                await send_text_message(wa_id, HANDOVER_BOT_RESUME)
            except Exception as exc:
                logger.debug(f"Could not send handover resume ack: {exc}")
            logger.info(f"HANDOVER_RESUMED | {wa_id} | user requested bot resume")
            # Fall through to normal bot processing below.
        else:
            # Waiting for a human agent — stay silent (save happens at webhook).
            return {
                "messages": [],
                "new_state": None,
                "human_handover": True,
                "user_type": None,
                "status": "ignored",
                "error": None,
            }

    # Customer directory: whoever talks to the bot gets a record on their very
    # first message, whichever channel delivered it (Meta webhook, send2 push,
    # or the send2 poller). Keyed on (tenant_id, wa_id) so nobody is duplicated
    # and later messages only refresh the row.
    try:
        from shared.tenancy.resolver import resolve_effective_tenant_id

        # Active Test Tenant from the admin Tenants page always wins over a
        # phone-bound hint from the poller/webhook. That is what makes
        # "Activate on WhatsApp" switch the live bot immediately.
        effective_tenant = resolve_effective_tenant_id(
            wa_id,
            tenant_id,
            allow_test_override=True,
        )
    except Exception as exc:
        logger.exception("ORCH_TENANT_RESOLUTION_FAILED | wa_id=%s", wa_id)
        return {
            "messages": [],
            "new_state": None,
            "human_handover": False,
            "user_type": None,
            "status": "error",
            "error": f"Tenant resolution failed: {exc}",
        }

    if not effective_tenant:
        logger.warning("ORCH_UNROUTED | wa_id=%s | no active tenant", wa_id)
        return {
            "messages": [],
            "new_state": None,
            "human_handover": False,
            "user_type": None,
            "status": "ignored",
            "error": "No active tenant is assigned to this WhatsApp number.",
        }

    tenant_id = effective_tenant
    try:
        from database import set_request_tenant, touch_customer

        set_request_tenant(effective_tenant)
        touch_customer(effective_tenant, wa_id, "")
        try:
            from shared.tenancy.resolver import active_test_tenant_id
            _active = active_test_tenant_id()
        except Exception:
            _active = None
        if _active and _active == effective_tenant:
            logger.info(
                "ORCH_TENANT | wa_id=%s | tenant=%s | source=active_test_tenant",
                wa_id, effective_tenant,
            )
        else:
            logger.info("ORCH_TENANT | wa_id=%s | tenant=%s", wa_id, effective_tenant)
        try:
            from backend.services import state_manager as sm
            # Stamp this WhatsApp user to the activated tenant so FAQ/KB
            # never fall back to another brand mid-conversation.
            sm.set_tenant(wa_id, effective_tenant)
        except Exception:
            try:
                from services import state_manager as sm
                sm.set_tenant(wa_id, effective_tenant)
            except Exception:
                logger.exception("SET_TENANT_FALLBACK_FAILED | wa=%s tenant=%s", wa_id, effective_tenant)
    except Exception as exc:
        logger.debug(f"Could not touch customer {wa_id}: {exc}")

    # A new inbound message reopens a conversation that was previously resolved,
    # so it reappears in the live inbox instead of staying hidden forever.
    try:
        from database import reopen_conversation_on_inbound
        reopen_conversation_on_inbound(wa_id)
    except Exception as exc:
        logger.debug(f"Could not reopen conversation for {wa_id}: {exc}")

    # 1b. Check Tenant Profile Intents, Flows, and Services
    try:
        from shared.tenancy.resolver import resolve_effective_tenant_id, resolve_tenant_for_user
        try:
            from services import intent_answers, flow_runner, service_answers
        except ImportError:
            from backend.services import intent_answers, flow_runner, service_answers
        from services.whatsapp_sender import send_text_message
        from database import set_request_tenant

        tenant_id = resolve_effective_tenant_id(wa_id, tenant_id)
        set_request_tenant(tenant_id)

        # 0. Check Main Menu & Cancel triggers
        clean_btn = (message or "").strip().lower()
        clean_btn = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", clean_btn).strip()
        is_menu_cmd = (
            clean_btn in ("main menu", "menu", "show options", "options", "start", "hi", "hello", "hey", "restart", "start over")
            or message in ("main_menu", "menu_main", "menu_start")
        )
        is_cancel_cmd = clean_btn in (
            "cancel", "stop", "exit", "quit", "abort", "back", "home", "leave", "close", "nevermind", "never mind"
        )

        if is_menu_cmd or is_cancel_cmd:
            flow_runner.clear_flow(wa_id)
            with get_db_context() as conn:
                conn.execute(
                    "UPDATE user_states SET state = 'B2C_MAIN_MENU', context_json = '{}', updated_at = CURRENT_TIMESTAMP WHERE wa_id = ?",
                    (wa_id,),
                )
            if is_cancel_cmd:
                await send_text_message(
                    wa_id,
                    "Okay, I've cancelled that. Type *menu* anytime to see options, or feel free to ask any question! 🙏"
                )
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": "B2C_MAIN_MENU",
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }
            else:
                await _send_b2c_menu(wa_id, tenant_id=tenant_id)
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": "B2C_MAIN_MENU",
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }

        # If user clicked a menu or action button, clear any stale flow & state first
        if message and (message.startswith(("menu_", "b2c_", "flow:", "flow_")) or clean_btn.startswith("menu_")):
            flow_runner.clear_flow(wa_id)
            with get_db_context() as conn:
                conn.execute(
                    "UPDATE user_states SET state = 'B2C_MAIN_MENU', context_json = '{}', updated_at = CURRENT_TIMESTAMP WHERE wa_id = ?",
                    (wa_id,),
                )
            user_state = ("B2C_MAIN_MENU", "{}")

        # 1. If user is currently running a flow, advance the flow with their reply
        if flow_runner.is_running(wa_id):
            outcome = flow_runner.handle_reply(wa_id, message, tenant_id=tenant_id)
            if outcome and outcome.get("handled"):
                if outcome.get("messages"):
                    from services.whatsapp_sender import send_button_message
                    for msg in outcome["messages"]:
                        if isinstance(msg, dict) and msg.get("type") == "buttons":
                            await send_button_message(wa_id, msg.get("text", ""), msg.get("buttons", []))
                        else:
                            await send_text_message(wa_id, msg)
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": user_state[0] if user_state else None,
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }
            # Greeting reset or topic switch: clear form and fall through once.
            # Greeting already maps to is_menu_cmd above on the next path —
            # here we only clear leftover flow state.
            if outcome and not outcome.get("handled"):
                flow_runner.clear_flow(wa_id)
                with get_db_context() as conn:
                    conn.execute(
                        "UPDATE user_states SET state = 'B2C_MAIN_MENU', context_json = '{}', updated_at = CURRENT_TIMESTAMP WHERE wa_id = ?",
                        (wa_id,),
                    )
                user_state = ("B2C_MAIN_MENU", "{}")
                if outcome.get("greeting_reset"):
                    await _send_b2c_menu(wa_id, tenant_id=tenant_id)
                    return {
                        "status": "processed",
                        "messages": drain_captured(),
                        "new_state": "B2C_MAIN_MENU",
                        "human_handover": False,
                        "user_type": "b2c",
                        "error": None,
                    }

        # 1b. Brochure / PDF ask — only files uploaded as brochure (admin_files /
        # profile brochure_url). Never product image cards or extracted media.
        try:
            try:
                from services.brochure_service import is_brochure_ask, send_relevant_brochure
            except ImportError:
                from backend.services.brochure_service import is_brochure_ask, send_relevant_brochure
            if is_brochure_ask(message, tenant_id=tenant_id):
                pressed = message if (message or "").startswith("menu_") else None
                sent = send_relevant_brochure(
                    wa_id,
                    query=message,
                    tenant_id=tenant_id,
                    button_id=pressed,
                )
                if not sent:
                    await send_text_message(
                        wa_id,
                        "Sorry, I don't have a brochure PDF available right now. "
                        "Please type *hi* for the menu, or ask our team.",
                    )
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": "B2C_MAIN_MENU",
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }
        except Exception as exc:
            logger.debug(f"brochure request failed for {wa_id}: {exc}")

        # 1b2. Stale WhatsApp list rows from a previous brand / old menu publish.
        # Must run *before* offerings browse — otherwise a foreign \"Our Services\"
        # tap still opens the active tenant catalogue (feels like multi-reply).
        try:
            from bots.customer_b2c.workflows import _is_stale_foreign_menu_tap
            if _is_stale_foreign_menu_tap(message, tenant_id):
                logger.info(
                    "STALE_FOREIGN_MENU_TAP | wa_id=%s | tenant=%s | q=%r",
                    wa_id, tenant_id, (message or "")[:80],
                )
                from bots.customer_b2c.workflows import _send_main_menu
                await _send_main_menu(
                    wa_id,
                    tenant_id=tenant_id,
                    body_prefix=(
                        "That option was from a previous brand menu. "
                        "Here is the *current* menu:\n\n"
                    ),
                )
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": "B2C_MAIN_MENU",
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }
        except Exception as exc:
            logger.debug(f"stale foreign menu check failed for {wa_id}: {exc}")

        # 1c. Tour package browsing: the package catalogue (list menu), a
        # package card with its booking button, and the booking enquiry.
        # Runs after the running flow (a live form keeps the reply) but
        # before the flow trigger, so a package name never starts an
        # unrelated flow.
        try:
            try:
                from services import offerings_flow
            except ImportError:
                from backend.services import offerings_flow
            offerings_result = await offerings_flow.handle_offerings_action(
                wa_id, message, tenant_id=tenant_id,
            )
            if offerings_result and offerings_result.get("handled"):
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": "B2C_MAIN_MENU",
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }
        except Exception as exc:
            logger.debug(f"offerings browsing failed for {wa_id}: {exc}")

        # 2. Check flow trigger for button ID or typed text (e.g. 'book a callback', 'schedule meeting')
        flow_name = (
            flow_runner.flow_for_button(wa_id, message, tenant_id=tenant_id)
            or flow_runner.flow_for_text(wa_id, message, tenant_id=tenant_id)
        )
        if flow_name:
            outcome = flow_runner.start_flow(wa_id, flow_name, tenant_id=tenant_id)
            if outcome.get("handled") and outcome.get("messages"):
                from services.whatsapp_sender import send_button_message
                for msg in outcome["messages"]:
                    if isinstance(msg, dict) and msg.get("type") == "buttons":
                        await send_button_message(wa_id, msg.get("text", ""), msg.get("buttons", []))
                    else:
                        await send_text_message(wa_id, msg)
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": outcome.get("new_state", "B2C_MAIN_MENU"),
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }

        # 3. Check intent by button ID, menu row title, or typed text (Careers, Technologies, Projects, Benefits, etc.)
        # Resolve the raw answer (intent or button) and synthesize it via the LLM
        intent_reply = (
            intent_answers.answer_for_button(wa_id, message, tenant_id=tenant_id)
            or intent_answers.answer_for_menu_title(wa_id, message, tenant_id=tenant_id)
            or intent_answers.answer_for_intent(wa_id, message, tenant_id=tenant_id)
            or intent_answers.answer_for_text(wa_id, message, tenant_id=tenant_id)
        )
        if intent_reply:
            from faq.service import synthesize_intent_answer
            synthesized = await synthesize_intent_answer(
                raw_content=intent_reply,
                intent_name=message,
                user_query=message,
                tenant_id=tenant_id,
            )
            from routing.button_actions import get_contextual_buttons
            from services.whatsapp_sender import send_button_message
            buttons = get_contextual_buttons(message, tenant_id=tenant_id, wa_id=wa_id, limit=3)
            if buttons:
                await send_button_message(wa_id, synthesized, buttons)
            else:
                await send_text_message(wa_id, synthesized)
            return {
                "status": "processed",
                "messages": drain_captured(),
                "new_state": "B2C_MAIN_MENU",
                "human_handover": False,
                "user_type": "b2c",
                "error": None,
            }

        # Check service query
        if service_answers.is_service_query(message):
            service_reply = service_answers.answer_for_text(wa_id, message, tenant_id=tenant_id)
            if service_reply:
                from faq.service import synthesize_intent_answer
                synthesized = await synthesize_intent_answer(
                    raw_content=service_reply,
                    intent_name="Services",
                    user_query=message,
                    tenant_id=tenant_id,
                )
                from routing.button_actions import get_contextual_buttons
                from services.whatsapp_sender import send_button_message
                buttons = get_contextual_buttons("services", tenant_id=tenant_id, wa_id=wa_id, limit=3)
                if buttons:
                    await send_button_message(wa_id, synthesized, buttons)
                else:
                    await send_text_message(wa_id, synthesized)
                return {
                    "status": "processed",
                    "messages": drain_captured(),
                    "new_state": "B2C_MAIN_MENU",
                    "human_handover": False,
                    "user_type": "b2c",
                    "error": None,
                }
    except Exception as exc:
        logger.debug(f"Tenant profile intent routing fallback for {wa_id}: {exc}")

    # 2. Determine User Type (B2B vs B2C)
    with get_db_context() as conn:
        is_distributor = conn.execute(
            "SELECT 1 FROM distributors WHERE wa_id=?", (wa_id,)
        ).fetchone()

    current_state = user_state[0] if user_state else None
    try:
        context = json.loads(user_state[1]) if user_state and user_state[1] else {}
    except (json.JSONDecodeError, TypeError):
        context = {}

    # 3. Route to correct modular workflow
    if is_distributor:
        from bots.distributor_b2b.workflows import handle_b2b_message
        result = await handle_b2b_message(wa_id, message, current_state, context)
        new_state_prefix = "B2B_"
        user_type = "b2b"
    else:
        from bots.customer_b2c.workflows import handle_b2c_message
        try:
            result = await handle_b2c_message(wa_id, message, current_state, context, tenant_id=tenant_id)
        except TypeError:
            result = await handle_b2c_message(wa_id, message, current_state, context)
        new_state_prefix = "B2C_"
        user_type = "b2c"

    # 4. Update State in DB (real schema uses context_json)
    new_state = current_state
    updated_context = context
    if result and "new_state" in result:
        updated_context = result.get("context", context)
        new_state = result["new_state"]
        if not new_state or not new_state.startswith(new_state_prefix):
            new_state = f"{new_state_prefix}{new_state}" if new_state else current_state

        # UPSERT — webhook + poller can both create the row; plain INSERT races
        # and drops the reply after WhatsApp already got it.
        ctx_json = json.dumps(updated_context)
        tid = (tenant_id or "").strip()
        if not tid:
            try:
                from shared.tenancy.resolver import resolve_tenant_for_user
                tid = resolve_tenant_for_user(wa_id) or "default"
            except Exception:
                tid = "default"
        with get_db_context() as conn:
            conn.execute(
                """
                INSERT INTO user_states (tenant_id, wa_id, state, context_json, lang, updated_at)
                VALUES (?, ?, ?, ?, 'en', CURRENT_TIMESTAMP)
                ON CONFLICT(tenant_id, wa_id) DO UPDATE SET
                    state = excluded.state,
                    context_json = excluded.context_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (tid, wa_id, new_state, ctx_json),
            )

    messages = drain_captured()

    handover = any(m.get("type") == "text" and "human" in (m.get("text") or "").lower()
                   for m in messages)

    return {
        "messages": messages,
        "new_state": new_state,
        "human_handover": handover,
        "user_type": user_type,
        "status": "processed",
        "error": None,
    }
