"""In-memory profile cache.

Keyed on ``(tenant_id, version)``. A publish bumps ``version`` in the DB and
purges the tenant's keys, so the version key is belt-and-braces: even if a purge
is missed (other worker, crashed process), a different version means a cache
miss rather than a stale profile.

No Redis (decision D4). TTL 60s is a safety net, not the primary mechanism.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

from .schemas import TenantProfile

TTL_SECONDS = 60.0

_lock = threading.RLock()
_cache: Dict[Tuple[str, int], Tuple[float, TenantProfile]] = {}
_hits = 0
_misses = 0


def get(tenant_id: str, version: int) -> Optional[TenantProfile]:
    global _hits, _misses
    key = (str(tenant_id), int(version))
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            _misses += 1
            return None
        expires_at, profile = entry
        if expires_at < now:
            _cache.pop(key, None)
            _misses += 1
            return None
        _hits += 1
        return profile


def put(profile: TenantProfile) -> None:
    key = (str(profile.tenant_id), int(profile.version))
    with _lock:
        _cache[key] = (time.monotonic() + TTL_SECONDS, profile)


def purge(tenant_id: str) -> int:
    """Drop every cached version for a tenant. Called on publish/rollback."""
    tid = str(tenant_id)
    with _lock:
        keys = [k for k in _cache if k[0] == tid]
        for k in keys:
            _cache.pop(k, None)
        return len(keys)


def purge_all() -> int:
    with _lock:
        n = len(_cache)
        _cache.clear()
        return n


def stats() -> dict:
    with _lock:
        total = _hits + _misses
        return {
            "entries": len(_cache),
            "hits": _hits,
            "misses": _misses,
            "hit_rate": round(_hits / total, 3) if total else 0.0,
            "ttl_seconds": TTL_SECONDS,
        }
