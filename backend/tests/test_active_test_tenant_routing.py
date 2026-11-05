"""Test-tenant overrides are limited to simulator routing, not live webhooks."""

import os
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for path in (BACKEND_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from shared.tenancy.resolver import (
    active_test_tenant_id,
    resolve_default_tenant,
    resolve_effective_tenant_id,
    resolve_tenant_from_payload,
    resolve_tenant_for_phone,
    resolve_tenant_for_user,
)


class ActiveTestTenantRoutingTests(unittest.TestCase):
    def test_active_test_beats_explicit_phone_bound_hint(self):
        with patch(
            "shared.tenancy.resolver._active_test_override",
            return_value="leeway-softech",
        ), patch(
            "shared.tenancy.store.get_tenant",
            return_value={"id": "leeway-softech", "slug": "leeway-softech", "status": "active"},
        ):
            tid = resolve_effective_tenant_id(
                "919512777899",
                tenant_id="meridian-bank",
            )
        self.assertEqual(tid, "leeway-softech")

    def test_orchestrator_style_hint_still_loses_to_active_test(self):
        """Poller may pass a phone-bound tenant_id; console override must win."""
        def fake_get_tenant(tid):
            mapping = {
                "state-bank-of-india": {
                    "id": "state-bank-of-india",
                    "slug": "state-bank-of-india",
                    "status": "active",
                },
                "leewaytest": {
                    "id": "leewaytest", "slug": "leewaytest", "status": "active",
                },
            }
            return mapping.get(tid)

        with (
            patch("shared.tenancy.resolver._active_test_override", return_value="state-bank-of-india"),
            patch("shared.tenancy.store.get_tenant", side_effect=fake_get_tenant),
        ):
            tid = resolve_effective_tenant_id(
                "919512777899",
                tenant_id="leewaytest",
                allow_test_override=True,
            )
        self.assertEqual(tid, "state-bank-of-india")

    def test_hint_used_when_no_active_test(self):
        with (
            patch("shared.tenancy.resolver._active_test_override", return_value=None),
            patch("shared.tenancy.store.get_tenant", return_value={"id": "2", "slug": "meridian-bank"}),
        ):
            tid = resolve_effective_tenant_id("919512777899", tenant_id="meridian-bank")
        self.assertEqual(tid, "meridian-bank")

    def test_unknown_webhook_phone_has_no_default_tenant(self):
        payload = {
            "entry": [{"id": "unregistered-waba", "changes": [{"value": {
                "metadata": {"phone_number_id": "unregistered-phone"},
            }}]}],
        }
        with (
            patch("shared.tenancy.resolver._active_test_override", return_value=None),
            patch("shared.tenancy.store.find_tenant_by_phone_id", return_value=None),
            patch("shared.tenancy.store.get_tenant", return_value=None),
            patch.dict(os.environ, {"DEFAULT_TENANT_ID": "leeway-softech"}),
        ):
            tid = resolve_tenant_from_payload(payload)

        self.assertIsNone(tid)

    def test_active_test_tenant_beats_webhook_phone_binding(self):
        """Frontend Active Test Tenant must win so single-number testing works."""
        payload = {
            "entry": [{"id": "tours-waba", "changes": [{"value": {
                "metadata": {"phone_number_id": "tours-phone"},
            }}]}],
        }

        def fake_get_tenant(tid):
            mapping = {
                "leeway-softech": {
                    "id": "leeway-softech", "slug": "leeway-softech", "status": "active",
                },
                "tours-travel": {
                    "id": "tours-travel", "slug": "tours-travel", "status": "active",
                },
            }
            return mapping.get(tid)

        with (
            patch("shared.tenancy.resolver._active_test_override", return_value="leeway-softech"),
            patch("shared.tenancy.store.find_tenant_by_phone_id", return_value="tours-travel"),
            patch("shared.tenancy.store.get_tenant", side_effect=fake_get_tenant),
        ):
            tid = resolve_tenant_from_payload(payload)

        self.assertEqual(tid, "leeway-softech")

    def test_phone_binding_used_when_no_active_test_tenant(self):
        payload = {
            "entry": [{"id": "tours-waba", "changes": [{"value": {
                "metadata": {"phone_number_id": "tours-phone"},
            }}]}],
        }
        with (
            patch("shared.tenancy.resolver._active_test_override", return_value=None),
            patch("shared.tenancy.store.find_tenant_by_phone_id", return_value="tours-travel"),
            patch("shared.tenancy.store.get_tenant", return_value={
                "id": "tours-travel", "slug": "tours-travel", "status": "active",
            }),
        ):
            tid = resolve_tenant_from_payload(payload)

        self.assertEqual(tid, "tours-travel")

    def test_phone_lookup_prefers_active_test_when_override_allowed(self):
        def fake_get_tenant(tid):
            mapping = {
                "state-bank-of-india": {
                    "id": "state-bank-of-india",
                    "slug": "state-bank-of-india",
                    "status": "active",
                },
                "leewaytest": {
                    "id": "leewaytest", "slug": "leewaytest", "status": "active",
                },
            }
            return mapping.get(tid)

        with (
            patch("shared.tenancy.resolver._active_test_override", return_value="state-bank-of-india"),
            patch("shared.tenancy.store.normalize_waba_phone_id", return_value="919999999999"),
            patch("shared.tenancy.store.find_tenant_by_phone_id", return_value="leewaytest"),
            patch("shared.tenancy.store.get_tenant", side_effect=fake_get_tenant),
        ):
            tid = resolve_tenant_for_phone("919999999999", allow_test_override=True)

        self.assertEqual(tid, "state-bank-of-india")

    def test_unstamped_whatsapp_user_has_no_implicit_default(self):
        with (
            patch("shared.tenancy.resolver._active_test_override", return_value=None),
            patch("shared.tenancy.store.tenant_id_for_user", return_value=None),
            patch("shared.tenancy.store.get_tenant", return_value=None),
            patch.dict(os.environ, {"DEFAULT_TENANT_ID": "leeway-softech"}),
        ):
            tid = resolve_tenant_for_user("synthetic-wa-id")

        self.assertIsNone(tid)

    def test_phone_number_lookup_uses_registered_number_without_default(self):
        with (
            patch("shared.tenancy.resolver._active_test_override", return_value=None),
            patch("shared.tenancy.store.normalize_waba_phone_id", return_value="919999999999"),
            patch("shared.tenancy.store.find_tenant_by_phone_id", return_value="tours-travel"),
            patch("shared.tenancy.store.get_tenant", return_value={
                "id": "42", "slug": "tours-travel", "status": "active",
            }),
        ):
            tid = resolve_tenant_for_phone("+91 99999 99999", allow_test_override=False)

        self.assertEqual(tid, "tours-travel")

    def test_disabled_phone_tenant_is_not_routed(self):
        payload = {
            "entry": [{"changes": [{"value": {
                "metadata": {"phone_number_id": "disabled-phone"},
            }}]}],
        }
        with (
            patch("shared.tenancy.resolver._active_test_override", return_value=None),
            patch("shared.tenancy.store.find_tenant_by_phone_id", return_value="tours-travel"),
            patch("shared.tenancy.store.get_tenant", return_value={
                "id": "42", "slug": "tours-travel", "status": "disabled",
            }),
        ):
            tid = resolve_tenant_from_payload(payload)

        self.assertIsNone(tid)


if __name__ == "__main__":
    unittest.main()
