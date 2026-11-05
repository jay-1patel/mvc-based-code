from fastapi import Header, HTTPException
import os
from routing.config import  logger
ADMIN_API_KEY=os.getenv("ADMIN_API_KEY")

def require_admin(x_api_key: str = Header(None, alias="X-API-Key")):
    """Protect admin/management endpoints.

    Enforced only when ADMIN_API_KEY is configured. If unset, access is
    allowed but a warning is logged (development convenience only).
    """
    if not ADMIN_API_KEY:
        logger.warning("ADMIN_API_KEY not set — admin endpoints are UNPROTECTED")
        return
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
