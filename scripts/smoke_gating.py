"""Feature gates are the real enforcement, not just hidden menu buttons.

Three properties checked here:
  1. a commerce tenant is completely unaffected (TrooGood regression guard)
  2. a non-commerce tenant is refused at the service layer, not just in the menu
  3. WhatsApp user resolution never assigns an unregistered user to a default
"""
import os
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from shared.tenancy import gating, resolver, store  # noqa: E402

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        FAILURES.append(f"{label}: got {got!r} want {want!r}")


def set_user_tenant(wa_id, tenant_id):
    import services.state_manager as sm

    assert sm.set_tenant(wa_id, tenant_id), f"could not tag {wa_id} as {tenant_id}"


def main():
    # Pin the deployment default the way a real deployment would. With two
    # tenants registered and nothing configured, the fallback is ambiguous by
    # design (and warns) rather than guessing.
    os.environ["DEFAULT_TENANT_ID"] = "troogood"

    store.ensure_tenant("troogood", vertical="ecommerce", display_name="TrooGood")
    store.ensure_tenant("leewaytest", vertical="it_software", display_name="Leeway Softech")

    shopper = "919999999901@wa_test"
    prospect = "919999999902@wa_test"
    stranger = "919999999903@wa_test"

    set_user_tenant(shopper, "troogood")
    set_user_tenant(prospect, "leewaytest")

    print("\n--- resolver: tenant for user comes from user_states ---")
    check("shopper", resolver.resolve_tenant_for_user(shopper), "troogood")
    check("prospect", resolver.resolve_tenant_for_user(prospect), "leewaytest")
    check("stranger remains unrouted", resolver.resolve_tenant_for_user(stranger), None)
    check("no wa_id remains unrouted", resolver.resolve_tenant_for_user(""), None)

    print("\n--- default tenant auto-detects a single registered shop ---")
    # The pre-tenant deployment has one shop and no DEFAULT_TENANT_ID. That must
    # resolve to the shop, not to a synthetic generic profile with commerce off.
    saved = os.environ.pop("DEFAULT_TENANT_ID", None)
    real_list, real_get = store.list_tenants, store.get_tenant
    try:
        store.get_tenant = lambda t: None if t == "default" else real_get(t)
        store.list_tenants = lambda status=None: (
            [{"id": "troogood", "status": "active"}] if status == "active" else real_list(status))
        check("single shop auto-detected", resolver.resolve_default_tenant(), "troogood")
    finally:
        store.get_tenant, store.list_tenants = real_get, real_list
        if saved is not None:
            os.environ["DEFAULT_TENANT_ID"] = saved

    print("\n--- 1. commerce tenant unaffected ---")
    check("troogood cart on", gating.is_enabled("troogood", "cart"), True)
    check("troogood orders on", gating.is_enabled("troogood", "orders"), True)
    check("shopper cart allowed", gating.guard_result(shopper, "cart"), None)
    check("shopper orders allowed", gating.guard_result(shopper, "orders"), None)
    check("stranger cart allowed", gating.guard_result(stranger, "cart"), None)

    print("\n--- 2. non-commerce tenant refused at the service layer ---")
    check("leewaytest cart off", gating.is_enabled("leewaytest", "cart"), False)
    blocked = gating.guard_result(prospect, "cart")
    check("prospect cart blocked", (blocked or {}).get("error"), "feature_disabled")
    check("prospect cart message", bool((blocked or {}).get("message")), True)
    blocked_orders = gating.guard_result(prospect, "orders")
    check("prospect orders blocked", (blocked_orders or {}).get("error"), "feature_disabled")

    print("\n--- refusal message is on-brand and commerce-free ---")
    msg = (blocked or {}).get("message", "")
    for term in ("cart", "checkout", "order"):
        check(f"refusal avoids {term!r}", term in msg.lower(), False)
    check("refusal names support", "Support" in msg or "support" in msg, True)

    print("\n--- cart_service actually refuses ---")
    import services.cart_service as cart

    out = cart.get_or_create_active_cart(prospect)
    check("get_or_create_active_cart blocked", out.get("error"), "feature_disabled")
    out = cart.add_to_cart(prospect, {"id": 1, "name": "Widget", "price": 10}, 1)
    check("add_to_cart blocked", out.get("error"), "feature_disabled")
    out = cart.start_checkout(prospect)
    check("start_checkout blocked", out.get("error"), "feature_disabled")
    out = cart.cart_summary(prospect)
    check("cart_summary blocked", out.get("error"), "feature_disabled")

    print("\n--- but a shopper still gets a real cart ---")
    out = cart.get_or_create_active_cart(shopper)
    check("shopper cart ok", "error" not in out, True)

    print("\n--- order_service actually refuses ---")
    import services.order_service as orders

    out = orders.format_orders_text(prospect)
    check("leeway refuses order list", "isn't something we handle" in out, True)
    out = orders.format_orders_text(shopper)
    check("shopper gets order text", "orders" in out.lower(), True)

    if FAILURES:
        print("\nFAILURES:")
        for f in FAILURES:
            print("  -", f)
        print("\nFAILED")
        sys.exit(1)
    print("\nOK")


if __name__ == "__main__":
    main()
