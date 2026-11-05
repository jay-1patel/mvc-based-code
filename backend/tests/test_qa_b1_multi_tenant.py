"""Integration-style tests for multi-tenant routing (B1).

Focuses on code-paths we can validate without heavy mocking:
  - orchestrator always honors Active Test Tenant from the admin console
  - products.py delegates to central resolver chain
  - the conditional expressions used across modules
"""

import pytest


class TestOrchestratorHonorsActiveTestTenant:
    """Console Active Test Tenant must win over a poller phone-bound hint."""

    def test_allow_test_override_always_true(self):
        source = open(
            "backend/services/orchestrator.py", encoding="utf-8"
        ).read()
        assert "allow_test_override=True" in source
        assert "allow_test_override=not bool(tenant_id)" not in source


class TestWebhookSetCorrectAllowOverride:
    """Verify routing/webhook uses correct conditional."""

    def test_allows_active_test_override(self):
        source = open("routing/webhook.py", encoding="utf-8").read()
        assert "allow_test_override=True" in source
        assert "allow_test_override=(tenant_id is None)" not in source


class TestProductsDelegatesToResolver:
    """products must NOT read get_active_test_tenant() directly."""

    def test_no_direct_get_active_test_call(self):
        """If this fails, products is reading the DB setting directly again."""
        source = open("backend/db/products.py", encoding="utf-8").read()
        # Must NOT have an import or call — a mention in a docstring is OK
        assert "from .settings import get_active_test_tenant" not in source
        assert "get_active_test_tenant()" not in source

    def test_calls_resolve_default_tenant(self):
        source = open("backend/db/products.py", encoding="utf-8").read()
        assert "resolve_default_tenant" in source


class TestProductIdsRespectsExplicitTenant:
    """Even simple lookups respect the passed-in tenant."""

    def test_makemytrip_resolves(self):
        from backend.db.products import _resolve_tenant_ids
        ids = _resolve_tenant_ids("makemytrip")
        assert "makemytrip" in ids
