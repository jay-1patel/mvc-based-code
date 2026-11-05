"""
B2C cart service — in-memory cart stored in context_json["cart"].

Cart shape:
{
    "items": [
        {"product_id": 1, "name": "...", "price": 199.0, "qty": 2, "media_url": "..."}
    ],
    "total_items": 3,
    "total_amount": 597.0
}
"""
import logging
from database import get_db_context

logger = logging.getLogger("b2c_cart")


def get_cart(context: dict) -> dict:
    return context.get("cart", {"items": [], "total_items": 0, "total_amount": 0.0})


def add_to_cart(context: dict, product: dict, qty: int) -> dict:
    """Add a product to the cart. Returns updated cart."""
    cart = context.get("cart", {"items": [], "total_items": 0, "total_amount": 0.0})
    items = cart.get("items", [])

    product_id = int(product.get("id", 0))
    price = float(product.get("price") or 0)
    name = product.get("name", "")
    media_url = product.get("media_url", "")

    existing = next((i for i in items if i["product_id"] == product_id), None)
    if existing:
        existing["qty"] += qty
    else:
        items.append({
            "product_id": product_id,
            "name": name,
            "price": price,
            "qty": qty,
            "media_url": media_url,
        })

    total_items = sum(i["qty"] for i in items)
    total_amount = sum(i["qty"] * i["price"] for i in items)

    cart = {
        "items": items,
        "total_items": total_items,
        "total_amount": round(total_amount, 2),
    }
    context["cart"] = cart
    return cart


def remove_from_cart(context: dict, product_id: int) -> dict:
    cart = context.get("cart", {"items": [], "total_items": 0, "total_amount": 0.0})
    items = [i for i in cart.get("items", []) if i["product_id"] != product_id]
    total_items = sum(i["qty"] for i in items)
    total_amount = sum(i["qty"] * i["price"] for i in items)
    cart = {
        "items": items,
        "total_items": total_items,
        "total_amount": round(total_amount, 2),
    }
    context["cart"] = cart
    return cart


def update_cart_qty(context: dict, product_id: int, qty: int) -> dict:
    cart = context.get("cart", {"items": [], "total_items": 0, "total_amount": 0.0})
    items = cart.get("items", [])
    for i in items:
        if i["product_id"] == product_id:
            i["qty"] = qty
            break
    total_items = sum(i["qty"] for i in items)
    total_amount = sum(i["qty"] * i["price"] for i in items)
    cart = {
        "items": items,
        "total_items": total_items,
        "total_amount": round(total_amount, 2),
    }
    context["cart"] = cart
    return cart


def clear_cart(context: dict) -> None:
    context["cart"] = {"items": [], "total_items": 0, "total_amount": 0.0}


def cart_summary_text(context: dict) -> str:
    """Human-readable cart summary for WhatsApp."""
    cart = get_cart(context)
    items = cart.get("items", [])
    if not items:
        return "🛒 Your cart is empty."
    lines = ["🛒 *Your Cart:*", ""]
    for i, item in enumerate(items, 1):
        line_total = item["qty"] * item["price"]
        lines.append(
            f"{i}. {item['name']}  x{item['qty']}  — ₹{line_total:,.0f}"
        )
    lines.append("")
    lines.append(f"*Total ({cart['total_items']} items): ₹{cart['total_amount']:,.0f}*")
    return "\n".join(lines)


def validate_stock(context: dict) -> list:
    """Revalidate all cart items against current stock. Returns list of issues."""
    cart = get_cart(context)
    issues = []
    for item in cart.get("items", []):
        try:
            with get_db_context() as conn:
                row = conn.execute(
                    "SELECT stock_quantity FROM products WHERE id = ?",
                    (item["product_id"],),
                ).fetchone()
                if row is None:
                    issues.append(f"{item['name']} is no longer available.")
                elif row[0] is not None and row[0] < item["qty"]:
                    issues.append(
                        f"{item['name']}: only {row[0]} units available (you requested {item['qty']})."
                    )
        except Exception as e:
            logger.warning(f"Stock check failed for product {item['product_id']}: {e}")
    return issues
