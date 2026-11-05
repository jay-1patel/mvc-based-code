import json
import requests
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from routing.config import SEND2_USERNAME, SEND2_PASSWORD, SEND2_SESSION_MSG_URL, TEMPLATE_PREFIX, SEND2_TEMPLATE_URL,logger
from ..database import (
    save_template, get_template, delete_template,
    list_templates, update_session_outbound, save_session_button,
)

SEND2_TEMPLATE_URL = SEND2_TEMPLATE_URL
TEMPLATES_JSON_PATH = Path(__file__).resolve().parent.parent / "dataset" / "templates.json"

# Scenario → template name mapping (uses configurable prefix)
TEMPLATE_BY_SCENARIO: Dict[str, str] = {
    "welcome":         f"{TEMPLATE_PREFIX}_welcome",
    "greeting":        f"{TEMPLATE_PREFIX}_greeting",
    "product":         f"{TEMPLATE_PREFIX}_product_reply",
    "policy":          f"{TEMPLATE_PREFIX}_policy_reply",
    "order_status":    f"{TEMPLATE_PREFIX}_order_status",
    "escalation":      f"{TEMPLATE_PREFIX}_escalation",
    "closing":         f"{TEMPLATE_PREFIX}_closing",
    "menu_prompt":     f"{TEMPLATE_PREFIX}_menu_prompt",
    "coupon_offer":    f"{TEMPLATE_PREFIX}_coupon_offer",
}

# Keywords to classify a response type from RAG result (generic, not product-specific)
_SCENARIO_KEYWORDS: Dict[str, List[str]] = {
    "product": [
        "product", "item", "catalogue", "catalog", "price", "cost", "mrp",
        "available", "stock", "variant", "specification", "feature",
    ],
    "policy": [
        "policy", "term", "condition", "return", "refund", "exchange",
        "warranty", "guarantee", "moq", "minimum order", "payment",
        "delivery", "shipping", "dispatch", "lead time",
    ],
    "order_status": [
        "order", "tracking", "status", "shipped", "delivered", "dispatched",
        "invoice", "receipt", "confirmation",
    ],
    "escalation": [
        "connect", "team", "follow up", "human", "person", "representative",
        "agent", "support", "help me", "speak to",
    ],
    "closing": [
        "thank you", "thanks", "goodbye", "bye", "that's all", "done",
        "no more", "all set", "perfect",
    ],
}


def load_templates() -> List[Dict]:
    try:
        with open(TEMPLATES_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load templates.json: {e}")
        return []


def get_template(name: str) -> Optional[Dict]:
    templates = load_templates()
    for t in templates:
        if t.get("name") == name:
            return t
    return None


def classify_response(text: str) -> str:
    """Classify a message/response to determine the best template scenario."""
    text_lower = text.lower()
    scores: Dict[str, int] = {}

    for scenario, keywords in _SCENARIO_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        scores[scenario] = score

    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return "general"
    return best


def build_template_params(scenario: str, answer: str) -> List[str]:
    """Build body params for a template based on scenario.
    Only returns params if the template expects {{1}} variables.
    """
    template_name = TEMPLATE_BY_SCENARIO.get(scenario)
    if not template_name:
        return [answer[:1024]] if answer else []
    tmpl = get_template(template_name)
    expected_params = len(tmpl.get("params", [])) if tmpl else 0
    if expected_params == 0:
        return []
    if scenario == "escalation":
        return [f"I'll connect you with our team — they'll follow up here shortly. {answer}".strip()[:1024]]
    if scenario in ("product", "policy", "order_status"):
        return [answer[:1024]]
    return [answer[:1024]] if answer else []


def _map_button_to_send2(btn: Dict) -> Dict:
    """Map a template button definition to send2.digital /template/create format."""
    btn_type = btn.get("type", "quick_reply")
    title = btn.get("title", "") or btn.get("label", "")

    if btn_type == "quick_reply":
        return {
            "button_type": "quick",
            "type": "quick",
            "label": title[:25],
        }
    elif btn_type in ("url", "cta_url"):
        return {
            "button_type": "cta",
            "type": "url",
            "label": title[:25],
            "url": btn.get("url", ""),
        }
    elif btn_type in ("phone", "cta_phone"):
        return {
            "button_type": "cta",
            "type": "phone",
            "label": title[:25],
            "number": btn.get("number", ""),
        }
    return {
        "button_type": "quick",
        "type": "quick",
        "label": title[:25],
    }


def _build_send2_template_payload(template: Dict, lang_code: str = "en") -> Dict:
    
    name = template["name"]
    category = template.get("category", "UTILITY")
    header_type = template.get("header_type", "noheader")
    body_template = template.get("body_template", "")
    footer = template.get("footer", "")
    header_text = template.get("header_text", "")
    buttons = template.get("buttons", [])

    payload = {
        "user_name": SEND2_USERNAME,
        "password": SEND2_PASSWORD,
        "language": lang_code,
        "template_name": name,
        "category": category,
        "subcategory": "custom",
        "header_type": header_type,
        "message_body": body_template,
        "footer": footer,
    }

    if header_type == "text" and header_text:
        payload["header_text"] = header_text

    if buttons:
        payload["buttons"] = [_map_button_to_send2(b) for b in buttons]

    return payload


def register_template_on_send2(template: Dict) -> Tuple[bool, str]:

    payload = _build_send2_template_payload(template)
    try:
        resp = requests.post(SEND2_TEMPLATE_URL, json=payload, timeout=30)
        body = resp.text[:300]
        logger.info(f"Template register '{template['name']}': status={resp.status_code}, body={body}")
        if resp.status_code == 200:
            return True, "registered"
        elif "already exists" in body.lower() or "duplicate" in body.lower():
            return True, "already_exists"
        else:
            return False, body
    except Exception as e:
        logger.error(f"register_template_on_send2({template['name']}) failed: {e}")
        return False, str(e)


def register_all_templates() -> Dict[str, Dict]:

    templates = load_templates()
    results = {}
    for t in templates:
        success, status = register_template_on_send2(t)
        results[t["name"]] = {"success": success, "status": status}
        logger.info(f"Template '{t['name']}': {status}")
    return results


def _send_template_message(
    to: str,
    template: Dict,
    params: List[str],
    lang_code: str = "en",
) -> bool:

    name = template.get("name")
    buttons = template.get("buttons", [])

    components = []
    if params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in params],
        })

    if buttons:
        button_list = []
        for b in buttons:
            button_list.append({
                "type": "reply",
                "reply": {
                    "id": b.get("id", f"{name}_btn_{len(button_list)}"),
                    "title": b.get("title", "")[:25],
                },
            })
        components.append({
            "type": "button",
            "sub_type": "quick_reply",
            "parameters": button_list,
        })

    payload = {
        "user_name": SEND2_USERNAME,
        "password": SEND2_PASSWORD,
        "contact_no": to,
        "message_type": "template",
        "template": {
            "name": name,
            "language": {"code": lang_code},
            "components": components,
        },
    }

    try:
        resp = requests.post(SEND2_SESSION_MSG_URL, json=payload, timeout=30)
        logger.info(f"Template sent '{name}' to {to}: status={resp.status_code}, body={resp.text[:200]}")
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"_send_template_message({name}, {to}) failed: {e}")
        return False


def send_template_by_name(to: str, template_name: str, params: List[str] = None) -> bool:
    """Send a named template to a recipient."""
    template = get_template(template_name)
    if not template:
        logger.error(f"Template '{template_name}' not found in templates.json")
        return False
    return _send_template_message(to, template, params or [])


def send_template_for_scenario(
    to: str,
    scenario: str,
    answer: str = "",
    force: bool = False,
) -> Tuple[bool, str]:
    
    from ..database import is_session_open, update_session_outbound

    template_name = TEMPLATE_BY_SCENARIO.get(scenario, TEMPLATE_BY_SCENARIO["product"])
    template = get_template(template_name)

    if not template:
        logger.warning(f"No template for scenario '{scenario}', using free-text")
        return False, "none"

    params = build_template_params(scenario, answer)

    if not force and is_session_open(to):
        # Session open — try to send as interactive free-text first
        # For rich responses with buttons, still use template
        if scenario in ("product", "policy", "order_status", "escalation"):
            success = _send_template_message(to, template, params)
            if success:
                update_session_outbound(to)
                return True, "template"
        # Short text responses can go as free-form
        if len(answer) <= 400:
            from .whatsapp import send_whatsapp_message
            success = send_whatsapp_message(to, answer)
            if success:
                update_session_outbound(to)
                return True, "text"

    # Session closed or template-required scenario → use template
    success = _send_template_message(to, template, params)
    if success:
        update_session_outbound(to)
    return success, "template"


def get_template_list() -> List[Dict]:
    """Return all templates with their metadata for admin UI."""
    templates = load_templates()
    return [
        {
            "name": t["name"],
            "category": t.get("category", ""),
            "description": t.get("description", ""),
            "body_template": t.get("body_template", ""),
            "footer": t.get("footer", ""),
            "header_type": t.get("header_type", "noheader"),
            "buttons": t.get("buttons", []),
            "params": t.get("params", []),
            "scenario": _get_scenario_for_template(t["name"]),
        }
        for t in templates
    ]


def _get_scenario_for_template(name: str) -> str:
    for scenario, tname in TEMPLATE_BY_SCENARIO.items():
        if tname == name:
            return scenario
    return "unknown"


def validate_template_payload(payload: dict) -> Tuple[bool, str]:
    """Validate a template payload before sending to send2.digital."""
    required_fields = ["template_name", "message_body", "category"]
    for field in required_fields:
        if field not in payload or not payload[field]:
            return False, f"Missing required field: {field}"

    name = payload["template_name"]
    if len(name) > 512:
        return False, "template_name too long (max 512 chars)"

    body = payload["message_body"]
    if len(body) > 1024:
        return False, "message_body too long (max 1024 chars)"

    buttons = payload.get("buttons", [])
    if len(buttons) > 3:
        return False, "Maximum 3 buttons allowed per template"

    for btn in buttons:
        btn_type = btn.get("button_type", "")
        if btn_type not in ("quick", "cta"):
            return False, f"Invalid button_type: {btn_type}"
        if btn_type == "cta":
            cta_type = btn.get("type", "")
            if cta_type not in ("url", "phone"):
                return False, f"Invalid CTA type: {cta_type}"
            if cta_type == "url" and not btn.get("url"):
                return False, "CTA URL button requires a url field"
            if cta_type == "phone" and not btn.get("number"):
                return False, "CTA phone button requires a number field"
        label = btn.get("label", "")
        if len(label) > 25:
            return False, f"Button label too long (max 25 chars): {label}"

    return True, "valid"


def create_template_on_send2(template_data: dict, lang_code: str = "en") -> Tuple[bool, str, dict]:
    """Create a template on send2.digital and track it locally.

    Args:
        template_data: Dict with template fields (name, category, body_template, etc.)
        lang_code: Language code (default 'en')

    Returns:
        (success, status, response_data)
    """
    name = template_data.get("template_name", template_data.get("name", ""))
    if not name:
        return False, "missing_name", {}

    payload = _build_send2_template_payload(template_data, lang_code)

    valid, msg = validate_template_payload(payload)
    if not valid:
        return False, msg, {}

    try:
        resp = requests.post(SEND2_TEMPLATE_URL, json=payload, timeout=30)
        body = resp.text[:500]
        logger.info(f"Template create '{name}': status={resp.status_code}, body={body}")

        if resp.status_code == 200:
            save_template(name, {
                **template_data,
                "status": "registered",
                "send2_response": body,
            })
            return True, "registered", {"status_code": 200, "body": body}
        elif "already exists" in body.lower() or "duplicate" in body.lower():
            save_template(name, {
                **template_data,
                "status": "already_exists",
                "send2_response": body,
            })
            return True, "already_exists", {"status_code": resp.status_code, "body": body}
        else:
            save_template(name, {
                **template_data,
                "status": "failed",
                "send2_response": body,
            })
            return False, body, {"status_code": resp.status_code, "body": body}
    except Exception as e:
        logger.error(f"create_template_on_send2('{name}') failed: {e}")
        save_template(name, {
            **template_data,
            "status": "error",
            "send2_response": str(e),
        })
        return False, str(e), {}


def update_template_on_send2(template_name: str, template_data: dict, lang_code: str = "en") -> Tuple[bool, str, dict]:
    """Update a template on send2.digital.

    Note: send2.digital may not support a dedicated update endpoint.
    This will attempt to re-create (which may update existing) or log the attempt.
    """
    payload = _build_send2_template_payload(template_data, lang_code)
    payload["template_name"] = template_name

    valid, msg = validate_template_payload(payload)
    if not valid:
        return False, msg, {}

    try:
        resp = requests.post(SEND2_TEMPLATE_URL, json=payload, timeout=30)
        body = resp.text[:500]
        logger.info(f"Template update '{template_name}': status={resp.status_code}, body={body}")

        if resp.status_code == 200 or "already exists" in body.lower():
            save_template(template_name, {
                **template_data,
                "status": "updated",
                "send2_response": body,
            })
            return True, "updated", {"status_code": resp.status_code, "body": body}
        else:
            save_template(template_name, {
                **template_data,
                "status": "update_failed",
                "send2_response": body,
            })
            return False, body, {"status_code": resp.status_code, "body": body}
    except Exception as e:
        logger.error(f"update_template_on_send2('{template_name}') failed: {e}")
        return False, str(e), {}


def delete_template_on_send2(template_name: str) -> Tuple[bool, str]:
    """Delete a template from send2.digital.

    Note: send2.digital API may not have a delete endpoint.
    This removes the local tracking record and attempts the API call.
    """
    try:
        resp = requests.delete(
            f"{SEND2_TEMPLATE_URL}/{template_name}",
            auth=(SEND2_USERNAME, SEND2_PASSWORD),
            timeout=30,
        )
        body = resp.text[:300]
        logger.info(f"Template delete '{template_name}': status={resp.status_code}, body={body}")
        if resp.status_code in (200, 204):
            delete_template(template_name)
            return True, "deleted"
        elif resp.status_code == 404:
            delete_template(template_name)
            return True, "not_found_removed_local"
        else:
            return False, f"status={resp.status_code}: {body}"
    except Exception as e:
        logger.error(f"delete_template_on_send2('{template_name}') failed: {e}")
        return False, str(e)


def sync_templates() -> Dict[str, Dict]:
    """Sync templates.json with send2.digital — register any missing templates."""
    templates = load_templates()
    results = {}
    for t in templates:
        name = t.get("name", "")
        existing = get_template(name)
        if existing and existing.get("status") == "registered":
            results[name] = {"success": True, "status": "skipped_already_registered"}
            continue
        success, status, detail = create_template_on_send2(t)
        results[name] = {"success": success, "status": status, "detail": detail}
        logger.info(f"Sync template '{name}': {status}")
    return results


def send_template_with_session_buttons(
    to: str,
    template_name: str,
    params: List[str] = None,
    session_wa_id: str = None,
    session_state: str = None,
    lang_code: str = "en",
) -> Tuple[bool, str]:
    """Send a template message with session-aware button tracking.

    If session_wa_id is provided, buttons are tracked in session_buttons table
    so clicks can be correlated back to the session and user state.
    """
    template = get_template(template_name)
    if not template:
        logger.error(f"Template '{template_name}' not found")
        return False, "template_not_found"

    buttons = template.get("buttons", [])
    if session_wa_id and buttons:
        for btn in buttons:
            btn_id = btn.get("id", f"{template_name}_btn_{len(buttons)}")
            save_session_button(
                button_id=btn_id,
                wa_id=session_wa_id,
                button_type=btn.get("type", "quick_reply"),
                label=btn.get("title", btn.get("label", "")),
                payload={"template_name": template_name, "button_id": btn_id},
                state=session_state or "MAIN_MENU",
                lang=lang_code,
            )

    success = _send_template_message(to, template, params or [], lang_code)
    if success and session_wa_id:
        update_session_outbound(session_wa_id)
    return success, "template" if success else "failed"
