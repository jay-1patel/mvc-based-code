"""
Fuzzy Matching for Typo Recovery

This module handles:
- Product name fuzzy matching with configurable similarity thresholds
- "Did you mean?" suggestions generation
- Interactive button creation for typo corrections
- Multi-language support for product matching
"""

import re
from typing import List, Dict, Tuple, Optional
from ..database import search_products, list_products
from .product_keywords import contains_product_keyword
from routing.config import logger


# Similarity thresholds for fuzzy matching
FUZZY_THRESHOLD_MIN = 70  # Minimum similarity to consider a match (0-100)
FUZZY_THRESHOLD_GOOD = 80  # Good match - show suggestion confidently
FUZZY_THRESHOLD_EXCELLENT = 90  # Excellent match - very likely intended product


def fuzzy_match_products(query: str, min_similarity: int = FUZZY_THRESHOLD_MIN) -> List[Dict]:
    """
    Find products that closely match the user's query using fuzzy string matching.

    Uses multiple matching strategies:
    1. Direct product name matching (highest weight)
    2. Category matching (medium weight)
    3. Description/tag matching (lower weight)

    Args:
        query: User's search query (potentially with typos)
        min_similarity: Minimum similarity score (0-100) to include in results

    Returns:
        list: Products with similarity scores, sorted by relevance
            [{"product": {...}, "similarity": 85, "match_type": "name"}, ...]
    """
    if not query or len(query.strip()) < 2:
        return []

    try:
        query_lower = query.lower().strip()
        query_words = [w for w in re.findall(r"[a-zA-Z]{3,}", query_lower) if len(w) >= 3]

        if not query_words:
            return []

        # Get all active products for matching
        all_products = list_products(active_only=True, limit=200)
        if not all_products:
            return []

        scored_products = []

        for product in all_products:
            scores = {
                "name": 0.0,
                "category": 0.0,
                "description": 0.0,
                "ingredients": 0.0
            }

            # Product name matching (highest priority)
            product_name = (product.get("name") or "").lower()
            name_similarity = _calculate_similarity(query_lower, product_name)
            scores["name"] = name_similarity * 3.0  # 3x weight for name matches

            # Category matching
            category = (product.get("category") or "").lower()
            if category and any(word in category for word in query_words):
                scores["category"] = 85.0  # High score for category match

            # Description matching
            description = (product.get("description") or "").lower()
            short_desc = (product.get("short_description") or "").lower()
            desc_text = f"{description} {short_desc}"
            desc_similarity = _calculate_word_overlap(query_words, desc_text)
            scores["description"] = desc_similarity * 1.5  # 1.5x weight for description

            # Ingredients matching
            ingredients = (product.get("ingredients") or [])
            if isinstance(ingredients, list):
                ingredients_text = " ".join(str(ingredient).lower() for ingredient in ingredients)
            else:
                ingredients_text = str(ingredients).lower()

            if ingredients_text and any(word in ingredients_text for word in query_words):
                scores["ingredients"] = 75.0  # Good score for ingredient matches

            # Calculate total score
            total_score = max(scores.values())  # Take best matching category

            if total_score >= min_similarity:
                # Determine match type for UX display
                match_type = "name" if scores["name"] == total_score else (
                    "category" if scores["category"] == total_score else (
                        "description" if scores["description"] == total_score else "ingredients"
                    )
                )

                scored_products.append({
                    "product": product,
                    "similarity": int(total_score),
                    "match_type": match_type,
                    "scores_breakdown": scores
                })

        # Sort by similarity score (highest first)
        scored_products.sort(key=lambda x: x["similarity"], reverse=True)

        logger.info(f"Fuzzy match for '{query[:30]}...': found {len(scored_products)} products above {min_similarity}% threshold")

        return scored_products[:5]  # Return top 5 matches

    except Exception as e:
        logger.error(f"Error in fuzzy matching: {e}")
        return []


def _calculate_similarity(str1: str, str2: str) -> float:
    """
    Calculate similarity between two strings using multiple algorithms.

    Combines:
    - SequenceMatcher (built-in Python) for exact/substring matching
    - Word overlap for partial matches
    - Prefix/suffix matching for typo patterns

    Args:
        str1, str2: Strings to compare

    Returns:
        float: Similarity score (0-100)
    """
    try:
        from difflib import SequenceMatcher

        # Method 1: SequenceMatcher (good for typos, misspellings)
        sequence_ratio = SequenceMatcher(None, str1.lower(), str2.lower()).ratio() * 100

        # Method 2: Word overlap (good for rearranged words)
        words1 = set(str1.lower().split())
        words2 = set(str2.lower().split())

        if words1 and words2:
            intersection = words1.intersection(words2)
            union = words1.union(words2)
            word_overlap = (len(intersection) / len(union)) * 100
        else:
            word_overlap = 0

        # Method 3: Prefix/suffix matching (good for partial typing)
        prefix_match = 0
        suffix_match = 0

        min_len = min(len(str1), len(str2))
        if min_len > 2:
            # Check prefix match
            for i in range(min_len):
                if str1.lower()[i] == str2.lower()[i]:
                    prefix_match += 1
                else:
                    break

            # Check suffix match
            for i in range(1, min_len + 1):
                if str1.lower()[-i] == str2.lower()[-i]:
                    suffix_match += 1
                else:
                    break

        prefix_score = (prefix_match / min_len) * 100 if min_len > 0 else 0
        suffix_score = (suffix_match / min_len) * 100 if min_len > 0 else 0

        # Combine all methods with weights
        # SequenceMatcher: 40%, Word Overlap: 30%, Prefix: 15%, Suffix: 15%
        combined_score = (
            (sequence_ratio * 0.40) +
            (word_overlap * 0.30) +
            (prefix_score * 0.15) +
            (suffix_score * 0.15)
        )

        return min(100.0, combined_score)

    except Exception as e:
        logger.debug(f"Error calculating similarity: {e}")
        return 0.0


def _calculate_word_overlap(query_words: List[str], text: str) -> float:
    """
    Calculate word overlap score between query words and text.

    Args:
        query_words: List of query words (lowercase)
        text: Text to search in

    Returns:
        float: Overlap score (0-100)
    """
    if not query_words or not text:
        return 0.0

    text_lower = text.lower()
    text_words = set(re.findall(r"[a-zA-Z]{3,}", text_lower))

    if not text_words:
        return 0.0

    # Count matching words
    matches = sum(1 for word in query_words if word in text_words)

    # Calculate overlap percentage
    overlap = (matches / len(query_words)) * 100 if query_words else 0

    return min(100.0, overlap)


def generate_did_you_mean_message(query: str, matched_products: List[Dict]) -> Tuple[str, List[Dict]]:
    """
    Generate "Did you mean?" message with interactive buttons.

    Creates a friendly message suggesting the closest matching products
    and provides quick-reply buttons for easy selection.

    Args:
        query: Original user query (with potential typos)
        matched_products: Products from fuzzy_match_products()

    Returns:
        tuple: (message_text, button_configs)
            - message_text: Friendly "Did you mean?" message
            - button_configs: List of button configurations for interactive replies
    """
    if not matched_products:
        return "", []

    try:
        # Select best matches (high similarity only)
        best_matches = [p for p in matched_products if p["similarity"] >= FUZZY_THRESHOLD_GOOD]

        if not best_matches:
            # No good matches, return empty
            return "", []

        # Build friendly message
        top_match = best_matches[0]
        top_product = top_match["product"]
        similarity = top_match["similarity"]

        if similarity >= FUZZY_THRESHOLD_EXCELLENT:
            # Very high confidence - direct suggestion
            message = f"Did you mean *{top_product['name']}*?"
        else:
            # Good but not excellent confidence - show multiple options
            product_names = [p["product"]["name"] for p in best_matches[:3]]
            if len(product_names) > 1:
                message = f"Did you mean one of these?\n" + "\n".join([f"• {name}" for name in product_names])
            else:
                message = f"Did you mean *{product_names[0]}*?"

        # Create interactive buttons
        buttons = []

        # Add top matches as buttons
        for match in best_matches[:2]:  # Max 2 product buttons
            product = match["product"]
            product_id = product.get("id")
            product_name = product.get("name", "")

            # Truncate name if too long for button (max 20 chars)
            button_title = product_name[:20] + ("..." if len(product_name) > 20 else "")

            buttons.append({
                "id": f"fuzzy_product_{product_id}",
                "title": button_title,
                "payload": {"product_id": product_id, "product_name": product_name}
            })

        # Add "No, search as is" button
        buttons.append({
            "id": "fuzzy_search_original",
            "title": "Search as typed",
            "payload": {"original_query": query}
        })

        return message, buttons

    except Exception as e:
        logger.error(f"Error generating 'Did you mean?' message: {e}")
        return "", []


def should_trigger_fuzzy_correction(query: str) -> bool:
    """
    Determine if a query should trigger fuzzy correction logic.

    Triggers for:
    - Queries with potential typos (mixed case, unusual patterns)
    - Low exact-match results but high fuzzy-match results
    - Queries that look like product searches

    Args:
        query: User's query

    Returns:
        bool: True if fuzzy correction should be attempted
    """
    if not query or len(query.strip()) < 3:
        return False

    query_lower = query.lower().strip()

    # Don't trigger for menu/navigation commands
    menu_keywords = ["menu", "main menu", "help", "start", "home", "back"]
    if any(keyword in query_lower for keyword in menu_keywords):
        return False

    # Don't trigger for greetings
    greeting_keywords = ["hi", "hello", "hey", "good morning", "good evening"]
    if any(keyword in query_lower for keyword in greeting_keywords):
        return False

    # Trigger for product-related keywords
    if contains_product_keyword(query_lower) or any(
        keyword in query_lower for keyword in ["buy", "price"]
    ):
        return True

    # Trigger if query contains numbers followed by text (common pattern)
    if re.search(r'\d+\s*[a-zA-Z]{3,}', query_lower):
        return True

    # Check for potential typos (mixed case, repeated letters)
    if re.search(r'[A-Z][a-z][A-Z]', query):  # Mixed case like "cHiKKi"
        return True

    if re.search(r'(.)\1{2,}', query_lower):  # Repeated letters like "chiiiiiki"
        return True

    return False


def handle_fuzzy_correction(query: str, wa_id: str) -> Optional[Dict]:
    """
    Main handler for fuzzy typo correction flow.

    Coordinates:
    1. Checking if fuzzy correction is needed
    2. Finding matching products
    3. Generating "Did you mean?" message
    4. Creating interactive buttons

    Args:
        query: User's query (potential typos)
        wa_id: User's WhatsApp ID

    Returns:
        dict: Correction result or None if no correction needed
            {
                "needs_correction": bool,
                "message": str,
                "buttons": list,
                "matches": list,
                "confidence": float
            }
    """
    try:
        if not should_trigger_fuzzy_correction(query):
            return None

        # Get fuzzy matches
        matches = fuzzy_match_products(query)

        if not matches or matches[0]["similarity"] < FUZZY_THRESHOLD_GOOD:
            # No good matches found
            return None

        # Generate "Did you mean?" message
        message, buttons = generate_did_you_mean_message(query, matches)

        if not message:
            return None

        # Calculate confidence
        top_match = matches[0]
        confidence = top_match["similarity"] / 100.0

        logger.info(f"Fuzzy correction triggered for '{query[:30]}...': top match={top_match['product']['name']} confidence={confidence:.2f}")

        return {
            "needs_correction": True,
            "message": message,
            "buttons": buttons,
            "matches": matches,
            "confidence": confidence,
            "original_query": query
        }

    except Exception as e:
        logger.error(f"Error in fuzzy correction handler: {e}")
        return None


def get_product_variants_suggestions(base_query: str, product_id: int = None) -> List[Dict]:
    """
    Get variant suggestions when user searches for a product base.

    Example: User searches "chikki" → suggests "Original Millet Chikki", "Peanut Chikki", etc.

    Args:
        base_query: Base product name (e.g., "chikki", "spread")
        product_id: Optional specific product ID for variant matching

    Returns:
        list: Variant product suggestions with similarity scores
    """
    try:
        base_lower = base_query.lower().strip()

        # Get products that might be variants
        all_products = list_products(active_only=True, limit=100)

        variant_suggestions = []

        for product in all_products:
            product_name = (product.get("name") or "").lower()

            # Check if this product could be a variant
            if base_lower in product_name or product_name in base_lower:
                similarity = _calculate_similarity(base_lower, product_name)

                if similarity >= FUZZY_THRESHOLD_MIN:
                    variant_suggestions.append({
                        "product": product,
                        "similarity": similarity,
                        "variant_type": _determine_variant_type(product_name)
                    })

        # Sort by similarity
        variant_suggestions.sort(key=lambda x: x["similarity"], reverse=True)

        return variant_suggestions[:5]

    except Exception as e:
        logger.error(f"Error getting product variants: {e}")
        return []


def _determine_variant_type(product_name: str) -> str:
    """
    Determine the variant type based on product name patterns.

    Args:
        product_name: Lowercase product name

    Returns:
        str: Variant type (flavor, size, category, etc.)
    """
    flavor_keywords = ["peanut", "pistachio", "almond", "cashew", "coconut", "original", "classic"]
    size_keywords = ["mini", "regular", "large", "family", "pack"]

    product_lower = product_name.lower()

    for keyword in flavor_keywords:
        if keyword in product_lower:
            return "flavor"

    for keyword in size_keywords:
        if keyword in product_lower:
            return "size"

    return "style"


# ── Typo Pattern Detection ───────────────────────────────────────────────

def detect_typo_patterns(query: str) -> Dict[str, List[str]]:
    """
    Detect common typo patterns in user queries.

    Helps with:
    - Understanding user typing behavior
    - Improving fuzzy matching algorithms
    - Collecting data for better typo correction models

    Args:
        query: User's query to analyze

    Returns:
        dict: Detected typo patterns with examples
    """
    patterns = {
        "mixed_case": [],
        "repeated_letters": [],
        "transposed_letters": [],
        "missing_spaces": [],
        "extra_spaces": []
    }

    if not query:
        return patterns

    try:
        # Mixed case detection
        if re.search(r'[a-z][A-Z]|[A-Z][a-z][A-Z]', query):
            patterns["mixed_case"].append(query)

        # Repeated letters detection (3+ same letters in a row)
        repeated = re.findall(r'(.)\1{2,}', query)
        if repeated:
            patterns["repeated_letters"].extend([f"{char}{char}{char}" for char in repeated])

        # Transposed letters detection (common pattern: "teh" instead of "the")
        common_transposes = ["teh", "adn", "taht", "whihc", "thier", "recieve"]
        for transpose in common_transposes:
            if transpose in query.lower():
                patterns["transposed_letters"].append(transpose)

        # Missing spaces detection
        if re.search(r'[a-z][A-Z]', query):
            patterns["missing_spaces"].append(query)

        # Extra spaces detection
        if "  " in query:
            patterns["extra_spaces"].append(query)

        return patterns

    except Exception as e:
        logger.error(f"Error detecting typo patterns: {e}")
        return patterns


# ── Quality Metrics for Fuzzy Matching ────────────────────────────────────

def evaluate_fuzzy_match_quality(matched_products: List[Dict], original_query: str) -> Dict:
    """
    Evaluate the quality of fuzzy matching results.

    Useful for:
    - Monitoring fuzzy matching performance
    - Optimizing similarity thresholds
    - Identifying edge cases

    Args:
        matched_products: Results from fuzzy_match_products()
        original_query: Original user query

    Returns:
        dict: Quality metrics and recommendations
    """
    if not matched_products:
        return {
            "quality": "no_matches",
            "confidence": 0.0,
            "recommendation": "Consider exact search or ask for clarification"
        }

    try:
        top_match = matched_products[0]
        similarity = top_match["similarity"]

        if similarity >= FUZZY_THRESHOLD_EXCELLENT:
            quality = "excellent"
            recommendation = "High confidence - auto-select or show prominently"
        elif similarity >= FUZZY_THRESHOLD_GOOD:
            quality = "good"
            recommendation = "Show as top suggestion with alternatives"
        elif similarity >= FUZZY_THRESHOLD_MIN:
            quality = "acceptable"
            recommendation = "Show multiple options for user selection"
        else:
            quality = "poor"
            recommendation = "Fallback to search or ask user to rephrase"

        # Calculate diversity of matches
        unique_categories = len(set(m["product"].get("category", "") for m in matched_products))

        return {
            "quality": quality,
            "confidence": similarity / 100.0,
            "match_count": len(matched_products),
            "unique_categories": unique_categories,
            "top_match": top_match["product"]["name"],
            "recommendation": recommendation
        }

    except Exception as e:
        logger.error(f"Error evaluating fuzzy match quality: {e}")
        return {
            "quality": "error",
            "confidence": 0.0,
            "recommendation": "Error in evaluation"
        }