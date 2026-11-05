"""The one place a feature flag is enforced.

Gating means *refuse politely*, never delete. Menus hide buttons as a
convenience; these helpers are the actual gate, called from the service and
route layers so a hidden button cannot be bypassed by typing the command.
"""

from __future__ import annotations

import logging
from typing import Optional

from .schemas import FEATURE_FLAGS, TenantProfile

logger = logging.getLogger("tenancy.gating")


class FeatureDisabled(Exception):
    """Raised by guarded operations when a tenant has the feature switched off."""

    def __init__(self, feature: str, message: str = ""):
        self.feature = feature
        self.message = message or f"This option isn't available right now."
        super().__init__(self.message)


def _profile(tenant_id: Optional[str]) -> TenantProfile:
    from .loader import get_tenant_profile
    from .resolver import resolve_default_tenant

    # An explicit tenant is honoured even when unregistered (an on-the-fly
    # profile still validates); an absent one resolves to a *registered* tenant
    # so a single-shop deployment never gets a synthetic generic profile.
    return get_tenant_profile(tenant_id or resolve_default_tenant())


def is_enabled(tenant_id: Optional[str], feature: str) -> bool:
    if feature not in FEATURE_FLAGS:
        logger.warning("Unknown feature flag checked: %r", feature)
        return False
    return _profile(tenant_id).feature_on(feature)


def refusal_message(tenant_id: Optional[str], feature: str) -> str:
    """A polite, on-brand refusal built from the profile's own vocabulary."""
    profile = _profile(tenant_id)
    support = profile.v("support_label") or "Support"
    phone = profile.brand.support_phone
    tail = f" You can also tap *{support}* to reach our team."
    if phone:
        tail += f" Or call us on {phone}."
    return (
        f"That isn't something we handle on WhatsApp right now.{tail}"
    )


def require(tenant_id: Optional[str], feature: str) -> TenantProfile:
    """Return the profile or raise FeatureDisabled.

    Usage in a service entry point::

        profile = require(wa_tenant_id, "cart")
    """
    profile = _profile(tenant_id)
    if not profile.feature_on(feature):
        raise FeatureDisabled(feature, refusal_message(tenant_id, feature))
    return profile


def guard(tenant_id: Optional[str], feature: str):
    """Decorator form for route handlers.

    Turns a disabled feature into a 200 with a friendly message rather than a
    4xx — WhatsApp bots should never answer a user with an error code.
    """
    def decorator(fn):
        def wrapper(*args, **kwargs):
            if not is_enabled(tenant_id, feature):
                return {"ok": False, "disabled": feature,
                        "message": refusal_message(tenant_id, feature)}
            return fn(*args, **kwargs)
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorator


# Intents that only make sense for commerce tenants. Kept here (not in the
# profile) so the mapping from an intent name to its gate is auditable in one
# place; the profile still owns the actual examples/phrases.
INTENT_GATES = {
    "place_order": "buy_now",
    "order_status": "track_order",
    "return_refund": "returns",
    "catalogue_request": "brochure_pdf",
    "bulk_enquiry": "distributors",
}


def intent_allowed(tenant_id: Optional[str], intent: str) -> bool:
    """Can this tenant handle this intent at all?"""
    profile = _profile(tenant_id)
    gate = INTENT_GATES.get(intent)
    if gate and not profile.feature_on(gate):
        return False
    return intent in profile.active_intent_names()


def intent_refusal(tenant_id: Optional[str], intent: str) -> str:
    """Message used when a gated intent is somehow reached anyway."""
    gate = INTENT_GATES.get(intent, "")
    return refusal_message(tenant_id, gate) if gate else (
        "I don't handle that here. Let me pass you to our team."
    )


def guard_result(wa_id: str, feature: str) -> Optional[dict]:
    """Refusal payload for a WhatsApp user whose tenant has ``feature`` off.

    Services return dicts (never raise) so a disabled feature reads as a normal
    turn in the conversation. Returns None when the feature is on, which is the
    case every time for a single-tenant deployment.

    Usage::

        blocked = guard_result(wa_id, "cart")
        if blocked:
            return blocked
    """
    from .resolver import resolve_tenant_for_user

    tenant_id = resolve_tenant_for_user(wa_id)
    if is_enabled(tenant_id, feature):
        return None
    logger.info("tenant %s blocked from %s", tenant_id, feature)
    return {
        "ok": False,
        "error": "feature_disabled",
        "feature": feature,
        "message": refusal_message(tenant_id, feature),
    }
