"""Business-hours evaluation.

Kept in shared/ so routing, the flow runner and the admin UI all agree on
whether it is "after hours". Out of hours is what selects a flow's
``out_of_hours_variant`` — it never blocks a conversation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from .defaults import DEFAULT_TENANT_ID
from .schemas import BusinessHours


def _local_now(hours: BusinessHours) -> datetime:
    tz = hours.timezone or "Asia/Kolkata"
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz))
    except Exception:
        return datetime.now()


def is_business_hours(hours: BusinessHours) -> bool:
    if hours.always_open:
        return True
    now = _local_now(hours)
    if now.weekday() not in hours.open_days:
        return False
    try:
        hh, mm = (int(p) for p in str(hours.open).split(":")[:2])
        current = now.hour * 60 + now.minute
        return (hh * 60 + mm) <= current < (int(hours.close[:2]) * 60 + int(hours.close[3:5]))
    except (TypeError, ValueError):
        return True


def is_open_for(tenant_id: Optional[str]) -> bool:
    from .loader import get_tenant_profile

    return is_business_hours(get_tenant_profile(tenant_id or DEFAULT_TENANT_ID).business_hours)


def greeting_for(tenant_id: Optional[str]) -> str:
    """Business-hours-aware acknowledgement used by flows and the menu header."""
    from .loader import get_tenant_profile

    profile = get_tenant_profile(tenant_id or DEFAULT_TENANT_ID)
    if is_business_hours(profile.business_hours):
        return f"Our team is available now — expect a reply shortly."
    return profile.business_hours.out_of_hours_message


def select_flow_name(tenant_id: Optional[str], flow_name: str) -> str:
    """Swap in the out-of-hours variant when the office is closed.

    Falls back to the original flow when no variant is defined, and never loops:
    a variant is returned as-is even though it is itself out of hours.
    """
    from .loader import get_tenant_profile

    profile = get_tenant_profile(tenant_id or DEFAULT_TENANT_ID)
    flow = profile.flow(flow_name)
    if not flow or not flow.out_of_hours_variant:
        return flow_name
    if is_business_hours(profile.business_hours):
        return flow_name
    return flow.out_of_hours_variant if profile.flow(flow.out_of_hours_variant) else flow_name
