"""Universal product and property browsing on WhatsApp.

Supports all tenant verticals:
- Real Estate (Apartments, luxury high-rises, villas, commercial spaces, retail)
- Tours & Travel (Tour packages, destinations, itineraries)
- Banking (Savings accounts, credit cards, loans)
- Finance & Advisory (Tax planning, advisory, GST compliance)
- IT & Software (Web development, mobile apps, custom software, consulting)
- E-commerce / Retail / General (Catalogue items)

Design guarantees:
1. Explore Offerings first sends an interactive **list** of all products /
   properties / services (paginated with a More row when needed).
2. When the user taps one row, send one interactive ``cta_url`` card
   (optional image header + description body + one URL button).
3. Reply buttons are NEVER mixed into that CTA payload (send2 rule).
4. Brochure / catalogue PDF is sent only when the user asks for a brochure.
5. Empty catalogue → clear text, no fake cards.
6. Pending product grounds FAQ follow-ups until hi / main menu / another offering.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional, Dict, Any, List

logger = logging.getLogger("offerings_flow")

# Row id prefixes for WhatsApp interactive lists and buttons
PROD_ROW_PREFIX = "prod_"
CAT_ROW_PREFIX = "cat_"
BOOK_PROD_PREFIX = "book_prod_"
MORE_ROW_PREFIX = "offerings_more_"

# WhatsApp Cloud API limits
ROW_TITLE_MAX = 24
ROW_DESC_MAX = 72
SECTION_TITLE_MAX = 24
MAX_LIST_ROWS = 10
# When more items remain, reserve the last list slot for the More row.
PAGE_ITEM_SLOTS = MAX_LIST_ROWS - 1
CARD_MAX_CHARS = 3800

VERTICAL_MORE_TITLES = {
    "tours_travel": "More packages",
    "real_estate": "More properties",
    "banking": "More products",
    "finance": "More services",
    "it_software": "More services",
    "ecommerce": "More products",
    "generic": "More offerings",
}

EMPTY_OFFERINGS_REPLY = (
    "We don't have any offerings listed yet. "
    "Type *hi* for the menu, or ask to speak to an expert."
)

# Vertical-tailored button titles for reply buttons (strictly <= 20 chars)
VERTICAL_BUTTON_TITLES = {
    "tours_travel": "🎒 Tour Packages",
    "real_estate": "🏢 View Properties",
    "banking": "🏦 Banking Products",
    "finance": "💼 Our Services",
    "it_software": "💻 Our Services",
    "ecommerce": "🛍️ Browse Products",
    "generic": "📋 Our Offerings",
}

# Vertical-tailored menu action IDs
VERTICAL_ACTION_IDS = {
    "tours_travel": "menu_packages",
    "real_estate": "menu_properties",
    "banking": "menu_banking_products",
    "finance": "menu_services",
    "it_software": "menu_services",
    "ecommerce": "menu_products",
    "generic": "menu_offerings",
}

# Vertical-tailored CTA titles on product cards (strictly <= 20 chars)
VERTICAL_CTA_TITLES = {
    "tours_travel": "Book This Package",
    "real_estate": "Schedule Site Visit",
    "banking": "Apply Now",
    "finance": "Request Proposal",
    "it_software": "Get a Quote",
    "ecommerce": "Buy / Enquire",
    "generic": "Enquire Now",
}

# send2 cta_url display_text (max 20 chars) — one URL button, never mixed with replies.
VERTICAL_CTA_URL_LABELS = {
    "tours_travel": "View Package",
    "real_estate": "View Property",
    "banking": "Learn More",
    "finance": "Learn More",
    "it_software": "Learn More",
    "ecommerce": "View Product",
    "generic": "Learn More",
}

# List button label (WhatsApp max 20 chars) — opens the offerings list.
LIST_BUTTON_TEXT = {
    "tours_travel": "Packages",
    "real_estate": "Properties",
    "banking": "Products",
    "finance": "Services",
    "it_software": "Services",
    "ecommerce": "Products",
    "generic": "Offerings",
}

ENQUIRY_BUTTON_ALIASES = {
    "book this package", "book this tour", "schedule site visit", "enquire property",
    "apply now", "request proposal", "get a quote", "buy / enquire", "enquire now",
    "enquire this product", "book now", "site visit",
}

ENQUIRY_VERBS = (
    "book", "booked", "booking", "apply", "enquire", "enquiry",
    "inquire", "inquiry", "interested", "visit", "site visit",
)

QUESTION_CUES_RE = re.compile(
    r"\b(where|which|why|who|whom|whose|when|how|what|can\s+i|can\s+we|is\s+there|are\s+there|will\s+there|will\s+we|will\s+i|do\s+you|do\s+we|could\s+you)\b",
    re.IGNORECASE,
)

ATTRIBUTE_INQUIRY_RE = re.compile(
    r"\b(pickup|pick\s*up|drop|drop\s*off|dropoff|hotel|hotels|stay|room|rooms|food|meal|meals|flight|flights|airfare|train|ticket|transport|cab|vehicle|refund|cancel|cancellation|guide|driver|timing|timings|rate|interest|emi|eligibility|bhk|sqft|possession|rera|amenities|maintenance|tenure|fees|charges)\b",
    re.IGNORECASE,
)


def _db():
    import database as db
    return db


def _state():
    try:
        from services import state_manager
    except ImportError:
        from backend.services import state_manager
    return state_manager


def _sender():
    try:
        from services import whatsapp_sender
    except ImportError:
        from backend.services import whatsapp_sender
    return whatsapp_sender


def _normalize(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(
        r"[\u2190-\u21FF\u2300-\u27BF\uFE0E\uFE0F\U0001F000-\U0001FAFF]", " ",
        str(text),
    )
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _slugify(text: str) -> str:
    clean = _normalize(text)
    return re.sub(r"\s+", "_", clean)[:40]


def get_tenant_vertical(tenant_id: str = None) -> str:
    """Resolve the industry vertical for a tenant with fallback to DB."""
    try:
        from shared.tenancy.loader import get_tenant_profile
        profile = get_tenant_profile(tenant_id)
        if profile and profile.vertical:
            return profile.vertical
    except Exception:
        # deliberate: resolver chain — fall through to the next source.
        logger.debug("TENANT_VERTICAL_RESOLVE_STEP_SKIPPED | source=profile tenant=%s", tenant_id, exc_info=True)
    try:
        from shared.tenancy import store
        rec = store.get_tenant(tenant_id)
        if rec and rec.get("vertical"):
            return rec["vertical"]
    except Exception:
        # deliberate: resolver chain — fall through to the next source.
        logger.debug("TENANT_VERTICAL_RESOLVE_STEP_SKIPPED | source=store tenant=%s", tenant_id, exc_info=True)
    return "generic"


def get_vertical_offerings_button(tenant_id: str = None) -> Optional[Dict[str, str]]:
    """Return vertical-tailored button spec (id, title) if tenant has active products."""
    db = _db()
    count = db.count_products(active_only=True, tenant_id=tenant_id)
    if count == 0:
        return None
    vert = get_tenant_vertical(tenant_id)
    return {
        "id": VERTICAL_ACTION_IDS.get(vert, VERTICAL_ACTION_IDS["generic"]),
        "title": VERTICAL_BUTTON_TITLES.get(vert, VERTICAL_BUTTON_TITLES["generic"])[:20],
    }


def _brand_name(tenant_id: str) -> str:
    try:
        from shared.tenancy.loader import get_tenant_profile
        profile = get_tenant_profile(tenant_id)
        if profile and profile.brand and profile.brand.name:
            return profile.brand.name
    except Exception:
        # deliberate: brand name is cosmetic — the vertical-based
        # fallback below is used.
        logger.debug("BRAND_NAME_LOOKUP_SKIPPED | tenant=%s", tenant_id, exc_info=True)
    vert = get_tenant_vertical(tenant_id)
    if vert == "real_estate":
        return "Properties"
    elif vert == "tours_travel":
        return "Tour Packages"
    elif vert == "banking":
        return "Banking Products"
    return "Our Offerings"


# ── Context Persistence ───────────────────────────────────────────────────────

def _product_attrs(product: dict) -> dict:
    attrs = product.get("attrs_json") or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except Exception:
            attrs = {}
    return attrs if isinstance(attrs, dict) else {}


def remember_product(wa_id: str, product: dict) -> None:
    """Remember the offering the user is viewing (for CTA + FAQ follow-ups)."""
    if not product:
        return
    attrs = _product_attrs(product)
    payload = {
        "id": product.get("id"),
        "name": product.get("name"),
        "category": product.get("category", ""),
        "price": product.get("price", ""),
        "tenant_id": product.get("tenant_id"),
        "destination": str(attrs.get("destination") or ""),
    }
    sm = _state()
    try:
        sm.ensure_conversation_row(wa_id)

        def apply(ctx):
            ctx["pending_product"] = payload
            # Mirror for FAQ paths that still read pending_package.
            ctx["pending_package"] = {
                "id": payload.get("id"),
                "name": payload.get("name") or "",
                "destination": payload.get("destination") or "",
            }

        sm.update_context_with_retry(wa_id, apply)
    except Exception as e:
        logger.warning("could not remember pending product for %s: %s", wa_id, e)


def pending_product(wa_id: str) -> dict:
    sm = _state()
    try:
        ctx, _version = sm.read_user_context(wa_id)
    except Exception:
        return {}
    p = ctx.get("pending_product")
    return p if isinstance(p, dict) else {}


def remember_offerings_page(wa_id: str, page: int, tenant_id: str = None) -> None:
    sm = _state()
    try:
        sm.ensure_conversation_row(wa_id)

        def apply(ctx):
            ctx["offerings_browse"] = {
                "page": int(page or 0),
                "tenant_id": tenant_id or "",
            }

        sm.update_context_with_retry(wa_id, apply)
    except Exception as e:
        logger.debug("could not remember offerings page for %s: %s", wa_id, e)


def offerings_browse_state(wa_id: str) -> dict:
    sm = _state()
    try:
        ctx, _version = sm.read_user_context(wa_id)
    except Exception:
        return {}
    browse = ctx.get("offerings_browse")
    return browse if isinstance(browse, dict) else {}


def clear_offerings_context(wa_id: str) -> None:
    """Clear pending offering + browse page (greeting / main menu / new browse)."""
    sm = _state()
    try:
        sm.ensure_conversation_row(wa_id)

        def apply(ctx):
            ctx.pop("pending_product", None)
            ctx.pop("pending_package", None)
            ctx.pop("offerings_browse", None)

        sm.update_context_with_retry(wa_id, apply)
    except Exception as e:
        logger.debug("could not clear offerings context for %s: %s", wa_id, e)


# ── Product Formatting ───────────────────────────────────────────────────────

def product_card(product: dict, vertical: str = "generic") -> str:
    """Format a human-readable WhatsApp detail card for any product/property."""
    name = product.get("name") or "Offering"
    category = product.get("category") or ""
    price = str(product.get("price") or "").strip()
    desc = product.get("description") or product.get("short_description") or ""
    attrs = product.get("attrs_json") or {}
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except Exception:
            attrs = {}

    icon = "📋"
    if vertical == "tours_travel":
        icon = "🎒"
    elif vertical == "real_estate":
        icon = "🏢"
    elif vertical == "banking":
        icon = "🏦"
    elif vertical == "finance":
        icon = "💼"
    elif vertical == "it_software":
        icon = "💻"

    lines = [f"{icon} *{name}*", ""]

    if category:
        lines.append(f"📁 *Category:* {category}")

    # Vertical-specific metadata
    if vertical == "real_estate":
        city = attrs.get("city") or attrs.get("location") or attrs.get("locality")
        if city:
            lines.append(f"📍 *Location:* {city}")
        config = attrs.get("configuration") or attrs.get("bhk") or attrs.get("property_type")
        if config:
            lines.append(f"📐 *Configuration:* {config}")
        carpet = attrs.get("carpet_area") or attrs.get("super_builtup_area") or attrs.get("area")
        if carpet:
            lines.append(f"📏 *Area / Size:* {carpet}")
        possession = attrs.get("possession") or attrs.get("possession_date")
        if possession:
            lines.append(f"⏳ *Possession:* {possession}")
        rera = attrs.get("rera_id") or attrs.get("rera")
        if rera:
            lines.append(f"📜 *RERA Reg:* `{rera}`")
    elif vertical == "tours_travel":
        dest = attrs.get("destination")
        if dest:
            lines.append(f"🗺️ *Destination:* {dest}")
        dur = attrs.get("duration")
        if dur:
            lines.append(f"⏱️ *Duration:* {dur}")
    elif vertical == "banking":
        interest = attrs.get("interest_rate") or attrs.get("rate")
        if interest:
            lines.append(f"📈 *Interest Rate / Charges:* {interest}")
        elig = attrs.get("eligibility") or attrs.get("min_balance")
        if elig:
            lines.append(f"✅ *Eligibility / Balance:* {elig}")

    # Pricing
    if price and price != "0":
        lines.append(f"💰 *Price / Starting Rate:* {price}")
    else:
        if vertical == "real_estate":
            lines.append("💰 *Pricing:* Contact for current price list & unit cost sheet")
        elif vertical == "banking":
            lines.append("💰 *Charges:* Nil / As per bank guidelines")
        else:
            lines.append("💰 *Pricing:* Custom quote based on requirements")

    # Description
    if desc:
        lines.extend(["", f"📋 *Overview:*\n{desc}"])

    # Inclusions / Amenities / Highlights
    highlights = attrs.get("amenities") or attrs.get("features") or attrs.get("includes")
    if highlights:
        lines.append("\n✨ *Key Highlights & Amenities:*")
        parts = re.split(r"[\n\r]+|(?<=\s)•", str(highlights))
        for p in parts[:10]:
            item = p.strip(" \t\r\n•-*")
            if item:
                lines.append(f"• {item[:150]}")

    # Itinerary if travel
    itinerary = attrs.get("itinerary")
    if itinerary and vertical == "tours_travel":
        lines.append("\n🗓️ *Itinerary Overview:*")
        text = str(itinerary).replace("\r\n", "\n")
        text = re.sub(r"(?<!^)(?=Day\s*\d+\s*:)", "\n", text)
        for part in text.split("\n")[:10]:
            l = part.strip(" \t\r\n•-*")
            if l:
                lines.append(f"• {l[:200]}")

    card = "\n".join(lines).strip()
    if len(card) > CARD_MAX_CHARS:
        card = card[:CARD_MAX_CHARS].rsplit("\n", 1)[0]
    return card


# ── Interactive List Menus ───────────────────────────────────────────────────

def _unique_categories(categories: list) -> list:
    """Dedupe category labels that differ only by capitalisation (stable order)."""
    seen = set()
    out = []
    for cat in categories or []:
        key = (cat or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((cat or "").strip())
    return out


def _more_title(vertical: str) -> str:
    return VERTICAL_MORE_TITLES.get(vertical, VERTICAL_MORE_TITLES["generic"])[:ROW_TITLE_MAX]


def _row_description(product: dict, vertical: str) -> str:
    """Compact list subtitle: vertical attrs when present, else short_description."""
    attrs = _product_attrs(product)
    bits: list[str] = []
    if vertical == "tours_travel":
        for key in ("destination", "duration"):
            val = str(attrs.get(key) or "").strip()
            if val:
                bits.append(val)
    elif vertical == "real_estate":
        for key in ("city", "location", "locality", "configuration", "bhk", "property_type"):
            val = str(attrs.get(key) or "").strip()
            if val and val not in bits:
                bits.append(val)
            if len(bits) >= 2:
                break
    elif vertical == "banking":
        for key in ("interest_rate", "rate", "eligibility"):
            val = str(attrs.get(key) or "").strip()
            if val:
                bits.append(val)
                break
    if bits:
        return " • ".join(bits)[:ROW_DESC_MAX]
    desc = str(product.get("short_description") or product.get("description") or "").strip()
    return (desc or "Tap to view full details")[:ROW_DESC_MAX]


def _sorted_products(products: list) -> list:
    """DB / onboarding order: ascending product id."""
    return sorted(products or [], key=lambda p: int(p.get("id") or 0))


def _page_slice(products: list, page: int) -> tuple[list, bool, int]:
    """Return (page_rows, has_more, next_page_index) for WhatsApp list menus."""
    page = max(0, int(page or 0))
    start = page * PAGE_ITEM_SLOTS
    if start >= len(products):
        return [], False, page
    remaining = products[start:]
    if len(remaining) <= MAX_LIST_ROWS:
        return remaining, False, page
    return remaining[:PAGE_ITEM_SLOTS], True, page + 1


def _list_section_title(vertical: str) -> str:
    return {
        "tours_travel": "Tour packages",
        "real_estate": "Properties",
        "banking": "Products",
        "finance": "Services",
        "it_software": "Services",
        "ecommerce": "Products",
        "generic": "Offerings",
    }.get(vertical, "Offerings")[:SECTION_TITLE_MAX]


def _list_body_text(vertical: str, total: int) -> str:
    noun = {
        "tours_travel": "packages",
        "real_estate": "properties",
        "banking": "products",
        "finance": "services",
        "it_software": "services",
        "ecommerce": "products",
    }.get(vertical, "offerings")
    if total == 1:
        return f"We have 1 {noun[:-1] if noun.endswith('s') else noun}. Tap to see details."
    return f"We have {total} {noun}. Tap one to see photo and details."


def build_offerings_list_sections(
    products: list,
    vertical: str,
    page: int = 0,
) -> tuple[list, int, bool]:
    """Build WhatsApp list sections for one page of offerings (+ optional More)."""
    page_rows, has_more, next_page = _page_slice(products, page)
    rows = []
    for product in page_rows:
        pid = product.get("id")
        name = (product.get("name") or "Offering").strip() or "Offering"
        rows.append({
            "id": f"{PROD_ROW_PREFIX}{pid}",
            "title": name[:ROW_TITLE_MAX],
            "description": _row_description(product, vertical),
        })
    if has_more:
        rows.append({
            "id": f"{MORE_ROW_PREFIX}{next_page}",
            "title": _more_title(vertical),
            "description": "See the next page",
        })
    if not rows:
        return [], page, False
    sections = [{
        "title": _list_section_title(vertical),
        "rows": rows,
    }]
    return sections, page, has_more


def _tenant_website(tenant_id: str = None) -> str:
    try:
        from shared.tenancy.loader import get_tenant_profile
        profile = get_tenant_profile(tenant_id)
        site = getattr(getattr(profile, "brand", None), "website", None) or ""
        site = str(site).strip()
        if site.startswith(("http://", "https://")):
            return site
    except Exception:
        # deliberate: website is cosmetic — empty string is returned.
        logger.debug("TENANT_WEBSITE_LOOKUP_SKIPPED | tenant=%s", tenant_id, exc_info=True)
    return ""


def offering_cta_url(product: dict, tenant_id: str = None) -> str:
    """Resolve the single CTA URL for an offering card (product attrs → brand site)."""
    attrs = _product_attrs(product or {})
    for key in (
        "cta_url", "website", "url", "booking_url", "link", "product_url",
        "page_url", "learn_more_url",
    ):
        raw = attrs.get(key) or (product or {}).get(key)
        val = str(raw or "").strip()
        if val.startswith(("http://", "https://")):
            return val
    return _tenant_website(tenant_id)


def _cta_card_body(product: dict, vertical: str) -> str:
    """Description for the interactive body (WhatsApp max ~1024)."""
    card = product_card(product, vertical=vertical)
    if len(card) <= 1000:
        return card
    # Prefer name + short overview so the image header stays the hero.
    name = (product.get("name") or "Offering").strip()
    desc = (
        product.get("short_description")
        or product.get("description")
        or _row_description(product, vertical)
        or ""
    ).strip()
    body = f"*{name}*\n\n{desc}".strip()
    return body[:1000]


async def send_offering_cta_card(
    wa_id: str, product: dict, tenant_id: str = None,
) -> bool:
    """Send one offering as send2 interactive ``cta_url`` (image header + description).

    Brochure PDFs are NOT attached here — ask for brochure separately.
    Reply-button enquire CTAs are not mixed into this payload (send2 rule).
    """
    remember_product(wa_id, product)
    sender = _sender()
    vertical = get_tenant_vertical(tenant_id)
    name = (product.get("name") or "").strip() or "Offering"
    body = _cta_card_body(product, vertical)
    cta_url = offering_cta_url(product, tenant_id)
    display = VERTICAL_CTA_URL_LABELS.get(vertical, VERTICAL_CTA_URL_LABELS["generic"])[:20]
    brand = _brand_name(tenant_id)

    try:
        from services.package_flow import package_media
    except ImportError:
        from backend.services.package_flow import package_media
    media_url, media_kind = package_media(product)
    # Explore/CTA cards: images only. Uploaded brochure PDFs are sent only when
    # the user asks for a brochure/PDF — never auto-attached to offering cards.
    image_url = media_url if media_kind == "image" and media_url else ""

    if cta_url:
        ok = await sender.send_cta_url_message(
            wa_id,
            body=body,
            cta_url=cta_url,
            display_text=display,
            image_url=image_url or None,
            footer_text=brand[:60] or " ",
            tenant_id=tenant_id,
        )
        logger.info(
            "OFFERING_CTA_SENT | wa_id=%s | product=%s | has_image=%s | ok=%s",
            wa_id, name, bool(image_url), ok,
        )
        return bool(ok)

    # No URL available — fall back to plain image + text (still no brochure).
    if image_url:
        try:
            await sender.send_image_message(wa_id, image_url, caption=name)
        except Exception as e:
            logger.warning("OFFERING_IMAGE_FALLBACK_FAILED | wa_id=%s | err=%s", wa_id, e)
    return bool(await sender.send_text_message(wa_id, body))


async def send_offerings_list(
    wa_id: str,
    tenant_id: str = None,
    category: str = None,
    page: int = 0,
    *,
    send_brochure: bool = False,
) -> bool:
    """Send a WhatsApp interactive **list** of offerings (products / properties / services).

    User taps a row → ``send_product_details`` sends image + text + URL button.
    Brochure PDFs are never attached here. ``send_brochure`` is ignored (compat).
    """
    db = _db()
    sender = _sender()
    vert = get_tenant_vertical(tenant_id)
    page = max(0, int(page or 0))

    if category:
        products = db.list_products(
            category=category, active_only=True, limit=200, tenant_id=tenant_id,
        )
    else:
        products = db.list_products(active_only=True, limit=200, tenant_id=tenant_id)
    products = _sorted_products(products)

    if not products:
        logger.info(
            "send_offerings_list: no active products for tenant %s (cat=%s)",
            tenant_id, category,
        )
        await sender.send_text_message(wa_id, EMPTY_OFFERINGS_REPLY)
        return True

    sections, page_idx, has_more = build_offerings_list_sections(products, vert, page)
    if not sections:
        await sender.send_text_message(wa_id, EMPTY_OFFERINGS_REPLY)
        return True

    brand = _brand_name(tenant_id)
    header = brand[:60] or _list_section_title(vert)
    body = _list_body_text(vert, len(products))
    button = LIST_BUTTON_TEXT.get(vert, LIST_BUTTON_TEXT["generic"])[:20]

    ok = await sender.send_list_menu(
        wa_id,
        header=header,
        body=body,
        button_text=button,
        sections=sections,
        tenant_id=tenant_id,
    )
    remember_offerings_page(wa_id, page_idx, tenant_id=tenant_id)
    logger.info(
        "OFFERINGS_LIST_SENT | wa_id=%s | tenant=%s | page=%s | items=%s | has_more=%s | ok=%s",
        wa_id, tenant_id, page_idx, len(products), has_more, ok,
    )
    return bool(ok)


async def send_product_details(wa_id: str, product: dict, tenant_id: str = None) -> bool:
    """After a list tap: send interactive image + text + URL button for one offering."""
    return await send_offering_cta_card(wa_id, product, tenant_id=tenant_id)


async def _start_enquiry(wa_id: str, product: Optional[dict], tenant_id: str) -> dict:
    """Launch the lead enquiry flow seeded with the product/property name."""
    try:
        from services import flow_runner
    except ImportError:
        from backend.services import flow_runner

    vertical = get_tenant_vertical(tenant_id)
    # Match the tenant's primary lead flow
    flow_name = "book_callback"
    if vertical == "tours_travel":
        flow_name = "book_package"
    elif vertical == "banking":
        flow_name = "enquire"

    seed = {}
    if product:
        p_name = product.get("name") or ""
        seed["product"] = p_name
        seed["package"] = p_name
        seed["property"] = p_name

    outcome = flow_runner.start_flow(
        wa_id, flow_name, tenant_id=tenant_id, seed=seed,
    )
    if not outcome.get("handled"):
        # Fallback to book_callback
        outcome = flow_runner.start_flow(
            wa_id, "book_callback", tenant_id=tenant_id, seed=seed,
        )

    if not outcome.get("handled"):
        logger.warning("enquiry flow not started for %s: %s", wa_id, outcome.get("error"))
        return {"handled": False, "action": "enquiry", "error": outcome.get("error")}

    sender = _sender()
    for msg in outcome.get("messages") or []:
        if isinstance(msg, dict) and msg.get("type") == "buttons":
            await sender.send_button_message(
                wa_id, msg.get("text", ""), msg.get("buttons", []), tenant_id=tenant_id,
            )
        else:
            await sender.send_text_message(wa_id, msg)

    return {
        "handled": True,
        "action": "enquiry",
        "product": product.get("name") if product else "",
    }


# ── Matchers ─────────────────────────────────────────────────────────────────

def _has_enquiry_verb(text: str) -> bool:
    needle = f" {_normalize(text)} "
    return any(f" {v}" in needle or needle.endswith(f" {v}") for v in ENQUIRY_VERBS)


def _is_enquiry_button_tap(text: str) -> bool:
    return _normalize(text) in ENQUIRY_BUTTON_ALIASES


_OFFERING_MENU_IDS = {
    "browse_offerings", "menu_offerings", "menu_packages", "menu_tour_packages",
    "menu_destinations", "menu_products", "menu_properties", "menu_projects",
    "menu_services", "menu_banking_products", "explore_properties",
}
# Intents on the *main* browse row only (not Cards/Loans/Technologies sub-rows).
_OFFERING_MENU_INTENTS = {
    "offerings", "package_details", "service_enquiry",
    "property_listings", "destination_details",
}
# Feature flags on the main browse row (not brochure_pdf / offering_details sub-rows).
_OFFERING_FEATURES = {
    "offerings", "products", "services", "products_schemes",
}
# Cross-vertical catalogue phrases that arrive as list-row titles from send2.
_OFFERING_TITLE_PHRASES = {
    # generic / onboarding
    "explore offerings", "browse offerings", "what we offer", "our offerings",
    "view offerings", "show offerings", "offerings",
    # ecommerce
    "view products", "our products", "show products", "products", "browse products",
    # tours
    "packages", "our packages", "tour packages", "holiday packages",
    "show packages", "browse packages", "destinations",
    # real estate
    "explore properties", "view properties", "our properties", "properties",
    "browse properties", "property list", "featured properties",
    # it / finance
    "our services", "services", "show services", "browse services", "solutions",
    # banking
    "banking products", "bank products",
}


def _is_offerings_menu_button(button) -> bool:
    """True for the tenant's main offerings/products/services/properties browse row."""
    if getattr(button, "enabled", True) is False:
        return False
    # Never treat brochure/PDF rows as image+text browse.
    intent = (getattr(button, "intent", None) or "").strip()
    if intent in ("brochure_request", "catalogue_request", "new_arrivals"):
        return False
    feature = (getattr(button, "requires_feature", None) or "").strip()
    bid = (getattr(button, "id", None) or "").strip()
    if bid in _OFFERING_MENU_IDS:
        return True
    if intent in _OFFERING_MENU_INTENTS:
        return True
    if feature in _OFFERING_FEATURES:
        return True
    return False


def _offerings_button_keys(tenant_id: str) -> set:
    """Ids and titles of the profile menu row that opens the offerings catalogue.

    Works for every vertical: ecommerce View Products, tours Packages,
    real-estate Explore Properties, IT/finance Our Services, banking
    Banking Products, and onboarding ``browse_offerings`` / Explore Offerings.
    send2 delivers the tap as the plain title, so titles must be matched.
    """
    keys: set = set()
    try:
        from shared.tenancy.loader import get_tenant_profile
        profile = get_tenant_profile(tenant_id)
        buttons = getattr(getattr(profile, "menu", None), "buttons", None) or []
    except Exception as e:
        logger.debug("could not read profile menu for %s: %s", tenant_id, e)
        return keys
    for button in buttons:
        if not _is_offerings_menu_button(button):
            continue
        bid = (getattr(button, "id", None) or "").strip()
        title = (getattr(button, "title", None) or "").strip()
        if bid:
            keys.add(bid)
            keys.add(_normalize(bid))
        if title:
            keys.add(title)
            keys.add(_normalize(title))
    return keys


def is_offerings_request(tenant_id: str, text: str, pressed_id: str = "") -> bool:
    """True when the user tapped/typed the tenant's offerings browse button.

    Universal across verticals: any matched browse row shows image+text CTA
    cards for that tenant's active products.
    """
    raw_id = (pressed_id or "").strip()
    raw_text = (text or "").strip()

    # Brochure / PDF asks must never open Explore cards.
    try:
        try:
            from services.brochure_service import is_brochure_ask
        except ImportError:
            from backend.services.brochure_service import is_brochure_ask
        if is_brochure_ask(raw_text, button_id=raw_id, tenant_id=tenant_id):
            return False
    except Exception:
        # deliberate: the brochure-ask guard is best-effort — if it errors,
        # the request is treated as a possible offerings request.
        logger.debug("BROCHURE_ASK_CHECK_SKIPPED | tenant=%s", tenant_id, exc_info=True)

    if not raw_id and raw_text.startswith(
        (PROD_ROW_PREFIX, CAT_ROW_PREFIX, BOOK_PROD_PREFIX, "menu_", "browse_")
    ):
        raw_id = raw_text

    if not raw_id and raw_text.startswith("menu_"):
        raw_id = raw_text

    # Only the *current* tenant's browse row may open its catalogue. A global
    # phrase match used to treat an old list tap like "Our Services" as browse
    # for whatever brand is active — that feels like "one tap, many menus".
    profile_keys = _offerings_button_keys(tenant_id)
    if raw_id and (raw_id in profile_keys or _normalize(raw_id) in profile_keys):
        return True
    needle = _normalize(raw_text)
    if needle and (raw_text in profile_keys or needle in profile_keys):
        return True

    # Fallback only when this tenant has no offerings browse row configured.
    if not profile_keys:
        if needle and needle in _OFFERING_TITLE_PHRASES:
            return True
        if raw_id and raw_id in _OFFERING_MENU_IDS:
            return True

    return False


def find_product(tenant_id: str, text: str, products: list = None) -> Optional[dict]:
    needle = _normalize(text)
    if not needle or len(needle) < 3:
        return None
    db = _db()
    if products is None:
        products = db.list_products(active_only=True, limit=100, tenant_id=tenant_id)
    if not products:
        return None

    # Exact match on full name
    for p in products:
        if needle == _normalize(p.get("name")):
            return p
    # Exact match on truncated row title
    for p in products:
        if needle == _normalize((p.get("name") or "")[:ROW_TITLE_MAX]):
            return p
    # Whole-word substring match inside sentence
    for p in products:
        title = _normalize((p.get("name") or "")[:ROW_TITLE_MAX])
        if len(title) >= 5 and re.search(rf"\b{re.escape(title)}\b", needle):
            return p
    return None


def _is_specific_product_question(text: str, product: dict) -> bool:
    if not text:
        return False
    raw = text.strip()
    has_qmark = "?" in raw
    has_attr = bool(ATTRIBUTE_INQUIRY_RE.search(raw))
    has_question_cue = bool(QUESTION_CUES_RE.search(raw))

    if has_attr:
        return True
    if has_qmark and has_question_cue:
        return True

    clean = _normalize(raw)
    p_title = _normalize(product.get("name") or "")
    words = clean.split()
    if len(words) > len(p_title.split()) + 3 and has_question_cue:
        return True

    return False


# ── Action Handler ───────────────────────────────────────────────────────────

async def handle_offerings_action(
    wa_id: str, text: str, tenant_id: str = None, pressed_id: str = ""
) -> Optional[dict]:
    """Unified handler for all product/property browsing across all tenant verticals.
    Returns None when the interaction is not about offerings so normal routing continues.
    """
    db = _db()
    raw_id = (pressed_id or "").strip()
    raw_text = (text or "").strip()

    if not raw_id and raw_text.startswith(
        (PROD_ROW_PREFIX, CAT_ROW_PREFIX, BOOK_PROD_PREFIX, MORE_ROW_PREFIX, "menu_", "browse_")
    ):
        raw_id = raw_text

    # 1. Product row tap from list menu (e.g. prod_71)
    if raw_id.startswith(PROD_ROW_PREFIX):
        pid_str = raw_id[len(PROD_ROW_PREFIX):]
        if pid_str.isdigit():
            prod = db.get_product(int(pid_str), tenant_id=tenant_id)
            if prod:
                await send_product_details(wa_id, prod, tenant_id=tenant_id)
                return {"handled": True, "action": "product_details", "product": prod.get("name")}

    # 1b. More pagination (id or vertical-aware title from send2)
    next_page = None
    if raw_id.startswith(MORE_ROW_PREFIX):
        suffix = raw_id[len(MORE_ROW_PREFIX):]
        if suffix.isdigit():
            next_page = int(suffix)
    if next_page is None:
        needle = _normalize(raw_text)
        vert = get_tenant_vertical(tenant_id)
        more_titles = {
            _normalize(_more_title(vert)),
            "more", "more packages", "more properties", "more products",
            "more services", "more offerings",
        }
        if needle in more_titles and offerings_browse_state(wa_id):
            browse = offerings_browse_state(wa_id)
            next_page = int(browse.get("page") or 0) + 1
    if next_page is not None:
        sent = await send_offerings_list(
            wa_id, tenant_id=tenant_id, page=next_page, send_brochure=False,
        )
        if sent:
            return {"handled": True, "action": "offerings_more", "page": next_page}
        return {"handled": True, "action": "offerings_more_empty", "page": next_page}

    # 2. Legacy category row tap (older menus) — still paginate products in that category
    if raw_id.startswith(CAT_ROW_PREFIX):
        cat_slug = raw_id[len(CAT_ROW_PREFIX):]
        categories = _unique_categories(
            db.list_product_categories(active_only=True, tenant_id=tenant_id)
        )
        matched_cat = None
        for cat in categories:
            if _slugify(cat) == cat_slug:
                matched_cat = cat
                break
        if matched_cat:
            sent = await send_offerings_list(
                wa_id, tenant_id=tenant_id, category=matched_cat, page=0, send_brochure=False,
            )
            if sent:
                return {"handled": True, "action": "category_products", "category": matched_cat}

    # 3. Enquiry / Site Visit button tap on a detail card
    if raw_id.startswith(BOOK_PROD_PREFIX):
        pid_str = raw_id[len(BOOK_PROD_PREFIX):]
        prod = db.get_product(int(pid_str), tenant_id=tenant_id) if pid_str.isdigit() else None
        return await _start_enquiry(wa_id, prod, tenant_id=tenant_id)

    if _is_enquiry_button_tap(raw_text):
        pending = pending_product(wa_id)
        prod = db.get_product(pending.get("id"), tenant_id=tenant_id) if pending.get("id") else None
        return await _start_enquiry(wa_id, prod, tenant_id=tenant_id)

    # 4. Catalog / offerings requested — always restart at page 1 (index 0)
    if is_offerings_request(tenant_id, raw_text, raw_id):
        clear_offerings_context(wa_id)
        sent = await send_offerings_list(
            wa_id, tenant_id=tenant_id, page=0, send_brochure=True,
        )
        if sent:
            return {"handled": True, "action": "offerings_list"}
        return None

    # 5. Free-text mention of a specific product / property
    prod = find_product(tenant_id, raw_text)
    if prod:
        if _has_enquiry_verb(raw_text):
            remember_product(wa_id, prod)
            return await _start_enquiry(wa_id, prod, tenant_id=tenant_id)
        if _is_specific_product_question(raw_text, prod):
            remember_product(wa_id, prod)
            logger.info(
                "PRODUCT_SPECIFIC_QUESTION | wa_id=%s | prod=%s | falling through to FAQ/RAG",
                wa_id, prod.get("name"),
            )
            return None
        await send_product_details(wa_id, prod, tenant_id=tenant_id)
        return {"handled": True, "action": "product_details", "product": prod.get("name")}

    return None


# Backward-compatibility alias
handle_package_action = handle_offerings_action
