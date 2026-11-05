"""
Embedding model singleton to save RAM by loading BGE-M3 model only once.

Reuses the FAQ service's embedding model instance so both FAQ and KB share
the same SentenceTransformer in memory.
"""

import os
from functools import lru_cache
from typing import Optional

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TORCH_DEVICE"] = "cpu"

from routing.config import logger


# Shared model reference
_embedding_model = None
_embedding_dim = None


def _try_import_shared_model():
    global _embedding_model, _embedding_dim
    try:
        import sys
        import importlib
        # Try the same import path main.py uses first (avoids double-loading)
        mod = None
        if "faq.service" in sys.modules:
            mod = sys.modules["faq.service"]
        elif "backend.faq.service" in sys.modules:
            mod = sys.modules["backend.faq.service"]
        else:
            # Force import via the path main.py uses
            mod = importlib.import_module("faq.service")
        shared_model = mod.embedding_model
        _embedding_model = shared_model
        if hasattr(_embedding_model, "get_embedding_dimension"):
            _embedding_dim = _embedding_model.get_embedding_dimension()
        elif hasattr(_embedding_model, "get_sentence_embedding_dimension"):
            _embedding_dim = _embedding_model.get_sentence_embedding_dimension()
        else:
            import numpy as np
            dummy_emb = _embedding_model.encode(["test"], show_progress_bar=False)
            _embedding_dim = dummy_emb.shape[1]
        logger.info(f"KB reusing shared embedding model from FAQ service. Dimension: {_embedding_dim}")
        return True
    except Exception as exc:
        logger.debug(f"KB could not reuse shared embedding model: {exc}")
        return False


_try_import_shared_model()


@lru_cache(maxsize=1)
def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        from routing.config import EMBEDDING_MODEL_PATH
        logger.info(f"KB loading its own embedding model from {EMBEDDING_MODEL_PATH}...")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_PATH)
    return _embedding_model


def get_embedding_dim() -> int:
    global _embedding_dim
    if _embedding_dim is None:
        model = get_embedding_model()
        if hasattr(model, "get_embedding_dimension"):
            _embedding_dim = model.get_embedding_dimension()
        elif hasattr(model, "get_sentence_embedding_dimension"):
            _embedding_dim = model.get_sentence_embedding_dimension()
        else:
            import numpy as np
            dummy_emb = model.encode(["test"], show_progress_bar=False)
            _embedding_dim = dummy_emb.shape[1]
    if _embedding_dim is None:
        raise ValueError("Could not determine embedding dimension")
    return _embedding_dim


def clear_embedding_cache():
    global _embedding_model, _embedding_dim
    get_embedding_model.cache_clear()
    _embedding_model = None
    _embedding_dim = None
    logger.info("Embedding model cache cleared")


def get_embedding_model_info() -> dict:
    global _embedding_model, _embedding_dim
    return {
        "model_path": "shared_with_faq",
        "dimension": _embedding_dim or get_embedding_dim(),
        "is_loaded": _embedding_model is not None,
    }


def get_embedding():
    return get_embedding_model()
