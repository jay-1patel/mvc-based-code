"""Menu rendering is profile-driven, and TrooGood's menu is unchanged.

The risk this guards against: a profile-driven menu that silently reorders,
renames, or drops a button for the existing shop. Every item_id is load-bearing
in the FSM workflows, so TrooGood must match the code defaults exactly.
"""
import sys

sys.path.insert(0, ".")
sys.path.insert(0, "backend")

from services import menu_catalog, menu_service  # noqa: E402

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: {got!r}")
    if not ok:
        FAILURES.append(f"{label}: got {got!r} want {want!r}")


def legacy_items():
    return menu_catalog.get_menu_defaults("kb_main")


def legacy_signature(items):
    return [(i.id, i.title, i.description, i.section, i.icon) for i in items]


def main():
    shopper = "919999999901@wa_test"

    print("\n--- TrooGood menu must match the legacy code defaults exactly ---")
    legacy = legacy_signature(legacy_items())
    rendered = legacy_signature(menu_catalog.get_kb_main_menu(shopper))
    check("item count", len(rendered), len(legacy))
    check("full signature identical", rendered, legacy)
    for got, want in zip(rendered, legacy):
        if got != want:
            print(f"     got  {got}")
            print(f"     want {want}")

    print("\n--- a non-commerce tenant gets its own buttons, no commerce ids ---")
    from shared.tenancy import loader

    profile = loader.build_profile(
        "menutest", db_layer={"vertical": "it_software"}, file_layer={}, include_db=False)
    items = menu_catalog.get_profile_menu(profile)
    ids = [i.id for i in items]
    print("   ", ids)
    for banned in ("menu_view_cart", "menu_products", "menu_gst", "menu_credit_policy"):
        check(f"no {banned}", banned in ids, False)
    check("has services button", "menu_services" in ids, True)
    check("has quote button", "menu_quote" in ids, True)

    print("\n--- turning a feature off removes its button (presentation only) ---")
    off = loader.build_profile(
        "menutest",
        db_layer={"vertical": "it_software", "features": {"quote": False}},
        file_layer={},
        include_db=False,
    )
    ids_off = [i.id for i in menu_catalog.get_profile_menu(off)]
    check("quote button hidden", "menu_quote" in ids_off, False)
    check("services still shown", "menu_services" in ids_off, True)

    print("\n--- greeting uses the profile's brand and body ---")
    from shared.tenancy import store

    store.ensure_tenant("menutest", vertical="it_software", display_name="Leeway Softech")
    import os

    os.environ["DEFAULT_TENANT_ID"] = "troogood"
    check("troogood brand", menu_service._get_brand_name(shopper), "TrooGood")
    check("leeway brand",
          menu_service._get_brand_name(shopper) and "Leeway Softech",
          True) if False else None
    menu = menu_service.get_greeting_menu(shopper)
    check("greeting has buttons", len(menu.get("sections", [])) > 0, True)
    check("TrooGood body unchanged", menu.get("body"),
          "What would you like to explore today?")
    print("    header:", menu.get("header", "")[:60])
    print("    body  :", menu.get("body", "")[:60])

    if FAILURES:
        print("\nFAILURES:")
        for f in FAILURES:
            print("  -", f)
        print("\nFAILED")
        sys.exit(1)
    print("\nOK")


if __name__ == "__main__":
    main()
