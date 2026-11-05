"""Safety guards for document bifurcation.

Prevents cross-vertical contamination, junk catalog rows, and brand/profile
overwrites when a PDF is uploaded into an already-configured tenant.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

VALID_VERTICALS = {
    "real_estate",
    "tours_travel",
    "ecommerce",
    "it_software",
    "banking",
    "finance",
    "healthcare",
    "generic",
}

# Strong lexical cues used to detect offerings that belong to another vertical.
_VERTICAL_OFFERING_HINTS: Dict[str, Tuple[str, ...]] = {
    "real_estate": (
        "bhk", "villa", "apartment", "penthouse", "sqft", "sq ft", "rera",
        "possession", "plot", "flat", "estate", "residency", "tower", "floor plan",
        "carpet area", "site visit",
    ),
    "tours_travel": (
        "tour", "package", "itinerary", "visa", "holiday", "destination",
        "nights", "night/", "flight", "hotel", "trip", "cruise", "sightseeing",
        "backwaters", "getaway",
    ),
    "banking": (
        "loan", "account", "debit card", "credit card", "deposit", "emi",
        "savings", "fixed deposit", "interest rate", "kyc", "ifsc", "atm",
        "fd rate", "savings account",
    ),
    "finance": (
        "mutual fund", "nbfc", "gold loan", "itr", "gst filing", "portfolio",
        "sip", "equity",
    ),
    "ecommerce": (
        "sku", "mrp", "pack of", "warranty", "cod", "add to cart", "chikki",
        "ghee", "snack", "in stock",
    ),
    "it_software": (
        "software", "saas", "api", "cloud", "devops", "microcontroller",
        "iot", "sensor", "irrigation", "dashboard", "kubernetes", "react",
        "fastapi", "mobile app", "tech stack",
    ),
    "healthcare": (
        "clinic", "doctor", "hospital", "patient", "consultation", "opd",
        "pharmacy", "diagnosis",
    ),
}

_JUNK_NAMES = frozenset({
    # Generic filler words that convey zero product information
    "item", "product", "products", "n/a", "na", "none", "test", "untitled",
    "name", "category", "service", "services", "offering", "offerings",
    "package", "packages", "title", "description",
    # Legal / policy headings — NEVER offerings
    "terms & conditions",
    "privacy policy",
    "data protection policy",
    "cookie policy",
    "refund policy",
    "cancellation policy",
    "disclaimer",
    # Structural noise: section markers, TOC, pagination, annexures
    "table of contents",
    "page 1", "page 2", "page 3", "page 4", "page 5",
    "page 6", "page 7", "page 8", "page 9", "page 10",
    "section 1", "section 2", "section 3",
    "appendix", "annexure",
    "note:", "note :",
    "price list notes",
    # FAQ-like and checklist noise from PDF extractions
    "frequently asked questions",
    "faqs",
    "booking checklist",
    "checklist",
})


def _is_junk_start(name: str) -> bool:
    """Return True when *name* begins with a junk heading pattern.

    Catches cases like ``"Terms & Conditions of Service"`` or
    ``"Booking Checklist Confirm …"`` that don't exactly match
    :data:`_JUNK_NAMES`.
    """
    stripped = name.strip().lstrip("─-•·*•–").strip()
    if not stripped:
        return False
    low = stripped.lower()
    junk_prefixes = (
        "terms", "terms &", "terms of", "privacy",
        "faq", "frequently asked",
        "checklist", "booking checklist",
        "disclaimer", "section ", "page ",
        "appendix", "annexure",
        "note:", "note :", "note.",
        "table of contents",
        "price list", "important notes",
    )
    return any(low.startswith(p) for p in junk_prefixes)


def _looks_like_sentence(text: str) -> bool:
    """Heuristic: a string is likely a sentence/paragraph rather than a name.

    Brand names and offering titles are short phrases; they rarely contain
    three-or-more function words ("and", "for", "the", "with", "or", "in",
    "to") scattered through the text.  Names longer than 25 chars should also
    be suspicious when combined with functional-word density.
    """
    STOP_WORDS = {"and", "for", "the", "with", "or", "in", "to", "of", "a",
                  "an", "on", "at", "by", "from", "as", "is", "was", "are",
                  "were", "been", "being", "have", "has", "had", "not", "no"}
    words = set(re.findall(r"\b[a-z]{2,}\b", text.lower()))
    if not words:
        return False
    func_count = len(words & STOP_WORDS)
    if func_count >= 3 and len(text) > 25:
        return True
    return False


def resolve_locked_vertical(
    tenant_id: str,
    extracted_vertical: str = "generic",
) -> str:
    """Prefer the tenant's registered/draft vertical over PDF-guessed vertical.

    Only falls back to the extracted value when the tenant has no real vertical
    yet (empty / generic). This stops an IoT PDF from flipping a banking tenant
    to ``it_software``.
    """
    extracted = (extracted_vertical or "generic").strip().lower()
    if extracted not in VALID_VERTICALS:
        extracted = "generic"

    locked = ""
    tid = (tenant_id or "").strip()
    if tid:
        try:
            from shared.tenancy import store as tenancy_store
            rec = tenancy_store.get_tenant(tid) or {}
            cand = str(rec.get("vertical") or "").strip().lower()
            if cand in VALID_VERTICALS and cand != "generic":
                locked = cand
        except Exception:
            # deliberate: resolver chain — fall through to the next source.
            logger.debug("LOCKED_VERTICAL_RESOLVE_STEP_SKIPPED | source=tenant tid=%s", tid, exc_info=True)
        if not locked:
            try:
                from shared.tenancy import store as tenancy_store
                draft = tenancy_store.get_draft(tid) or {}
                cand = str(draft.get("vertical") or "").strip().lower()
                if cand in VALID_VERTICALS and cand != "generic":
                    locked = cand
            except Exception:
                # deliberate: resolver chain — fall through to the next source.
                logger.debug("LOCKED_VERTICAL_RESOLVE_STEP_SKIPPED | source=draft tid=%s", tid, exc_info=True)
        if not locked:
            try:
                from shared.tenancy import loader as tenancy_loader
                prof = tenancy_loader.get_tenant_profile(tid)
                cand = str(getattr(prof, "vertical", "") or "").strip().lower()
                if cand in VALID_VERTICALS and cand != "generic":
                    locked = cand
            except Exception:
                # deliberate: resolver chain — no locked vertical found.
                logger.debug("LOCKED_VERTICAL_RESOLVE_STEP_SKIPPED | source=profile tid=%s", tid, exc_info=True)

    return locked or extracted


def is_junk_offering_name(name: str) -> bool:
    """Reject placeholders, junk headings and sentence-like blobs from becoming offerings.

    Three layers of defence:
    1. Exact-set lookup (_JUNK_NAMES — O(1))
    2. Prefix-based junk heading detection ("Terms …", "FAQ …")
    3. Length guard for long noise strings that slipped through
    """
    n = (name or "").strip()
    if len(n) < 3:
        return True
    # Numeric-only or numeric-wrapped noise → junk
    if re.fullmatch(r"\d{1,4}", n):
        return True
    if re.fullmatch(r"[\d\W_]+", n):
        return True
    # Exact set match (canonical junk list)
    if n.casefold() in _JUNK_NAMES:
        return True
    # "Product 1", "Item 2" style noise
    if re.fullmatch(r"(product|item|offering|package)\s*\d+", n, flags=re.I):
        return True
    # Prefix guard: catches "Terms & Conditions of Service",
    # "Booking Checklist Confirm …", "FAQ section header" etc.
    if _is_junk_start(n):
        return True
    # Offerings longer than ~50 chars are suspicious (real product names
    # are short phrases; anything longer is usually section text).
    if len(n) > 50:
        return True
    # Sentence-level noise with moderate functional-word density
    if _looks_like_sentence(n):
        return True
    return False


def offering_vertical_scores(
    name: str = "",
    category: str = "",
    description: str = "",
    attrs: Any = None,
) -> Dict[str, int]:
    blob = f"{name} {category} {description}"
    if isinstance(attrs, dict):
        try:
            blob += " " + json.dumps(attrs, ensure_ascii=False)
        except Exception:
            blob += " " + str(attrs)
    blob_l = blob.lower()
    return {
        vertical: sum(1 for cue in cues if cue in blob_l)
        for vertical, cues in _VERTICAL_OFFERING_HINTS.items()
    }


def offering_matches_vertical(
    name: str,
    category: str = "",
    description: str = "",
    attrs: Any = None,
    locked_vertical: str = "generic",
) -> bool:
    """True when the offering is compatible with the tenant vertical.

    Rejects rows that clearly belong to another vertical with no signal for the
    locked one (Prestige estates into tours_travel, IoT sensors into banking).
    """
    locked = (locked_vertical or "generic").strip().lower()
    if locked not in VALID_VERTICALS or locked == "generic":
        return True

    scores = offering_vertical_scores(name, category, description, attrs)
    own = scores.get(locked, 0)
    best_other_vertical, best_other = "", 0
    for vertical, score in scores.items():
        if vertical == locked:
            continue
        if score > best_other:
            best_other_vertical, best_other = vertical, score

    if best_other >= 2 and own == 0:
        return False
    if best_other >= 3 and own < best_other:
        return False
    # Soft reject: name alone is a strong foreign cue (e.g. "Prestige Gardenia Estates")
    if best_other >= 2 and own == 0 and best_other_vertical:
        return False
    return True


def is_placeholder_brand_value(key: str, value: Any, tenant_id: str = "") -> bool:
    """Detect invented / default brand fields that must not overwrite real ones."""
    raw = str(value or "").strip()
    if not raw:
        return True
    tid = (tenant_id or "").strip().lower()
    low = raw.lower()
    placeholders = {
        "name": {
            tid.replace("-", " "),
            tid.replace("_", " "),
            "your company",
            "company name",
            "brand name",
        },
        "bot_name": {
            f"{tid} assistant",
            "assistant",
            "bot name",
            "support bot",
        },
        "website": {
            f"https://www.{tid}.com",
            f"http://www.{tid}.com",
            f"www.{tid}.com",
            "https://example.com",
            "http://example.com",
        },
        "support_email": {
            f"support@{tid}.com",
            "support@example.com",
            "info@example.com",
        },
        "support_phone": {
            "+91 9800000000",
            "+91 0000000000",
            "0000000000",
            "9999999999",
        },
        "tagline": {"", "your tagline", "tagline"},
        "signature": {
            f"{tid.replace('-', ' ').title()} — at your service.",
            "at your service",
        },
    }
    bucket = placeholders.get(key) or set()
    if low in bucket:
        return True
    if key == "website" and tid and tid in low and low.endswith(".com") and "http" in low:
        # Generated https://www.{tenant}.com style
        if re.fullmatch(rf"https?://(www\.)?{re.escape(tid)}\.com/?", low):
            return True
    if key == "support_phone" and re.fullmatch(r"[\s+0-9-]{8,}", raw) and set(re.sub(r"\D", "", raw)) <= {"0", "9"}:
        return True

    # ── Brand-name corruption guards (key=="name"): stop PDF-section text from
    #    becoming a real tenant's display / company name. ──────────────────────
    if key == "name":
        # Too long for a brand/company name (real names rarely exceed 40 chars)
        if len(raw) > 40:
            return True
        # Starts with known junk heading patterns ("Terms …", "FAQ …", etc.)
        if _is_junk_start(raw):
            return True
        # Reads like a paragraph/sentence rather than a proper name
        if _looks_like_sentence(raw):
            return True

    return False


def merge_brand_fill_empty(
    existing_brand: Optional[Dict[str, Any]],
    incoming_brand: Optional[Dict[str, Any]],
    tenant_id: str = "",
) -> Dict[str, Any]:
    """Fill only empty/placeholder brand keys; never overwrite real values."""
    merged = dict(existing_brand or {})
    for key, val in (incoming_brand or {}).items():
        if val in (None, "", [], {}):
            continue
        if is_placeholder_brand_value(key, val, tenant_id):
            continue
        cur = merged.get(key)
        if not cur or is_placeholder_brand_value(key, cur, tenant_id):
            merged[key] = val
    return merged


def filter_offerings_for_tenant(
    offerings: Any,
    locked_vertical: str,
) -> Tuple[list, list]:
    """Return (accepted, rejected) offerings after junk + vertical filters."""
    accepted: list = []
    rejected: list = []
    raw = offerings if isinstance(offerings, list) else []
    for off in raw:
        if not isinstance(off, dict):
            rejected.append({"name": str(off), "reason": "not_object"})
            continue
        name = str(off.get("name") or "").strip()
        if is_junk_offering_name(name):
            rejected.append({"name": name or "(empty)", "reason": "junk_name"})
            continue
        category = str(off.get("category") or "")
        desc = str(off.get("description") or off.get("short_description") or "")
        attrs = off.get("attrs")
        if not offering_matches_vertical(
            name, category, desc, attrs, locked_vertical=locked_vertical
        ):
            rejected.append({
                "name": name,
                "reason": "off_vertical",
                "locked_vertical": locked_vertical,
            })
            continue
        accepted.append(off)
    return accepted, rejected
