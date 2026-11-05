"""Active Test Tenant must not make every FAQ chunk look like the same brand."""

import os
import sys
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
for path in (BACKEND_DIR, PROJECT_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from faq.service import _canonical_tenant_id, _resolve_tenant_id, _tenant_matches


class FaqTenantIsolationTests(unittest.TestCase):
    def test_canonical_ignores_active_test_override(self):
        with patch(
            "shared.tenancy.resolver.active_test_tenant_id",
            return_value="makemytrip-india-private-limited",
        ), patch(
            "shared.tenancy.store.get_tenant",
            side_effect=lambda tid: {
                "id": tid,
                "slug": tid,
                "status": "active",
            },
        ):
            self.assertEqual(
                _canonical_tenant_id("leeway-softech"),
                "leeway-softech",
            )

    def test_resolve_search_scope_uses_active_test(self):
        with patch(
            "shared.tenancy.resolver.active_test_tenant_id",
            return_value="makemytrip-india-private-limited",
        ), patch(
            "shared.tenancy.store.get_tenant",
            side_effect=lambda tid: {
                "id": tid,
                "slug": tid,
                "status": "active",
            },
        ):
            self.assertEqual(
                _resolve_tenant_id("leeway-softech"),
                "makemytrip-india-private-limited",
            )

    def test_foreign_chunk_does_not_match_active_search_target(self):
        """Regression: Active Test used to rewrite chunk ids during match."""
        with patch(
            "shared.tenancy.resolver.active_test_tenant_id",
            return_value="makemytrip-india-private-limited",
        ), patch(
            "shared.tenancy.store.get_tenant",
            side_effect=lambda tid: {
                "id": tid,
                "slug": tid,
                "status": "active",
            },
        ):
            self.assertFalse(
                _tenant_matches(
                    "leeway-softech",
                    "makemytrip-india-private-limited",
                )
            )
            self.assertTrue(
                _tenant_matches(
                    "makemytrip-india-private-limited",
                    "makemytrip-india-private-limited",
                )
            )


if __name__ == "__main__":
    unittest.main()
