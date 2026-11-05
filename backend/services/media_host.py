"""Public media hosting for WhatsApp (Meta must be able to fetch the URL).

Order for images:
  1. ImgHippo — if ``IMGHIPPO_API_KEY`` has credits
  2. img402.dev — free, no API key (≤1 MB permanent on free tier)
  3. BasePic — free, no API key
  4. catbox.moe — last resort (often blocked by Meta)

PDFs / other docs:
  Prefer ``PUBLIC_BASE_URL`` (Cloudflare tunnel) + local ``/uploaded_files/...``.
  Catbox uploads often succeed but Meta cannot fetch them, so tunnel-first.
"""

from __future__ import annotations

import logging
import mimetypes
import os
from typing import Optional

import httpx

logger = logging.getLogger("media_host")

IMGHIPPO_API_URL = "https://api.imghippo.com/v1/upload"
IMG402_API_URL = "https://img402.dev/api/free"
BASEPIC_API_URL = "https://basepic.com/api/v1/images"
CATBOX_API_URL = "https://catbox.moe/user/api.php"

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _is_image(filename: str, content_type: str = "") -> bool:
    ct = (content_type or "").lower()
    if ct.startswith("image/"):
        return True
    return os.path.splitext(filename or "")[1].lower() in _IMAGE_EXTS


def _guess_content_type(filename: str, content_type: str = "") -> str:
    if content_type:
        return content_type
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed or "application/octet-stream"


def _upload_imghippo(content: bytes, filename: str) -> Optional[str]:
    try:
        from routing.config import IMGHIPPO_API_KEY
    except Exception:
        IMGHIPPO_API_KEY = os.getenv("IMGHIPPO_API_KEY", "")
    if not IMGHIPPO_API_KEY:
        return None
    try:
        resp = httpx.post(
            IMGHIPPO_API_URL,
            headers={"X-API-Key": IMGHIPPO_API_KEY},
            files={"file": (filename, content, "application/octet-stream")},
            timeout=60,
        )
        if resp.is_success:
            data = resp.json()
            if data.get("success") or data.get("status") == 200:
                url = (data.get("data") or {}).get("url")
                if url:
                    logger.info("media_host: imghippo -> %s", url)
                    return url
        logger.warning(
            "media_host: imghippo rejected: %s %s",
            resp.status_code,
            (resp.text or "")[:160],
        )
    except Exception as exc:
        logger.warning("media_host: imghippo failed: %s", exc)
    return None


def _upload_img402(content: bytes, filename: str, content_type: str) -> Optional[str]:
    try:
        resp = httpx.post(
            IMG402_API_URL,
            files={"file": (filename, content, content_type)},
            timeout=60,
        )
        if resp.is_success:
            data = resp.json()
            url = data.get("url")
            if url and str(url).startswith("http"):
                logger.info("media_host: img402 -> %s", url)
                return url
        logger.warning(
            "media_host: img402 rejected: %s %s",
            resp.status_code,
            (resp.text or "")[:160],
        )
    except Exception as exc:
        logger.warning("media_host: img402 failed: %s", exc)
    return None


def _upload_basepic(content: bytes, filename: str, content_type: str) -> Optional[str]:
    try:
        resp = httpx.post(
            BASEPIC_API_URL,
            files={"image": (filename, content, content_type)},
            timeout=60,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            url = data.get("image_url") or data.get("url") or data.get("download_url")
            if url and str(url).startswith("http"):
                logger.info("media_host: basepic -> %s", url)
                return url
        logger.warning(
            "media_host: basepic rejected: %s %s",
            resp.status_code,
            (resp.text or "")[:160],
        )
    except Exception as exc:
        logger.warning("media_host: basepic failed: %s", exc)
    return None


def _upload_catbox(content: bytes, filename: str) -> Optional[str]:
    try:
        resp = httpx.post(
            CATBOX_API_URL,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (filename, content, "application/octet-stream")},
            timeout=60,
        )
        if resp.is_success and not (resp.text or "").startswith("error"):
            url = (resp.text or "").strip()
            if url.startswith("http"):
                logger.info("media_host: catbox -> %s", url)
                return url
        logger.warning(
            "media_host: catbox rejected: %s %s",
            resp.status_code,
            (resp.text or "")[:160],
        )
    except Exception as exc:
        logger.warning("media_host: catbox failed: %s", exc)
    return None


def _public_base_url() -> str:
    try:
        from routing.config import PUBLIC_BASE_URL
        return (PUBLIC_BASE_URL or "").strip().rstrip("/")
    except Exception:
        return (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")


def _upload_dir() -> str:
    """Absolute path to ``backend/uploaded_files``."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "uploaded_files"))


def host_via_public_base(
    content: bytes,
    filename: str,
    *,
    subdir: str = "media",
) -> Optional[str]:
    """Save locally and return ``PUBLIC_BASE_URL/uploaded_files/...`` when tunnel is set."""
    base = _public_base_url()
    if not base or not content:
        return None
    safe = os.path.basename(filename or "file.bin") or "file.bin"
    dest_dir = os.path.join(_upload_dir(), subdir)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, safe)
    with open(dest, "wb") as handle:
        handle.write(content)
    url = f"{base}/uploaded_files/{subdir}/{safe}"
    logger.info("media_host: public_base -> %s", url)
    return url


def public_url_for_local_path(local_path: str) -> Optional[str]:
    """Map a file under ``uploaded_files`` to its Cloudflare public URL."""
    base = _public_base_url()
    if not base or not local_path:
        return None
    abs_path = os.path.abspath(local_path)
    root = _upload_dir()
    try:
        rel = os.path.relpath(abs_path, root)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    rel_url = "/uploaded_files/" + rel.replace("\\", "/")
    return f"{base}{rel_url}"


def upload_public_media(
    content: bytes,
    filename: str,
    content_type: str = "",
    *,
    subdir: str = "media",
) -> Optional[str]:
    """Upload bytes and return a public HTTPS URL WhatsApp can fetch, or None."""
    if not content:
        return None
    name = filename or "upload.bin"
    ctype = _guess_content_type(name, content_type)
    if _is_image(name, ctype):
        for uploader in (
            lambda: _upload_imghippo(content, name),
            lambda: _upload_img402(content, name, ctype),
            lambda: _upload_basepic(content, name, ctype),
            lambda: host_via_public_base(content, name, subdir=subdir or "product_images"),
            lambda: _upload_catbox(content, name),
        ):
            url = uploader()
            if url:
                return url
        return None
    # PDFs / docs: Cloudflare tunnel first (Meta-fetchable), catbox last.
    return (
        host_via_public_base(content, name, subdir=subdir or "brochures")
        or _upload_catbox(content, name)
    )


def upload_public_image(content: bytes, filename: str, content_type: str = "") -> Optional[str]:
    """Image-only helper (same pipeline as ``upload_public_media`` for images)."""
    name = filename or "image.png"
    if not _is_image(name, content_type):
        name = f"{os.path.splitext(name)[0] or 'image'}.png"
    return upload_public_media(content, name, content_type or "image/png", subdir="product_images")
