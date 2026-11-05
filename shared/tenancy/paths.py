"""Canonical filesystem locations for the tenancy substrate.

One definition, imported everywhere. ``kb/services/multi_tenant.py`` currently
derives its own ``clients/`` path from ``routing.config.BASE_DIR``; Phase 7
absorbs that module and points it here.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# shared/tenancy/paths.py -> shared/tenancy -> shared -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
ROUTING_DIR = REPO_ROOT / "routing"
KB_DIR = REPO_ROOT / "kb"
EVAL_DIR = REPO_ROOT / "eval"


def _dir_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    if raw and str(raw).strip():
        p = Path(str(raw).strip())
        return p if p.is_absolute() else (REPO_ROOT / p).resolve()
    return default


# Per-tenant config packs: clients/<tenant_id>/config.json + clients/<id>/knowledge/
CLIENTS_DIR = _dir_from_env("CLIENTS_DIR", REPO_ROOT / "clients")

# Tenant knowledge data that used to live inside the backend folder.
# One definition for every consumer; override with BACKEND_CLIENTS_DIR.
BACKEND_CLIENTS_DIR = _dir_from_env(
    "BACKEND_CLIENTS_DIR", REPO_ROOT / "data" / "tenants"
)


def validate_backend_clients_dir() -> Path:
    """Fail loudly at startup if the moved tenant-data root is wrong."""
    root = BACKEND_CLIENTS_DIR
    try:
        valid = root.is_dir() and any(child.is_dir() for child in root.iterdir())
    except Exception:
        valid = False
    if not valid:
        logger.error(
            "TENANT_DATA_PATH_INVALID | path=%s | expected a directory containing tenant folders",
            root,
        )
        raise RuntimeError(f"TENANT_DATA_PATH_INVALID: {root}")
    return root


def client_dir(tenant_id: str) -> Path:
    tid = str(tenant_id or "").strip()

    # Prefer the registered tenant slug's pack over short aliases like "l"
    # (clients/l was a second Leeway pack and mixed headers with leewaysoftech).
    try:
        from . import store
        rec = store.get_tenant(tid)
        if rec:
            slug = str(rec.get("slug") or "").strip()
            for candidate in (slug, slug.replace("-", ""), slug.replace("_", "")):
                if candidate and (CLIENTS_DIR / candidate).exists() and (
                    CLIENTS_DIR / candidate / "config.json"
                ).exists():
                    return CLIENTS_DIR / candidate
    except Exception:
        # deliberate: store lookup is one of several ways to find the
        # client dir — the remaining candidates are still tried.
        logger.debug("CLIENT_DIR_LOOKUP_SKIPPED | tid=%s", tid, exc_info=True)

    # No default/leeway tenant mapping: every tenant must resolve to its own
    # client pack (or none). Never remap onto the leewaysoftech pack.

    primary = CLIENTS_DIR / tid
    if primary.exists() and (primary / "config.json").exists():
        return primary

    return primary


def client_config_path(tenant_id: str) -> Path:
    return client_dir(tenant_id) / "config.json"


def client_knowledge_dir(tenant_id: str) -> Path:
    return client_dir(tenant_id) / "knowledge"


def ensure_client_dir(tenant_id: str) -> Path:
    d = client_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "knowledge").mkdir(exist_ok=True)
    return d
