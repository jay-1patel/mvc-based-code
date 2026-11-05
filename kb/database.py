"""
Thin re-export: all database helpers live in backend.database.
This module re-exports them so that KB code using relative imports
(e.g. ``from ..database import X``) continues to work unchanged.
"""
import os
import importlib.util

_backend_db = os.path.join(os.path.dirname(__file__), "..", "backend", "database.py")
_spec = importlib.util.spec_from_file_location("backend_database", _backend_db)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Re-export every public name from backend.database
for _name in dir(_mod):
    if not _name.startswith("_"):
        globals()[_name] = getattr(_mod, _name)
