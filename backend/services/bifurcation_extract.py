"""Deterministic text-extraction helpers for the auto-bifurcation pipeline.

Split out of services/auto_bifurcator.py (2026-10): this module owns the pure
functions — slug/menu/brand sanitizers, chunking, heuristic corpus extraction
and multi-pass merge. The AutoBifurcator class (document IO + LLM calls + DB
writes) lives in services/auto_bifurcator.py and re-exports everything here,
so `from services.auto_bifurcator import <helper>` keeps working.
"""

import json
import logging
import os
import re
import string
from typing import Any, Dict, List, Optional, Tuple

from shared.tenancy import schemas as tenancy_schemas
from shared.tenancy.defaults import get_vertical_defaults
from services.bifurcation_guards import (
    is_junk_offering_name,
    is_placeholder_brand_value,
    merge_brand_fill_empty,
    offering_matches_vertical,
)

try:
    from database import product_slug_taken
except ImportError:  # imported as part of the backend package
    from backend.database import product_slug_taken

logger = logging.getLogger("auto_bifurcator")

# Target vertical options supported by tenancy schema
VALID_VERTICALS = set(tenancy_schemas.VERTICALS)

# LLM extraction prompts live as plain-text templates so they can be reviewed
# and tweaked without touching code. Tokens are substituted with str.replace
# (the bodies contain raw JSON braces, so str.format is not usable).
_PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
_BIFURCATION_PROMPT_TEMPLATE: Optional[str] = None


def _bifurcation_prompt_template() -> str:
    """Load the bifurcation extraction prompt template (cached)."""
    global _BIFURCATION_PROMPT_TEMPLATE
    if _BIFURCATION_PROMPT_TEMPLATE is None:
        path = os.path.join(_PROMPT_DIR, "bifurcation_extract.txt")
        with open(path, encoding="utf-8") as fh:
            _BIFURCATION_PROMPT_TEMPLATE = fh.read()
    return _BIFURCATION_PROMPT_TEMPLATE


def _clean_text(text: str) -> str:
    """Normalize whitespace and strip non-printable characters."""
    if not text:
        return ""
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _slugify(text: str) -> str:
    """Produce clean lowercase slug."""
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "item"


def _ensure_free_slug(base: str, used_slugs: set, tenant_id: str = "") -> str:
    """Find a product slug not yet used by this tenant (and this batch).

    products.slug was UNIQUE across the whole table while the catalog is
    per-tenant, so a second tenant uploading "Personal Loan" collided with the
    first tenant's row and crashed the publish. The schema now enforces
    UNIQUE(tenant_id, slug); this keeps slugs distinct within the tenant and
    tolerates databases that predate the migration.
    """
    candidate = (base or "item").strip("-") or "item"
    n = 2
    while candidate in used_slugs or product_slug_taken(candidate, tenant_id or None):
        candidate = f"{base}-{n}"
        n += 1
    used_slugs.add(candidate)
    return candidate


def _menu_button_id(text: str) -> str:
    """WhatsApp menu button ids use underscores (match vertical defaults).

    ``_slugify`` collapses separators to hyphens, which made PDF extracts emit
    ``menu-products`` alongside default ``menu_products`` and doubled the menu.
    """
    raw = (text or "").strip()
    if not raw:
        return "menu_item"
    # Preserve already-normalized ids; only re-slugify free-form titles.
    if re.fullmatch(r"[a-zA-Z0-9_-]+", raw):
        return re.sub(r"[-_\s]+", "_", raw.lower()).strip("_") or "menu_item"
    return _slugify(raw).replace("-", "_") or "menu_item"


def _normalize_menu_button_key(value: str) -> str:
    """Compare button ids/titles ignoring hyphen vs underscore and case."""
    return re.sub(r"[-_\s]+", "_", (value or "").strip().casefold()).strip("_")


def _menu_items_from_vertical_defaults(vertical: str, *, limit: int = 10) -> List[Dict[str, Any]]:
    """Copy vertical default menu rows with real flow/intent (never requires_feature as action)."""
    try:
        from shared.tenancy.defaults import get_vertical_defaults

        defaults = list(
            (get_vertical_defaults(vertical) or {}).get("menu", {}).get("buttons") or []
        )
    except Exception:
        defaults = []

    items: List[Dict[str, Any]] = []
    for b in defaults[: max(0, limit)]:
        if not isinstance(b, dict):
            continue
        item: Dict[str, Any] = {
            "id": b.get("id", "menu_item"),
            "title": str(b.get("title", "Explore"))[:24],
            "description": str(b.get("description", "View options"))[:72],
            "section": str(b.get("section", "Explore"))[:24],
        }
        # Prefer flow, then intent — never treat a feature flag as the action.
        if b.get("flow"):
            item["flow"] = str(b["flow"])
            item["action"] = str(b["flow"])
        elif b.get("intent"):
            item["intent"] = str(b["intent"])
            item["action"] = str(b["intent"])
        else:
            item["action"] = "offerings"
        if b.get("requires_feature"):
            item["requires_feature"] = str(b["requires_feature"])
        if b.get("icon"):
            item["icon"] = b["icon"]
        items.append(item)
    return items


def _sanitize_menu_buttons(
    menu_items_raw: List[Any],
    *,
    vertical: str = "generic",
) -> List[Dict[str, Any]]:
    """Build unique menu.buttons: underscore ids, no title/id duplicates vs defaults."""
    defaults: List[Dict[str, Any]] = []
    try:
        from shared.tenancy.defaults import get_vertical_defaults
        from shared.tenancy.schemas import FEATURE_FLAGS

        defaults = list(
            (get_vertical_defaults(vertical) or {}).get("menu", {}).get("buttons") or []
        )
        feature_flag_names = set(FEATURE_FLAGS)
    except Exception:
        defaults = []
        feature_flag_names = set()

    default_by_norm_id: Dict[str, Dict[str, Any]] = {}
    default_by_title: Dict[str, Dict[str, Any]] = {}
    for b in defaults:
        if not isinstance(b, dict):
            continue
        bid = str(b.get("id") or "").strip()
        title = str(b.get("title") or "").strip()
        if bid:
            default_by_norm_id[_normalize_menu_button_key(bid)] = b
        if title:
            default_by_title[_normalize_menu_button_key(title)] = b

    menu_buttons: List[Dict[str, Any]] = []
    seen_ids: set = set()
    seen_titles: set = set()

    for i, item in enumerate(menu_items_raw or []):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or f"Option {i + 1}").strip()[:24]
        desc = (item.get("description") or "").strip()[:72]
        sec = (item.get("section") or "Explore").strip()[:24]
        action = str(
            item.get("action") or item.get("flow") or item.get("intent") or "faq"
        ).strip().lower()

        raw_id = item.get("id") or item.get("title") or f"opt_{i}"
        b_id = _menu_button_id(str(raw_id))
        title_key = _normalize_menu_button_key(title)
        id_key = _normalize_menu_button_key(b_id)

        # Prefer the vertical default id when title or normalized id already exists.
        matched = default_by_norm_id.get(id_key) or default_by_title.get(title_key)
        if matched and matched.get("id"):
            b_id = str(matched["id"])
            id_key = _normalize_menu_button_key(b_id)

        if id_key in seen_ids or (title_key and title_key in seen_titles):
            continue
        seen_ids.add(id_key)
        if title_key:
            seen_titles.add(title_key)

        button_dict: Dict[str, Any] = {
            "id": b_id,
            "title": title,
            "description": desc,
            "section": sec,
            "sort_order": len(menu_buttons) + 1,
            "enabled": True if item.get("enabled", True) else False,
        }
        if item.get("icon") or (matched and matched.get("icon")):
            button_dict["icon"] = item.get("icon") or matched.get("icon")

        # Explicit flow/intent win. Feature-flag "actions" inherit from the
        # matched vertical default so banking/finance/ecommerce keep real targets.
        if item.get("flow"):
            button_dict["flow"] = str(item["flow"])
        elif item.get("intent"):
            button_dict["intent"] = str(item["intent"])
        elif matched and (
            action in feature_flag_names
            or action in ("offerings", "catalog", "offering_details", "products", "services", "faq", "info")
        ):
            # Generic / feature-flag actions on a known default row: keep the
            # vertical's real target (or none, e.g. View Cart is feature-only).
            if matched.get("flow"):
                button_dict["flow"] = str(matched["flow"])
            elif matched.get("intent"):
                button_dict["intent"] = str(matched["intent"])
        # Map kit "action" labels onto schema fields (MenuButton forbids "action")
        elif action in ("offerings", "catalog", "offering_details", "products"):
            button_dict["intent"] = "offerings"
        elif action == "services":
            button_dict["intent"] = "services"
        elif action in ("technologies", "projects", "careers", "benefits"):
            button_dict["intent"] = action
        elif action in ("brochure", "brochure_request", "brochure_pdf"):
            button_dict["intent"] = "brochure_request"
        elif action in ("quote", "get_quote", "pricing"):
            button_dict["flow"] = "get_quote"
        elif action in ("book_callback", "callback", "appointment"):
            button_dict["flow"] = "book_callback"
        elif action in ("enquire", "inquire"):
            # Banking "Check Eligibility" etc. — must not become book_callback.
            button_dict["flow"] = "enquire"
        elif action in ("raise_ticket", "support", "ticket", "help"):
            button_dict["flow"] = "raise_ticket"
        elif action in ("faq", "info"):
            button_dict["intent"] = "faq" if b_id == "faq" else b_id
        else:
            button_dict["intent"] = b_id

        req = item.get("requires_feature") or (matched.get("requires_feature") if matched else None)
        if req:
            button_dict["requires_feature"] = str(req)

        menu_buttons.append(button_dict)

    return menu_buttons


def _sanitize_business_hours(raw_hours: Any) -> Dict[str, Any]:
    """Normalize kit/LLM hours into BusinessHours schema fields (no ``raw``)."""
    if not isinstance(raw_hours, dict):
        raw_hours = {}
    hours = {
        "timezone": str(raw_hours.get("timezone") or "Asia/Kolkata"),
        "always_open": bool(raw_hours.get("always_open", False)),
        "open": str(raw_hours.get("open") or "09:30"),
        "close": str(raw_hours.get("close") or "18:30"),
        "open_days": list(raw_hours.get("open_days") or [0, 1, 2, 3, 4, 5]),
    }
    if raw_hours.get("out_of_hours_message"):
        hours["out_of_hours_message"] = str(raw_hours["out_of_hours_message"])

    # Parse free-text like "09:30 AM - 06:30 PM (Mon-Sat)" from kit tables
    raw_label = str(raw_hours.get("raw") or "").strip()
    if raw_label:
        times = re.findall(r"(\d{1,2}:\d{2})\s*(AM|PM)?", raw_label, flags=re.IGNORECASE)
        if len(times) >= 2:
            def _to_24(t: str, meridiem: str) -> str:
                hh, mm = t.split(":")
                h = int(hh)
                m = meridiem.upper() if meridiem else ""
                if m == "PM" and h < 12:
                    h += 12
                if m == "AM" and h == 12:
                    h = 0
                return f"{h:02d}:{mm}"

            hours["open"] = _to_24(times[0][0], times[0][1])
            hours["close"] = _to_24(times[1][0], times[1][1])
        low = raw_label.lower()
        if "mon-sat" in low or "mon – sat" in low or "monday-saturday" in low:
            hours["open_days"] = [0, 1, 2, 3, 4, 5]
        elif "24/7" in low or "always" in low:
            hours["always_open"] = True
    # Ensure open_days are ints 0..6
    clean_days = []
    for d in hours["open_days"]:
        try:
            di = int(d)
        except Exception:
            continue
        if 0 <= di <= 6:
            clean_days.append(di)
    hours["open_days"] = clean_days or [0, 1, 2, 3, 4, 5]
    return hours


def _is_placeholder_brand_value(key: str, value: Any, tenant_id: str) -> bool:
    # Compatibility wrapper — real logic lives in bifurcation_guards.
    return is_placeholder_brand_value(key, value, tenant_id)


# ── Full-corpus helpers (kit-agnostic uploads) ──────────────────────────────
_CHUNK_CHARS = 14000
_CHUNK_OVERLAP = 600
_MAX_LLM_CHUNKS = 10
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s\-]?)?(?:\(?\d{2,5}\)?[\s\-]?)?\d{5,10}(?:[\s\-]?\d{3,6})?"
)
_URL_RE = re.compile(r"https?://[^\s\)\]\>\"']+|www\.[^\s\)\]\>\"']+", re.IGNORECASE)
_FAQ_BOLD_RE = re.compile(
    r"(?m)^\s*[-*]?\s*\*\*(.+?\?)\*\*\s*[-–—:]?\s*(.+?)(?=\n\s*[-*]?\s*\*\*|\n#{1,3}\s|\n---|\Z)",
    re.DOTALL,
)
_FAQ_QA_RE = re.compile(
    r"(?mi)^\s*(?:Q(?:uestion)?\s*[:.)]\s*)(.+?\?)\s*\n\s*(?:A(?:nswer)?\s*[:.)]\s*)(.+?)(?=\n\s*(?:Q(?:uestion)?\s*[:.)]|\n#{1,3}\s|\n---|\Z))",
    re.DOTALL,
)


def _chunk_text(text: str, size: int = _CHUNK_CHARS, overlap: int = _CHUNK_OVERLAP) -> List[str]:
    """Split long corpus into overlapping windows so nothing is dropped for the LLM."""
    text = text or ""
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n and len(chunks) < _MAX_LLM_CHUNKS:
        end = min(start + size, n)
        if end < n:
            # Prefer breaking on a paragraph / heading boundary
            window = text[start:end]
            br = max(window.rfind("\n## "), window.rfind("\n\n"), window.rfind("\n---"))
            if br > size // 3:
                end = start + br
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    # Tail remainder if we hit MAX_LLM_CHUNKS early — append a densified tail sample
    if chunks and start + overlap < n and len(chunks) >= _MAX_LLM_CHUNKS:
        tail = text[-size:].strip()
        if tail and tail not in chunks:
            chunks[-1] = chunks[-1] + "\n\n--- TAIL OF DOCUMENT ---\n" + tail
    return chunks


def _normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if len(re.sub(r"\D", "", digits)) < 10:
        return ""
    return raw.strip()


def _guess_vertical(text: str) -> str:
    sample = (text or "").lower()
    slug_hints = (
        ("it_software", "it_software"),
        ("software & it", "it_software"),
        ("cpaas", "it_software"),
        ("saas product", "it_software"),
        ("real_estate", "real_estate"),
        ("rera", "real_estate"),
        ("tours_travel", "tours_travel"),
        ("itinerary", "tours_travel"),
        ("ecommerce", "ecommerce"),
        ("add to cart", "ecommerce"),
        ("healthcare", "healthcare"),
        ("patient", "healthcare"),
        ("banking", "banking"),
        ("ifsc", "banking"),
        ("gst filing", "finance"),
        ("nbfc", "finance"),
    )
    for needle, slug in slug_hints:
        if needle in sample:
            return slug
    scores = {
        "it_software": sum(
            1
            for w in (
                "software", "devops", "tech stack", "fastapi", "react", "kubernetes",
                "flutter", "machine learning", "whatsapp business api", "cpaas", "saas",
            )
            if w in sample
        ),
        "real_estate": sum(1 for w in ("property", "bhk", "villa", "penthouse", "rera", "carpet area") if w in sample),
        "tours_travel": sum(1 for w in ("flight", "tour", "itinerary", "visa", "holiday") if w in sample),
        "ecommerce": sum(1 for w in ("sku", "cart", "warranty", "cod", "shipping") if w in sample),
        "healthcare": sum(1 for w in ("clinic", "doctor", "hospital", "consultation") if w in sample),
        "banking": sum(1 for w in ("credit card", "atm", "savings account", "loan emi") if w in sample),
        "finance": sum(1 for w in ("gst", "itr", "gold loan", "accounting") if w in sample),
    }
    best = max(scores.items(), key=lambda kv: kv[1])
    return best[0] if best[1] > 0 else "generic"


def _section_body(text: str, headings: Tuple[str, ...], max_chars: int = 4500) -> str:
    """Pull body text under the first matching markdown/plain heading."""
    if not text:
        return ""
    pattern = "|".join(re.escape(h) for h in headings)
    # Match: ## 4. Technologies / …  OR  ### Careers  OR  **Why choose…**
    # Stop only at next H2 (##) / H1 or thematic break — keep ### subsections.
    m = re.search(
        rf"(?im)^(?:#{{1,3}}\s*|\*\*)?(?:\d+[.)]\s*)?(?:{pattern})[^\n]*?(?:\*\*)?\s*$"
        rf"\n+(.*?)(?=\n##\s|\n#\s|\n---\s*\n|\Z)",
        text,
        flags=re.DOTALL,
    )
    if not m:
        return ""
    body = _clean_text(m.group(1))
    lines = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("![") and "](" in s:
            continue
        lines.append(line)
    body = "\n".join(lines).strip()
    return body[:max_chars]


def _extract_markdown_table_rows(text: str) -> List[List[str]]:
    """Parse simple GitHub-flavored markdown tables into row lists."""
    rows: List[List[str]] = []
    for block in re.split(r"\n{2,}", text or ""):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip().startswith("|")]
        if len(lines) < 2:
            continue
        parsed = []
        for ln in lines:
            if re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?$", ln):
                continue
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if cells:
                parsed.append(cells)
        if len(parsed) >= 2:
            rows.extend(parsed)
    return rows


def heuristic_extract_corpus(text: str, tenant_id: str = "") -> Dict[str, Any]:
    """Deterministic extraction from any free-form / scraped document (no kit needed)."""
    text = text or ""
    brand: Dict[str, str] = {}

    # Company name: first H1 or leading title line
    h1 = re.search(r"(?m)^#\s+(.+?)(?:\s*[–—-].*)?$", text)
    if h1:
        name = re.sub(r"\s*[–—:].*$", "", h1.group(1)).strip()
        name = re.sub(r"\b(website knowledge base|knowledge base|onboarding)\b", "", name, flags=re.I).strip(" –—-|")
        if name and len(name) > 2:
            brand["name"] = name[:80]

    tag = re.search(r"(?im)(?:tagline|motto)\s*[:\-–]\s*\*?([^*\n]{8,120})\*?", text)
    if tag:
        brand["tagline"] = tag.group(1).strip(" *")
    else:
        italic = re.search(r"(?m)^\s*\*([^*\n]{12,120})\*\s*$", text)
        if italic:
            brand["tagline"] = italic.group(1).strip()

    emails = _EMAIL_RE.findall(text)
    # Prefer sales/support/info over personal
    ranked_emails = sorted(
        set(emails),
        key=lambda e: (
            0 if re.search(r"^(sales|support|info|hello|contact|admin)@", e, re.I) else 1,
            len(e),
        ),
    )
    if ranked_emails:
        brand["support_email"] = ranked_emails[0]

    phones: List[str] = []
    for m in re.finditer(
        r"(?:\+91[\s\-]?)?(?:0)?[6-9]\d{9}|\+\d{1,3}[\s\-]?\d{6,14}",
        text,
    ):
        p = _normalize_phone(m.group(0))
        if p and p not in phones:
            phones.append(p)
    if phones:
        brand["support_phone"] = phones[0]

    urls = _URL_RE.findall(text)
    site = ""
    for u in urls:
        low = u.lower().rstrip(").,;")
        if any(x in low for x in ("facebook", "instagram", "linkedin", "twitter", "youtube", "wa.me")):
            continue
        if "/assets/" in low or low.endswith((".webp", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".avif")):
            continue
        site = u.rstrip(").,;")
        break
    if site:
        brand["website"] = site if site.startswith("http") else f"https://{site}"

    if brand.get("name"):
        brand["bot_name"] = f"{brand['name'].split()[0]} Assistant"
        brand["signature"] = f"{brand['name']} — At your service."

    # Hours
    hours_m = re.search(
        r"(?i)(?:business\s+hours|working\s+hours|office\s+hours|timing)\s*[:\-–]\s*([^\n]{6,80})",
        text,
    )
    always = bool(re.search(r"\b24\s*/\s*7\b|\balways\s+open\b", text, re.I))
    business_hours: Dict[str, Any] = {
        "timezone": "Asia/Kolkata",
        "open": "09:30",
        "close": "18:30",
        "open_days": [0, 1, 2, 3, 4, 5],
        "always_open": always,
    }
    if hours_m:
        business_hours["raw"] = hours_m.group(1).strip()

    # FAQs
    faqs: List[Dict[str, str]] = []
    seen_q: set = set()
    _faq_noise = re.compile(
        r"(?i)^(source|note|pages?|http|www\.|menu:|read more|click|image)",
    )

    def _add_faq(q: str, a: str, category: str = "General") -> None:
        q = _clean_text(q).strip(" *")
        a = _clean_text(a)
        if len(q) < 12 or len(a) < 8:
            return
        if not q.endswith("?"):
            return
        if _faq_noise.search(q) or "http" in q.lower():
            return
        if a.lower().startswith("http") and len(a) < 80:
            return
        key = q.lower()
        if key in seen_q:
            return
        seen_q.add(key)
        faqs.append({"question": q[:300], "answer": a[:2000], "category": category})

    for q, a in _FAQ_BOLD_RE.findall(text):
        _add_faq(q, a)
    for q, a in _FAQ_QA_RE.findall(text):
        _add_faq(q, a)
    for m in re.finditer(
        r"(?m)^\s*[-*]\s+\*\*(.+?\?)\*\*\s*(.+)$",
        text,
    ):
        _add_faq(m.group(1), m.group(2))

    # Info-page intents from common headings
    info_specs = [
        ("technologies", ("technologies", "tech stack", "our stack", "technologies / development")),
        ("projects", ("projects", "portfolio", "case studies", "our work", "trusted partners", "impact numbers")),
        ("careers", ("careers", "open positions", "hiring", "jobs", "life @ leeway", "life @")),
        ("benefits", ("why choose", "why us", "benefits", "why work with", "why choose leeway")),
    ]
    intents: List[Dict[str, Any]] = []
    for name, heads in info_specs:
        body = _section_body(text, heads)
        if body and len(body) > 40:
            intents.append({
                "name": name,
                "description": name.replace("_", " ").title(),
                "keywords": [name, name.replace("_", " ")],
                "examples": [f"tell me about {name}", f"what about your {name}"],
                "answer": body[:3500],
                "flow": None,
                "enabled": True,
            })

    # Offerings from markdown product/service tables and ### headings under Services
    offerings: List[Dict[str, Any]] = []
    seen_off: set = set()

    _offering_blocklist = {
        "need", "solutions", "category", "product", "role", "department", "office",
        "location", "client", "logo", "frontend", "backend", "ai & ml", "mobile",
        "databases and data platforms", "certified security and quality",
        "proven at scale", "government-grade trust", "true end-to-end delivery",
    }

    def _add_offering(name: str, category: str, desc: str, media: str = "") -> None:
        name = _clean_text(name).strip(" *")
        if len(name) < 3:
            return
        key = name.lower()
        if key in seen_off or key in _offering_blocklist or key.startswith("product"):
            return
        # Skip job-title / office-looking rows without a product signal
        if category and category.lower() in ("sales", "tester", "account", "hr", "manager", "design", "support", "mumbai", "bhavnagar", "delhi/ncr"):
            return
        seen_off.add(key)
        offerings.append({
            "name": name[:120],
            "category": (category or "Services")[:80],
            "price": "",
            "short_description": (desc or "")[:180],
            "description": (desc or name)[:2000],
            "unit": "package",
            "media_url": media or "",
            "media_type": "image" if media else "",
            "attrs": {},
        })

    def _skip_table_rows(start_idx: int) -> int:
        j = start_idx + 1
        header_words = {
            "product", "service", "name", "package", "role", "need", "client",
            "office", "category", "what it does",
        }
        while j < len(md_rows):
            first = (md_rows[j][0] if md_rows[j] else "").strip().lower()
            row_l = " ".join(c.lower() for c in md_rows[j])
            if first in header_words or (
                any(k in row_l for k in ("what it does", "department", "experience", "address"))
                and first in header_words.union({"product", "role", "office", "need"})
            ):
                break
            # New header if most cells look like labels
            if first in header_words:
                break
            j += 1
        return j

    md_rows = _extract_markdown_table_rows(text)
    i = 0
    while i < len(md_rows):
        row = md_rows[i]
        header_l = " ".join(c.lower() for c in row)
        first_h = (row[0] if row else "").strip().lower()
        is_career = "role" in header_l and ("department" in header_l or "experience" in header_l)
        is_need_matrix = first_h == "need" or ("need" in header_l and "solution" in header_l)
        is_logo_table = "client" in header_l and "logo" in header_l
        is_office_table = first_h == "office" or ("office" in header_l and "address" in header_l)
        is_tech_table = first_h == "category" and "technologies" in header_l
        is_productish = any(
            k in header_l for k in ("product", "what it does", "service name", "package name", "sku")
        )
        if is_career or is_need_matrix or is_logo_table or is_office_table or is_tech_table:
            i = _skip_table_rows(i)
            continue
        if is_productish:
            headers = [c.lower() for c in row]
            i += 1
            while i < len(md_rows):
                data = md_rows[i]
                first = (data[0] if data else "").strip().lower()
                if first in ("product", "service", "name", "package", "role", "need", "office", "category"):
                    break
                name = data[0] if data else ""
                desc = data[1] if len(data) > 1 else ""
                cat = "Services"
                if "category" in headers:
                    try:
                        ci = headers.index("category")
                        cat = data[ci] if ci < len(data) else cat
                    except Exception:
                        # deliberate: keep the default "Services" category
                        # when the category column cannot be read.
                        logger.debug("CATEGORY_COLUMN_LOOKUP_SKIPPED | keeping default category", exc_info=True)
                link = ""
                for cell in data:
                    um = _URL_RE.search(cell)
                    if um:
                        link = um.group(0).rstrip(").,;")
                        break
                if name and not str(name).lower().endswith("?"):
                    _add_offering(str(name), str(cat), str(desc), link)
                i += 1
            continue
        i += 1

    # Numbered service groups only (1. **Name** – desc) — skip why-choose / qualities lists
    for m in re.finditer(
        r"(?m)^\s*\d+\.\s+\*\*([^*]{3,80})\*\*\s*[–—:-]\s*(.+)$",
        text,
    ):
        title = m.group(1).strip()
        low = title.lower()
        if low in _offering_blocklist or low in ("source", "note", "headline"):
            continue
        # Prefer capability-style titles
        if any(w in low for w in ("cpaas", "marketing", "development", "hosting", "security", "ai", "mobile", "web", "growth", "communication", "software", "cloud", "data")):
            _add_offering(title, "Services", m.group(2))

    # Knowledge articles from ## sections
    articles: List[Dict[str, str]] = []
    for m in re.finditer(
        r"(?m)^(#{2,3})\s+(.+?)\s*$",
        text,
    ):
        title = m.group(2).strip()
        if re.match(r"(?i)pages still to scrape|source:|note:", title):
            continue
        start = m.end()
        nxt = re.search(r"(?m)^#{2,3}\s+", text[start:])
        end = start + nxt.start() if nxt else len(text)
        body = _clean_text(text[start:end])[:6000]
        if len(body) < 80:
            continue
        articles.append({
            "title": re.sub(r"^\d+\.\s*", "", title)[:120],
            "category": "Company",
            "markdown_content": f"# {title}\n\n{body}",
        })
        if len(articles) >= 20:
            break

    vertical = _guess_vertical(text)
    return {
        "vertical": vertical,
        "brand": brand,
        "business_hours": business_hours,
        "menu_items": [],
        "intents": intents,
        "offerings": offerings,
        "faqs": faqs,
        "knowledge_articles": articles,
    }


def _dedupe_key_offering(item: Dict[str, Any]) -> str:
    return _slugify(str(item.get("name") or ""))


def _dedupe_key_faq(item: Dict[str, Any]) -> str:
    return _slugify(str(item.get("question") or ""))[:80]


def _dedupe_key_intent(item: Dict[str, Any]) -> str:
    return str(item.get("name") or "").strip().lower()


def _dedupe_key_article(item: Dict[str, Any]) -> str:
    return _slugify(str(item.get("title") or ""))


def merge_bifurcation_results(*parts: Optional[Dict[str, Any]], tenant_id: str = "") -> Dict[str, Any]:
    """Union multiple bifurcation JSON blobs; prefer richer non-placeholder values."""
    merged: Dict[str, Any] = {
        "vertical": "generic",
        "brand": {},
        "business_hours": {},
        "menu_items": [],
        "intents": [],
        "offerings": [],
        "faqs": [],
        "knowledge_articles": [],
    }
    vertical_votes: Dict[str, int] = {}

    def _prefer_brand(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
        for key, val in (src or {}).items():
            if not val:
                continue
            if _is_placeholder_brand_value(key, val, tenant_id):
                continue
            cur = dst.get(key)
            if not cur or _is_placeholder_brand_value(key, cur, tenant_id) or len(str(val)) > len(str(cur)):
                dst[key] = val

    def _extend(list_key: str, items: Any, key_fn) -> None:
        if not isinstance(items, list):
            return
        existing: Dict[str, int] = {}
        for i, x in enumerate(merged[list_key]):
            if isinstance(x, dict):
                ek = str(key_fn(x) or "").strip().lower()
                if ek:
                    existing[ek] = i
        for item in items:
            if not isinstance(item, dict):
                continue
            sk = str(key_fn(item) or "").strip().lower()
            if not sk:
                continue
            if sk in existing:
                cur = merged[list_key][existing[sk]]
                for field in ("answer", "description", "short_description", "markdown_content", "question"):
                    if len(str(item.get(field) or "")) > len(str(cur.get(field) or "")):
                        cur[field] = item[field]
                for field, val in item.items():
                    if val and (field not in cur or not cur.get(field)):
                        cur[field] = val
            else:
                existing[sk] = len(merged[list_key])
                merged[list_key].append(dict(item))

    for part in parts:
        if not part or not isinstance(part, dict):
            continue
        v = part.get("vertical")
        if v and v in VALID_VERTICALS:
            vertical_votes[v] = vertical_votes.get(v, 0) + 1
        _prefer_brand(merged["brand"], part.get("brand") or {})
        bh = part.get("business_hours")
        if isinstance(bh, dict) and (bh.get("raw") or bh.get("open") or bh.get("always_open")):
            if not merged["business_hours"] or bh.get("raw") or bh.get("always_open"):
                merged["business_hours"] = {**merged["business_hours"], **bh}
        _extend(
            "menu_items",
            part.get("menu_items"),
            lambda x: _normalize_menu_button_key(str(x.get("id") or x.get("title") or "")),
        )
        _extend("intents", part.get("intents"), _dedupe_key_intent)
        _extend("offerings", part.get("offerings"), _dedupe_key_offering)
        _extend("faqs", part.get("faqs"), _dedupe_key_faq)
        _extend("knowledge_articles", part.get("knowledge_articles"), _dedupe_key_article)

    if vertical_votes:
        merged["vertical"] = max(vertical_votes.items(), key=lambda kv: kv[1])[0]
    elif merged.get("vertical") not in VALID_VERTICALS:
        merged["vertical"] = "generic"

    brand = merged["brand"]
    if brand.get("name") and not brand.get("bot_name"):
        brand["bot_name"] = f"{str(brand['name']).split()[0]} Assistant"
    if brand.get("name") and not brand.get("signature"):
        brand["signature"] = f"{brand['name']} — At your service."

    if len(merged["menu_items"]) < 3:
        merged["menu_items"].extend(
            _menu_items_from_vertical_defaults(merged["vertical"], limit=10)
        )

    return merged
