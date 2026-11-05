"""
Shared validators for B2B + B2C checkout details.
"""
import re
import html
import os
import logging
from database import get_db_context

logger = logging.getLogger("validators")


def sanitize_name(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r'[<>&*_`\[\]{}\\]', "", text)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\x00-\x1F\x7F]", "", text)
    return text.strip()


def sanitize_address(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"[<>&`\\]", "", text)
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    return text.strip()


def validate_name(raw: str) -> dict:
    if not raw or not raw.strip():
        return {"valid": False, "error": "Please enter a name."}
    cleaned = sanitize_name(raw)
    if len(cleaned) < 2:
        return {"valid": False, "error": "Name must be at least 2 characters."}
    if len(cleaned) > 60:
        return {"valid": False, "error": "Name too long (max 60 characters)."}
    if not re.search(r"[a-zA-Z]", cleaned):
        return {"valid": False, "error": "Please enter a valid name."}
    return {"valid": True, "normalized": cleaned}


def validate_mobile(raw: str) -> dict:
    """Indian mobile -> E.164 (+91XXXXXXXXXX)."""
    if not raw:
        return {"valid": False, "error": "Please enter a mobile number."}
    mobile = re.sub(r"[\s\-\(\)]", "", raw.strip())
    if mobile.startswith("+91"):
        mobile = mobile[3:]
    elif mobile.startswith("91") and len(mobile) == 12:
        mobile = mobile[2:]
    elif mobile.startswith("0") and len(mobile) == 11:
        mobile = mobile[1:]
    if re.match(r"^[6-9][0-9]{9}$", mobile):
        return {"valid": True, "normalized": f"+91{mobile}"}
    return {
        "valid": False,
        "error": "Invalid mobile number. Enter 10 digits starting with 6-9.",
    }


def validate_pincode(raw: str) -> dict:
    if not raw:
        return {"valid": False, "error": "Please enter a pincode."}
    pincode = raw.strip().replace(" ", "")
    if re.match(r"^[1-9][0-9]{5}$", pincode):
        return {"valid": True, "normalized": pincode}
    return {"valid": False, "error": "Invalid pincode. Enter 6 digits (e.g., 380001)."}


def check_serviceability(pincode: str) -> dict:
    """DB first -> env-var prefix fallback."""
    try:
        with get_db_context() as conn:
            row = conn.execute(
                "SELECT is_active FROM serviceable_pincodes WHERE pincode = ?",
                (pincode,),
            ).fetchone()
            if row is not None:
                if row[0]:
                    return {"serviceable": True}
                return {
                    "serviceable": False,
                    "message": (
                        f"Sorry, we don't deliver to *{pincode}* yet.\n\n"
                        "Enter a different pincode or type *cancel*."
                    ),
                }
    except Exception as e:
        logger.warning(f"Serviceability DB check failed: {e}")
    default = (
        "360,361,362,363,364,365,370,380,382,383,384,385,"
        "387,388,389,390,391,392,393,394,395,396"
    )
    prefixes = os.getenv("SERVICEABLE_PINCODE_PREFIXES", default).split(",")
    if any(pincode.startswith(p.strip()) for p in prefixes):
        return {"serviceable": True}
    return {
        "serviceable": False,
        "message": (
            f"Sorry, we don't deliver to *{pincode}* yet.\n\n"
            "Enter a different pincode or type *cancel*."
        ),
    }


def validate_address(raw: str) -> dict:
    if not raw or not raw.strip():
        return {"valid": False, "error": "Please enter your address."}
    cleaned = sanitize_address(raw)
    if len(cleaned) < 10:
        return {
            "valid": False,
            "error": "Address too short. Include house number, street, city.",
        }
    if len(cleaned) > 250:
        return {"valid": False, "error": "Address too long (max 250 characters)."}
    return {"valid": True, "normalized": cleaned}


def validate_qty(raw: str, moq: int = 1, max_qty: int = 99, stock: int | None = None) -> dict:
    """B2C: max_qty=99. B2B: pass max_qty=None or a large cap."""
    try:
        qty = int(raw.strip())
    except (ValueError, AttributeError):
        return {"valid": False, "error": "Please enter a valid number."}
    if qty < moq:
        return {"valid": False, "error": f"Minimum order quantity is {moq}."}
    if max_qty is not None and qty > max_qty:
        return {"valid": False, "error": f"Maximum quantity is {max_qty}."}
    if stock is not None and qty > stock:
        return {"valid": False, "error": f"Only {stock} units available."}
    return {"valid": True, "normalized": qty}
