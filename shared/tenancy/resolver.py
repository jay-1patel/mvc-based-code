"""Resolve incoming WhatsApp traffic only to registered, active tenants.

Meta's webhook supplies a phone-number ID and may also include the display
number. Unknown or inactive phone bindings are left unrouted rather than sent
to a default tenant.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .defaults import DEFAULT_TENANT_ID

logger = logging.getLogger("tenancy.resolver")

_UNSET = object()


def _extract_phone_ids(payload: Dict[str, Any]) -> list[str]:
    """All plausible phone-number identifiers in a WABA webhook, best first."""
    out: list[str] = []
    try:
        entry = (payload.get("entry") or [{}])[0] or {}
        changes = (entry.get("changes") or [{}])[0] or {}
        value = changes.get("value") or {}

        raw = entry.get("id")
        if raw not in (None, ""):
            out.append(str(raw))

        meta = value.get("metadata") or {}
        for key in ("phone_number_id", "display_phone_number", "display_phone_number_id"):
            val = meta.get(key)
            if val not in (None, ""):
                out.append(str(val))
    except (AttributeError, IndexError, TypeError) as exc:
        logger.debug("Could not extract phone ids from payload: %s", exc)
    return out


def _active_test_override() -> Optional[str]:
    try:
        from database import get_active_test_tenant
        val = get_active_test_tenant()
        if val:
            return canonical_tenant_id(val) or val
    except Exception:
        # deliberate: no readable override — resolution continues
        # without it.
        logger.debug("ACTIVE_TEST_OVERRIDE_LOOKUP_SKIPPED", exc_info=True)
    return None


def active_test_tenant_id() -> Optional[str]:
    """Frontend-activated tenant, or None.

    When set, this is the *only* tenant the bot may answer as — it overrides
    DEFAULT_TENANT_ID, phone bindings, and stale per-user stamps. Admin APIs
    that pass an explicit tenant for editing still use that explicit id.
    """
    override = _active_test_override()
    if not override:
        return None
    return _registered_active_tenant(override)


def canonical_tenant_id(tenant_id: Optional[str]) -> Optional[str]:
    """Map id / slug / display aliases to the tenant registry slug (or id)."""
    raw = str(tenant_id or "").strip()
    if not raw:
        return None
    from . import store

    rec = store.get_tenant(raw)
    if rec:
        return str(rec.get("slug") or rec.get("id") or raw)
    return raw


def _registered_active_tenant(tenant_id: Optional[str]) -> Optional[str]:
    """Return a canonical tenant id only when it is registered and active."""
    candidate = canonical_tenant_id(tenant_id)
    if not candidate:
        return None
    from . import store

    record = store.get_tenant(candidate)
    if not record or str(record.get("status") or "active").strip().lower() != "active":
        return None
    return str(record.get("slug") or record.get("id") or candidate)


def resolve_effective_tenant_id(
    wa_id: str = "",
    tenant_id: Optional[str] = None,
    *,
    allow_test_override: bool = True,
) -> Optional[str]:
    """Single-number WhatsApp routing: which tenant profile handles this message.

    Order:
      1. ``active_test_tenant`` from the admin console (or ``#tenant`` in chat)
      2. An explicit hint (webhook phone binding, simulator, caller)
      3. ``user_states.tenant_id`` for this ``wa_id``

    Step 1 always wins so enabling a tenant in the console routes the single
    WhatsApp number to that bot (and cannot leak another tenant's menu from
    phone binding or a stale per-user stamp). Pass ``allow_test_override=False``
    only when you intentionally ignore the console override. Missing or
    inactive tenants return None; inbound traffic must never default to
    another tenant.
    """
    if allow_test_override:
        active_override = active_test_tenant_id()
        if active_override:
            # One debug line per call is enough; INFO spam made logs unreadable.
            logger.debug(
                "ACTIVE_TEST_TENANT | routing wa_id=%s to %s (ignoring hint=%s)",
                wa_id or "?", active_override, tenant_id,
            )
            return active_override

    hint = _registered_active_tenant(tenant_id)
    if hint:
        return hint

    return resolve_tenant_for_user(wa_id, allow_test_override=False)


def resolve_tenant_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    """Resolve an inbound WABA message to the tenant that should answer it.

    When Active Test Tenant is set in the admin console (single-number WhatsApp
    testing), that selection always wins — so switching tenants in the UI
    immediately changes which bot answers, even if SEND2 / Meta phone ids are
    still bound to another tenant. With no override, route by phone binding.
    """
    active_override = active_test_tenant_id()
    if active_override:
        logger.debug(
            "ACTIVE_TEST_TENANT | routing inbound webhook to %s",
            active_override,
        )
        return active_override

    for phone_id in _extract_phone_ids(payload or {}):
        active_tenant = resolve_tenant_for_phone(
            phone_id,
            allow_test_override=False,
        )
        if active_tenant:
            logger.debug("Resolved active tenant %s from phone id %s", active_tenant, phone_id)
            return active_tenant

    # Second pass: a tenant whose slug or id matches the id field directly.
    for candidate in _extract_phone_ids(payload or {}):
        active_tenant = _registered_active_tenant(candidate)
        if active_tenant:
            return active_tenant

    if _extract_phone_ids(payload or {}):
        logger.warning(
            "Unbound or inactive WABA phone id(s) %s — ignoring inbound message",
            _extract_phone_ids(payload or {}),
        )
    return None


def resolve_tenant_for_phone(
    phone_id: str,
    *,
    allow_test_override: bool = True,
) -> Optional[str]:
    """Resolve a phone binding; unknown or inactive numbers return None."""
    override = _active_test_override() if allow_test_override else None
    if override:
        active_override = _registered_active_tenant(override)
        if active_override:
            return active_override

    from . import store

    raw_phone_id = str(phone_id or "").strip()
    candidates = [raw_phone_id] if raw_phone_id else []
    try:
        normalized = store.normalize_waba_phone_id(raw_phone_id)
        if normalized not in candidates:
            candidates.append(normalized)
    except ValueError:
        # deliberate: an unnormalizable phone id is still tried raw.
        logger.debug("PHONE_ID_NORMALIZE_SKIPPED | phone_id=%s", raw_phone_id, exc_info=True)
    for candidate in candidates:
        tid = store.find_tenant_by_phone_id(candidate)
        if tid:
            active_tenant = _registered_active_tenant(tid)
            if active_tenant:
                return active_tenant
    return None


def bind_phone_id(tenant_id: str, phone_id: str) -> bool:
    """Register a WABA phone-id for a tenant. Idempotent."""
    from . import store

    return store.set_waba_phone_id(tenant_id, phone_id)


def resolve_default_tenant() -> Optional[str]:
    """The tenant an unrecognised request belongs to.

    Resolution order:
      1. Frontend Active Test Tenant (absolute override of DEFAULT_TENANT_ID)
      2. ``DEFAULT_TENANT_ID`` from the environment, if that tenant is registered
      3. the single active tenant, when exactly one is registered

    There is deliberately NO default tenant: when several tenants are registered
    and none is configured, we return None instead of guessing (or falling back
    to a leftover "default"/leeway pack). Callers must handle None.
    """
    active_override = active_test_tenant_id()
    if active_override:
        return active_override

    from . import store

    configured = os.getenv("DEFAULT_TENANT_ID", "").strip()
    # Never treat the synthetic "default" id as a real tenant.
    if configured and configured != "default" and store.get_tenant(configured):
        return configured

    active = [t for t in store.list_tenants(status="active") if t.get("id")]
    if len(active) == 1:
        return str(active[0].get("slug") or active[0]["id"])
    return None


def resolve_tenant_for_user(
    wa_id: str,
    *,
    allow_test_override: bool = True,
) -> Optional[str]:
    """Tenant that owns a WhatsApp user.

    In single-number testing mode, active test tenant override takes absolute
    precedence so testers don't get trapped in stale tenant states.
    """
    override = _active_test_override() if allow_test_override else None
    if override:
        active_override = _registered_active_tenant(override)
        if active_override:
            return active_override

    if not wa_id:
        return None

    from . import store

    tid = store.tenant_id_for_user(wa_id)
    if tid:
        active_tenant = _registered_active_tenant(tid)
        if active_tenant:
            return active_tenant
        logger.warning(
            "user %s is stamped with unregistered or inactive tenant %r; ignoring message",
            wa_id, tid,
        )

    return None
