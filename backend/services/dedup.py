"""
Two-level webhook idempotency.

L1: in-process TTL cache (fast, best-effort, worker-local)
L2: webhook_dedup table, INSERT OR IGNORE (atomic, authoritative, cross-worker)

Also: short-window content fingerprint so Meta webhook + send2 poller cannot
double-answer the same "hi" when each path carries a different message id.
"""
import time
import logging
import hashlib
from database import get_db_context

logger = logging.getLogger("dedup")
_DEDUP_TTL = 300  # 5 minutes
_CONTENT_TTL = 25  # seconds — covers poller lag vs push webhook
_l1_cache: dict = {}
_content_cache: dict = {}


def clear_ephemeral_caches() -> None:
    """Drop in-memory dedup after Active Test Tenant switches.

    Lets the next ``hi`` / menu tap through instead of being treated as a
    cross-channel duplicate from the previous tenant session.
    """
    _l1_cache.clear()
    _content_cache.clear()


def _l1_check_and_set(message_id: str) -> bool:
    """True if already seen in THIS worker."""
    now = time.time()
    expired = [k for k, exp in _l1_cache.items() if exp < now]
    for k in expired:
        del _l1_cache[k]
    if message_id in _l1_cache:
        return True
    _l1_cache[message_id] = now + _DEDUP_TTL
    return False


def _l2_check_and_set(message_id: str, wa_id: str) -> bool:
    """True if ANY worker already claimed this message (authoritative)."""
    try:
        with get_db_context() as conn:
            # Check if send2 poller already claimed it
            row = conn.execute(
                "SELECT 1 FROM processed_inbound_messages WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
            if row is not None:
                return True
            cur = conn.execute(
                "INSERT OR IGNORE INTO webhook_dedup (message_id, wa_id, processed_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (message_id, wa_id),
            )
            # Also keep processed_inbound_messages in sync so poller won't re-answer
            conn.execute(
                "INSERT OR IGNORE INTO processed_inbound_messages (message_id, wa_id, channel) "
                "VALUES (?, ?, 'webhook')",
                (message_id, wa_id),
            )
            return cur.rowcount == 0  # ignored insert = already existed
    except Exception as e:
        # Fail closed: treat as already seen so we do not re-answer old poll rows
        # when SQLite is locked / unavailable. L1 already stamped this worker.
        logger.error(
            "DEDUP_FAIL_CLOSED | L2 DB error for %s — treating as duplicate: %s",
            message_id, e,
        )
        return True


def is_duplicate(message_id: str, wa_id: str) -> bool:
    if not message_id:
        return False
    if _l1_check_and_set(message_id):
        return True
    if _l2_check_and_set(message_id, wa_id):
        logger.info(f"Dedup L2 hit (cross-worker): {message_id} from {wa_id}")
        return True
    return False


def try_claim_message(message_id: str, wa_id: str, channel: str = "send2") -> bool:
    """Atomically claim an inbound message id. True = this caller owns it.

    Used by the send2 poller so it cannot race the Meta webhook (which claims
    via ``is_duplicate`` before processing). Check-then-mark after send left a
    window where both paths answered the same "hi".

    On DB failure: fail closed (return False) and keep the id in the L1 cache so
    this worker does not re-answer until the durable ledger is writable again.
    """
    if not message_id:
        return True  # no id → fall through to content fingerprint
    if message_id in _l1_cache and _l1_cache[message_id] >= time.time():
        return False
    # Stamp L1 only after a successful durable claim — unless DB fails, in which
    # case we stamp L1 and refuse the claim (fail closed).
    try:
        with get_db_context() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_inbound_messages WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
            if row is not None:
                _l1_check_and_set(message_id)
                return False
            row2 = conn.execute(
                "SELECT 1 FROM webhook_dedup WHERE message_id = ? LIMIT 1",
                (message_id,),
            ).fetchone()
            if row2 is not None:
                _l1_check_and_set(message_id)
                return False
            cur = conn.execute(
                "INSERT OR IGNORE INTO processed_inbound_messages (message_id, wa_id, channel) "
                "VALUES (?, ?, ?)",
                (message_id, wa_id, channel),
            )
            conn.execute(
                "INSERT OR IGNORE INTO webhook_dedup (message_id, wa_id, processed_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (message_id, wa_id),
            )
            if cur.rowcount > 0:
                _l1_check_and_set(message_id)
                return True
            _l1_check_and_set(message_id)
            return False
    except Exception as e:
        logger.error(
            "DEDUP_FAIL_CLOSED | try_claim_message failed for %s — skipping until DB recovers: %s",
            message_id, e,
        )
        _l1_check_and_set(message_id)
        return False


def release_inbound_claim(message_id: str) -> None:
    """Undo a poller/webhook claim so a failed send can be retried.

    Claims are written before the reply goes out (to stop double-answers). If
    processing or delivery then fails, leaving the claim in place permanently
    drops the customer message. Clear both durable tables and the L1 cache.
    """
    if not message_id:
        return
    _l1_cache.pop(message_id, None)
    try:
        with get_db_context() as conn:
            conn.execute(
                "DELETE FROM processed_inbound_messages WHERE message_id = ?",
                (message_id,),
            )
            conn.execute(
                "DELETE FROM webhook_dedup WHERE message_id = ?",
                (message_id,),
            )
    except Exception as e:
        logger.error(f"release_inbound_claim failed for {message_id}: {e}")


def _content_key(wa_id: str, text: str) -> str:
    norm = " ".join((text or "").strip().lower().split())
    digest = hashlib.sha1(f"{wa_id}|{norm}".encode("utf-8")).hexdigest()[:24]
    return f"c:{digest}"


def is_duplicate_content(wa_id: str, text: str, window_seconds: int = _CONTENT_TTL) -> bool:
    """True if the same wa_id + text was claimed within the short window.

    Catches Meta wamid vs send2 messageid mismatch (and multi-worker races) on
    the same customer "hi". Uses a short-TTL durable row so a later legitimate
    "hi" after the window still gets through.
    """
    if not wa_id or not (text or "").strip():
        return False
    key = _content_key(wa_id, text)
    now = time.time()
    ttl = max(5, int(window_seconds))
    expired = [k for k, exp in _content_cache.items() if exp < now]
    for k in expired:
        del _content_cache[k]
    if key in _content_cache:
        # Refresh so a long poll window does not re-open mid-session.
        _content_cache[key] = now + ttl
        logger.debug("Dedup content hit: wa_id=%s text=%r", wa_id, text[:40])
        return True

    try:
        with get_db_context() as conn:
            row = conn.execute(
                "SELECT processed_at FROM webhook_dedup WHERE message_id = ? LIMIT 1",
                (key,),
            ).fetchone()
            if row is not None:
                raw = row[0] if not hasattr(row, "keys") else row["processed_at"]
                age = None
                if raw is not None:
                    try:
                        # SQLite CURRENT_TIMESTAMP is UTC "YYYY-MM-DD HH:MM:SS"
                        from datetime import datetime
                        stamped = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
                        age = (datetime.utcnow() - stamped).total_seconds()
                    except Exception:
                        age = 0
                if age is not None and age < ttl:
                    _content_cache[key] = now + (ttl - max(0, age))
                    logger.debug(
                        "Dedup content L2 hit: wa_id=%s text=%r", wa_id, text[:40]
                    )
                    return True
                conn.execute("DELETE FROM webhook_dedup WHERE message_id = ?", (key,))
            cur = conn.execute(
                "INSERT OR IGNORE INTO webhook_dedup (message_id, wa_id, processed_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, wa_id),
            )
            if cur.rowcount == 0:
                _content_cache[key] = now + ttl
                logger.debug(
                    "Dedup content L2 race: wa_id=%s text=%r", wa_id, text[:40]
                )
                return True
    except Exception as e:
        logger.debug(f"content dedup DB skip: {e}")

    _content_cache[key] = now + ttl
    return False
