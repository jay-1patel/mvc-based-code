"""
Safe WhatsApp Message Formatter for Dynamic Menus

This module provides safe formatting of menu items for WhatsApp with comprehensive
validation and length checks to prevent malformed messages.

Safety Features:
- Length validation (0, 1-3, 4-10, >10 items)
- String length limits for WhatsApp compatibility
- Exception handling for all formatting operations
- Fallback text responses on formatting failures
"""

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("whatsapp_formatter")


# ── Safety Constants ─────────────────────────────────────────────────────

WHATSAPP_BUTTON_LIMIT = 3        # Max buttons in interactive button format
WHATSAPP_LIST_MIN = 4            # Min items for list format
WHATSAPP_LIST_MAX = 10           # Max items in list format
WHATSAPP_TITLE_MAX_LENGTH = 24    # Max chars for button/list titles
WHATSAPP_DESC_MAX_LENGTH = 72     # Max chars for list descriptions
WHATSAPP_SECTION_MAX_LENGTH = 24   # Max chars for section titles
WHATSAPP_BODY_MAX_LENGTH = 1024   # Max chars for message body


# ── Main Safe Formatting Function ───────────────────────────────────────────

def format_menu_for_whatsapp(menu_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Safely format menu items for WhatsApp with comprehensive validation.

    Args:
        menu_items: List of menu item dictionaries with keys:
                    - title (str): Display text
                    - payload (str): Action ID
                    - Optional: description, section, etc.

    Returns:
        dict with keys:
        - success (bool): Whether formatting succeeded
        - format_type (str): "button", "list", or "text"
        - interactive (dict): WhatsApp interactive object or None
        - error (str | None): Error message if formatting failed
        - warning (str | None): Warning message if issues occurred
    """
    try:
        # Validate input
        if not menu_items:
            logger.warning("Empty menu items list")
            return {
                "success": False,
                "format_type": "error",
                "interactive": None,
                "error": "No menu items to display"
            }

        if not isinstance(menu_items, list):
            logger.error(f"Invalid menu_items type: {type(menu_items)}")
            return {
                "success": False,
                "format_type": "error",
                "interactive": None,
                "error": "Invalid menu data format"
            }

        item_count = len(menu_items)
        logger.info(f"Formatting {item_count} menu items for WhatsApp")

        # Route to appropriate formatter based on item count
        if item_count == 0:
            return _error_response("No menu items available")

        elif 1 <= item_count <= WHATSAPP_BUTTON_LIMIT:
            return _format_as_buttons(menu_items)

        elif WHATSAPP_LIST_MIN <= item_count <= WHATSAPP_LIST_MAX:
            return _format_as_list(menu_items)

        elif item_count > WHATSAPP_LIST_MAX:
            # Truncate and warn
            warning_msg = f"Menu has {item_count} items, limiting to {WHATSAPP_LIST_MAX}"
            logger.warning(warning_msg)
            truncated_items = menu_items[:WHATSAPP_LIST_MAX]
            result = _format_as_list(truncated_items)
            result["warning"] = warning_msg
            return result

        else:
            # Shouldn't reach here, but safety fallback
            logger.error(f"Unexpected item count: {item_count}")
            return _format_as_text_fallback(menu_items[:10])

    except Exception as e:
        logger.error(f"Critical error in format_menu_for_whatsapp: {e}", exc_info=True)
        return _error_response(f"Formatting error: {str(e)}")


# ── Safe Formatting Functions ─────────────────────────────────────────────────

def _format_as_buttons(menu_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Format menu items as WhatsApp interactive buttons (1-3 items).
    Safe formatting with string length validation.
    """
    try:
        buttons = []

        for item in menu_items:
            try:
                # Extract and validate data
                title = str(item.get("title", "Option")).strip()
                payload = str(item.get("payload", "")).strip()

                if not title or not payload:
                    logger.warning(f"Skipping invalid button item: {item}")
                    continue

                # Truncate to WhatsApp limits
                if len(title) > WHATSAPP_TITLE_MAX_LENGTH:
                    title = title[:WHATSAPP_TITLE_MAX_LENGTH - 3] + "..."
                    logger.debug(f"Button title truncated: {title}")

                # Create button object
                buttons.append({
                    "type": "reply",
                    "reply": {
                        "id": payload,
                        "title": title
                    }
                })

            except Exception as item_error:
                logger.warning(f"Error formatting button item: {item_error}")
                continue

        if not buttons:
            return _error_response("No valid buttons to display")

        return {
            "success": True,
            "format_type": "button",
            "interactive": {
                "type": "buttons",
                "body": "Please select an option:",
                "buttons": buttons
            },
            "error": None,
            "warning": None
        }

    except Exception as e:
        logger.error(f"Error in button formatting: {e}", exc_info=True)
        return _error_response(f"Button formatting error: {str(e)}")


def _format_as_list(menu_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Format menu items as WhatsApp interactive list (4-10 items).
    Safe formatting with section grouping and validation.
    """
    try:
        # Group items by section if available
        sections = {}

        for item in menu_items:
            try:
                # Extract and validate data
                title = str(item.get("title", "Option")).strip()
                payload = str(item.get("payload", "")).strip()
                description = str(item.get("description", "")).strip()
                section = str(item.get("section", "General")).strip()

                if not title or not payload:
                    logger.warning(f"Skipping invalid list item: {item}")
                    continue

                # Truncate to WhatsApp limits
                if len(title) > WHATSAPP_TITLE_MAX_LENGTH:
                    title = title[:WHATSAPP_TITLE_MAX_LENGTH - 3] + "..."

                if len(description) > WHATSAPP_DESC_MAX_LENGTH:
                    description = description[:WHATSAPP_DESC_MAX_LENGTH - 3] + "..."

                if len(section) > WHATSAPP_SECTION_MAX_LENGTH:
                    section = section[:WHATSAPP_SECTION_MAX_LENGTH - 3] + "..."

                # Add to section
                if section not in sections:
                    sections[section] = []

                sections[section].append({
                    "id": payload,
                    "title": title,
                    "description": description or "Tap to select"
                })

            except Exception as item_error:
                logger.warning(f"Error formatting list item: {item_error}")
                continue

        if not sections:
            return _error_response("No valid list items to display")

        # Convert to WhatsApp format
        whatsapp_sections = []

        for section_name, rows in sections.items():
            try:
                whatsapp_sections.append({
                    "title": section_name,
                    "rows": rows
                })
            except Exception as section_error:
                logger.warning(f"Error formatting section: {section_error}")
                continue

        if not whatsapp_sections:
            return _error_response("No valid sections to display")

        return {
            "success": True,
            "format_type": "list",
            "interactive": {
                "type": "list",
                "header": "Menu Options",
                "body": "Please select an option from the list below:",
                "button": "View Options",
                "footer": "Tap to select",
                "sections": whatsapp_sections
            },
            "error": None,
            "warning": None
        }

    except Exception as e:
        logger.error(f"Error in list formatting: {e}", exc_info=True)
        return _error_response(f"List formatting error: {str(e)}")


def _format_as_text_fallback(menu_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Safe text fallback when interactive formatting fails.
    Returns simple numbered list as text.
    """
    try:
        text_lines = ["Please select an option:"]

        for i, item in enumerate(menu_items[:10], 1):  # Max 10 items
            try:
                title = str(item.get("title", "Option")).strip()
                if title:
                    text_lines.append(f"{i}. {title}")
            except Exception as item_error:
                logger.warning(f"Error formatting text item: {item_error}")
                continue

        if len(text_lines) <= 1:
            return _error_response("No valid menu items to display")

        return {
            "success": True,
            "format_type": "text",
            "interactive": None,
            "response": "\n".join(text_lines),
            "error": None,
            "warning": "Displaying as text - interactive unavailable"
        }

    except Exception as e:
        logger.error(f"Error in text fallback formatting: {e}", exc_info=True)
        return _error_response(f"Text formatting error: {str(e)}")


def _error_response(error_message: str) -> Dict[str, Any]:
    """Return standardized error response."""
    return {
        "success": False,
        "format_type": "error",
        "interactive": None,
        "error": error_message,
        "warning": None
    }


# ── Utility Functions ─────────────────────────────────────────────────────

def validate_menu_item(item: Dict[str, Any]) -> bool:
    """
    Validate a single menu item has required fields.
    Returns True if valid, False otherwise.
    """
    try:
        if not isinstance(item, dict):
            return False

        title = item.get("title", "").strip()
        payload = item.get("payload", "").strip()

        return bool(title and payload)

    except Exception as e:
        logger.warning(f"Error validating menu item: {e}")
        return False


def sanitize_text_for_whatsapp(text: str, max_length: int = WHATSAPP_BODY_MAX_LENGTH) -> str:
    """
    Safely sanitize text for WhatsApp with length limits.
    Returns truncated text if too long.
    """
    try:
        if not text:
            return ""

        text = str(text).strip()

        if len(text) > max_length:
            text = text[:max_length - 3] + "..."
            logger.debug(f"Text sanitized and truncated to {max_length} chars")

        return text

    except Exception as e:
        logger.warning(f"Error sanitizing text: {e}")
        return "Text unavailable"


def get_formatting_limits() -> Dict[str, int]:
    """Return formatting limits for monitoring/validation."""
    return {
        "button_limit": WHATSAPP_BUTTON_LIMIT,
        "list_min": WHATSAPP_LIST_MIN,
        "list_max": WHATSAPP_LIST_MAX,
        "title_max": WHATSAPP_TITLE_MAX_LENGTH,
        "description_max": WHATSAPP_DESC_MAX_LENGTH,
        "section_max": WHATSAPP_SECTION_MAX_LENGTH,
        "body_max": WHATSAPP_BODY_MAX_LENGTH
    }


def estimate_message_size(interactive_obj: Dict[str, Any]) -> int:
    """
    Estimate the size of an interactive message object.
    Returns approximate character count.
    """
    try:
        import json

        if not interactive_obj:
            return 0

        json_str = json.dumps(interactive_obj, ensure_ascii=False)
        return len(json_str)

    except Exception as e:
        logger.warning(f"Error estimating message size: {e}")
        return 0