"""Compatibility shim — implementation parked in the legacy area.

Live modules still import this path. The implementation file was moved
without changing its code; this shim loads that file with the backend
package context it expects and re-exports its names.
"""
import importlib.util
import sys
from pathlib import Path

_impl_path = Path(__file__).resolve().parent.parent / "legacy" / "backend" / "database_multi_tenant.py"
_spec = importlib.util.spec_from_file_location(
    "backend._legacy_database_multi_tenant_impl", _impl_path
)
_impl = importlib.util.module_from_spec(_spec)
_impl.__package__ = "backend"
sys.modules[_spec.name] = _impl
_spec.loader.exec_module(_impl)

globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
__all__ = [k for k in globals() if not k.startswith("_")]
