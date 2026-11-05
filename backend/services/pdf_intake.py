"""PDF intake — one client PDF in, every item in its own place, per tenant.

This module owns one responsibility: **placement**. What is in the document
decides where it is written, and the contract is fixed:

    company details    -> tenant config  ``brand``
    operating hours    -> tenant config  ``business_hours``
    tone / guardrails  -> tenant config  ``guardrails``
    WhatsApp options   -> tenant config  ``menu.buttons``
    products           -> ``products`` table, the row's own category
    services           -> the SAME ``products`` table, ``category="Services"``
    FAQs / policies    -> ``faq_dataset`` (``content_type="faq"``)
    long-form prose    -> ``knowledge_base`` (+ chunks + embeddings)

``products`` is not two tables. ``backend/routes/offerings.py`` documents one
table of records per tenant — "products, services, packages, departments" —
relabelled per vertical. So a service is a ``products`` row tagged
``category="Services"``. That column is the only thing keeping products and
services apart, and getting it wrong silently loses every service in a
document, so ``_categorize()`` is deliberately explicit about it.

Extraction is delegated to :mod:`extract` (``process_file``), which already
handles multipage PDFs, table extraction and OCR for scanned pages. Nothing
here re-implements parsing.

Everything is deterministic — regexes and table headers, no model call — so the
same document always lands in the same places.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("pdf_intake")

#: Meta's list-message limits. Exceeding them makes WhatsApp reject the whole
#: message, so menu titles/descriptions are truncated on the way in.
#: See routing/whatsapp.py:301.
WA_TITLE_LIMIT = 24
WA_DESC_LIMIT = 72

#: What the review screen renders: bucket -> (human label, where it lands).
PLACEMENT_LABELS: Dict[str, Tuple[str, str]] = {
    "brand": ("Company details", "Profile → brand"),
    "business_hours": ("Operating hours", "Profile → business hours"),
    "products": ("Products", "Offerings → category as printed"),
    "services": ("Services", "Offerings → category “Services”"),
    "faqs": ("FAQs & policies", "FAQ dataset"),
    "articles": ("Company & policy articles", "Knowledge base + vector index"),
    "menu": ("WhatsApp menu options", "Profile → menu buttons"),
    "guardrails": ("Bot persona & guardrails", "Profile → guardrails"),
}

#: Section markers the onboarding kits are generated with
#: (reports/generate_onboarding_kits.py). Honoured first because they make
#: placement exact; unmarked documents fall back to headings and heuristics.
SECTION_MARKERS: Dict[str, str] = {
    "brand": r"\[SECTION:\s*BRAND(?:\s*&\s*PROFILE)?\]",
    "menu": r"\[SECTION:\s*WHATSAPP(?:\s*MENU)?\]",
    "catalog": r"\[SECTION:\s*CATALOG(?:\s*&\s*OFFERINGS)?\]",
    "faqs": r"\[SECTION:\s*FAQS?(?:\s*&\s*POLICIES)?\]",
    "guardrails": r"\[SECTION:\s*BOT\s*PERSONA(?:\s*&\s*GUARDRAILS)?\]",
}

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
#: Preferred shape: an explicit country code followed by a 10-digit national
#: number — the only shape the tenancy schema accepts for a phone.
_PHONE_CC_RE = re.compile(r"\+\d{1,3}[\s\-.]?\d{5}[\s\-.]?\d{5}")
_PHONE_LOOSE_RE = re.compile(r"\+?\d[\d\s\-()]{8,15}\d")
_URL_RE = re.compile(r"(?:https?://|www\.)[\w.-]+\.[A-Za-z]{2,}(?:/\S*)?")
_TZ_RE = re.compile(r"\b([A-Za-z]+/[A-Za-z_]+)\b")
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([AaPp]\.?[Mm]\.?)?\b")

_DAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}

#: Words that mark a line as prose rather than a catalogue entry.
_NOISE_RE = re.compile(
    r"\b(page|contents|catalogue|catalog|contact|phone|email|address|website|"
    r"about us|terms|conditions|index|table of contents|disclaimer|copyright|"
    r"all rights reserved)\b",
    re.IGNORECASE,
)

#: A price in any of the shapes client documents actually print.
_PRICE_RE = re.compile(
    r"(?:₹|`| ́|rs\.?|inr)\s*(\d{1,7}(?:[,\d]{0,6})?(?:\.\d{1,2})?)"
    r"|(\d{1,7}(?:[,\d]{0,6})?(?:\.\d{1,2})?)\s*(?:/-|rs\.?|₹|`| ́)",
    re.IGNORECASE,
)

#: Signals that a row is a service rather than a physical product. Services are
#: usually priced per period, per engagement, or as a contract.
_SERVICE_HINTS = (
    "service", "consultation", "consulting", "installation", "install",
    "repair", "maintenance", "subscription", "per month", "/month", "monthly",
    "per hour", "/hr", "hourly", "annual", "annually", "amc", "training",
    "audit", "advisory", "retainer", "support plan", "warranty", "setup",
    "onboarding fee", "per visit", "per session", "per booking",
    # healthcare
    "treatment", "therapy", "diagnosis", "procedure", "checkup", "opd",
    "surgery", "vaccination", "physiotherapy", "counselling",
    # finance / banking
    "advisory", "filing", "gst registration", "bookkeeping", "compliance",
    "portfolio management", "wealth management",
    # it_software
    "saas", "implementation", "integration", "devops", "sprint", "cloud hosting",
    "managed service", "support contract",
    # logistics
    "freight", "shipping", "cargo", "customs clearance", "warehousing",
)

#: Vertical-specific product type hints — words that mark an item as a *product*
#: (not a service) in a particular industry.  Keyed by vertical slug.
_VERTICAL_PRODUCT_HINTS: Dict[str, Tuple[str, ...]] = {
    "real_estate": (
        "villa", "plot", "flat", "apartment", "floor", "bungalow", "penthouse",
        "bhk", "sqft", "sq ft", "carpet area", "super area", "tower", "block",
        "unit type", "possession", "configuration",
    ),
    "ecommerce": (
        "sku", "variant", "pack of", "in stock", "model", "colour", "size",
        "material", "weight", "brand", "mrp", "listed price",
    ),
    "it_software": (
        "software", "license", "module", "plugin", "sdk", "api plan",
        "seats", "users", "edition", "version",
    ),
    "tours_travel": (
        "itinerary", "nights", "destination", "package", "tour", "hotel", "flight",
        "visa", "sightseeing", "cruise", "cab",
    ),
    "healthcare": (
        "medicine", "drug", "tablet", "injection", "device", "equipment",
        "supplement", "kit", "test kit",
    ),
    "banking": (
        "account", "deposit", "fd", "rd", "ncd", "bond", "insurance plan",
        "credit card", "debit card", "locker",
    ),
    "finance": (
        "fund", "scheme", "plan", "portfolio", "mf", "ulip", "nps",
    ),
}

_HEADER_NAME = ("name", "product", "service", "item", "package", "property",
                "plan", "offering", "title", "unit type")
_HEADER_CATEGORY = ("category", "type", "group", "segment", "vertical", "kind")
_HEADER_PRICE = ("price", "rate", "amount", "cost", "mrp", "fee", "tariff",
                 "starting", "charges")
_HEADER_DESC = ("description", "details", "features", "specification", "spec",
                "about", "remarks", "notes", "inclusions")

_FAQ_Q_RE = re.compile(r"^\s*(?:q(?:uestion)?\s*[:.\-)]|question\s*[:.\-)])\s*(.+)$", re.IGNORECASE)
_FAQ_A_RE = re.compile(r"^\s*(?:a(?:nswer)?\s*[:.\-)]|answer\s*[:.\-)])\s*(.+)$", re.IGNORECASE)


# ──────────────────────────────────────────────────────────────────────────────
# Small text helpers
# ──────────────────────────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Collapse the whitespace PDF text extraction scatters through a page."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _slugify(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-") or "item"


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _cell(row: List[Any], idx: Optional[int]) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return str(row[idx] or "").strip()


def _price_in(text: str) -> Optional[str]:
    m = _PRICE_RE.search(text or "")
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").strip() or None


def _is_service(name: str, category: str, description: str,
                vertical: str = "generic") -> bool:
    """Services are rows in ``products`` that must be tagged as such.

    An explicit category mentioning "service" always wins.  Vertical-specific
    product hints take precedence over the generic service hints — e.g. "villa"
    in real_estate is always a product, not a service regardless of language.
    """
    if "service" in (category or "").lower():
        return True
    haystack = f"{name} {description}".lower()
    # Vertical-specific product words override the service heuristic
    product_hints = _VERTICAL_PRODUCT_HINTS.get(vertical, ())
    if any(hint in haystack for hint in product_hints):
        return False
    return any(hint in haystack for hint in _SERVICE_HINTS)


# ──────────────────────────────────────────────────────────────────────────────
# Marker-first section partitioning
# ──────────────────────────────────────────────────────────────────────────────

def _marker_bucket(tag: str) -> Optional[str]:
    """Map a ``[SECTION: …]`` tag onto a bucket."""
    t = (tag or "").upper()
    if "BRAND" in t:
        return "brand"
    if "MENU" in t:
        return "menu"
    if "CATALOG" in t or "OFFERING" in t:
        return "catalog"
    if "FAQ" in t or "POLIC" in t:
        return "faqs"
    if "PERSONA" in t or "GUARD" in t:
        return "guardrails"
    return None


#: Used only when a document carries no markers at all.
#: Order matters: faqs and guardrails are checked first so that sections like
#: "Terms & Conditions" don't bleed into catalog.
_HEADING_HINTS: List[Tuple[str, Tuple[str, ...]]] = [
    ("faqs", (
        "faq", "faqs", "frequently asked", "question", "questions & answers",
        "q&a", "q & a", "policy", "policies", "terms & conditions",
        "terms and conditions", "refund policy", "return policy", "cancellation",
        "disclaimer", "privacy policy", "shipping policy", "delivery policy",
        "rera", "legal", "compliance",
    )),
    ("guardrails", (
        "persona", "guardrail", "tone of voice", "never say",
        "escalation", "bot behaviour", "bot behavior", "communication style",
        "bot persona", "chatbot guidelines", "what the bot must not",
    )),
    ("menu", (
        "whatsapp menu", "menu options", "main menu", "bot menu",
        "conversation options", "chat options", "quick replies",
    )),
    ("catalog", (
        "catalog", "catalogue", "product", "products", "our products",
        "service", "services", "our services", "offering", "offerings",
        "price list", "pricing", "rate card", "rates", "fee schedule",
        "menu of services", "packages", "plans", "schemes", "properties",
        "listings", "portfolio", "treatments", "courses", "modules",
        "residential", "commercial", "investment options", "loan products",
        "deposits", "accounts", "tours", "itineraries", "procedures",
    )),
    ("brand", (
        "about us", "about the company", "company profile", "company overview",
        "who we are", "our story", "our mission", "our vision", "introduction",
        "contact us", "contact details", "reach us", "get in touch",
        "office address", "headquarters", "branch", "working hours",
        "business hours", "operating hours",
    )),
]


def _heuristic_buckets(lines: List[str]) -> Dict[str, List[str]]:
    """Split unmarked text by heading keywords.

    Deliberately conservative: a line only switches the active bucket when it is
    short and reads like a heading, so a paragraph that merely mentions
    "products" does not hijack the rest of the document.

    Falls back to a scoring pass for the first 40 lines when no heading matches
    at all (e.g. a single-section document).  The bucket that scores highest on
    contact / price / Q-A signals claims the whole document.
    """
    buckets: Dict[str, List[str]] = {k: [] for k in
                                     ("brand", "menu", "catalog", "faqs", "guardrails")}
    current = "brand"
    heading_matched = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # A heading: short and does not end with a sentence terminator.
        is_headingish = len(stripped) <= 80 and not stripped.endswith((".", "?")) \
                        and not stripped[-1].isdigit()
        if is_headingish:
            low = stripped.lower()
            for bucket, hints in _HEADING_HINTS:
                if any(h in low for h in hints):
                    current = bucket
                    heading_matched = True
                    break
        buckets[current].append(stripped)

    if not heading_matched:
        # Scoring pass: count signals for the first 80 lines
        sample = lines[:80]
        sample_text = " ".join(sample).lower()
        scores = {
            "brand": sum([
                sample_text.count("@"),                  # emails
                sample_text.count("www."),               # URLs
                len(re.findall(r"\+\d", sample_text)),   # phone numbers
                sample_text.count("founded"),
                sample_text.count("established"),
            ]),
            "catalog": sum([
                len(re.findall(r"₹|\brs\b|\bprice\b|\brate\b|\bcost\b", sample_text)),
                sample_text.count("product"),
                sample_text.count("service"),
                sample_text.count("package"),
            ]),
            "faqs": sum([
                len(re.findall(r"\bq\b|\bques\b|\bfaq\b|\bpolic", sample_text)),
                sample_text.count("?"),
            ]),
        }
        best_bucket = max(scores, key=lambda k: scores[k])
        if scores[best_bucket] > 0:
            buckets[best_bucket].extend(buckets["brand"])
            if best_bucket != "brand":
                buckets["brand"] = []

    return buckets


def _partition(text: str) -> Dict[str, List[str]]:
    """Split document text into the five marker buckets.

    Marker path first (exact), heading heuristics second.
    """
    sections = {k: [] for k in ("brand", "menu", "catalog", "faqs", "guardrails")}
    has_markers = any(re.search(p, text, re.IGNORECASE) for p in SECTION_MARKERS.values())
    if not has_markers:
        return _heuristic_buckets([ln for ln in text.split("\n")])

    parts = re.split(r"(\[SECTION:\s*[^\]]+\])", text, flags=re.IGNORECASE)
    current = "brand"
    for part in parts:
        part = part.strip()
        if not part:
            continue
        header = re.match(r"\[SECTION:\s*([^\]]+)\]", part, re.IGNORECASE)
        if header:
            current = _marker_bucket(header.group(1)) or current
            continue
        sections[current].extend(ln for ln in part.split("\n") if ln.strip())
    return sections


# ──────────────────────────────────────────────────────────────────────────────
# Field extractors
# ──────────────────────────────────────────────────────────────────────────────

def _extract_contacts(text: str) -> Dict[str, Any]:
    """Pull the company's own contact details out of the brand section."""
    out: Dict[str, Any] = {}
    emails = _EMAIL_RE.findall(text or "")
    if emails:
        # Prefer a care/support/info address over an incidental one.
        ranked = sorted(
            set(emails),
            key=lambda e: (0 if re.match(r"^(care|support|info|help|contact|sales|hello)", e, re.I) else 1, len(e)),
        )
        out["support_email"] = ranked[0]

    phones = _PHONE_CC_RE.findall(text or "")
    if not phones:
        phones = _PHONE_LOOSE_RE.findall(text or "")
    if phones:
        # Normalise to the +<cc><10 digits> shape the schema validates.
        for raw in phones:
            digits = re.sub(r"\D", "", raw)
            if len(digits) == 10:
                out["support_phone"] = f"+91{digits}"
                break
            if 11 <= len(digits) <= 13:
                out["support_phone"] = f"+{digits}"
                break

    urls = [u for u in _URL_RE.findall(text or "")
            if not any(s in u.lower() for s in ("facebook", "instagram", "twitter", "linkedin", "youtube", "wa.me"))]
    if urls:
        url = urls[0]
        if url.lower().startswith("www."):
            url = f"https://{url}"
        out["website"] = url
    return out


def _extract_brand(section_lines: List[str], fallback_name: str) -> Dict[str, Any]:
    text = "\n".join(section_lines)
    brand: Dict[str, Any] = {
        "name": fallback_name,
        "bot_name": "",
        "tagline": "",
        "signature": "",
    }

    for pat in (
        r"(?:company|business|brand|firm|organisation|organization)\s*name\s*[:\-]\s*(.+)",
        r"(?:^|\n)\s*name\s*[:\-]\s*(.+)",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            brand["name"] = m.group(1).strip().split("\n")[0][:120]
            break

    for pat in (r"assistant\s*name\s*[:\-]\s*(.+)", r"bot\s*name\s*[:\-]\s*(.+)",
                r"chatbot\s*name\s*[:\-]\s*(.+)"):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            brand["bot_name"] = m.group(1).strip().split("\n")[0][:60]
            break

    for pat in (r"tagline\s*[:\-]\s*(.+)", r"slogan\s*[:\-]\s*(.+)"):
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            brand["tagline"] = m.group(1).strip().split("\n")[0][:160]
            break

    m = re.search(r"signature\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    if m:
        brand["signature"] = m.group(1).strip().split("\n")[0][:160]

    brand.update(_extract_contacts(text))
    return brand


def _parse_days(text: str) -> Optional[List[int]]:
    """Read a day range like "Monday to Saturday" or "Mon–Fri"."""
    window = text or ""
    m = re.search(
        r"\b(mon|monday|tue|tues|tuesday|wed|weds|wednesday|thu|thur|thurs|thursday|fri|friday|sat|saturday|sun|sunday)"
        r"\s*(?:to|through|thru|until|till|-|–|—)\s*"
        r"(mon|monday|tue|tues|tuesday|wed|weds|wednesday|thu|thur|thurs|thursday|fri|friday|sat|saturday|sun|sunday)\b",
        window, re.IGNORECASE,
    )
    if m:
        start = _DAY_NAMES.get(m.group(1).lower())
        end = _DAY_NAMES.get(m.group(2).lower())
        if start is not None and end is not None:
            if start <= end:
                return list(range(start, end + 1))
            return list(range(start, 7)) + list(range(0, end + 1))
    mentioned = sorted({_DAY_NAMES[t.lower()] for t in _DAY_NAMES if re.search(rf"\b{t}\b", window, re.I)})
    return mentioned or None


def _to_24h(hour: int, minute: int, meridiem: Optional[str]) -> Optional[str]:
    if meridiem:
        mer = meridiem.replace(".", "").lower()
        if hour == 12:
            hour = 0
        if mer.startswith("p"):
            hour += 12
    if not (0 <= hour <= 23):
        return None
    return f"{hour:02d}:{minute:02d}"


def _extract_hours(text: str) -> Dict[str, Any]:
    """Read the trading window, keeping the raw line for the review screen."""
    hours: Dict[str, Any] = {"timezone": "Asia/Kolkata", "always_open": True,
                             "open": "09:00", "close": "21:00",
                             "open_days": [0, 1, 2, 3, 4, 5, 6], "raw": ""}

    window = ""
    for line in (text or "").split("\n"):
        if re.search(r"\b(hours?|timing|open|opening|working|business hours)\b", line, re.I):
            window = line.strip()
            break
    if not window:
        return hours
    hours["raw"] = window

    tz = _TZ_RE.search(window)
    if tz:
        hours["timezone"] = tz.group(1)

    times: List[str] = []
    for h, mi, mer in _TIME_RE.findall(window):
        if not mer and not re.search(r":", window):
            continue
        parsed = _to_24h(int(h), int(mi or 0), mer or None)
        if parsed and parsed not in times:
            times.append(parsed)
    if len(times) >= 2:
        hours["open"], hours["close"] = times[0], times[1]
        hours["always_open"] = False
    elif len(times) == 1:
        hours["always_open"] = False

    days = _parse_days(window)
    if days:
        hours["open_days"] = days
    return hours


def _extract_menu(section_lines: List[str], tables: List[List[List[str]]]) -> List[Dict[str, Any]]:
    """Build WhatsApp menu options, keeping ``id`` unique and titles within limits."""
    raw: List[Tuple[str, str]] = []

    for table in tables:
        for row in table:
            if len(row) >= 2:
                title, desc = _cell(row, 0), _cell(row, 1)
                if title and len(title) <= 60 and title.lower() not in _HEADER_NAME:
                    raw.append((title, desc))

    for line in section_lines:
        line = line.strip().lstrip("-•*0123456789. ").strip()
        if not line or len(line) > 80:
            continue
        if ":" in line:
            title, _, desc = line.partition(":")
            raw.append((title.strip(), desc.strip()))
        else:
            raw.append((line, ""))

    items: List[Dict[str, Any]] = []
    used: set[str] = set()
    for title, desc in raw:
        title = title.strip()
        if not title or len(title) > 40 or _NOISE_RE.search(title):
            continue
        base = _slugify(title)[:WA_TITLE_LIMIT] or "option"
        uid, n = base, 2
        while uid in used:
            uid = f"{base[:WA_TITLE_LIMIT - 2]}-{n}"
            n += 1
        used.add(uid)
        items.append({
            "id": uid,
            "title": _truncate(title, WA_TITLE_LIMIT),
            "description": _truncate(desc, WA_DESC_LIMIT),
            "section": "General",
        })
        if len(items) >= 10:
            break
    return items


def _header_index(row: List[str], keywords: Tuple[str, ...]) -> Optional[int]:
    for idx, cell in enumerate(row):
        low = str(cell or "").strip().lower()
        if low and any(low.startswith(k) or k in low for k in keywords):
            return idx
    return None


def _table_offerings(table: List[List[str]]) -> List[Dict[str, Any]]:
    """Read a catalogue table using its header row to place each column."""
    if not table:
        return []
    header_idx = None
    for i, row in enumerate(table[:3]):
        if sum(1 for c in row if str(c or "").strip()) >= 2 and _header_index(row, _HEADER_NAME) is not None:
            header_idx = i
            break
    if header_idx is None:
        return []

    header = [str(c or "").strip() for c in table[header_idx]]
    col_name = _header_index(header, _HEADER_NAME)
    col_cat = _header_index(header, _HEADER_CATEGORY)
    col_price = _header_index(header, _HEADER_PRICE)
    col_desc = _header_index(header, _HEADER_DESC)
    if col_name is None:
        return []

    out: List[Dict[str, Any]] = []
    for row in table[header_idx + 1:]:
        name = _cell(row, col_name)
        if not name or len(name) > 120 or _NOISE_RE.search(name):
            continue
        price = _cell(row, col_price) or _price_in(" ".join(str(c or "") for c in row)) or ""
        description = _cell(row, col_desc)
        out.append({
            "name": name,
            "category": _cell(row, col_cat),
            "price": price,
            "short_description": _truncate(description or name, 120),
            "description": description or name,
        })
    return out


def _line_offerings(lines: List[str]) -> List[Dict[str, Any]]:
    """Fallback: prose lines that look like "Name … <price>"."""
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip().lstrip("-•*0123456789. ").strip()
        if not line or len(line) > 200 or _NOISE_RE.search(line):
            continue
        price = _price_in(line)
        if not price:
            continue
        name = _PRICE_RE.sub("", line).strip(" -–—:|.\t")
        name = re.sub(r"\b(price|rate|per|starting|from|mrp)\b\s*$", "", name, flags=re.I).strip(" -–—:|.\t")
        if not name or len(name) < 3 or len(name) > 90:
            continue
        out.append({
            "name": name,
            "category": "",
            "price": price,
            "short_description": _truncate(line, 120),
            "description": line,
        })
    return out


def _extract_offerings(section_lines: List[str],
                       tables: List[List[List[str]]],
                       vertical: str = "generic") -> List[Dict[str, Any]]:
    """Products and services, each tagged so they land under the right category."""
    raw = _table_offerings_from(tables) or _line_offerings(section_lines)

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in raw:
        # Fuzzy deduplication: normalise whitespace and common OCR noise
        key = re.sub(r"\s+", " ", item["name"].lower().strip(" -–."))
        if key in seen:
            continue
        seen.add(key)
        is_service = _is_service(item["name"], item["category"], item["description"],
                                 vertical=vertical)
        # Keep the category the document printed, but never let a service sit
        # under a generic product label — that is how services go missing.
        category = (item.get("category") or "").strip()
        if is_service and category.lower() in ("", "product", "products", "general", "item"):
            category = "Services"
        if not category:
            category = "Products"
        out.append({
            "name": item["name"],
            "category": category,
            "price": item["price"],
            "short_description": item["short_description"],
            "description": item["description"],
            "unit": "piece",
        })
    return out


def _table_offerings_from(tables: List[List[List[str]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for table in tables:
        out.extend(_table_offerings(table))
    return out


def _extract_faqs(section_lines: List[str], tables: List[List[List[str]]]) -> List[Dict[str, Any]]:
    """Q&A pairs, from explicit Q/A lines or a two-column question/answer table."""
    faqs: List[Dict[str, Any]] = []

    question: Optional[str] = None
    for line in section_lines:
        line = line.strip()
        if not line:
            continue
        mq = _FAQ_Q_RE.match(line)
        ma = _FAQ_A_RE.match(line)
        if mq:
            question = mq.group(1).strip()
            continue
        if ma and question:
            faqs.append({"question": _truncate(question, 300), "answer": ma.group(1).strip(),
                         "category": "General"})
            question = None
            continue
        # Still waiting on the "A:" — this line continues the question; once
        # answered, further lines extend the answer.
        if question:
            question += f" {line}"
        elif faqs:
            faqs[-1]["answer"] += f" {line}"

    if not faqs:
        for table in tables:
            if not table:
                continue
            header = [str(c or "").strip().lower() for c in table[0]]
            q_idx = _header_index(header, ("question", "query", "q"))
            a_idx = _header_index(header, ("answer", "response", "a"))
            if q_idx is None or a_idx is None:
                continue
            for row in table[1:]:
                q, a = _cell(row, q_idx), _cell(row, a_idx)
                if q and a:
                    faqs.append({"question": _truncate(q, 300), "answer": a, "category": "General"})

    if not faqs:
        # "Q: ... A: ..." all on one line
        for line in section_lines:
            m = re.match(r"\s*Q(?:uestion)?\s*[:.\-)]\s*(.+?)\s+A(?:nswer)?\s*[:.\-)]\s*(.+)$",
                         line.strip(), re.IGNORECASE)
            if m:
                faqs.append({"question": _truncate(m.group(1), 300), "answer": m.group(2).strip(), "category": "General"})
    return faqs


def _extract_guardrails(section_lines: List[str]) -> Dict[str, Any]:
    text = "\n".join(section_lines)
    never: List[str] = []
    for line in section_lines:
        low = line.lower()
        if re.search(r"\b(never|do not|don't|must not|avoid|forbidden)\b", low):
            cleaned = re.sub(r"^[-•*\d.\s]+", "", line).strip()
            if cleaned and len(cleaned) <= 200:
                never.append(cleaned)

    tone = ""
    m = re.search(r"\btone(?:\s*of\s*voice)?\s*[:\-]\s*(.+)", text, re.IGNORECASE)
    if m:
        tone = m.group(1).strip().split("\n")[0][:120]

    escalations: List[str] = []
    for line in section_lines:
        if re.search(r"\bescalat", line, re.IGNORECASE):
            cleaned = re.sub(r"^[-•*\d.\s]+", "", line).strip()
            if cleaned:
                escalations.append(cleaned[:160])

    return {"tone": tone, "never_state": never[:25], "escalation_triggers": escalations[:15]}


def _extract_articles(faq_lines: List[str], catalog_lines: List[str],
                      brand_lines: List[str], full_text: str) -> List[Dict[str, Any]]:
    """Long-form prose, minus the lines already placed elsewhere."""
    claimed = set()
    for group in (faq_lines, catalog_lines, brand_lines):
        for line in group:
            claimed.add(line.strip())

    paragraphs, buf = [], []
    for line in full_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if len(" ".join(buf)) > 200:
                paragraphs.append(" ".join(buf))
            buf = []
            continue
        if stripped in claimed or _NOISE_RE.search(stripped):
            continue
        if re.match(r"\[SECTION:", stripped, re.IGNORECASE):
            continue
        buf.append(stripped)
    if len(" ".join(buf)) > 200:
        paragraphs.append(" ".join(buf))

    articles: List[Dict[str, Any]] = []
    for i, para in enumerate(paragraphs[:12]):
        articles.append({
            "title": _truncate(para.split(".")[0], 80) or f"Article {i + 1}",
            "category": "Company",
            "markdown_content": para,
        })
    return articles


def _infer_vertical(text: str) -> str:
    """Score the document against vertical vocabulary to pick a default."""
    low = (text or "").lower()
    scores = {
        "real_estate": ("property", "properties", "apartment", "flat", "bhk", "sq ft", "sqft",
                        "project", "possession", "rera", "plot", "villa", "tower"),
        "ecommerce": ("sku", "add to cart", "shipping", "cod", "order", "return policy",
                      "mrp", "pack of", "in stock", "delivery charge"),
        "it_software": ("software", "saas", "license", "api", "subscription plan",
                        "implementation", "sprint", "devops", "cloud", "sla"),
        "tours_travel": ("itinerary", "package", "tour", "destination", "hotel", "flight",
                         "visa", "sightseeing", "nights"),
        "healthcare": ("clinic", "patient", "doctor", "consultation", "treatment",
                       "appointment", "diagnosis", "opd", "surgery"),
        "banking": ("loan", "interest rate", "emi", "account", "deposit", "credit card",
                    "insurance", "premium", "kyc"),
        "finance": ("tax", "gst", "audit", "advisory", "returns", "compliance",
                    "bookkeeping", "accounting"),
    }
    best, best_score = "generic", 0
    for vertical, words in scores.items():
        score = sum(1 for w in words if w in low)
        if score > best_score:
            best, best_score = vertical, score
    return best


# ──────────────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────────────

class PdfIntakeService:
    """Turn a tenant's PDF into a reviewable draft, then place it on approval.

    The service never writes anything until :meth:`publish` is called by an
    explicit approval, and even then it merges into the tenant's existing draft
    rather than replacing it.
    """

    def __init__(self, tenant_id: str, actor: Optional[dict] = None):
        self.tenant_id = (tenant_id or "").strip().lower()
        self.actor = actor or {
            "username": "pdf_intake",
            "role": "super_admin",
            "tenant_id": self.tenant_id,
        }

    # ── extraction ────────────────────────────────────────────────────────

    def extract(self, filepath: str) -> Dict[str, Any]:
        """Extract text and tables from one PDF via :mod:`extract`."""
        try:
            from extract import process_file
        except ImportError:  # running from the repo root
            from backend.extract import process_file

        warnings: List[str] = []
        results = process_file(filepath)
        if not results:
            return {"filename": Path(filepath).name, "type": None, "pages": [],
                    "tables": [], "warnings": ["Could not read the document."]}
        if results.get("type") != "pdf":
            return {"filename": Path(filepath).name, "type": results.get("type"),
                    "pages": [], "tables": [],
                    "warnings": ["Only PDF documents are accepted."]}

        pages = results.get("pages") or []
        empty = [p["page"] for p in pages if not (p.get("text") or "").strip()]
        if empty:
            warnings.append(
                f"Page(s) {', '.join(str(p) for p in empty[:8])} produced no text — "
                "they are image-only and OCR did not return anything. Install tesseract "
                "or check the pages are legible."
            )
        ocr_pages = [p["page"] for p in pages if p.get("source") == "ocr" and (p.get("text") or "").strip()]
        if ocr_pages:
            warnings.append(f"OCR was used on page(s) {', '.join(str(p) for p in ocr_pages[:8])} — verify the text is accurate.")

        return {
            "filename": results.get("file") or Path(filepath).name,
            "type": "pdf",
            "pages": pages,
            "tables": [t.get("data") or [] for t in (results.get("tables") or [])],
            "warnings": warnings,
        }

    def extract_bytes(self, filename: str, content: bytes) -> Dict[str, Any]:
        """Extract a PDF supplied as bytes, without persisting it anywhere.

        :mod:`extract` reads from a path, so the bytes go through a temp file
        that is removed immediately. Used by the staging paths, which must not
        leave an admin_files row behind.
        """
        suffix = Path(filename).suffix or ".pdf"
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            doc = self.extract(tmp)
        finally:
            try:
                os.remove(tmp)
            except OSError:
                # deliberate: temp-file cleanup in a finally block — a file
                # that cannot be removed must not mask the real result.
                logger.debug("TEMP_FILE_REMOVE_SKIPPED | path=%s", tmp, exc_info=True)
        doc["filename"] = Path(filename).name
        return doc

    def stage(self, files: List[Tuple[str, bytes]]) -> Dict[str, Any]:
        """Parse documents held in memory and return the reviewable draft.

        The in-memory counterpart of :meth:`process`, for callers that already
        have the bytes (the onboarding-kit uploads).
        """
        docs = [self.extract_bytes(name, content) for name, content in files]
        return self.bifurcate(docs)

    # ── bifurcation ───────────────────────────────────────────────────────

    def bifurcate(self, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Sort every extracted item into the bucket it belongs to.

        Returns a draft shaped like the onboarding draft the console already
        knows how to review, plus a ``placement`` breakdown for the UI.
        """
        warnings: List[str] = []
        for doc in docs:
            warnings.extend(doc.get("warnings") or [])

        full_text = _clean("\n\n".join(
            "\n".join(p.get("text") or "" for p in doc.get("pages") or [])
            for doc in docs
        ))
        tables: List[List[List[str]]] = []
        for doc in docs:
            for table in doc.get("tables") or []:
                if table:
                    tables.append([[str(c or "").strip() for c in row] for row in table])

        sections = _partition(full_text)
        vertical = _infer_vertical(full_text)

        brand = _extract_brand(sections["brand"], fallback_name=self.tenant_id)
        hours = _extract_hours("\n".join(sections["brand"]) or full_text)
        menu_items = _extract_menu(sections["menu"], tables)
        offerings = _extract_offerings(sections["catalog"], tables)
        faqs = _extract_faqs(sections["faqs"], tables)
        guardrails = _extract_guardrails(sections["guardrails"])
        articles = _extract_articles(sections["faqs"], sections["catalog"],
                                     sections["brand"], full_text)

        products = [o for o in offerings if (o.get("category") or "").lower() != "services"]
        services = [o for o in offerings if (o.get("category") or "").lower() == "services"]

        placement = {
            "brand": 1 if brand.get("name") else 0,
            "business_hours": 1 if not hours.get("always_open") else 0,
            "products": len(products),
            "services": len(services),
            "faqs": len(faqs),
            "articles": len(articles),
            "menu": len(menu_items),
            "guardrails": len(guardrails.get("never_state") or []),
        }

        empty_buckets = [PLACEMENT_LABELS[k][0] for k, v in placement.items() if not v]
        if empty_buckets:
            warnings.append(
                "Nothing found for: " + ", ".join(empty_buckets) +
                ". Add [SECTION: …] headings to the PDF, or fill these in on the review screen."
            )

        return {
            "status": "staged",
            "tenant_id": self.tenant_id,
            "vertical": vertical,
            "brand": brand,
            "business_hours": hours,
            "menu_items": menu_items,
            "offerings": offerings,
            "faqs": faqs,
            "knowledge_articles": articles,
            "guardrails": guardrails,
            "source_files": [d.get("filename") for d in docs if d.get("filename")],
            "stats": {
                "offerings_count": len(offerings),
                "menu_items_count": len(menu_items),
                "faqs_count": len(faqs),
                "articles_count": len(articles),
            },
            "placement": placement,
            "warnings": warnings,
        }

    # ── validation ────────────────────────────────────────────────────────

    def validate(self, draft: Dict[str, Any]) -> Tuple[Optional[Any], List[str]]:
        """Check the draft against the tenant schema before anything is written.

        The schema forbids unknown keys and validates contacts, so a bad phone
        number or an invented vertical is caught here — on the review screen —
        rather than corrupting a live tenant.
        """
        try:
            from shared.tenancy import schemas as tenancy_schemas
        except ImportError:
            from backend.shared.tenancy import schemas as tenancy_schemas  # type: ignore

        warnings: List[str] = list(draft.get("warnings") or [])

        def only(model, data: Dict[str, Any]) -> Dict[str, Any]:
            allowed = set(model.model_fields.keys())
            return {k: v for k, v in (data or {}).items() if k in allowed}

        vertical = draft.get("vertical") or "generic"
        if vertical not in tenancy_schemas.VERTICALS:
            warnings.append(f"Vertical '{vertical}' is not recognised; falling back to 'generic'.")
            vertical = "generic"

        brand_data = only(tenancy_schemas.Brand, draft.get("brand") or {})
        for field, checker, label in (
            ("support_email", tenancy_schemas.is_valid_email, "email"),
            ("support_phone", tenancy_schemas.is_valid_phone, "phone number"),
        ):
            value = brand_data.get(field)
            if value and not checker(value):
                warnings.append(f"Dropped an invalid {label}: '{value}'.")
                brand_data.pop(field, None)

        menu_data = only(tenancy_schemas.MenuSpec, {
            "key": "kb_main",
            "header": brand_data.get("name") or self.tenant_id,
            "body": f"Welcome to {brand_data.get('name') or self.tenant_id}! How can we help?",
            "footer": "Select an option or type your question",
            "button_text": "Show Options",
            "buttons": [only(tenancy_schemas.MenuButton, b) for b in (draft.get("menu_items") or [])],
        })

        payload = only(tenancy_schemas.TenantProfile, {
            "tenant_id": self.tenant_id,
            "vertical": vertical,
            "display_name": brand_data.get("name") or self.tenant_id,
            "brand": brand_data,
            "business_hours": only(tenancy_schemas.BusinessHours, draft.get("business_hours") or {}),
            "guardrails": only(tenancy_schemas.Guardrails, {
                "never_state": (draft.get("guardrails") or {}).get("never_state") or [],
                "escalate_keywords": (draft.get("guardrails") or {}).get("escalation_triggers") or [],
            }),
            "menu": menu_data,
        })

        try:
            profile = tenancy_schemas.TenantProfile(**payload)
        except Exception as exc:  # ValidationError and friends
            warnings.append(f"The profile did not validate: {exc}")
            return None, warnings
        return profile, warnings

    # ── publish ───────────────────────────────────────────────────────────

    def publish(self, draft: Dict[str, Any], background_tasks: Any = None) -> Dict[str, Any]:
        """Write every bucket to its destination, then publish a config version.

        Merges into the tenant's existing draft — keys this PDF did not supply
        keep their current values, so a hand-tuned menu or flow list survives.
        """
        from database import (get_db_context, list_products, record_admin_audit_event,
                              save_chunks, save_product, update_product)

        summary: Dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "vertical": draft.get("vertical", "generic"),
            "brand": (draft.get("brand") or {}).get("name", self.tenant_id),
            "published": False,
            "version": None,
            "products_created": 0,
            "products_updated": 0,
            "services_created": 0,
            "faqs_created": 0,
            "kb_chunks_indexed": 0,
            "menu_buttons_count": len(draft.get("menu_items") or []),
            "warnings": list(draft.get("warnings") or []),
        }

        profile, warnings = self.validate(draft)
        summary["warnings"] = warnings
        published_config = profile.model_dump() if profile is not None else None

        self._publish_config(draft, published_config, summary, get_db_context)
        self._place_products(draft, summary, list_products, save_product, update_product, get_db_context)
        self._place_faqs(draft, summary, save_chunks)
        self._place_knowledge(draft, summary, background_tasks)

        try:
            with get_db_context() as conn:
                record_admin_audit_event(
                    conn,
                    action="tenant_pdf_intake_published",
                    actor=self.actor,
                    resource_type="tenant",
                    resource_id=self.tenant_id,
                    tenant_id=self.tenant_id,
                    details={
                        "products_created": summary["products_created"],
                        "services_created": summary["services_created"],
                        "faqs_created": summary["faqs_created"],
                        "kb_chunks_indexed": summary["kb_chunks_indexed"],
                        "menu_buttons_count": summary["menu_buttons_count"],
                        "version": summary["version"],
                    },
                )
        except Exception as exc:
            logger.warning("Audit event for pdf intake failed: %s", exc)

        return summary

    def _publish_config(self, draft: Dict[str, Any], published_config: Optional[dict],
                        summary: Dict[str, Any], get_db_context) -> None:
        """Merge the document's profile keys into the tenant's live config."""
        try:
            from shared.tenancy import cache as tenancy_cache
            from shared.tenancy import store as tenancy_store
        except ImportError:
            from backend.shared.tenancy import cache as tenancy_cache  # type: ignore
            from backend.shared.tenancy import store as tenancy_store  # type: ignore

        try:
            draft_payload = tenancy_store.get_draft(self.tenant_id) or {}
            draft_payload["tenant_id"] = self.tenant_id
            draft_payload["vertical"] = draft.get("vertical", "generic")

            brand = draft.get("brand") or {}
            if brand.get("name"):
                draft_payload["display_name"] = brand["name"]
                # Merge into any existing brand rather than replacing it, so a
                # logo_url or bot_name set in the console is not lost.
                merged_brand = dict(draft_payload.get("brand") or {})
                merged_brand.update({k: v for k, v in brand.items() if v not in (None, "")})
                draft_payload["brand"] = merged_brand

            menu_items = draft.get("menu_items") or []
            if menu_items:
                # Same sanitizer the kit/bifurcation path uses: underscore ids,
                # and titles/ids that already exist as vertical defaults reuse
                # the default's id, so "Technologies" merges with
                # menu_technologies instead of doubling the menu.
                try:
                    from services.bifurcation_extract import _sanitize_menu_buttons
                except ImportError:  # pragma: no cover
                    from backend.services.bifurcation_extract import _sanitize_menu_buttons  # type: ignore
                try:
                    menu_items = _sanitize_menu_buttons(
                        menu_items, vertical=draft.get("vertical") or "generic"
                    )
                except Exception as exc:  # never lose the extract to a sanitizer bug
                    logger.warning("MENU_SANITIZE_FAILED | tenant=%s | %s", self.tenant_id, exc)
            if menu_items:
                draft_payload["menu"] = {
                    "key": (draft_payload.get("menu") or {}).get("key", "kb_main"),
                    "header": brand.get("name") or self.tenant_id,
                    "body": f"Welcome to {brand.get('name') or self.tenant_id}! How can we help?",
                    "footer": "Select an option or type your question",
                    "button_text": "Show Options",
                    "buttons": menu_items,
                }

            hours = draft.get("business_hours") or {}
            if hours.get("raw") or not hours.get("always_open", True):
                draft_payload["business_hours"] = hours

            never = (draft.get("guardrails") or {}).get("never_state") or []
            if never:
                merged_guardrails = dict(draft_payload.get("guardrails") or {})
                merged_guardrails["never_state"] = never
                draft_payload["guardrails"] = merged_guardrails

            with get_db_context() as conn:
                tenancy_store.save_draft(
                    self.tenant_id, draft_payload,
                    updated_by=self.actor.get("username", "admin"), conn=conn,
                )
                try:
                    version = tenancy_store.publish_version(
                        self.tenant_id, draft_payload,
                        published_by=self.actor.get("username", "admin"), conn=conn,
                    )
                    summary["version"] = version
                    summary["published"] = True
                    tenancy_cache.purge(self.tenant_id)
                    logger.info("PDF_INTAKE_PUBLISHED | tenant=%s | v=%s", self.tenant_id, version)
                except Exception as exc:
                    summary["warnings"].append(f"Could not publish a config version: {exc}")
        except Exception as exc:
            logger.error("Config placement failed for %s: %s", self.tenant_id, exc, exc_info=True)
            summary["warnings"].append(f"Profile was not written: {exc}")

    def _place_products(self, draft: Dict[str, Any], summary: Dict[str, Any],
                        list_products, save_product, update_product, get_db_context) -> None:
        """Products and services into the one `products` table, by category."""
        offerings = draft.get("offerings") or []
        if not offerings:
            return
        try:
            existing = {
                (p.get("name") or "").strip().lower(): p
                for p in list_products(tenant_id=self.tenant_id, active_only=False, limit=1000)
            }
        except Exception:
            existing = {}

        for item in offerings:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            category = (item.get("category") or "Products").strip() or "Products"
            is_service = category.lower() == "services"
            price = (item.get("price") or "").strip() or None
            short_desc = (item.get("short_description") or name).strip()
            description = (item.get("description") or short_desc).strip()

            found = existing.get(name.lower())
            try:
                if found:
                    update_product(
                        found["id"], tenant_id=self.tenant_id,
                        price=price, description=description,
                        short_description=short_desc, category=category,
                    )
                    summary["products_updated"] += 1
                else:
                    save_product(
                        name=name,
                        slug=f"{self.tenant_id}-{_slugify(name)}",
                        category=category,
                        price=price or "",
                        short_description=short_desc,
                        description=description,
                        unit=item.get("unit") or "piece",
                        tenant_id=self.tenant_id,
                        actor=self.actor,
                    )
                    if is_service:
                        summary["services_created"] += 1
                    else:
                        summary["products_created"] += 1
            except Exception as exc:
                logger.debug("Product placement skipped for %r: %s", name, exc)

    def _place_faqs(self, draft: Dict[str, Any], summary: Dict[str, Any], save_chunks) -> None:
        """Q&A pairs into `faq_dataset`, in the shape the bot already retrieves."""
        faqs = draft.get("faqs") or []
        if not faqs:
            return
        source = (draft.get("source_files") or ["pdf_intake"])[0]
        chunks = []
        for faq in faqs:
            question = (faq.get("question") or "").strip()
            answer = (faq.get("answer") or "").strip()
            if not question or not answer:
                continue
            chunks.append({
                "source_file": source,
                "content_type": "faq",
                "content": f"Question: {question}\nAnswer: {answer}",
                "page_number": 1,
            })
        if not chunks:
            return
        try:
            save_chunks(chunks, module="faq", tenant_id=self.tenant_id)
            summary["faqs_created"] = len(chunks)
        except Exception as exc:
            logger.error("FAQ placement failed for %s: %s", self.tenant_id, exc)
            summary["warnings"].append(f"FAQs were not written: {exc}")

    def _place_knowledge(self, draft: Dict[str, Any], summary: Dict[str, Any],
                         background_tasks: Any) -> None:
        """Index the document's prose into the tenant's vector knowledge base."""
        articles = draft.get("knowledge_articles") or []
        if not articles:
            return
        try:
            from kb.services.rag import add_document_to_kb
        except ImportError:
            try:
                from backend.kb.services.rag import add_document_to_kb  # type: ignore
            except ImportError:
                summary["warnings"].append("Knowledge base module unavailable; articles were not indexed.")
                return

        source = (draft.get("source_files") or ["pdf_intake"])[0]
        text = "\n\n".join(a.get("markdown_content") or "" for a in articles if a.get("markdown_content"))
        if not text.strip():
            return
        try:
            result = add_document_to_kb(
                filename=source, text=text, category="company",
                media_type="pdf", tenant_id=self.tenant_id,
            )
            if result.get("success"):
                summary["kb_chunks_indexed"] = int(result.get("chunks") or 0)
            else:
                summary["warnings"].append(f"Knowledge base indexing failed: {result.get('error')}")
        except Exception as exc:
            logger.error("Knowledge placement failed for %s: %s", self.tenant_id, exc)
            summary["warnings"].append(f"Knowledge base was not indexed: {exc}")
            return

        self._schedule_reindex(background_tasks)

    def _schedule_reindex(self, background_tasks: Any) -> None:
        """Rebuild the vector index off the request thread.

        The rebuild is already tenant-partitioned (kb/services/rag.py), but it
        walks every document, so it does not belong on the request path.
        """

        def _rebuild():
            try:
                from kb.services.rag import build_chunks_for_kb, generate_missing_embeddings, rebuild_faiss_index
                build_chunks_for_kb()
                generate_missing_embeddings()
                rebuild_faiss_index()
                logger.info("PDF_INTAKE_INDEX_REBUILT | tenant=%s", self.tenant_id)
            except Exception as exc:
                logger.warning("Vector index rebuild after pdf intake failed: %s", exc)

        if background_tasks is not None:
            background_tasks.add_task(_rebuild)
        else:
            import threading
            threading.Thread(target=_rebuild, daemon=True).start()

    # ── coordinator ───────────────────────────────────────────────────────

    def process(self, filepaths: List[str], publish: bool = False,
                background_tasks: Any = None) -> Dict[str, Any]:
        """Extract → bifurcate → (optionally) publish, for one or more PDFs."""
        docs = [self.extract(path) for path in filepaths]
        draft = self.bifurcate(docs)
        if not publish:
            return draft
        return self.publish(draft, background_tasks=background_tasks)
