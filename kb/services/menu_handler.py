
import logging
from typing import Dict, Callable, Optional
from backend.database import (
    get_menu_item, list_menu_items, list_products,
    get_products_by_category, get_user_state, set_user_state
)

logger = logging.getLogger("menu_handler")

# ── Action Handler Functions ─────────────────────────────────────────────────

async def show_products(wa_id: str, context: dict = None) -> dict:
    """Show product catalog to user."""
    try:
        products = list_products(active_only=True, limit=50)
        if not products:
            return {
                "success": True,
                "response": "No products available currently. Please check back later!",
                "media_url": None,
                "media_type": None,
                "interactive": None
            }

        # Build interactive menu with products
        products_text = "📦 **Available Products:**\n\n"
        for product in products[:10]:  # Show first 10
            name = product.get("name", "Unknown")
            price = product.get("price", "N/A")
            unit = product.get("unit", "piece")
            category = product.get("category", "General")

            products_text += f"• **{name}**\n"
            products_text += f"  Price: {price}/{unit}\n"
            products_text += f"  Category: {category}\n\n"

        if len(products) > 10:
            products_text += f"\n_... and {len(products) - 10} more products_"

        return {
            "success": True,
            "response": products_text,
            "media_url": None,
            "media_type": None,
            "interactive": _build_product_menu()
        }
    except Exception as e:
        logger.error(f"Error in show_products: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve the products. Please try again later.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def fetch_nutrition(wa_id: str, context: dict = None) -> dict:
    """Fetch nutrition information for products."""
    try:
        return {
            "success": True,
            "response": "🔍 **Nutrition Information**\n\nPlease type the product name you're interested in, and I'll provide the nutrition details.\n\nExample: \"Nutrition for Almonds\" or \"Show nutrition for Cashews\"",
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_products", "title": "View Products"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in fetch_nutrition: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't process your nutrition request. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def track_order(wa_id: str, context: dict = None) -> dict:
    """Track order status."""
    try:
        state = get_user_state(wa_id)

        # Set user state to await order ID
        from ..database import set_user_state
        set_user_state(wa_id, "AWAITING_ORDER_ID")

        return {
            "success": True,
            "response": "📦 **Track Your Order**\n\nPlease enter your Order ID to check the status.\n\nYou can find your Order ID in your order confirmation message.",
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in track_order: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't process your tracking request. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def raise_complaint(wa_id: str, context: dict = None) -> dict:
    """Handle customer complaints."""
    try:
        # Set user state to await complaint type
        from ..database import set_user_state
        set_user_state(wa_id, "AWAITING_COMPLAINT_TYPE")

        return {
            "success": True,
            "response": "📝 **Raise a Complaint**\n\nPlease select the type of issue you're facing:",
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "complaint_delivery", "title": "Delivery Issue"}},
                    {"type": "reply", "reply": {"id": "complaint_product", "title": "Product Quality"}},
                    {"type": "reply", "reply": {"id": "complaint_billing", "title": "Billing/Payment"}},
                    {"type": "reply", "reply": {"id": "complaint_other", "title": "Other"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in raise_complaint: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't process your complaint request. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def show_shipping_info(wa_id: str, context: dict = None) -> dict:
    """Show shipping and delivery information."""
    try:
        shipping_text = """
🚚 **Shipping & Delivery Information**

• **Standard Delivery:** 5-7 business days
• **Express Delivery:** 2-3 business days
• **Free Shipping:** On orders above ₹500
• **Shipping Charges:** ₹40 for orders below ₹500

**Delivery Areas:**
We deliver to all major cities and towns across India.

**Tracking:**
You'll receive real-time tracking updates via WhatsApp and SMS.

For any shipping-related queries, please type "shipping help".
        """

        return {
            "success": True,
            "response": shipping_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_track_order", "title": "Track Order"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in show_shipping_info: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve shipping information. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def show_recipes(wa_id: str, context: dict = None) -> dict:
    """Show recipe suggestions."""
    try:
        recipes_text = """
🍳 **Recipe Suggestions**

Here are some popular recipes using our products:

1. **Almond Smoothie**
   - Soaked almonds, milk, honey, dates
   - Blend and serve chilled

2. **Cashew Curry**
   - Cashews, tomatoes, onions, spices
   - Rich and creamy Indian curry

3. **Pistachio Ice Cream**
   - Pistachios, cream, sugar, cardamom
   - Homemade dessert

Would you like detailed recipes for any of these? Just let me know!
        """

        return {
            "success": True,
            "response": recipes_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_products", "title": "View Products"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in show_recipes: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve recipe suggestions. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def connect_to_human(wa_id: str, context: dict = None) -> dict:
    """Connect user to human support agent."""
    try:
        # Set user state to talking to human
        from ..database import set_user_state
        set_user_state(wa_id, "TALKING_TO_HUMAN")

        human_text = """
👤 **Connecting to Human Support**

Thank you for your patience. You're being connected to our support team.

Our support hours are:
- Monday to Friday: 9 AM to 6 PM
- Saturday: 10 AM to 4 PM

You'll receive a response shortly. In the meantime, feel free to describe your query in detail.
        """

        return {
            "success": True,
            "response": human_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Return to Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in connect_to_human: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't connect you to support. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def show_new_arrivals(wa_id: str, context: dict = None) -> dict:
    """Show new product arrivals."""
    try:
        products = list_products(active_only=True, limit=20)

        # Get products created in last 30 days (simulated by sorting)
        new_products = sorted(products, key=lambda p: p.get("created_at", ""), reverse=True)[:5]

        if not new_products:
            return {
                "success": True,
                "response": "✨ **New Releases**\n\nNo new products this week. Check out our existing catalog!",
                "media_url": None,
                "media_type": None,
                "interactive": None
            }

        arrivals_text = "✨ **New Releases**\n\n"
        for product in new_products:
            name = product.get("name", "Unknown")
            price = product.get("price", "N/A")
            description = product.get("short_description", "")

            arrivals_text += f"🆕 **{name}**\n"
            arrivals_text += f"   {description}\n"
            arrivals_text += f"   Price: {price}\n\n"

        return {
            "success": True,
            "response": arrivals_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_products", "title": "All Products"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in show_new_arrivals: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve new releases. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def show_catalog(wa_id: str, context: dict = None) -> dict:
    """Show product catalog from FAQ."""
    try:
        catalog_text = """
📚 **Product Brochure**

Our brochure includes:

• **Nuts & Dry Fruits:** Almonds, Cashews, Pistachios, Walnuts
• **Spices:** Saffron, Cardamom, Cinnamon, Cloves
• **Flours:** Almond flour, Coconut flour, Multigrain atta
• **Oils:** Almond oil, Coconut oil, Sesame oil

Would you like detailed information about any category?
        """

        return {
            "success": True,
            "response": catalog_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_products", "title": "Browse Products"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in show_catalog: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve the brochure. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def return_policy(wa_id: str, context: dict = None) -> dict:
    """Show return policy information."""
    try:
        policy_text = """
🔄 **Return & Refund Policy**

**Returns Accepted Within:**
- 7 days for food products
- 15 days for non-food items

**Conditions:**
- Products must be unopened and in original packaging
- Food items must be within expiration date
- Proof of purchase required

**Refund Process:**
- Refunds processed within 5-7 business days
- Amount credited to original payment method

**Contact:**
For returns, please contact us at support@example.com or call +91-XXXXXXXXXX

For specific return requests, please connect with our support team.
        """

        return {
            "success": True,
            "response": policy_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_complaint", "title": "Contact Support"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in return_policy: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve the return policy. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def about_company(wa_id: str, context: dict = None) -> dict:
    """Show about company information."""
    try:
        company_text = """
🏢 **About Our Company**

We are a leading provider of premium quality nuts, dry fruits, spices, and healthy food products.

**Our Mission:**
To deliver the highest quality, freshest products directly from farms to your doorstep.

**Why Choose Us:**
✓ Direct sourcing from certified farmers
✓ 100% natural and organic products
✓ No artificial preservatives or additives
✓ Quality tested at every stage
✓ Competitive wholesale pricing

**Our Journey:**
Started in 2020, we've grown from a small family business to serving thousands of happy customers across India.

**Certifications:**
- FSSAI compliant
- Organic certified
- ISO 22000

For more information, visit our website or contact our sales team.
        """

        return {
            "success": True,
            "response": company_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_products", "title": "Our Products"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in about_company: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve company information. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


async def payment_terms(wa_id: str, context: dict = None) -> dict:
    """Show payment terms and conditions."""
    try:
        payment_text = """
💳 **Payment Terms & Conditions**

**Payment Methods Accepted:**
- Credit/Debit Cards (Visa, Mastercard, RuPay)
- Net Banking (all major banks)
- UPI (Google Pay, PhonePe, Paytm)
- Cash on Delivery (select locations)
- Wallets (Paytm, Amazon Pay)

**Payment Terms:**
- Full payment required for COD orders
- Online payments: Instant order confirmation
- Bulk orders: Credit facility available for approved customers

**Refund Policy:**
- Automatic refund for cancelled orders
- 5-7 business days for amount credit

**Security:**
- 100% secure payment gateway
- PCI DSS compliant
- No card details stored

For payment-related queries, please contact our billing department.
        """

        return {
            "success": True,
            "response": payment_text.strip(),
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "action_track_order", "title": "Track Order"}},
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }
    except Exception as e:
        logger.error(f"Error in payment_terms: {e}")
        return {
            "success": False,
            "response": "Sorry, I couldn't retrieve payment information. Please try again.",
            "media_url": None,
            "media_type": None,
            "interactive": None
        }


# ── Action Handler Map ─────────────────────────────────────────────────────────

ACTION_HANDLERS: Dict[str, Callable] = {
    # Product-related actions
    "action_products": show_products,
    "action_show_products": show_products,
    "action_nutrition": fetch_nutrition,
    "action_fetch_nutrition": fetch_nutrition,
    "action_new_arrivals": show_new_arrivals,
    "action_catalog": show_catalog,

    # Order-related actions
    "action_track_order": track_order,
    "action_complaint": raise_complaint,

    # Information actions
    "action_shipping": show_shipping_info,
    "action_recipes": show_recipes,
    "action_human": connect_to_human,

    # Policy actions
    "action_return_policy": return_policy,
    "action_about": about_company,
    "action_about_company": about_company,
    "action_payment_terms": payment_terms,
}


# ── Main Handler Function ─────────────────────────────────────────────────────

async def handle_menu_click(action_id: str, wa_id: str, context: dict = None) -> dict:
    """
    Handle menu button clicks by dispatching to appropriate handler.

    Args:
        action_id: The payload/action ID from the clicked button
        wa_id: WhatsApp ID of the user
        context: Optional context dict with additional information

    Returns:
        dict: Response with keys:
            - success: bool
            - response: str (message text)
            - media_url: str | None
            - media_type: str | None
            - interactive: dict | None (button/list menu)
    """
    if not action_id:
        logger.warning("handle_menu_click called with empty action_id")
        return {
            "success": False,
            "response": "Invalid menu selection. Please try again or select Main Menu.",
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }

    # Normalize action_id (remove common prefixes)
    clean_action_id = action_id.strip()
    if clean_action_id.startswith("menu_"):
        clean_action_id = clean_action_id.replace("menu_", "action_", 1)

    # Special handling for main_menu
    if clean_action_id == "main_menu":
        return {
            "success": True,
            "response": "Returning to main menu...",
            "media_url": None,
            "media_type": None,
            "interactive": None  # Will trigger main menu display
        }

    # Check if action exists in handlers
    handler = ACTION_HANDLERS.get(clean_action_id)

    if not handler:
        # Try with action_ prefix if not present
        if not clean_action_id.startswith("action_"):
            clean_action_id = f"action_{clean_action_id}"
            handler = ACTION_HANDLERS.get(clean_action_id)

    if handler:
        try:
            logger.info(f"Dispatching action '{clean_action_id}' to handler")
            result = await handler(wa_id, context)
            return result
        except Exception as e:
            logger.error(f"Error executing handler for '{clean_action_id}': {e}", exc_info=True)
            return {
                "success": False,
                "response": f"Sorry, there was an error processing your request: {str(e)}",
                "media_url": None,
                "media_type": None,
                "interactive": {
                    "type": "buttons",
                    "buttons": [
                        {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                    ]
                }
            }
    else:
        logger.warning(f"No handler found for action_id: '{clean_action_id}'")
        return {
            "success": False,
            "response": f"Unknown menu action: {action_id}\n\nPlease select a valid option or return to Main Menu.",
            "media_url": None,
            "media_type": None,
            "interactive": {
                "type": "buttons",
                "buttons": [
                    {"type": "reply", "reply": {"id": "main_menu", "title": "Main Menu"}}
                ]
            }
        }


# ── Helper Functions ─────────────────────────────────────────────────────────

def _build_product_menu() -> dict:
    """Build product catalog menu for interactive response."""
    try:
        products = list_products(active_only=True, limit=20)
        sections = []

        # Group by category
        categories = {}
        for product in products:
            cat = product.get("category", "General")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(product)

        for category, cat_products in list(categories.items())[:5]:
            rows = []
            for product in cat_products[:5]:
                rows.append({
                    "id": f"product_{product.get('id', '')}",
                    "title": product.get("name", "Unknown")[:24],
                    "description": f"{product.get('price', 'N/A')} per {product.get('unit', 'piece')}"[:72]
                })

            sections.append({
                "title": category[:24],
                "rows": rows
            })

        if not sections:
            return None

        return {
            "type": "list",
            "header": "Product Catalog",
            "body": "Browse our products by category:",
            "button": "Browse Products",
            "footer": "Tap to view details",
            "sections": sections
        }
    except Exception as e:
        logger.error(f"Error building product menu: {e}")
        return None


def get_available_actions() -> list:
    """Return list of available action payloads for admin API."""
    return list(ACTION_HANDLERS.keys())


def get_handler_info(action_id: str) -> dict:
    """Get information about a specific handler."""
    handler = ACTION_HANDLERS.get(action_id)
    if not handler:
        return None

    return {
        "action_id": action_id,
        "handler_name": handler.__name__,
        "description": handler.__doc__ or "No description available",
        "is_async": True
    }