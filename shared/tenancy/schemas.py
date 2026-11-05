"""Pydantic models for the tenant profile.

A tenant profile is the *whole* behavioural surface of a brand: what features
exist, what the bot calls things, which intents it understands, which flows it
can run, and which guardrails bind the LLM.

Merge order (decision D5 + the guiding principles):
    vertical defaults  ->  clients/<id>/config.json  ->  DB override

Lists replace, never append. Everything below validates on load, so a bad
publish is rejected at write time rather than blowing up mid-conversation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Feature flags ─────────────────────────────────────────────────────────
# Every flag defaults to OFF except the ones a vertical explicitly turns on.
# Gating is additive: a feature is live only when BOTH the flag is on AND the
# code path checks it. See shared/tenancy/gating.py.
FEATURE_FLAGS = (
    # commerce (TrooGood)
    "cart",
    "buy_now",
    "orders",
    "payments",
    "track_order",
    "returns",
    "brochure_pdf",
    "distributors",
    # catalogue / offerings (all verticals)
    "offerings",
    "offering_details",
    # content types a vertical surfaces in its menu
    "services",
    "products",
    "products_schemes",
    # conversational commerce / lead gen
    "lead_capture",
    "quote",
    "callback",
    "book_appointment",
    "handoff",
    # support substrate
    "faq",
    "kb",
    "complaints",
    "campaigns",
    "human_handover",
)

# Menu / intent rows that declare a flow must keep that flow's feature live.
# Kits often ship ``Custom Itinerary → get_quote`` while leaving ``quote=false``,
# which shows the button then dumps the tap into FAQ.
_FLOW_FEATURE_HINTS: Dict[str, str] = {
    "get_quote": "quote",
    "book_callback": "callback",
    "book_appointment": "book_appointment",
    "enquire": "lead_capture",
    "raise_ticket": "complaints",
    "place_order": "orders",
    "track_order": "track_order",
}

VERTICALS = (
    "ecommerce",
    "it_software",
    "tours_travel",
    "banking",
    "finance",
    "healthcare",
    "real_estate",
    "generic",
)

STEP_TYPES = (
    # MVP (build now)
    "ask",          # free-text question, optional validation
    "choice",       # pick one of N
    "confirm",      # yes/no gate
    "say",          # send a message, continue
    "save_lead",    # persist collected data
    "notify",       # email or signed webhook
    "handoff",      # mark conversation paused + alert a human
    # data variants of save_lead + notify (implemented as composites)
    "book_appointment",
    "create_ticket",
)


class _Base(BaseModel):
    # populate_by_name lets a field carry a friendlier JSON alias (e.g. the
    # "validate" step rule is stored as validate_rule in Python).
    # Ignore unknown kit/admin fields (legal_name, domain_answers, …) so a rich
    # client pack still loads. Falling back to vertical defaults used to replace
    # the real brand with the literal "Brand" and mix tenant greetings.
    model_config = ConfigDict(
        extra="ignore", validate_assignment=True, populate_by_name=True
    )


# ── Contact-field checks ────────────────────────────────────────────────────────────
# Empty means "not set" and always passes; only filled-in fields are checked.
# These run wherever a profile is built, so a bad value is rejected at draft
# save (as a warning), at publish / approval (hard 422), and on every load.

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# Phone numbers must carry their country code followed by exactly 10 digits;
# spaces, dashes and parentheses are tolerated, e.g. "+91 98765 43210".
# The same country-code table the registration wizard uses: it rules out
# "country code + 11 digits" look-alikes such as +91 1800 123 4567, which a
# plain length check cannot distinguish from a 3-digit code + 10 digits.
_PHONE_RE = re.compile(r"^\+\d{11,13}$")
_PHONE_COUNTRY_CODES = frozenset((
    '212', '213', '216', '218', '220', '221', '222', '223', '224', '225',
    '226', '227', '228', '229', '230', '231', '232', '233', '234', '235',
    '236', '237', '238', '239', '240', '241', '242', '243', '244', '245',
    '246', '248', '249', '250', '251', '252', '253', '254', '255', '256',
    '257', '258', '260', '261', '262', '263', '264', '265', '266', '267',
    '268', '269', '290', '291', '297', '298', '299', '350', '351', '352',
    '353', '354', '355', '356', '357', '358', '359', '370', '371', '372',
    '373', '374', '375', '376', '377', '378', '380', '381', '382', '383',
    '385', '386', '387', '389', '420', '421', '423', '500', '501', '502',
    '503', '504', '505', '506', '507', '508', '509', '590', '591', '592',
    '593', '594', '595', '596', '597', '598', '599', '670', '672', '673',
    '674', '675', '676', '677', '678', '679', '680', '681', '682', '683',
    '685', '686', '687', '688', '689', '690', '691', '692', '850', '852',
    '853', '855', '856', '870', '880', '886', '960', '961', '962', '963',
    '964', '965', '966', '967', '968', '970', '971', '972', '973', '974',
    '975', '976', '977', '992', '993', '994', '995', '996', '998', '20', '27',
    '30', '31', '32', '33', '34', '36', '39', '40', '41', '43', '44', '45',
    '46', '47', '48', '49', '51', '52', '53', '54', '55', '56', '57', '58',
    '60', '61', '62', '63', '64', '65', '66', '81', '82', '84', '86', '90',
    '91', '92', '93', '94', '95', '98', '1', '7',
))
_URL_RE = re.compile(r"^(https?://)?([\w-]+\.)+[A-Za-z]{2,}(:\d+)?(/\S*)?$")
# First URL-shaped token inside noisy text, e.g. "https://x.co (rates: https://y.co)".
_URL_TOKEN_RE = re.compile(r"https?://[^\s()<>\",;]+|(?<![\w/.-])[\w-]+\.[A-Za-z]{2,}(?:/\S*)?", re.I)


def _require_email(value: str, field: str) -> str:
    v = (value or "").strip()
    if v and not _EMAIL_RE.fullmatch(v):
        raise ValueError(f"{field} '{v}' is not a valid email address")
    return value


def _phone_ok(compact: str) -> bool:
    if not _PHONE_RE.fullmatch(compact):
        return False
    body = compact[1:]
    return any(
        body.startswith(cc) and len(body) - len(cc) == 10
        for cc in _PHONE_COUNTRY_CODES
    )


def extract_first_url(value: str) -> str:
    """Pick the first valid URL from kit/LLM text that wraps it in notes."""
    raw = (value or "").strip()
    if not raw:
        return ""
    candidates = [raw]
    candidates.extend(m.group(0) for m in _URL_TOKEN_RE.finditer(raw))
    for candidate in candidates:
        cand = candidate.strip().rstrip(").,;:]")
        if _URL_RE.fullmatch(cand):
            if not cand.lower().startswith(("http://", "https://")):
                cand = "https://" + cand
            return cand
    return ""


def extract_first_valid_phone(value: str) -> str:
    """Pick the first +CC 10-digit number from kits that paste multi-line contacts."""
    raw = (value or "").strip()
    if not raw:
        return ""
    candidates = [raw]
    candidates.extend(
        m.group(0).strip()
        for m in re.finditer(r"\+\d[\d\s\-()]{8,24}\d", raw)
    )
    seen = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        compact = re.sub(r"[\s\-()]", "", candidate)
        if _phone_ok(compact):
            return candidate
    return ""


def _require_phone(value: str, field: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return value
    compact = re.sub(r"[\s\-()]", "", raw)
    if _phone_ok(compact):
        return value
    # Kits often ship "toll-free / landline / WhatsApp +91 …" — keep the first valid mobile.
    extracted = extract_first_valid_phone(raw)
    if extracted:
        return extracted
    raise ValueError(
        f"{field} must be a country code followed by a 10-digit number, e.g. +91 98765 43210 (got '{value}')"
    )


def _require_url(value: str, field: str) -> str:
    v = (value or "").strip()
    if not v or _URL_RE.fullmatch(v):
        return value
    # Kits/LLM extracts paste "https://x.co (rates & pages: https://y.co)" —
    # keep the first URL instead of failing the whole profile publish.
    extracted = extract_first_url(v)
    if extracted:
        return extracted
    raise ValueError(f"{field} '{v}' is not a valid website URL")


def missing_contact_fields(brand) -> List[str]:
    """Required contact fields every publishable profile must carry."""
    missing: List[str] = []
    if not str(getattr(brand, "support_email", "") or "").strip():
        missing.append("brand.support_email")
    if not str(getattr(brand, "support_phone", "") or "").strip():
        missing.append("brand.support_phone")
    return missing


def is_valid_email(value: str) -> bool:
    """Public email check for other modules (e.g. the distributor API)."""
    v = (value or "").strip()
    return bool(v) and bool(_EMAIL_RE.fullmatch(v))


def is_valid_phone(value: str) -> bool:
    """Country code followed by exactly 10 digits — the one rule for profiles."""
    v = re.sub(r"[\s\-()]", "", (value or "").strip())
    if not v or not _PHONE_RE.fullmatch(v):
        return False
    body = v[1:]
    return any(
        body.startswith(cc) and len(body) - len(cc) == 10
        for cc in _PHONE_COUNTRY_CODES
    )


def is_valid_phone_input(value: str, wanted: int = 10) -> bool:
    """Validate user-entered phone number in conversation flows and forms.

    Accepts:
    - Exactly `wanted` digits (e.g. 10 digits: 9876543210, with optional spaces/dashes/parentheses)
    - Leading trunk prefix '0' + `wanted` digits (e.g. 09876543210)
    - Starting with '+' and a valid country code (1-3 digits) + exactly `wanted` digits (e.g. +91 9876543210)
    - Country code '91' without '+' + exactly 10 digits (e.g. 919876543210)

    Rejects:
    - Any number where subscriber digits exceed `wanted` (e.g. 11, 12, 13, 15 digits)
    - A '+' number whose digits after the country code are fewer OR more than
      `wanted` (e.g. +91 12345678, +9198765432101)
    - Numbers with fewer than `wanted` digits
    - Strings with invalid characters
    """
    if not value:
        return False
    raw = value.strip()
    if not re.match(r"^\+?[\d\s\-()]+$", raw):
        return False
    digits = re.sub(r"\D", "", raw)

    # An explicit '+<country code>' must be followed by exactly `wanted`
    # subscriber digits (e.g. +91 98765 43210). This is checked first: a string
    # like "+91 12345678" totals 10 digits but only 8 after the country code, so
    # it must NOT pass the bare-length rule below.
    if raw.startswith("+"):
        return any(
            digits.startswith(cc) and len(digits) - len(cc) == wanted
            for cc in _PHONE_COUNTRY_CODES
        )

    if len(digits) == wanted:
        return True
    if digits.startswith("0") and len(digits) == wanted + 1:
        return True
    if wanted == 10 and len(digits) == 12 and digits.startswith("91"):
        return True
    return False


# ── Leaves ────────────────────────────────────────────────────────────────

class Features(_Base):
    """Which capabilities this tenant has switched on."""

    cart: bool = False
    buy_now: bool = False
    orders: bool = False
    payments: bool = False
    track_order: bool = False
    returns: bool = False
    brochure_pdf: bool = False
    distributors: bool = False
    offerings: bool = True
    offering_details: bool = True
    services: bool = True
    products: bool = True
    products_schemes: bool = True
    lead_capture: bool = False
    quote: bool = False
    callback: bool = False
    book_appointment: bool = False
    handoff: bool = True
    faq: bool = True
    kb: bool = True
    complaints: bool = True
    campaigns: bool = False
    human_handover: bool = True

    def is_on(self, flag: str) -> bool:
        return bool(getattr(self, flag, False)) if flag in FEATURE_FLAGS else False


class Vocabulary(_Base):
    """Vertical-aware nouns. Replaces every hardcoded product/cart/order string."""

    item_noun: str = "products"
    item_noun_singular: str = "product"
    browse_label: str = "Browse Products"
    catalog_label: str = "View Brochure"
    catalog_noun: str = "brochure"
    order_noun: str = "order"
    order_noun_plural: str = "orders"
    cart_noun: str = "cart"
    checkout_label: str = "Checkout"
    brochure_label: str = "Brochure"
    brochure_noun: str = "brochure"
    offer_noun: str = "offer"
    offer_noun_plural: str = "offers"
    lead_noun: str = "enquiry"
    enquiry_label: str = "Enquire"
    quote_label: str = "Get a Quote"
    callback_label: str = "Request a Callback"
    support_label: str = "Support"
    track_label: str = "Track Order"


class Brand(_Base):
    name: str = "Brand"
    tagline: str = ""
    website: str = ""
    support_email: str = ""
    support_phone: str = ""
    signature: str = ""
    bot_name: str = ""
    # Publicly hosted company logo, shown in the tenant's console header.
    logo_url: str = ""

    @field_validator("website")
    @classmethod
    def _check_website(cls, v):
        return _require_url(v, "brand.website")

    @field_validator("support_email")
    @classmethod
    def _check_support_email(cls, v):
        return _require_email(v, "brand.support_email")

    @field_validator("support_phone")
    @classmethod
    def _check_support_phone(cls, v):
        return _require_phone(v, "brand.support_phone")

    def display_name(self) -> str:
        return self.bot_name or self.name


class PromptSpec(_Base):
    """Everything the LLM system prompt needs, as data.

    This is the shape that used to be hardcoded in ``routing/prompts.py`` under
    ``COMPANY_TYPE_CONFIGS``, keyed off a ``COMPANY_TYPE`` env var. Moving it
    into the profile is what lets a new tenant ship without touching prompt code.
    The legacy dict is still used as a fallback for call sites that have not been
    migrated (and for eval runs against the old stack).
    """

    name: str = ""
    tone: str = "helpful"
    industry: str = "business"
    # What a question in this business is *about*. Used to decide whether a
    # question is domain-specific enough to answer from the catalogue/KB.
    domain_specific_queries: str = "products, services, pricing, or support"
    domain_specific_topics: str = "products, services, pricing, support, information"
    # Questions that mean "hand this to a person".
    support_trigger_topics: str = "custom requests, complex inquiries, urgent issues"
    complex_topics: str = "custom requirements, enterprise needs, complex issues"
    # What the "browse" surface lists - products, services, destinations...
    listing_type: str = "products, services, or information"
    # Free-text domain nouns. Classification must never see a term that this
    # profile has switched off; lint_forbidden_terms enforces that.
    keywords: List[str] = Field(default_factory=list)
    # Per-tenant overrides of the shared prompt templates. Empty means "use the
    # built-in template for this vertical".
    system_prompt: str = ""
    strict_prompt: str = ""


class BusinessHours(_Base):
    """Local trading window. Out of hours, flows switch to a callback variant."""

    timezone: str = "Asia/Kolkata"
    always_open: bool = True
    open: str = "09:00"
    close: str = "21:00"
    open_days: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])
    out_of_hours_message: str = (
        "We are currently outside our working hours. Leave your details and "
        "our team will call you back on the next working day."
    )

    @field_validator("open_days")
    @classmethod
    def _valid_days(cls, v):
        for d in v:
            if d not in range(7):
                raise ValueError(f"open_days entries must be 0..6, got {d}")
        return v


class Guardrails(_Base):
    """What the bot must never do, and what to do instead."""

    never_state: List[str] = Field(default_factory=list)
    never_state_notes: Dict[str, str] = Field(default_factory=dict)
    escalate_keywords: List[str] = Field(default_factory=list)
    escalation_message: str = "Let me connect you with our team."
    forbidden_terms: List[str] = Field(default_factory=list)
    handoff_keywords: List[str] = Field(default_factory=lambda: ["human", "agent", "person"])

    def blocks(self, text: str) -> bool:
        low = (text or "").lower()
        return any(t and t.lower() in low for t in self.forbidden_terms)


class NotificationChannel(_Base):
    """Where a lead / handoff / quote gets pushed."""

    type: Literal["email", "webhook"]
    to: str
    label: str = ""
    enabled: bool = True
    # Only used for type == "webhook"
    secret: str = ""

    @model_validator(mode="after")
    def _check_to(self):
        v = (self.to or "").strip()
        if not v:
            return self
        if self.type == "email" and not _EMAIL_RE.fullmatch(v):
            raise ValueError(f"channel destination '{v}' is not a valid email address")
        if self.type == "webhook" and not _URL_RE.fullmatch(v):
            raise ValueError(f"channel destination '{v}' is not a valid URL")
        return self


class Notifications(_Base):
    """Per-tenant delivery config. CATALOGUE_PDF_URL becomes notifications.brochure_url."""

    sales_email: str = ""
    support_email: str = ""
    brochure_url: str = ""
    brochure_label: str = ""
    channels: List[NotificationChannel] = Field(default_factory=list)

    @field_validator("sales_email")
    @classmethod
    def _check_sales_email(cls, v):
        return _require_email(v, "notifications.sales_email")

    @field_validator("support_email")
    @classmethod
    def _check_support_email(cls, v):
        return _require_email(v, "notifications.support_email")

    @field_validator("brochure_url")
    @classmethod
    def _check_brochure_url(cls, v):
        return _require_url(v, "notifications.brochure_url")

    def active_channels(self, kind: str | None = None) -> List[NotificationChannel]:
        out = [c for c in self.channels if c.enabled and c.to]
        if kind:
            out = [c for c in out if c.type == kind]
        return out


# ── Menu ──────────────────────────────────────────────────────────────────

class MenuButton(_Base):
    """A single row in the WhatsApp list menu.

    ``id`` is load-bearing: the flow runner and routing dispatch on it exactly.
    """

    id: str
    title: str
    description: str = ""
    section: str = "General"
    icon: str = ""
    sort_order: int = 0
    # Independently controls whether this option appears in the customer menu.
    enabled: bool = True
    # Button only renders when this feature flag is on (None = always).
    requires_feature: Optional[str] = None
    # Button only renders outside business hours when True.
    out_of_hours_only: bool = False
    # Optional flow to start when tapped.
    flow: Optional[str] = None
    # Optional intent to route to when tapped.
    intent: Optional[str] = None


def _menu_button_title_key(title: str) -> str:
    """Normalize menu titles so 'Our Services' matches across kit/default clones."""
    return re.sub(r"[-_\s]+", "_", (title or "").strip().casefold()).strip("_")


def _prefer_menu_button(existing: Dict[str, Any], challenger: Dict[str, Any]) -> Dict[str, Any]:
    """Keep one button per title; prefer vertical ``menu_*`` ids and richer fields."""
    ex_id = str(existing.get("id") or "")
    ch_id = str(challenger.get("id") or "")
    keep = existing
    drop = challenger
    if ch_id.startswith("menu_") and not ex_id.startswith("menu_"):
        keep, drop = challenger, existing
    # Fill blanks on the survivor from the discarded clone (kit text, icons…).
    out = dict(keep)
    for key in ("description", "section", "icon", "intent", "flow", "requires_feature"):
        if not out.get(key) and drop.get(key):
            out[key] = drop[key]
    if out.get("enabled") is None and drop.get("enabled") is not None:
        out["enabled"] = drop["enabled"]
    return out


def _dedupe_menu_button_dicts(buttons: List[Any]) -> List[Any]:
    """Collapse same-title menu rows (e.g. menu_services vs our_services)."""
    if not isinstance(buttons, list) or len(buttons) < 2:
        return buttons
    kept: List[Any] = []
    index: Dict[str, int] = {}
    for item in buttons:
        if not isinstance(item, Mapping):
            kept.append(item)
            continue
        raw = dict(item)
        title_key = _menu_button_title_key(str(raw.get("title") or ""))
        if not raw.get("id") and title_key:
            # An id-less button (old LLM bifurcator writes) would otherwise
            # fail MenuButton validation and drop the tenant to the skeleton.
            raw["id"] = re.sub(r"[^a-z0-9]+", "_", str(raw.get("title") or "").lower()).strip("_") or "menu_item"
        if not title_key:
            kept.append(raw)
            continue
        pos = index.get(title_key)
        if pos is None:
            index[title_key] = len(kept)
            kept.append(raw)
            continue
        kept[pos] = _prefer_menu_button(kept[pos], raw)
    return kept


class MenuSpec(_Base):
    key: str = "kb_main"
    header: str = ""
    body: str = "What would you like help with?"
    footer: str = "Or type your question directly"
    button_text: str = "Show Options"
    buttons: List[MenuButton] = Field(default_factory=list)

    @field_validator("buttons", mode="before")
    @classmethod
    def _collapse_duplicate_titles(cls, value: Any) -> Any:
        """Last-line defence: kit slug ids must not double vertical default titles."""
        return _dedupe_menu_button_dicts(value) if isinstance(value, list) else value

    def visible_buttons(
        self, features: Features, *, in_business_hours: bool = True
    ) -> List[MenuButton]:
        """Filter menu visibility. Services remain the real entitlement gate."""
        out = []
        for b in self.buttons:
            if not b.enabled:
                continue
            if not (getattr(b, "title", None) or "").strip():
                continue
            if b.requires_feature and not features.is_on(b.requires_feature):
                continue
            if b.out_of_hours_only and in_business_hours:
                continue
            out.append(b)
        return sorted(out, key=lambda b: b.sort_order)


# ── Intents ───────────────────────────────────────────────────────────────

class IntentSpec(_Base):
    name: str
    examples: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    # Intent is only live when this feature flag is on (None = always).
    requires_feature: Optional[str] = None
    # Flow to start when this intent wins.
    flow: Optional[str] = None
    # Informational intents answer directly with this text instead of routing
    # to the RAG pipeline (projects, careers, technologies, benefits panels).
    # Tenant data, resolved through the profile merge chain.
    answer: Optional[str] = None
    enabled: bool = True


# ── Flows ─────────────────────────────────────────────────────────────────

class FlowStep(_Base):
    """One step. A flow is just an ordered list of these in the profile.

    Branching is expressed with ``on`` (map of answer -> next step id) plus the
    implicit fallthrough to the next step in the list. No code branches, ever.
    """

    id: str
    type: str

    # ask / say / confirm / handoff / create_ticket
    prompt: str = ""
    # collected-data key this step writes to (ask)
    key: str = ""
    # email | phone | name | digits | min_len:<n>
    # Named validate_rule in Python because `validate` is a pydantic attribute;
    # profile JSON uses the shorter "validate".
    validate_rule: str = Field(default="", alias="validate")
    optional: bool = False
    invalid_message: str = ""
    # number of invalid attempts before the flow bails out
    max_attempts: int = 3
    # ask: the value may already be known (e.g. the package the user tapped
    # before the flow started). When true and the key is already collected,
    # the step is skipped instead of asking again.
    prefill: bool = False

    # choice
    options: List[str] = Field(default_factory=list)

    # confirm
    yes_label: str = "Yes"
    no_label: str = "No"

    # say / confirm terminal text
    message: str = ""

    # save_lead
    collect_keys: List[str] = Field(default_factory=list)
    lead_source: str = ""

    # notify
    channels: List[str] = Field(default_factory=list)
    subject: str = ""

    # handoff
    handoff_reason: str = ""
    store_context: bool = True
    # handoff: also float the conversation to the top of the admin live inbox
    # as a human-handover item, so an agent picks it up.
    escalate: bool = False

    # routing
    on: Dict[str, str] = Field(default_factory=dict)
    next: str = ""
    goto: str = ""

    @field_validator("type")
    @classmethod
    def _known_type(cls, v):
        if v not in STEP_TYPES:
            raise ValueError(
                f"unknown step type {v!r}; expected one of {sorted(STEP_TYPES)}"
            )
        return v


class FlowSpec(_Base):
    name: str
    intent: str = ""
    # Flow is only offered when this feature flag is on (None = always).
    requires_feature: Optional[str] = None
    description: str = ""
    start_message: str = ""
    success_message: str = ""
    failure_message: str = "Something went wrong. Please try again."
    # Offer a callback instead of live handoff when out of business hours.
    out_of_hours_variant: Optional[str] = None
    steps: List[FlowStep] = Field(default_factory=list)

    def step(self, step_id: str) -> Optional[FlowStep]:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def first_step_id(self) -> str:
        return self.steps[0].id if self.steps else ""

    def link_steps(self) -> None:
        """Fill in implicit ``next`` hops so authoring stays terse.

        A step with no ``on``/``next``/``goto`` falls through to the step below
        it in the list. Without this, a flow that reads as a straight line
        silently stalls after its first step.
        """
        for i, step in enumerate(self.steps):
            if step.on or step.goto:
                continue
            if step.next:
                continue
            if i + 1 < len(self.steps):
                step.next = self.steps[i + 1].id

    def validate_graph(self) -> None:
        """Reject dangling goto/next references at load time."""
        ids = {s.id for s in self.steps}
        dupes = [s.id for s in self.steps if [x.id for x in self.steps].count(s.id) > 1]
        if dupes:
            raise ValueError(f"flow '{self.name}' has duplicate step ids: {sorted(set(dupes))}")
        for s in self.steps:
            for target in list(s.on.values()) + [s.goto]:
                if target and target not in ids:
                    raise ValueError(
                        f"flow '{self.name}' step '{s.id}' points at unknown step '{target}'"
                    )
            if s.next and s.next not in ids:
                raise ValueError(
                    f"flow '{self.name}' step '{s.id}' points at unknown step '{s.next}'"
                )


# ── Root ──────────────────────────────────────────────────────────────────

class TenantProfile(_Base):
    """The complete behavioural definition of one tenant."""

    tenant_id: str
    vertical: str = "generic"
    display_name: str = ""
    status: str = "active"

    features: Features = Field(default_factory=Features)
    vocabulary: Vocabulary = Field(default_factory=Vocabulary)
    brand: Brand = Field(default_factory=Brand)
    prompt: PromptSpec = Field(default_factory=PromptSpec)
    menu: MenuSpec = Field(default_factory=MenuSpec)
    flows: List[FlowSpec] = Field(default_factory=list)
    intents: List[IntentSpec] = Field(default_factory=list)
    guardrails: Guardrails = Field(default_factory=Guardrails)
    business_hours: BusinessHours = Field(default_factory=BusinessHours)
    notifications: Notifications = Field(default_factory=Notifications)

    # Set by the loader, never by a client config file.
    version: int = 0
    source: str = "defaults"

    # ── validators ────────────────────────────────────────────────────────
    @field_validator("vertical")
    @classmethod
    def _known_vertical(cls, v):
        if v not in VERTICALS:
            raise ValueError(f"unknown vertical {v!r}; expected one of {sorted(VERTICALS)}")
        return v

    @field_validator("flows")
    @classmethod
    def _unique_flow_names(cls, v):
        names = [f.name for f in v]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate flow names: {dupes}")
        for f in v:
            f.link_steps()
            f.validate_graph()
        return v

    @field_validator("intents")
    @classmethod
    def _unique_intents(cls, v):
        names = [i.name for i in v]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate intent names: {dupes}")
        return v

    @model_validator(mode="after")
    def _sync_features_from_menu_flows(self):
        """Turn on features required by published menu/intent flows.

        A visible ``Custom Itinerary`` row with ``flow=get_quote`` must not be
        blocked by ``features.quote=false`` — that path falls through to FAQ and
        mixes tenant answers.
        """
        flow_by_name = {f.name: f for f in self.flows}

        def _enable_for_flow(flow_name: Optional[str]) -> None:
            name = (flow_name or "").strip()
            if not name:
                return
            flow = flow_by_name.get(name)
            flag = (
                ((flow.requires_feature if flow else None) or "").strip()
                or _FLOW_FEATURE_HINTS.get(name)
            )
            if not flag or flag not in FEATURE_FLAGS:
                return
            if not self.features.is_on(flag):
                setattr(self.features, flag, True)

        for button in self.menu.buttons:
            if getattr(button, "enabled", True) is False:
                continue
            _enable_for_flow(getattr(button, "flow", None))

        for intent in self.intents:
            if not getattr(intent, "enabled", True):
                continue
            _enable_for_flow(getattr(intent, "flow", None))

        return self

    # ── lookups ───────────────────────────────────────────────────────────
    def flow(self, name: str) -> Optional[FlowSpec]:
        for f in self.flows:
            if f.name == name:
                return f
        return None

    def intent(self, name: str) -> Optional[IntentSpec]:
        for i in self.intents:
            if i.name == name:
                return i
        return None

    def active_intents(self) -> List[IntentSpec]:
        """Intents live only when their feature flag is on. This is the gate that
        makes 'place order' unreachable for a non-commerce tenant."""
        return [
            i for i in self.intents
            if i.enabled and (not i.requires_feature or self.features.is_on(i.requires_feature))
        ]

    def active_intent_names(self) -> List[str]:
        return [i.name for i in self.active_intents()]

    def active_flows(self) -> List[FlowSpec]:
        return [
            f for f in self.flows
            if not f.requires_feature or self.features.is_on(f.requires_feature)
        ]

    def feature_on(self, flag: str) -> bool:
        return self.features.is_on(flag)

    def v(self, key: str, default: str = "") -> str:
        """Vocabulary accessor: ``profile.v("browse_label")``."""
        return getattr(self.vocabulary, key, None) or default

    def to_payload(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude={"version", "source"})
