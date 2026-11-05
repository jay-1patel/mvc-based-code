"""Deterministic kit table parsers for Brand / Operations / Guardrails.

Used by ``auto_bifurcator`` so complete onboarding kits fill Profile (brand,
hours, notifications, vocabulary, features, greeting) plus domain answers and
full guardrails — not only the short legacy field set.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from shared.tenancy.schemas import FEATURE_FLAGS

_FEATURE_SET = set(FEATURE_FLAGS)


def _cell(row: List[Any], idx: int = 0) -> str:
    if idx < 0:
        idx = len(row) + idx
    if not row or idx >= len(row) or idx < 0:
        return ""
    return str(row[idx] or "").strip()


def _split_list(value: str) -> List[str]:
    """Split comma / newline / bullet lists into clean tokens."""
    if not value:
        return []
    parts: List[str] = []
    for chunk in re.split(r"[\n,|;]+", value):
        token = chunk.strip().lstrip("-•* ").strip()
        if token and token not in ("—", "-", "–"):
            parts.append(token)
    return parts


def _parse_hhmm(raw: str, default: str) -> str:
    text = (raw or "").strip()
    m = re.search(r"(\d{1,2}):(\d{2})", text)
    if not m:
        return default
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return f"{h:02d}:{mi:02d}"
    return default


def _truthy(raw: str) -> Optional[bool]:
    v = (raw or "").strip().lower()
    if v in ("yes", "true", "1", "y", "always open", "always"):
        return True
    if v in ("no", "false", "0", "n"):
        return False
    return None


def parse_brand_kit_tables(
    tables: List[List[List[Any]]],
    text: str = "",
) -> Dict[str, Any]:
    """Parse BRAND & PROFILE kit tables into brand / hours / extras."""
    fields: Dict[str, str] = {}

    for tbl in tables or []:
        for row in tbl or []:
            if len(row) < 2:
                continue
            k = _cell(row, 0).lower()
            v = _cell(row, -1)
            if not k or not v or k in ("parameter", "field", "description"):
                continue

            # Order matters — more specific keys first
            if "tagline" in k or "proposition" in k:
                fields["tagline"] = v
            elif "whatsapp" in k and ("bot" in k or "number" in k or "waba" in k or "msisdn" in k):
                fields["waba_phone"] = v
            elif "bot name" in k or "assistant name" in k or "ai bot" in k:
                fields["bot_name"] = v
            elif "tenant id" in k or "tenant code" in k:
                fields["tenant_id"] = v
            elif "industry vertical" in k or k.strip() == "vertical":
                fields["vertical_hint"] = v
            elif "display name" in k or "brand / display" in k or k == "brand name":
                fields["brand_name"] = v
            elif "legal name" in k or "company legal" in k:
                fields["legal_name"] = v
            elif "company name" in k and "legal" not in k:
                fields.setdefault("legal_name", v)
            elif "logo" in k:
                fields["logo_url"] = v
            elif "brochure url" in k or (k.startswith("brochure") and "url" in k):
                fields["brochure_url"] = v
            elif "brochure label" in k:
                fields["brochure_label"] = v
            elif "website" in k or (k.strip() in ("official website", "official website url")):
                fields["website"] = v
            elif "sales" in k and "email" in k:
                fields["sales_email"] = v
            elif "notifications support" in k or ("notifications" in k and "email" in k):
                fields["notifications_support_email"] = v
            elif "support email" in k or (k.strip() == "email") or (
                "email" in k and "sales" not in k and "notification" not in k
            ):
                fields["support_email"] = v
            elif "support phone" in k or "helpline" in k or (
                "phone" in k and "whatsapp" not in k and "bot" not in k
            ):
                fields["support_phone"] = v
            elif "closing signature" in k or k.strip() == "signature":
                fields["signature"] = v
            elif "greeting" in k or "welcome" in k:
                fields["greeting"] = v
            elif "timezone" in k or "time zone" in k:
                fields["timezone"] = v
            elif "always open" in k:
                fields["always_open"] = v
            elif k.strip() in ("opens", "open time", "open"):
                fields["open"] = v
            elif k.strip() in ("closes", "close time", "close"):
                fields["close"] = v
            elif "open days" in k or "operating days" in k:
                fields["open_days_raw"] = v
            elif "out-of-hours" in k or "out of hours" in k:
                fields["out_of_hours_message"] = v
            elif "business hours" in k or ("hours" in k and "out" not in k and "open" not in k):
                fields["hours"] = v
            elif "tone" in k or "persona" in k:
                fields["tone"] = v
            elif "vocabulary" in k and "plural" in k:
                fields["voc_item"] = v
            elif "vocabulary" in k and "singular" in k:
                fields["voc_singular"] = v
            elif "vocabulary" in k and "browse" in k:
                fields["voc_browse"] = v
            elif "vocabulary" in k and "brochure" in k:
                fields["voc_brochure"] = v
            elif "vocabulary" in k and "lead" in k:
                fields["voc_lead"] = v
            elif "vocabulary" in k and ("enquire" in k or "enquiry" in k or "button" in k):
                fields["voc_enquire"] = v
            elif "vocabulary" in k and "support" in k:
                fields["voc_support"] = v
            elif "features enabled" in k or (k.startswith("features") and "enable" in k):
                fields["features_enabled"] = v
            elif "features disabled" in k or (k.startswith("features") and "disable" in k):
                fields["features_disabled"] = v

    return fields


def fields_to_brand_bundle(
    fields: Dict[str, str],
    *,
    tenant_id: str,
    default_vertical: str = "generic",
) -> Tuple[Dict[str, Any], Dict[str, Any], str, Dict[str, Any]]:
    """Turn parsed kit fields into brand, hours, vertical, extras."""
    company = (
        fields.get("brand_name")
        or fields.get("legal_name")
        or tenant_id.replace("-", " ").replace("_", " ").title()
    )
    brand: Dict[str, Any] = {
        "name": company,
        "bot_name": fields.get("bot_name") or f"{company} Advisor",
        "tagline": fields.get("tagline") or f"Official WhatsApp Concierge for {company}",
        "website": fields.get("website") or f"https://www.{tenant_id}.com",
        "support_email": fields.get("support_email") or f"support@{tenant_id}.com",
        "support_phone": fields.get("support_phone") or "+91 9876543210",
        "signature": fields.get("signature") or f"{company} — At your service.",
    }
    if fields.get("logo_url"):
        brand["logo_url"] = fields["logo_url"]

    hours_str = fields.get("hours") or "09:30 AM - 06:30 PM (Mon-Sat)"
    always = _truthy(fields.get("always_open") or "")
    business_hours: Dict[str, Any] = {
        "timezone": fields.get("timezone") or "Asia/Kolkata",
        "open": _parse_hhmm(fields.get("open") or "", "09:30"),
        "close": _parse_hhmm(fields.get("close") or "", "18:30"),
        "open_days": [0, 1, 2, 3, 4, 5],
        "raw": hours_str,
        "always_open": bool(always) if always is not None else False,
    }
    if fields.get("out_of_hours_message"):
        business_hours["out_of_hours_message"] = fields["out_of_hours_message"]

    # Vertical hint from brand table
    vertical = default_vertical
    hint = (fields.get("vertical_hint") or "").lower()
    for needle, slug in (
        ("it_software", "it_software"),
        ("software", "it_software"),
        ("real_estate", "real_estate"),
        ("real estate", "real_estate"),
        ("tours_travel", "tours_travel"),
        ("travel", "tours_travel"),
        ("ecommerce", "ecommerce"),
        ("e-commerce", "ecommerce"),
        ("healthcare", "healthcare"),
        ("banking", "banking"),
        ("finance", "finance"),
        ("generic", "generic"),
    ):
        if needle in hint:
            vertical = slug
            break

    extras: Dict[str, Any] = {}
    if fields.get("greeting"):
        extras["greeting"] = fields["greeting"]
    if fields.get("waba_phone"):
        extras["waba_phone"] = fields["waba_phone"]

    notifications: Dict[str, str] = {}
    if fields.get("sales_email"):
        notifications["sales_email"] = fields["sales_email"]
    if fields.get("notifications_support_email"):
        notifications["support_email"] = fields["notifications_support_email"]
    if fields.get("brochure_url"):
        notifications["brochure_url"] = fields["brochure_url"]
    if fields.get("brochure_label"):
        notifications["brochure_label"] = fields["brochure_label"]
    if notifications:
        extras["notifications"] = notifications

    vocabulary: Dict[str, str] = {}
    mapping = (
        ("voc_item", "item_noun"),
        ("voc_singular", "item_noun_singular"),
        ("voc_browse", "browse_label"),
        ("voc_brochure", "brochure_label"),
        ("voc_lead", "lead_noun"),
        ("voc_enquire", "enquiry_label"),
        ("voc_support", "support_label"),
    )
    for src, dst in mapping:
        if fields.get(src):
            vocabulary[dst] = fields[src]
    if vocabulary:
        extras["vocabulary"] = vocabulary

    if fields.get("tone"):
        extras["tone"] = fields["tone"]

    features: Dict[str, bool] = {}
    for token in _split_list(fields.get("features_enabled") or ""):
        key = token.strip().lower().replace(" ", "_").replace("-", "_")
        if key in _FEATURE_SET:
            features[key] = True
    for token in _split_list(fields.get("features_disabled") or ""):
        key = token.strip().lower().replace(" ", "_").replace("-", "_")
        if key in _FEATURE_SET:
            features[key] = False
    if features:
        extras["features"] = features

    return brand, business_hours, vertical, extras


def parse_operations_domain_tables(
    tables: List[List[List[Any]]],
    text: str = "",
) -> Dict[str, Any]:
    """Parse OPERATIONS & DOMAIN kit tables into answers + classifier keywords."""
    answers: List[Dict[str, str]] = []
    keywords: List[str] = []

    for tbl in tables or []:
        if not tbl:
            continue
        header = " ".join(_cell(r, 0).lower() for r in tbl[:1])
        header += " " + " ".join(str(c).lower() for c in (tbl[0] or []))
        # Accept domain tables even without perfect headers
        for row in tbl[1:] if len(tbl) > 1 else tbl:
            if len(row) < 2:
                continue
            label = _cell(row, 0)
            # Skip header-like rows
            if label.lower() in ("domain field", "field", "parameter", "why we need it"):
                continue
            help_text = _cell(row, 1) if len(row) >= 3 else ""
            value = _cell(row, -1)
            if not label or not value:
                continue
            if label.lower() == value.lower():
                continue
            answers.append({"label": label, "help": help_text, "value": value})
            # Chip-like values become classifier keywords
            for token in _split_list(value):
                if 2 <= len(token) <= 48 and not token.startswith("http"):
                    keywords.append(token.lower())

    # Dedupe keywords preserving order
    seen = set()
    uniq_kw: List[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            uniq_kw.append(kw)

    return {
        "domain_answers": answers,
        "keywords": uniq_kw[:80],
        "topics": "; ".join(a["label"] + ": " + a["value"][:120] for a in answers[:12]),
    }


def parse_guardrails_kit_tables(tables: List[List[List[Any]]]) -> Dict[str, Any]:
    """Parse BOT PERSONA & GUARDRAILS kit tables into profile guardrails."""
    guardrails: Dict[str, Any] = {
        "tone": "consultative, polished, and professional",
        "never_state": [
            "Never offer unauthorized discounts or unlisted pricing.",
            "Never promise delivery or possession dates outside official brochure.",
            "Never request passwords, OTPs, or credit card details.",
        ],
        "escalation_triggers": ["human", "agent", "manager", "dispute", "representative"],
        "forbidden_terms": [],
        "handoff_keywords": ["human", "agent", "person", "manager"],
    }
    greeting = ""

    for tbl in tables or []:
        for row in tbl or []:
            if len(row) < 2:
                continue
            k = _cell(row, 0).lower()
            v = _cell(row, 1) if len(row) == 2 else _cell(row, -1)
            if not k or not v:
                continue
            if "tone" in k or "persona" in k:
                guardrails["tone"] = v
            elif "welcome" in k or "greeting" in k:
                greeting = v
            elif "forbidden" in k:
                guardrails["forbidden_terms"] = _split_list(v)
            elif "handoff" in k:
                guardrails["handoff_keywords"] = [t.lower() for t in _split_list(v)]
                guardrails["escalation_triggers"] = list(
                    dict.fromkeys(
                        list(guardrails.get("escalation_triggers") or [])
                        + [t.lower() for t in _split_list(v)]
                    )
                )
            elif "escalate keyword" in k or k.strip() == "escalate on" or (
                "escalate" in k and "message" not in k and "rule" not in k
            ):
                guardrails["escalate_keywords"] = [t.lower() for t in _split_list(v)]
                guardrails["escalation_triggers"] = list(
                    dict.fromkeys(
                        list(guardrails.get("escalation_triggers") or [])
                        + [t.lower() for t in _split_list(v)]
                    )
                )
            elif "escalation message" in k or (k.strip() == "escalation message"):
                guardrails["escalation_message"] = v
            elif "guardrail" in k or "never" in k:
                # DOCX often flattens bullet rows into one cell joined by " - "
                rules = [
                    r.strip().lstrip("123456789.-*• ")
                    for r in re.split(r"[\n]+|(?:\s+[–—-]\s+)", v)
                    if r.strip()
                ]
                rules = [r for r in rules if r and r not in ("-", "–", "—")]
                if rules:
                    guardrails["never_state"] = rules
            elif "escalat" in k or ("human" in k and "keyword" not in k):
                guardrails["escalation_rules"] = v
                if "escalation_message" not in guardrails:
                    guardrails["escalation_message"] = v

    if greeting:
        guardrails["greeting"] = greeting
    return guardrails


def merge_profile_extras_into_draft(draft: Dict[str, Any], extras: Dict[str, Any]) -> Dict[str, str]:
    """Apply notifications / vocabulary / features / greeting onto a draft. Returns flags."""
    flags: Dict[str, str] = {}
    if not extras:
        return flags

    if extras.get("greeting"):
        menu = dict(draft.get("menu") or {})
        menu["body"] = str(extras["greeting"])
        if not menu.get("header"):
            menu["header"] = (draft.get("brand") or {}).get("name") or draft.get("display_name") or "Menu"
        if not menu.get("button_text"):
            menu["button_text"] = "Select Option"
        draft["menu"] = menu
        flags["greeting"] = "1"

    if extras.get("notifications"):
        notes = dict(draft.get("notifications") or {})
        for k, v in extras["notifications"].items():
            if v:
                notes[k] = v
        draft["notifications"] = notes
        flags["notifications"] = "1"

    if extras.get("vocabulary"):
        voc = dict(draft.get("vocabulary") or {})
        voc.update({k: v for k, v in extras["vocabulary"].items() if v})
        draft["vocabulary"] = voc
        flags["vocabulary"] = "1"

    if extras.get("features"):
        feats = dict(draft.get("features") or {})
        feats.update(extras["features"])
        draft["features"] = feats
        flags["features"] = "1"

    if extras.get("tone"):
        prompt = dict(draft.get("prompt") or {})
        prompt["tone"] = extras["tone"]
        draft["prompt"] = prompt
        flags["tone"] = "1"

    if extras.get("waba_phone"):
        # Stored on tenant row elsewhere; keep on draft for operators who read it
        draft["waba_phone_hint"] = extras["waba_phone"]
        flags["waba"] = "1"

    return flags


def merge_operations_into_draft(draft: Dict[str, Any], operations: Dict[str, Any]) -> bool:
    """Write domain answers into prompt keywords / topics."""
    if not operations:
        return False
    prompt = dict(draft.get("prompt") or {})
    changed = False
    kws = operations.get("keywords") or []
    if kws:
        existing = [str(x).lower() for x in (prompt.get("keywords") or [])]
        merged = list(existing)
        for kw in kws:
            if kw not in merged:
                merged.append(kw)
        prompt["keywords"] = merged[:100]
        changed = True
    topics = (operations.get("topics") or "").strip()
    if topics:
        prev = str(prompt.get("domain_specific_topics") or "").strip()
        prompt["domain_specific_topics"] = (prev + "\n" + topics).strip() if prev else topics
        changed = True
    answers = operations.get("domain_answers") or []
    if answers:
        # Keep raw answers for audit / future UI
        draft["domain_answers"] = answers
        changed = True
    if changed:
        draft["prompt"] = prompt
    return changed
