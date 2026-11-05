"""Create-tenant must not treat display_name aliases as an id conflict.

get_tenant() resolves brand names for inbound routing (e.g. "Leeway Softech"
→ tenant leewaytest). Registration used that helper and falsely returned 409
when the requested id/slug was free.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import database
from shared.tenancy import store as tenancy_store


class TenantCreateConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "create_conflict.sqlite3")
        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()
        database.init_db()
        tenancy_store.ensure_tenant(
            "leewaytest",
            slug="leewaytest",
            vertical="it_software",
            display_name="Leeway Softech",
        )

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_get_tenant_still_resolves_display_name_alias(self):
        hit = tenancy_store.get_tenant("leeway-softech")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "leewaytest")

    def test_registry_conflict_ignores_display_name_alias(self):
        self.assertIsNone(
            tenancy_store.find_registry_conflict("leeway-softech", "leeway-softech")
        )

    def test_registry_conflict_detects_exact_id(self):
        hit = tenancy_store.find_registry_conflict("leewaytest")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "leewaytest")

    def test_registry_conflict_detects_normalized_slug_twin(self):
        tenancy_store.ensure_tenant(
            "leewaysoftech",
            slug="leewaysoftech",
            vertical="it_software",
            display_name="Other",
        )
        hit = tenancy_store.find_registry_conflict("leeway-softech", "leeway-softech")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["id"], "leewaysoftech")

    def test_new_id_can_be_registered_when_only_display_name_collides(self):
        self.assertIsNone(
            tenancy_store.find_registry_conflict("brand-new-co", "brand-new-co")
        )
        tenancy_store.ensure_tenant(
            "brand-new-co",
            slug="brand-new-co",
            vertical="generic",
            display_name="Leeway Softech",  # same brand name, different id
        )
        row = tenancy_store.get_tenant("brand-new-co")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], "brand-new-co")


if __name__ == "__main__":
    unittest.main()
