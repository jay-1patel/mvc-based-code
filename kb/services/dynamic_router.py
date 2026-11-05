"""
Safe Generic Action Dispatcher for Dynamic Menu System

This module provides a 100% dynamic menu system with comprehensive safety checks:
- Depth limiting to prevent infinite loops
- Exception handling for all operations
- Safe fallbacks for failed operations
- Input validation and sanitization

Architecture:
- execute_action(): Generic dispatcher with depth protection
- Each action type has safe exception handling
- No hardcoded menu logic - fully data-driven
"""

import logging
import json
from typing import Dict, Any, Optional
from backend.database import (
    get_menu_item, list_menu_items, get_child_menu_items,
    get_menu_item_by_id, get_root_menu_items, get_user_state, set_user_state
)

logger = logging.getLogger("dynamic_router")


# ── Safety Constants ─────────────────────────────────────────────────────

MAX_MENU_DEPTH = 3  # Prevent infinite menu loops
ALLOWED_ACTION_TYPES = ["text", "rag", "menu"]  # Restrict to safe operations
DEFAULT_FALLBACK_MESSAGE = "Sorry, I encountered an error processing your request. Please try again or contact support."


# ── Safe Action Type Handlers ───────────────────────────────────────────────

async def handle_text_action(action_data: str, wa_id: str = None, context: dict = None) -> dict:
    """
    Safe handler for 'text' action type.
    Returns the action_data string directly with length validation.
    """
    try:
        # Validate input
        if not action_data:
            logger.warning("TEXT action: empty action_data")
            return _safe_response(
                response="No content available.",
                action_type="text"
            )

        # Sanitize and limit length
        text = str(action_data).strip()
        if len(text) > 4096:  # WhatsApp message limit
            logger.warning(f"TEXT action: text too long ({len(text)} chars), truncating")
            text = text[:4090] + "..."

        logger.info(f"TEXT action: returning {len(text)} chars")

        return _safe_response(
            response=text,
            action_type="text"
        )

    except Exception as e:
        logger.error(f"Error in text action handler: {e}", exc_info=True)
        return _safe_response(
            response=DEFAULT_FALLBACK_MESSAGE,
            action_type="error",
            error=f"Text action error: {str(e)}"
        )


async def handle_rag_action(action_data: str, wa_id: str = None, context: dict = None) -> dict:
    """
    Safe handler for 'rag' action type with comprehensive exception handling.
    Queries vector DB using action_data as collection name with fallback.
    Handles CollectionNotFound and all other exceptions safely.
    """
    try:
        # Validate input
        if not action_data:
            logger.warning("RAG action: empty action_data")
            return _safe_response(
                response="Please specify what you'd like to search for.",
                action_type="rag"
            )

        # Parse action_data (can be simple string or JSON)
        params = {}
        collection = action_data
        query = context.get("user_query", "") if context else ""
        limit = 5

        try:
            if action_data.startswith("{"):
                params = json.loads(action_data)
                collection = params.get("collection", "faq")
                query = params.get("query", query)
                limit = params.get("limit", 5)
        except json.JSONDecodeError:
            # If not valid JSON, treat as simple collection name
            collection = str(action_data).strip()

        logger.info(f"RAG action: collection={collection}, query={query[:50] if query else 'N/A'}")

        # Import RAG functions safely with exception handling
        try:
            from faq.service import faq_index

            # Check if index is built and available
            if not hasattr(faq_index, 'is_built') or not faq_index.is_built:
                logger.warning(f"FAQ index not built for collection: {collection}")
                return _safe_response(
                    response=f"Search service for '{collection}' is currently being updated. Please try again in a few minutes.",
                    action_type="error",
                    error="Index not built"
                )

            if query:
                try:
                    results = faq_index.search(query, k=min(limit, 10))
                    response_text = _format_rag_results_safe(results, collection)
                except Exception as search_error:
                    # Handle CollectionNotFound or search errors
                    error_msg = str(search_error).lower()
                    if "collection" in error_msg or "not found" in error_msg:
                        logger.warning(f"Collection not found: {collection}")
                        return _safe_response(
                            response=f"Sorry, the '{collection}' database is not available. Please try a different search or contact support.",
                            action_type="error",
                            error="Collection not found"
                        )
                    else:
                        logger.error(f"RAG search error: {search_error}")
                        return _safe_response(
                            response="Search encountered an error. Please try rephrasing your question or contact support.",
                            action_type="error",
                            error=f"Search failed: {str(search_error)}"
                        )
            else:
                response_text = f"🔍 **{collection.title()} Search**\n\nPlease provide a search query to search the {collection} database."

            return _safe_response(
                response=response_text,
                action_type="rag"
            )

        except ImportError as import_error:
            logger.error(f"RAG import error: {import_error}")
            return _safe_response(
                response="Search service temporarily unavailable. Please try again later.",
                action_type="error",
                error="RAG service import failed"
            )

        except Exception as rag_error:
            # Catch-all for any RAG-related errors
            logger.error(f"RAG system error: {rag_error}", exc_info=True)

            # Check for specific error types
            error_msg = str(rag_error).lower()
            if "collection" in error_msg:
                return _safe_response(
                    response="The requested database is not available. Please contact support.",
                    action_type="error",
                    error="Collection unavailable"
                )
            elif "connection" in error_msg or "timeout" in error_msg:
                return _safe_response(
                    response="Search service is temporarily unavailable. Please try again later.",
                    action_type="error",
                    error="Service unavailable"
                )
            else:
                return _safe_response(
                    response="Search encountered an error. Please try rephrasing your question.",
                    action_type="error",
                    error=f"RAG error: {str(rag_error)}"
                )

    except Exception as e:
        logger.error(f"Critical error in RAG action handler: {e}", exc_info=True)
        return _safe_response(
            response=DEFAULT_FALLBACK_MESSAGE,
            action_type="error",
            error=f"RAG handler error: {str(e)}"
        )


async def handle_menu_action(action_data: str, wa_id: str = None, context: dict = None, depth: int = 0) -> dict:
    """
    Safe handler for 'menu' action type with depth limiting.
    Fetches and formats sub-menu items with infinite loop prevention.
    """
    try:
        # Check depth limit to prevent infinite loops
        if depth > MAX_MENU_DEPTH:
            logger.error(f"MENU action: depth limit exceeded ({depth} > {MAX_MENU_DEPTH})")
            return _safe_response(
                response="Sorry, this menu is too deep. Please return to the main menu.",
                action_type="error",
                error="Menu depth limit exceeded"
            )

        # Parse parent_id from action_data
        parent_id = None
        try:
            if action_data and action_data.lower() not in ["null", "none", ""]:
                if action_data.isdigit():
                    parent_id = int(action_data)
                elif action_data.startswith("{"):
                    params = json.loads(action_data)
                    parent_id = params.get("parent_id")
                else:
                    # Try to treat as integer
                    parent_id = int(action_data)
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"MENU action: invalid parent_id format: {action_data} - {e}")
            parent_id = None

        logger.info(f"MENU action: parent_id={parent_id}, depth={depth}")

        # Fetch menu items safely
        try:
            if parent_id is None:
                menu_items = get_root_menu_items(active_only=True)
            else:
                menu_items = get_child_menu_items(parent_id, active_only=True)

            # Validate results
            if not menu_items:
                logger.warning(f"MENU action: no items found for parent_id={parent_id}")
                return _safe_response(
                    response="No menu items available.",
                    action_type="menu",
                    error="No menu items found"
                )

            # Limit number of items to prevent overwhelming responses
            if len(menu_items) > 10:
                logger.warning(f"MENU action: too many items ({len(menu_items)}), limiting to 10")
                menu_items = menu_items[:10]

            # Import WhatsApp formatter safely
            try:
                from kb.services.whatsapp_formatter import format_menu_for_whatsapp
                interactive_menu = format_menu_for_whatsapp(menu_items)

                if interactive_menu.get("error"):
                    # Formatting failed, return safe text response
                    return _safe_response(
                        response=f"Please select from these options:\n" + "\n".join([
                            f"{i+1}. {item.get('title', 'Option')}"
                            for i, item in enumerate(menu_items)
                        ]),
                        action_type="menu",
                        error=interactive_menu.get("error")
                    )

                return _safe_response(
                    response="Please select an option:",
                    action_type="menu",
                    interactive=interactive_menu
                )

            except ImportError as e:
                logger.error(f"WhatsApp formatter import error: {e}")
                # Fallback: simple text menu
                menu_text = "Please select an option:\n"
                for i, item in enumerate(menu_items, 1):
                    menu_text += f"{i}. {item.get('title', 'Option')}\n"

                return _safe_response(
                    response=menu_text.strip(),
                    action_type="menu",
                    error="Formatter unavailable"
                )

        except Exception as db_error:
            logger.error(f"Database error fetching menu items: {db_error}", exc_info=True)
            return _safe_response(
                response="Unable to load menu. Please try again or return to main menu.",
                action_type="error",
                error=f"Database error: {str(db_error)}"
            )

    except Exception as e:
        logger.error(f"Critical error in menu action handler: {e}", exc_info=True)
        return _safe_response(
            response=DEFAULT_FALLBACK_MESSAGE,
            action_type="error",
            error=f"Menu handler error: {str(e)}"
        )


# ── Main Safe Generic Dispatcher ─────────────────────────────────────────────

async def execute_action(action_type: str, action_data: str, wa_id: str = None,
                       context: dict = None, depth: int = 0) -> dict:
    """
    Safe generic action dispatcher with comprehensive error handling.

    Args:
        action_type: Type of action ("text", "rag", "menu")
        action_data: Data required for the action
        wa_id: WhatsApp ID of the user (optional)
        context: Additional context (user query, session info, etc.)
        depth: Current menu depth (prevents infinite loops)

    Returns:
        dict with guaranteed structure, even on errors:
        {
            "success": bool,
            "response": str,
            "action_type": str,
            "error": str | None,
            "interactive": dict | None
        }
    """
    try:
        # Validate action_type
        if not action_type or not isinstance(action_type, str):
            logger.warning(f"Invalid action_type: {action_type}")
            return _safe_response(
                response=f"Invalid action type configured. Please contact support.",
                action_type="error",
                error="Invalid action_type"
            )

        action_type = action_type.strip().lower()

        if action_type not in ALLOWED_ACTION_TYPES:
            logger.warning(f"Unsupported action_type: {action_type}")
            return _safe_response(
                response=f"Action '{action_type}' is not supported. Please contact support.",
                action_type="error",
                error=f"Unsupported action_type: {action_type}"
            )

        # Validate action_data
        if action_data is None:
            action_data = ""

        # Route to appropriate handler with safety
        handlers = {
            "text": handle_text_action,
            "rag": handle_rag_action,
            "menu": lambda data, uid, ctx: handle_menu_action(data, uid, ctx, depth),
        }

        handler = handlers.get(action_type)
        if not handler:
            logger.error(f"No handler found for action_type: {action_type}")
            return _safe_response(
                response=f"No handler available for action: {action_type}",
                action_type="error",
                error="Handler not found"
            )

        logger.info(f"Executing safe action: type={action_type}, data={action_data[:100]}, depth={depth}")

        # Execute handler with exception handling
        try:
            result = await handler(action_data, wa_id, context)

            # Validate response structure
            if not isinstance(result, dict):
                logger.error(f"Handler returned invalid result type: {type(result)}")
                return _safe_response(
                    response=DEFAULT_FALLBACK_MESSAGE,
                    action_type="error",
                    error="Invalid handler response"
                )

            # Ensure required fields exist
            if "response" not in result:
                result["response"] = DEFAULT_FALLBACK_MESSAGE
            if "success" not in result:
                result["success"] = True
            if "action_type" not in result:
                result["action_type"] = action_type
            if "interactive" not in result:
                result["interactive"] = None
            if "error" not in result:
                result["error"] = None

            return result

        except Exception as handler_error:
            logger.error(f"Handler execution error for {action_type}: {handler_error}", exc_info=True)
            return _safe_response(
                response=DEFAULT_FALLBACK_MESSAGE,
                action_type="error",
                error=f"Handler execution failed: {str(handler_error)}"
            )

    except Exception as e:
        logger.error(f"Critical error in execute_action: {e}", exc_info=True)
        return _safe_response(
            response=DEFAULT_FALLBACK_MESSAGE,
            action_type="error",
            error=f"Dispatcher error: {str(e)}"
        )


# ── Safety Helper Functions ─────────────────────────────────────────────────────

def _safe_response(response: str, action_type: str, error: str = None, interactive: dict = None) -> dict:
    """Return standardized safe response with guaranteed structure."""
    return {
        "success": error is None,  # Success if no error
        "response": str(response) if response else DEFAULT_FALLBACK_MESSAGE,
        "action_type": action_type,
        "error": error,
        "interactive": interactive,
        "media_url": None,
        "media_type": None
    }


def _format_rag_results_safe(results: Any, source: str) -> str:
    """Safely format RAG results with comprehensive validation."""
    try:
        if not results:
            return f"No results found in {source}. Please try rephrasing your question."

        # Ensure results is a list
        if not isinstance(results, list):
            logger.warning(f"RAG results not a list: {type(results)}")
            return f"Unable to process results from {source}. Please try again."

        # Format results safely
        response = f"📚 **Results from {source}:**\n\n"
        count = 0

        for result in results[:5]:  # Max 5 results
            try:
                if isinstance(result, dict):
                    content = result.get("content", result.get("text", result.get("answer", "")))
                    metadata = result.get("metadata", {})
                    source_file = metadata.get("source", metadata.get("filename", "Unknown"))
                else:
                    content = str(result)
                    source_file = "Unknown"

                if not content:
                    continue

                response += f"{count + 1}. {str(content)[:300]}...\n"
                response += f"   _Source: {source_file}_\n\n"
                count += 1

            except Exception as item_error:
                logger.warning(f"Error formatting RAG item: {item_error}")
                continue

        if count == 0:
            return f"No valid results found in {source}. Please try rephrasing your question."

        if len(results) > 5:
            response += f"\n_... and {len(results) - 5} more results_"

        return response.strip()

    except Exception as e:
        logger.error(f"Error formatting RAG results: {e}", exc_info=True)
        return f"Error processing search results from {source}. Please try again."


# ── Utility Functions ─────────────────────────────────────────────────────────────

def get_allowed_action_types() -> list:
    """Return list of allowed action types for admin API."""
    return ALLOWED_ACTION_TYPES.copy()


def get_action_type_description(action_type: str) -> str:
    """Get human-readable description of action type."""
    descriptions = {
        "text": "Display static text message",
        "rag": "Search knowledge base with RAG",
        "menu": "Display sub-menu options"
    }
    return descriptions.get(action_type, "Unknown action type")


def get_safety_limits() -> dict:
    """Return safety limits and constraints for monitoring."""
    return {
        "max_menu_depth": MAX_MENU_DEPTH,
        "allowed_action_types": ALLOWED_ACTION_TYPES,
        "max_response_length": 4096,
        "max_menu_items": 10
    }