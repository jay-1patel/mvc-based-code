"""
Intent Classification and Context Management Orchestrator

This module handles:
- Intent classification with confidence scores
- Conversational context tracking (active product, category, etc.)
- Low confidence detection for fallback menu triggers
- Query enhancement with context for better LLM responses
"""

import re
import json
from typing import Dict, List, Tuple, Optional
from ..database import get_user_state, update_user_context, search_products
from .product_keywords import get_product_keywords
from routing.config import logger


def classify_intent_with_confidence(query: str, wa_id: str) -> Tuple[str, float]:
    """
    Classify user intent with confidence score.

    Returns:
        tuple: (intent_category, confidence_score)
        - intent_category: "product_search", "pricing", "information", "greeting", "unknown"
        - confidence_score: float 0.0-1.0
    """
    if not query:
        return "unknown", 0.0

    query_lower = query.lower().strip()

    # High-confidence product patterns
    product_keywords = ["buy", "price", "cost", "order", "get", "want", "need", "looking for"]
    product_indicators = get_product_keywords()

    # High-confidence information patterns
    info_keywords = ["nutrition", "ingredient", "calories", "protein", "health", "benefit", "allergen"]

    # High-confidence greeting patterns
    greeting_keywords = ["hi", "hello", "hey", "start", "begin", "menu"]

    # Calculate scores for each category
    scores = {
        "product_search": 0.0,
        "pricing": 0.0,
        "information": 0.0,
        "greeting": 0.0,
        "unknown": 0.1  # Base score for unknown
    }

    # Product search intent
    for keyword in product_keywords:
        if keyword in query_lower:
            scores["product_search"] += 0.2

    for indicator in product_indicators:
        if indicator in query_lower:
            scores["product_search"] += 0.15

    # Pricing intent
    if any(word in query_lower for word in ["price", "cost", "rate", "cheap", "expensive", "mrp"]):
        scores["pricing"] += 0.3

    # Information intent
    for keyword in info_keywords:
        if keyword in query_lower:
            scores["information"] += 0.25

    # Greeting intent
    for keyword in greeting_keywords:
        if keyword in query_lower:
            scores["greeting"] += 0.3

    # Normalize scores to 0-1 range
    max_score = max(scores.values()) if scores else 0.1
    if max_score > 1.0:
        max_score = 1.0

    # Get best intent
    best_intent = max(scores, key=scores.get)
    confidence = min(1.0, scores[best_intent])

    logger.debug(f"Intent classification: query='{query[:50]}...' intent={best_intent} confidence={confidence:.2f}")

    return best_intent, confidence


def get_conversation_context(wa_id: str) -> Dict:
    """
    Get current conversation context for a user.

    Returns context dictionary with:
        - active_product_id: int | None
        - active_product_name: str | None
        - active_category: str | None
        - last_topic: str | None
        - conversation_turns: int
        - context_age_minutes: int
    """
    try:
        state_data = get_user_state(wa_id)
        context = state_data.get("context_json", {})

        # Ensure context has expected structure
        if not isinstance(context, dict):
            context = {}

        # Add metadata about context
        updated_at = state_data.get("updated_at", "")
        conversation_turns = context.get("conversation_turns", 0)

        return {
            "active_product_id": context.get("active_product_id"),
            "active_product_name": context.get("active_product_name"),
            "active_category": context.get("active_category"),
            "last_topic": context.get("last_topic"),
            "conversation_turns": conversation_turns,
            "context_age_minutes": _calculate_context_age(updated_at)
        }
    except Exception as e:
        logger.error(f"Error getting conversation context for {wa_id}: {e}")
        return {
            "active_product_id": None,
            "active_product_name": None,
            "active_category": None,
            "last_topic": None,
            "conversation_turns": 0,
            "context_age_minutes": 0
        }


def _calculate_context_age(updated_at: str) -> int:
    """Calculate how old the context is in minutes."""
    if not updated_at:
        return 999  # Very old context

    try:
        from datetime import datetime
        # Parse timestamp format from SQLite
        if "T" in updated_at:  # ISO format
            dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        else:  # SQLite format
            dt = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")

        now = datetime.now(dt.tzinfo)
        age_minutes = int((now - dt).total_seconds() / 60)
        return max(0, age_minutes)
    except Exception:
        return 999


def update_conversation_context(wa_id: str, **context_updates) -> bool:
    """
    Update conversation context for a user.

    Args:
        wa_id: User's WhatsApp ID
        **context_updates: Key-value pairs to update in context
            - active_product_id: int | None
            - active_product_name: str | None
            - active_category: str | None
            - last_topic: str | None
            - conversation_turns: int (auto-incremented if not provided)

    Returns:
        bool: Success status
    """
    try:
        current_context = get_conversation_context(wa_id)

        # Auto-increment conversation turns if not explicitly set
        if "conversation_turns" not in context_updates:
            context_updates["conversation_turns"] = current_context.get("conversation_turns", 0) + 1

        # Merge with existing context
        updated_context = {**current_context, **context_updates}

        # Update in database
        return update_user_context(wa_id, updated_context)

    except Exception as e:
        logger.error(f"Error updating conversation context for {wa_id}: {e}")
        return False


def enhance_query_with_context(query: str, wa_id: str) -> str:
    """
    Enhance user query with conversation context for better LLM understanding.

    When a user has active context (e.g., active_product_name), silently prepend
    context to their query so the LLM can provide more relevant, contextual answers.

    Example:
        User: "How much is it?"
        Context: active_product_name="Chikki Spread"
        Enhanced: "Regarding Chikki Spread, how much is it?"

    Args:
        query: Original user query
        wa_id: User's WhatsApp ID

    Returns:
        str: Enhanced query with context prepended (if relevant context exists)
    """
    try:
        context = get_conversation_context(wa_id)

        # No relevant context to add
        if not context.get("active_product_name") and not context.get("active_category"):
            return query

        # Only enhance if context is recent (<30 minutes) and query is ambiguous
        context_age = context.get("context_age_minutes", 999)
        if context_age > 30:
            logger.debug(f"Context too old ({context_age} minutes), not enhancing query")
            return query

        # Detect if query needs context enhancement
        query_lower = query.lower().strip()

        # Pronouns and references that need context
        context_needing_phrases = [
            "it", "its", "this", "that", "this one", "that one",
            "how much", "price", "cost", "buy", "order",
            "nutrition", "ingredients", "calories", "protein",
            "show me", "tell me", "details"
        ]

        needs_context = any(phrase in query_lower for phrase in context_needing_phrases)

        if not needs_context:
            return query

        # Build enhanced query
        enhanced_parts = []

        if context.get("active_product_name"):
            enhanced_parts.append(f"Regarding {context['active_product_name']}")
        elif context.get("active_category"):
            enhanced_parts.append(f"Regarding products in {context['active_category']}")

        if enhanced_parts:
            enhanced_query = f"{', '.join(enhanced_parts)}, {query}"
            logger.info(f"Enhanced query with context: '{query[:30]}...' -> '{enhanced_query[:50]}...'")
            return enhanced_query

        return query

    except Exception as e:
        logger.error(f"Error enhancing query with context: {e}")
        return query


def should_trigger_fallback_menu(query: str, wa_id: str, confidence_threshold: float = 0.40) -> bool:
    """
    Determine if we should show visual fallback menu instead of processing the query.

    Triggers fallback menu when:
    - Intent confidence is below threshold (default 0.40)
    - User has recent conversation context (to avoid frustration)
    - Query is not a clear menu/navigation request

    Args:
        query: User's query
        wa_id: User's WhatsApp ID
        confidence_threshold: Minimum confidence for normal processing (default 0.40)

    Returns:
        bool: True if fallback menu should be shown
    """
    try:
        # Get intent classification
        intent, confidence = classify_intent_with_confidence(query, wa_id)

        # Check if confidence is below threshold
        if confidence < confidence_threshold:
            logger.info(f"Low confidence detected: {confidence:.2f} < {confidence_threshold} for query: '{query[:50]}...'")

            # But don't trigger fallback for menu/navigation requests
            query_lower = query.lower().strip()
            menu_keywords = ["menu", "main menu", "start", "begin", "help", "options"]
            if any(keyword in query_lower for keyword in menu_keywords):
                logger.debug("Skipping fallback - menu/navigation request detected")
                return False

            # Check if user has recent conversation (avoid fallback for new users)
            context = get_conversation_context(wa_id)
            if context.get("conversation_turns", 0) >= 2:
                logger.info("Triggering fallback menu - low confidence with conversation history")
                return True

        return False

    except Exception as e:
        logger.error(f"Error checking fallback menu trigger: {e}")
        return False


def extract_product_context_from_response(response_text: str, wa_id: str) -> Optional[Dict]:
    """
    Extract product context from LLM response to update conversation state.

    When the LLM responds with specific product information, extract that
    context to improve follow-up queries.

    Args:
        response_text: LLM response text
        wa_id: User's WhatsApp ID

    Returns:
        dict: Extracted context updates or None
    """
    try:
        # Look for product mentions in response
        # Pattern: "Product: {name}" or "*{product_name}*" (WhatsApp bold)
        product_patterns = [
            r'Product:\s*([A-Za-z][A-Za-z0-9\s\-\&]+)',  # "Product: Chikki Spread"
            r'\*([A-Za-z][A-Za-z0-9\s\-\&]{5,30})\*',      # "*Chikki Spread*" (bold)
        ]

        context_updates = {}

        for pattern in product_patterns:
            matches = re.findall(pattern, response_text, re.IGNORECASE)
            if matches:
                product_name = matches[0].strip()
                # Try to find this product in database
                products = search_products(product_name, limit=1)
                if products:
                    product = products[0]
                    context_updates.update({
                        "active_product_id": product.get("id"),
                        "active_product_name": product.get("name"),
                        "active_category": product.get("category"),
                        "last_topic": "product_details"
                    })
                    logger.info(f"Extracted product context from response: {product_name}")
                    break

        if context_updates:
            update_conversation_context(wa_id, **context_updates)
            return context_updates

        return None

    except Exception as e:
        logger.error(f"Error extracting product context from response: {e}")
        return None


# ── Context-Aware Quick Replies Generation ───────────────────────────────────

def get_context_aware_buttons(wa_id: str) -> List[Dict]:
    """
    Generate context-aware quick reply buttons based on conversation state.

    Returns different button sets based on:
    - Active product being discussed
    - Current category being browsed
    - General conversation state

    Args:
        wa_id: User's WhatsApp ID

    Returns:
        list: Button configurations with id, title, and optional url
    """
    try:
        context = get_conversation_context(wa_id)

        # Base buttons - always included
        buttons = [
            {"id": "main_menu", "title": "🏠 Main Menu"},
            {"id": "menu_ai", "title": "🤖 Ask AI"},
        ]

        # Product-specific buttons
        if context.get("active_product_id") and context.get("active_product_name"):
            product_id = context["active_product_id"]
            buttons.extend([
                {"id": f"menu_buy_{product_id}", "title": f"💰 Buy {context['active_product_name'][:15]}"},
                {"id": f"menu_nutrition_{product_id}", "title": "🥗 Nutrition Info"},
                {"id": "menu_human", "title": "👤 Talk to Human"},
            ])
        # Category-specific buttons
        elif context.get("active_category"):
            category = context["active_category"]
            buttons.extend([
                {"id": f"cat_{category}", "title": f"📦 Browse {category}"},
                {"id": "menu_products", "title": "🛍️ All Products"},
                {"id": "menu_human", "title": "👤 Talk to Human"},
            ])
        # General conversation buttons
        else:
            buttons.extend([
                {"id": "menu_products", "title": "🛍️ View Products"},
                {"id": "menu_catalog", "title": "📋 Product Brochure"},
                {"id": "menu_human", "title": "👤 Talk to Human"},
            ])

        # Limit to 3 buttons (WhatsApp limit)
        return buttons[:3]

    except Exception as e:
        logger.error(f"Error generating context-aware buttons: {e}")
        # Return default buttons on error
        return [
            {"id": "main_menu", "title": "🏠 Main Menu"},
            {"id": "menu_products", "title": "🛍️ View Products"},
            {"id": "menu_human", "title": "👤 Talk to Human"},
        ]


# ── Conversational State Summary ───────────────────────────────────────────

def get_conversation_state_summary(wa_id: str) -> Dict:
    """
    Get a comprehensive summary of the user's conversation state.

    Useful for debugging, analytics, and providing context to human agents.

    Args:
        wa_id: User's WhatsApp ID

    Returns:
        dict: Complete conversation state summary
    """
    try:
        context = get_conversation_context(wa_id)
        state_data = get_user_state(wa_id)

        return {
            "wa_id": wa_id,
            "current_state": state_data.get("state", "MAIN_MENU"),
            "language": state_data.get("lang", "en"),
            "conversation_context": {
                "active_product": {
                    "id": context.get("active_product_id"),
                    "name": context.get("active_product_name"),
                },
                "active_category": context.get("active_category"),
                "last_topic": context.get("last_topic"),
                "conversation_turns": context.get("conversation_turns", 0),
                "context_age_minutes": context.get("context_age_minutes", 0),
            },
            "state_age_minutes": _calculate_context_age(state_data.get("updated_at", "")),
            "is_active_session": context.get("context_age_minutes", 999) < 30
        }

    except Exception as e:
        logger.error(f"Error getting conversation state summary: {e}")
        return {
            "wa_id": wa_id,
            "error": str(e)
        }


# ── Context Reset Management ───────────────────────────────────────────────

def reset_conversation_context(wa_id: str, reason: str = "manual") -> bool:
    """
    Reset conversation context for a user.

    Use when:
    - User explicitly requests ("start over", "new conversation")
    - Too much time has passed (>30 minutes)
    - Context is no longer relevant
    - User navigates to main menu

    Args:
        wa_id: User's WhatsApp ID
        reason: Why context is being reset ("manual", "timeout", "menu_navigation", etc.)

    Returns:
        bool: Success status
    """
    try:
        logger.info(f"Resetting conversation context for {wa_id} - reason: {reason}")

        # Clear all context fields
        return update_user_context(wa_id, {
            "active_product_id": None,
            "active_product_name": None,
            "active_category": None,
            "last_topic": None,
            "conversation_turns": 0,
            "context_reset_reason": reason,
            "context_reset_time": _get_current_timestamp()
        })

    except Exception as e:
        logger.error(f"Error resetting conversation context: {e}")
        return False


def _get_current_timestamp() -> str:
    """Get current timestamp in database format."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-ESCALATION LOGIC
#
# After the RAG engine returns a result, we decide whether to suppress the RAG
# answer and hand the conversation to a human agent:
#
#   1. If the best similarity score of the retrieved chunk(s) < 0.30, OR
#   2. The user explicitly typed "speak to human" (or a common variant),
#
# then we do NOT send the RAG answer. Instead we send
# "I'm not sure about that. Connecting you to support...", set
# `user_states.human_handover = True`, and STOP processing.
#
# The decision logic lives in `customer_chatbot.escalation`; this function is
# the orchestrator-level helper that the kb_handler DEFAULT pipeline calls so
# escalation stays centralized and testable.
# ═══════════════════════════════════════════════════════════════════════════

def check_auto_escalation(
    user_text: str,
    sources: List[Dict],
    wa_id: str,
    similarity_threshold: float = 0.30,
) -> Tuple[bool, Optional[Dict]]:
    """
    Evaluate whether the RAG answer should be suppressed in favour of a human
    handover.

    Args:
        user_text: The user's original message.
        sources: Retrieved sources from the RAG pipeline; each entry should
            carry a numeric `score` (FAISS cosine similarity).
        wa_id: User's WhatsApp ID.
        similarity_threshold: Minimum acceptable similarity (default 0.30).

    Returns:
        (escalate, result):
        - escalate is True when the answer must be suppressed.
        - result is a dict shaped like `_result(...)` (answer/route/interactive/
          success/media_url/media_type/whatsapp_sent) when escalating, else None.
    """
    # The auto-escalation decision logic lives in the optional
    # `customer_chatbot.escalation` package, which is not present in this
    # deployment. When it is unavailable, we never auto-escalate: the normal
    # RAG / KB pipeline handles the reply.
    try:
        from customer_chatbot.escalation import (  # type: ignore
            should_escalate,
            mark_human_handover,
            get_escalation_message,
        )
    except Exception:
        return False, None

    # Centralised decision: low similarity OR explicit human request.
    if not should_escalate(user_text, sources):
        return False, None

    # Suppress the RAG answer: mark handover, prepare escalation response.
    mark_human_handover(wa_id)
    message = get_escalation_message()

    logger.info(
        f"AUTO_ESCALATION | wa_id={wa_id} | threshold={similarity_threshold} | "
        f"query='{user_text[:80]}'"
    )

    return True, {
        "answer": message,
        "route": "auto_escalation",
        "interactive": None,
        "success": True,
        "media_url": None,
        "media_type": None,
        "whatsapp_sent": False,
    }
