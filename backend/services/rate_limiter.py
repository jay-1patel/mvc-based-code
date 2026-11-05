"""In-memory rate limiter for webhook endpoints.

Protects the bot from message floods/spam. Limits are per-client (by IP) and
per-whatsapp-user (by wa_id). In-memory only — adequate for single-worker
deployments. For multi-worker, back this with Redis.
"""
import time
import logging
from collections import defaultdict, deque
from fastapi import Request, HTTPException

logger = logging.getLogger("rate_limiter")

_LIMIT = 30          # max requests
_WINDOW = 60         # per N seconds
_IP_LIMIT = 30
_IP_WINDOW = 60

_per_wa: dict[str, deque] = defaultdict(deque)
_per_ip: dict[str, deque] = defaultdict(deque)


def _check(bucket: dict, key: str, limit: int, window: int) -> bool:
    now = time.monotonic()
    q = bucket[key]
    while q and q[0] <= now - window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


async def rate_limit_wa(wa_id: str) -> None:
    """Dependency: limit per WhatsApp user. wa_id may be derived from the request body."""
    if not _check(_per_wa, wa_id or "unknown", _LIMIT, _WINDOW):
        logger.warning(f"RATE_LIMITED wa_id={wa_id}")
        raise HTTPException(status_code=429, detail="Too many messages. Please slow down.")


async def rate_limit_ip(request: Request) -> None:
    """Dependency: limit per source IP."""
    ip = request.client.host if request.client else "unknown"
    if not _check(_per_ip, ip, _IP_LIMIT, _IP_WINDOW):
        logger.warning(f"RATE_LIMITED ip={ip}")
        raise HTTPException(status_code=429, detail="Too many requests.")
