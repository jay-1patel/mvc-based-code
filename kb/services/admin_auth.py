"""Admin account authentication for the frontend admin panel.

Implements password hashing (PBKDF2-HMAC-SHA256), self-contained signed
tokens (HMAC-SHA256) and FastAPI dependencies. This is separate from the
legacy `services/auth.py` X-API-Key guard used by the old admin endpoints.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Optional

from fastapi import Header, HTTPException, Depends

from routing.config import ADMIN_JWT_SECRET, ADMIN_TOKEN_TTL_HOURS, logger

_PBKDF2_ITERATIONS = 120_000


def hash_password(password: str) -> tuple:
    """Return (hash_b64, salt_b64)."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return (
        base64.b64encode(digest).decode("ascii"),
        base64.b64encode(salt).decode("ascii"),
    )


def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    try:
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return hmac.compare_digest(digest, expected)


def generate_recovery_key() -> str:
    return secrets.token_urlsafe(18)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_token(username: str, role: str, permissions: dict,
                 ttl_hours: int = ADMIN_TOKEN_TTL_HOURS) -> str:
    payload = {
        "username": username,
        "role": role,
        "permissions": permissions,
        "exp": int(time.time()) + ttl_hours * 3600,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(ADMIN_JWT_SECRET.encode("utf-8"), body.encode("ascii"),
                           hashlib.sha256).digest())
    return f"{body}.{sig}"


def decode_token(token: str) -> Optional[dict]:
    try:
        body, sig = token.split(".", 1)
        expected = _b64url(hmac.new(ADMIN_JWT_SECRET.encode("utf-8"), body.encode("ascii"),
                                    hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64url_decode(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def require_admin_token(authorization: str = Header(None, alias="Authorization")):
    """Validate a `Bearer <token>` header and return the token payload."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:].strip()
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return payload


def require_super_admin(authorization: str = Header(None, alias="Authorization")):
    """Require a valid token whose role is super_admin."""
    payload = require_admin_token(authorization)
    if payload.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin privileges required")
    return payload


def require_perm(permission: str):
    """Return a dependency that requires a specific permission (super_admin always passes)."""
    def _checker(payload: dict = Depends(require_admin_token)):
        if payload.get("role") == "super_admin":
            return payload
        if (payload.get("permissions") or {}).get(permission):
            return payload
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
    return _checker


def admin_public(admin: dict) -> dict:
    """Strip secrets before returning an admin row to the frontend."""
    return {
        "username": admin.get("username"),
        "role": admin.get("role", "sub_admin"),
        "permissions": admin.get("permissions") or {},
        "created_at": admin.get("created_at"),
    }
