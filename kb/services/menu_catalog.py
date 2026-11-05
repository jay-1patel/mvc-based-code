"""
Thin adapter for the unified menu catalog.

In standalone KB mode the ``services`` package resolves to ``kb/services/``,
so this module loads the canonical implementation from
``backend/services/menu_catalog.py`` and re-exports it.

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
    from services.menu_catalog import (
        get_kb_main_menu,
        get_main_menu_buttons,
    )
finally:
    sys.path.remove(_backend_dir)

__all__ = [
    "get_kb_main_menu",
    "get_main_menu_buttons",
]