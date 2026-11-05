"""Compatibility shim — implementation parked in the legacy area.

The application does not register this router. The shim keeps the historical
import/file location usable while the implementation lives with the parked
legacy code.
"""
import importlib.util
import sys
from pathlib import Path

_impl_path = (
    Path(__file__).resolve().parents[2]
    / "legacy" / "backend" / "routes" / "tenant_admin.py"
)
_spec = importlib.util.spec_from_file_location(
    "backend.routes._legacy_tenant_admin_impl", _impl_path
)
_impl = importlib.util.module_from_spec(_spec)
_impl.__package__ = "backend.routes"


def __getattr__(name):
    if "_impl_loaded" not in globals():
        sys.modules[_spec.name] = _impl
        _spec.loader.exec_module(_impl)
        globals()["_impl_loaded"] = True
        globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
    return globals()[name]


def __dir__():
    return sorted(set(globals()) | set(vars(_impl)))
