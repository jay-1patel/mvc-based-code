"""Profile linters.

The point of the whole exercise is that a non-commerce tenant never says
"cart". These linters make that a hard, checkable property rather than a code
review habit.

    lint_forbidden_terms(profile)  commerce wording in a profile that forbids it
    lint_intent_gating(profile)    an intent live that its feature flag forbids
    lint_step_reachability(flow)   a step no path can reach
    lint_profile(profile)           all of the above
"""

from __future__ import annotations

from typing import Any, Dict, List

from .loader import get_tenant_profile, profile_strings
from .schemas import TenantProfile

# Wording that must never appear for a non-commerce tenant. Derived from the
# commerce vertical's own vocabulary so the two stay in step.
COMMERCE_TERMS = (
    "cart", "add to cart", "checkout", "buy now", "place order", "order now",
    "track order", "order status", "shipping", "delivery charge", "mrp",
    "shopping cart", "your order",
)

# Intent name -> the feature flag that must be on for it to be live.
INTENT_GATES = {
    "place_order": "buy_now",
    "order_status": "track_order",
    "return_refund": "returns",
    "catalogue_request": "brochure_pdf",
    "bulk_enquiry": "distributors",
    "quote_request": "quote",
    "callback_request": "callback",
    "booking_inquiry": "book_appointment",
}


# Fields that are themselves vocabularies of banned/flagged words. Scanning them
# against the forbidden list is circular - "add to cart" is banned, and the field
# that says so contains the string "add to cart". Everything else IS user-facing
# copy and must be clean.
_NON_COPY_PATHS = (
    "guardrails.forbidden_terms",
    "guardrails.escalate_keywords",
    "guardrails.handoff_keywords",
    "guardrails.never_state",
)


def lint_forbidden_terms(profile: TenantProfile) -> List[Dict[str, Any]]:
    """Any user-facing profile string that contains a term the guardrails forbid."""
    terms = {t.lower() for t in profile.guardrails.forbidden_terms if t}
    if not terms:
        return []
    hits: List[Dict[str, Any]] = []
    for text in profile_strings(profile, exclude=_NON_COPY_PATHS):
        if len(text) > 400:
            continue  # long blobs are KB-ish prose, not UI copy
        low = text.lower()
        for term in terms:
            if term in low:
                hits.append({"term": term, "text": text[:160]})
    return hits


def lint_intent_gating(profile: TenantProfile) -> List[Dict[str, Any]]:
    """An intent live while the feature it depends on is off.

    The profile's own requires_feature is authoritative; INTENT_GATES is a
    belt-and-braces check so a tenant cannot smuggle a commerce intent in
    without declaring the gate.
    """
    problems: List[Dict[str, Any]] = []
    active = set(profile.active_intent_names())
    for name in active:
        gate = INTENT_GATES.get(name)
        if gate and not profile.feature_on(gate):
            problems.append({"intent": name, "requires_feature": gate, "active": True})
    for intent in profile.intents:
        gate = intent.requires_feature
        if gate and intent.enabled and gate in active and not profile.feature_on(gate):
            problems.append({"intent": intent.name, "requires_feature": gate})
    return problems


# Step types that produce a side effect the business cares about: a lead row, a
# notification, a ticket, or a human handoff. A flow that collects a phone number
# and never reaches one of these is a silent data-loss bug.
SIDE_EFFECT_STEPS = (
    "save_lead", "notify", "handoff", "create_ticket", "book_appointment", "escalate",
)


def lint_step_reachability(flow) -> List[Dict[str, Any]]:
    """Unreachable steps, dangling hops, or a flow that never persists anything."""
    problems: List[Dict[str, Any]] = []
    if not flow.steps:
        return [{"flow": flow.name, "problem": "flow has no steps"}]

    ids = {s.id for s in flow.steps}
    for step in flow.steps:
        for target in list(step.on.values()) + [step.goto, step.next]:
            if target and target not in ids:
                problems.append({
                    "flow": flow.name,
                    "step": step.id,
                    "problem": f"points at unknown step '{target}'",
                })

    reachable = {flow.first_step_id()}
    pending = [flow.first_step_id()]
    while pending:
        current = pending.pop()
        step = flow.step(current)
        if step is None:
            continue
        for target in list(step.on.values()) + [step.goto, step.next]:
            if target and target not in reachable:
                reachable.add(target)
                pending.append(target)

    for step in flow.steps:
        if step.id not in reachable:
            problems.append({"flow": flow.name, "step": step.id, "problem": "unreachable"})

    side_effects = [s for s in flow.steps
                    if s.type in SIDE_EFFECT_STEPS and s.id in reachable]
    if not side_effects:
        problems.append({
            "flow": flow.name,
            "problem": (
                "no reachable side-effect step ("
                + " / ".join(SIDE_EFFECT_STEPS) + ") - collected data is discarded"
            ),
        })
    return problems


def lint_profile(profile: TenantProfile) -> Dict[str, Any]:
    forbidden = lint_forbidden_terms(profile)
    gating = lint_intent_gating(profile)
    reach: List[Dict[str, Any]] = []
    for flow in profile.flows:
        reach.extend(lint_step_reachability(flow))

    return {
        "tenant_id": profile.tenant_id,
        "vertical": profile.vertical,
        "version": profile.version,
        "forbidden_terms": forbidden,
        "intent_gating": gating,
        "flow_reachability": reach,
        "ok": not (forbidden or gating or reach),
    }


def lint_tenant(tenant_id: str) -> Dict[str, Any]:
    return lint_profile(get_tenant_profile(tenant_id))


def commerce_vocabulary_present(profile: TenantProfile) -> List[str]:
    """Which commerce nouns this profile still uses (informational)."""
    out = []
    for term in COMMERCE_TERMS:
        if any(term in s.lower()
               for s in profile_strings(profile, exclude=_NON_COPY_PATHS) if len(s) <= 200):
            out.append(term)
    return sorted(out)
