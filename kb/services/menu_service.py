"""
Thin adapter for the unified menu rendering service.

In standalone KB mode the ``services`` package resolves to ``kb/services/``,
so this module loads the canonical implementation from
``backend/services/menu_service.py`` and re-exports it.

When running the unified backend, ``backend/services/`` is found first and
this adapter is not used.
"""

import os
import sys

_backend_dir = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "backend")
)

# Temporarily put backend/ first so the canonical modules are found.
sys.path.insert(0, _backend_dir)
try:
    from services.menu_service import (
        MenuItem,
        build_list_menu,
        build_button_menu,
        personalize_header,
        get_greeting_menu,
    )
finally:
    sys.path.remove(_backend_dir)

__all__ = [
    "MenuItem",
    "build_list_menu",
    "build_button_menu",
    "personalize_header",
    "get_greeting_menu",
]
