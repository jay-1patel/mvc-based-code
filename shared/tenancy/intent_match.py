"""
Deterministic intent matching over a tenant profile's active intents.

Serves two consumers:
  * eval/run.py — the headless eval harness must never depend on a live LLM.
  * the routing layer — the rules-first tier of the profile-driven routing
    order (rules -> embeddings -> classifier -> LLM fallback).

Tiers, in order:
  1. exact example match (case-insensitive)
  2. keyword containment, longest keyword wins (word-boundary aware)
  3. embedding similarity against each active intent's examples (bge-m3)

The matcher only ever returns intents the tenant has switched on
(profile.active_intents) — a commerce phrase for a non-commerce tenant can
never come back as ``place_order``.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional, Tuple

logger = logging.getLogger("tenancy.intent_match")

# Below this cosine similarity the embedding tier says "no match".
EMBEDDING_MIN_SCORE = 0.45

_model = None


def _word_in_text(needle: str, text: str) -> bool:
    """Keyword containment on word boundaries — 'hi' must not match 'this'."""
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text) is not None


def _embedding_model():
    global _model
    if _model is None:
        # kb.services.embeddings owns the bge-m3 singleton (CPU-forced).
        import sys
        from pathlib import Path

        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)
        from kb.services.embeddings import get_embedding_model
        _model = get_embedding_model()
    return _model


def match_by_rules(text: str, intents) -> Optional[Tuple[str, float]]:
    """Tier 1 + 2. Returns (intent_name, score) or None. Never raises on LLM
    availability because it never touches one."""
    t = (text or "").strip().lower()
    if not t:
        return None

    for intent in intents:
        for ex in intent.examples:
            if (ex or "").strip().lower() == t:
                return intent.name, 1.0

    best = (None, 0.0)
    for intent in intents:
        for kw in intent.keywords:
            kw = (kw or "").strip().lower()
            if not kw:
                continue
            if _word_in_text(kw, t):
                score = min(1.0, 0.4 + len(kw) / 20.0)
                if score > best[1]:
                    best = (intent.name, score)
    return best if best[0] else None


def match_by_embeddings(text: str, intents) -> Optional[Tuple[str, float]]:
    """Tier 3. Cosine similarity of the utterance against each active intent's
    examples; best wins if it clears EMBEDDING_MIN_SCORE."""
    try:
        model = _embedding_model()
    except Exception as e:
        logger.debug("embedding tier unavailable: %s", e)
        return None

    phrases: List[str] = []
    owners: List[str] = []
    for intent in intents:
        for ex in intent.examples:
            if (ex or "").strip():
                phrases.append(ex.strip())
                owners.append(intent.name)
    if not phrases:
        return None

    import numpy as np

    texts = [text.strip()] + phrases
    emb = model.encode(texts, normalize_embeddings=True).astype("float32")
    sims = emb[1:] @ emb[0]
    best_idx = int(np.argmax(sims))
    best = float(sims[best_idx])
    if best < EMBEDDING_MIN_SCORE:
        return None
    return owners[best_idx], best


def match_intent(text: str, profile, *, use_embeddings: bool = True):
    """Best active intent for an utterance, or None.

    Returns (intent_name, score, tier) where tier is 'rule' or 'embedding'.
    Only intents from profile.active_intents() can win — gating happens before
    matching, not after, so a disabled feature's intent is unreachable.
    """
    intents = profile.active_intents()
    hit = match_by_rules(text, intents)
    if hit:
        return hit[0], hit[1], "rule"
    if use_embeddings:
        hit = match_by_embeddings(text, intents)
        if hit:
            return hit[0], hit[1], "embedding"
    return None, 0.0, "none"
