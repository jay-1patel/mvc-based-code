"""B2B WhatsApp list-menu definitions.

Each ``get_*_menu`` helper returns the exact JSON structure required by the
shared ``send_list_menu(wa_id, header, body, button_text, sections)`` function.

B2B distributors share the single unified KB menu from ``services.menu_catalog``
so every persona sees identical options. Menu items are rendered through
``services.menu_service`` which enforces WhatsApp interactive limits.
"""

from services.menu_catalog import get_kb_main_menu
from services.menu_service import build_list_menu, personalize_header


def get_dist_main_menu(wa_id: str = "") -> dict:
    """Level-1 main menu (unified KB menu) for B2B distributors."""
    items = get_kb_main_menu(wa_id)
    header = personalize_header(wa_id, user_type="b2c")
    menu = build_list_menu(
        header=header,
        body="What would you like to explore today?",
        button_text="Show Options",
        items=items,
    )
    menu.pop("type", None)
    return menu


def get_product_catalog_menu(products) -> dict:
    """List-menu of products fetched from the database.

    Args:
        products: Iterable of dicts with keys id/name/price/unit.

    Returns:
        dict: List-menu structure for the given products.
    """
    from services.menu_service import MenuItem

    rows = []
    for i, p in enumerate(products, start=1):
        title = p.get("name", f"Product {i}")
        desc = ""
        if p.get("price"):
            desc = f"Rs {p['price']}"
        if p.get("unit"):
            desc = f"{desc} per {p['unit']}".strip()
        rows.append(MenuItem(
            id=str(p.get("id", i)),
            title=title,
            description=desc or "Tap to select",
            section="Products",
        ))

    menu = build_list_menu(
        header="Product Catalog",
        body="Select a product to add to your order:",
        button_text="Select Product",
        items=rows,
    )
    menu.pop("type", None)
    return menu


def get_ticket_category_menu() -> dict:
    """Support-ticket category selection."""
    from services.menu_service import MenuItem

    items = [
        MenuItem(id="tcat_delivery", title="Delivery / Shipping", description="", section="Categories"),
        MenuItem(id="tcat_billing", title="Billing / Invoice", description="", section="Categories"),
        MenuItem(id="tcat_product", title="Product / Quality", description="", section="Categories"),
        MenuItem(id="tcat_account", title="Account / Access", description="", section="Categories"),
        MenuItem(id="tcat_other", title="Other", description="", section="Categories"),
    ]
    menu = build_list_menu(
        header="Support Ticket",
        body="What is your issue about?",
        button_text="Select Category",
        items=items,
    )
    menu.pop("type", None)
    return menu