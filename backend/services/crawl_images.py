"""Crawl image collector — download, filter, dedupe, store under UPLOAD_DIR.

Stores under ``<UPLOAD_DIR>/crawl_images/<tenant_id>/<run_id>/`` and writes a
row per candidate into ``tenant_crawl_images``. SVG excluded; max 10MB/file;
no per-crawl count cap; skip <2KB or dimensions <24px; dedupe by source URL
and SHA-256 (duplicate rows marked, bytes stored once).
"""
from __future__ import annotations

import hashlib
import io
import logging
import mimetypes
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup

from services.site_crawler import BlockedUrlError, assert_public_url
from services.url_scraper import USER_AGENT, clean_text, extract_best_image_url, slugify

logger = logging.getLogger("crawl_images")

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def _get_image_guarded(client: httpx.Client, url: str) -> httpx.Response:
    """GET an image with SSRF validation of the URL and each redirect hop.

    Image URLs come from crawled-page content, so a hostile site must not be
    able to make the server probe internal addresses (M2).
    """
    current = url
    resp: Optional[httpx.Response] = None
    for _hop in range(6):
        assert_public_url(current)
        resp = client.get(
            current, headers={"User-Agent": USER_AGENT}, follow_redirects=False
        )
        if resp.status_code not in _REDIRECT_STATUSES:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        current = urllib.parse.urljoin(current, location)
    assert resp is not None
    return resp

# Per-crawl image-count cap REMOVED by product decision (2026-11-12,
# same call that removed the page-count cap): a large catalogue crawl
# found 524 image candidates and silently stored only the first 100.
# None = unlimited; per-file size/dimension filters and dedupe remain.
MAX_IMAGES_PER_CRAWL: Optional[int] = None
MAX_BYTES = 10 * 1024 * 1024  # 10 MB
MIN_BYTES = 2048  # 2 KB
MIN_DIM = 24
ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/x-png",
    "image/pjpeg",
}


def upload_dir() -> Path:
    env = (os.environ.get("UPLOAD_DIR") or "").strip()
    if env:
        return Path(env)
    # Fallback mirrors extract.py / admin routes when env unset
    return Path(__file__).resolve().parent.parent / "uploaded_files"


def product_images_dir() -> Path:
    """Served product-images directory (approve copies staged files here)."""
    base = upload_dir()
    path = base / "product_images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def crawl_images_dir(tenant_id: str, run_id: int) -> Path:
    path = upload_dir() / "crawl_images" / str(tenant_id) / str(run_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _guess_ext(content_type: str, source_url: str) -> str:
    ext = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    if ext in {".jpe", ".jpeg"}:
        ext = ".jpg"
    if not ext:
        path = urllib.parse.urlparse(source_url).path
        suffix = Path(path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
            ext = ".jpg" if suffix == ".jpeg" else suffix
        else:
            ext = ".jpg"
    return ext


def _image_dimensions(content: bytes) -> Tuple[Optional[int], Optional[int]]:
    try:
        from PIL import Image
        with Image.open(io.BytesIO(content)) as img:
            return img.size[0], img.size[1]
    except Exception:
        return None, None


def _best_srcset_candidate(srcset: str) -> str:
    """Pick the best URL from a srcset: largest width descriptor wins,
    then largest density, else the last candidate."""
    entries: List[Tuple[float, str]] = []
    for part in (srcset or "").split(","):
        tokens = part.strip().split()
        if not tokens:
            continue
        url = tokens[0]
        score = 0.0
        if len(tokens) > 1:
            desc = tokens[1].lower()
            try:
                if desc.endswith("w"):
                    score = float(desc[:-1])
                elif desc.endswith("x"):
                    score = float(desc[:-1]) * 1000
            except ValueError:
                score = 0.0
        entries.append((score, url))
    if not entries:
        return ""
    entries.sort(key=lambda e: e[0])
    return entries[-1][1]


def _heuristic_skip_reason(source_url: str, kind: str, alt: str = "") -> Optional[str]:
    """Filename/URL heuristics for images that are never knowledge:
    sprites, favicons, icons and tracking pixels. Only plain content
    images (kind == "image") are judged this way — logos keep their
    existing stored-for-brand behaviour, og/product images are content.

    A file that is evidently a logo (name or alt text) is exempt from
    the icon rule: brand logos are often served from an /icons/ asset
    path (health check 2026-11-12: an animated logo GIF was skipped as
    icon_excluded for exactly that reason).
    """
    if kind != "image":
        return None
    path = urllib.parse.urlparse(source_url or "").path.lower()
    base = path.rsplit("/", 1)[-1]
    if "sprite" in base:
        return "sprite_excluded"
    if "favicon" in base:
        return "favicon_excluded"
    if "pixel" in base or "tracker" in base or "1x1" in base:
        return "tracking_pixel"
    is_logo = "logo" in base or "logo" in (alt or "").lower()
    if not is_logo and (
        base.startswith("icon") or "-icon" in base or "_icon" in base or "/icons/" in path
    ):
        return "icon_excluded"
    return None


_CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", re.I)
_CSS_IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|bmp)(?:[?#]|$)", re.I)


def _css_background_urls(css_text: str) -> List[str]:
    """Image URLs referenced by CSS url(...) in background declarations.

    Only raster image extensions are returned (fonts, SVGs, data URIs and
    gradients are not knowledge images)."""
    urls: List[str] = []
    for m in _CSS_URL_RE.finditer(css_text or ""):
        u = m.group(1).strip()
        if not u or u.startswith("data:"):
            continue
        if not _CSS_IMG_EXT_RE.search(u):
            continue
        urls.append(u)
    return urls


def collect_image_candidates(pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Harvest image URLs from crawled page payloads (html + structured fields)."""
    candidates: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def _add(
        url: Optional[str],
        *,
        kind: str,
        source_page: str,
        alt: str = "",
        offering_name: str = "",
        section: str = "",
    ) -> None:
        if not url:
            return
        url = url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return
        lower = url.lower()
        if ".svg" in lower.split("?")[0]:
            return
        if url in seen:
            # Still record as a candidate for offering linkage if new offering
            if offering_name:
                candidates.append({
                    "source_url": url,
                    "kind": kind,
                    "source_page_url": source_page,
                    "alt_text": alt or "",
                    "offering_name": offering_name,
                    "section": section or "",
                    "already_seen": True,
                })
            return
        seen.add(url)
        candidates.append({
            "source_url": url,
            "kind": kind,
            "source_page_url": source_page,
            "alt_text": alt or "",
            "offering_name": offering_name or "",
            "section": section or "",
            "already_seen": False,
        })

    for page in pages or []:
        page_url = page.get("url") or ""
        _add(page.get("logo_url"), kind="logo", source_page=page_url)
        _add(page.get("og_image"), kind="og", source_page=page_url)
        _add(page.get("twitter_image"), kind="twitter", source_page=page_url)

        for item in page.get("json_ld_offerings") or []:
            _add(
                item.get("media_url"),
                kind="product",
                source_page=page_url,
                offering_name=str(item.get("name") or ""),
            )

        for card in page.get("visual_cards") or []:
            _add(
                card.get("media_url"),
                kind="product",
                source_page=page_url,
                offering_name=str(card.get("name") or ""),
            )

        # JSON-LD image fields (Organization logo, Product image arrays, etc.)
        for blob in page.get("json_ld_raw") or []:
            _walk_json_ld_images(blob, page_url, _add)

        html = page.get("html") or ""
        if html:
            soup = BeautifulSoup(html, "html.parser")
            # Section tracking: the nearest preceding h1/h2/h3 for each img,
            # so a captured image knows which part of the page it belongs to.
            section_by_img: Dict[int, str] = {}
            current_section = ""
            # CSS-background images (heroes/banners on page-builder sites
            # are usually background-image, not <img>): inline styles are
            # collected during this same document-order walk so they get
            # the correct preceding-heading section.
            inline_backgrounds: List[Tuple[str, str]] = []
            style_block_texts: List[str] = []
            for el in soup.find_all(True):
                if el.name in ("h1", "h2", "h3"):
                    heading_text = clean_text(el.get_text())
                    if heading_text:
                        current_section = heading_text
                elif el.name == "img":
                    section_by_img[id(el)] = current_section
                if el.name == "style":
                    style_block_texts.append(el.get_text() or "")
                style_attr = el.get("style") or ""
                if "background" in style_attr and "url(" in style_attr:
                    for bg_url in _css_background_urls(style_attr):
                        inline_backgrounds.append((bg_url, current_section))
            for img in soup.find_all("img"):
                # Prefer the srcset best candidate (largest width), then raw
                # src resolution so tiny tracking pixels still reach the
                # download stage (where size skips are applied/recorded).
                alt = (img.get("alt") or "").strip()
                kind = "logo" if "logo" in (alt + " " + " ".join(img.get("class") or [])).lower() else "image"
                srcset_best = _best_srcset_candidate(img.get("srcset") or "")
                raw = (
                    srcset_best
                    or img.get("src")
                    or img.get("data-src")
                    or img.get("data-lazy-src")
                    or ""
                ).strip()
                section = section_by_img.get(id(img), "")
                if raw and not raw.startswith("data:"):
                    _add(urllib.parse.urljoin(page_url, raw), kind=kind, source_page=page_url, alt=alt, section=section)
                else:
                    best = extract_best_image_url(img, page_url)
                    _add(best, kind=kind, source_page=page_url, alt=alt, section=section)
            # srcset-only picture sources — best candidate only, matching img srcset
            for source in soup.find_all("source", srcset=True):
                best = _best_srcset_candidate(source.get("srcset") or "")
                if best:
                    resolved = urllib.parse.urljoin(page_url, best)
                    _add(resolved, kind="image", source_page=page_url)
            # CSS backgrounds: inline styles first (section known), then
            # <style> block declarations (page-level, no specific section).
            for bg_url, bg_section in inline_backgrounds:
                _add(
                    urllib.parse.urljoin(page_url, bg_url),
                    kind="image", source_page=page_url, section=bg_section,
                )
            for css_text in style_block_texts:
                for bg_url in _css_background_urls(css_text):
                    _add(
                        urllib.parse.urljoin(page_url, bg_url),
                        kind="image", source_page=page_url,
                    )

    return candidates


def _walk_json_ld_images(node: Any, page_url: str, add_fn) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_json_ld_images(item, page_url, add_fn)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _walk_json_ld_images(node["@graph"], page_url, add_fn)
    schema_type = str(node.get("@type", "")).lower()
    kind = "logo" if "organization" in schema_type or "brand" in schema_type else "product"
    for key in ("image", "logo", "photo", "thumbnailUrl"):
        val = node.get(key)
        if not val:
            continue
        urls: List[str] = []
        if isinstance(val, str):
            urls = [val]
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, str):
                    urls.append(v)
                elif isinstance(v, dict) and v.get("url"):
                    urls.append(str(v["url"]))
        elif isinstance(val, dict) and val.get("url"):
            urls = [str(val["url"])]
        offering = str(node.get("name") or "") if kind == "product" else ""
        for u in urls:
            resolved = urllib.parse.urljoin(page_url, u)
            add_fn(
                resolved,
                kind=kind if key != "logo" else "logo",
                source_page=page_url,
                offering_name=offering,
            )
    for v in node.values():
        if isinstance(v, (dict, list)):
            _walk_json_ld_images(v, page_url, add_fn)


def download_and_store_crawl_images(
    *,
    tenant_id: str,
    run_id: int,
    pages: List[Dict[str, Any]],
    timeout: float = 12.0,
) -> List[Dict[str, Any]]:
    """Download eligible images, persist DB rows, return image records."""
    from database import (
        find_crawl_image_by_sha,
        find_crawl_image_by_source_url,
        insert_crawl_image,
    )

    candidates = collect_image_candidates(pages)
    dest_root = crawl_images_dir(tenant_id, run_id)
    stored_count = 0
    results: List[Dict[str, Any]] = []
    url_to_id: Dict[str, int] = {}

    with httpx.Client(trust_env=False, timeout=timeout, follow_redirects=True) as client:
        for cand in candidates:
            source_url = cand["source_url"]
            kind = cand.get("kind") or "image"
            offering_name = cand.get("offering_name") or ""
            alt = cand.get("alt_text") or ""
            source_page = cand.get("source_page_url") or ""
            section = cand.get("section") or ""

            # Filename heuristics: sprites / favicons / icons / tracking
            # pixels are never knowledge — record the skip, never fetch.
            heuristic_reason = _heuristic_skip_reason(source_url, kind, alt)
            if heuristic_reason:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": heuristic_reason,
                })
                results.append({
                    "id": row_id, "source_url": source_url, "skipped": True,
                    "skip_reason": heuristic_reason, "kind": kind,
                    "alt_text": alt, "source_page_url": source_page,
                    "section": section,
                })
                continue

            # Cap stored (non-skipped, non-duplicate-new-bytes) images.
            # MAX_IMAGES_PER_CRAWL is None (unlimited) by product decision;
            # the branch remains only for a future explicit override.
            if MAX_IMAGES_PER_CRAWL is not None and stored_count >= MAX_IMAGES_PER_CRAWL:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": "max_images",
                })
                results.append({"id": row_id, "source_url": source_url, "skipped": True, "skip_reason": "max_images"})
                continue

            # Dedupe by source URL within this run
            existing = find_crawl_image_by_source_url(run_id, source_url)
            if existing:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "local_path": existing.get("local_path"),
                    "served_url": existing.get("served_url"),
                    "sha256": existing.get("sha256"),
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "is_duplicate": True,
                    "duplicate_of": existing["id"],
                    "skipped": existing.get("skipped", False),
                    "skip_reason": "duplicate_url" if not existing.get("skipped") else existing.get("skip_reason"),
                })
                results.append({
                    "id": row_id, "source_url": source_url, "is_duplicate": True,
                    "duplicate_of": existing["id"], "kind": kind,
                    "offering_name": offering_name,
                    "local_path": existing.get("local_path"),
                    "served_url": existing.get("served_url"),
                })
                continue

            try:
                resp = _get_image_guarded(client, source_url)
            except BlockedUrlError:
                # M2: non-public image target — record the skip, never fetch.
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": "blocked: non-public address",
                })
                results.append({"id": row_id, "skipped": True, "skip_reason": "blocked: non-public address"})
                continue
            except Exception as exc:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": f"fetch_error:{str(exc)[:120]}",
                })
                results.append({"id": row_id, "skipped": True, "skip_reason": "fetch_error"})
                continue

            if resp.status_code != 200:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": f"http_{resp.status_code}",
                })
                results.append({"id": row_id, "skipped": True})
                continue

            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            if content_type == "image/svg+xml" or content_type not in ALLOWED_CONTENT_TYPES:
                # Also reject if URL path ends with .svg even with wrong content-type
                if ".svg" in urllib.parse.urlparse(source_url).path.lower() or content_type == "image/svg+xml":
                    skip_reason = "svg_excluded"
                elif not content_type.startswith("image/"):
                    skip_reason = f"non_image:{content_type or 'unknown'}"
                else:
                    skip_reason = f"content_type:{content_type}"
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "content_type": content_type,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": skip_reason,
                })
                results.append({"id": row_id, "skipped": True, "skip_reason": skip_reason})
                continue

            content = resp.content or b""
            byte_size = len(content)
            if byte_size > MAX_BYTES:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "content_type": content_type,
                    "byte_size": byte_size,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": "too_large",
                })
                results.append({"id": row_id, "skipped": True, "skip_reason": "too_large"})
                continue

            if byte_size < MIN_BYTES:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "content_type": content_type,
                    "byte_size": byte_size,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": "too_small_bytes",
                })
                results.append({"id": row_id, "skipped": True, "skip_reason": "too_small_bytes"})
                continue

            width, height = _image_dimensions(content)
            if width is not None and height is not None and (width < MIN_DIM or height < MIN_DIM):
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "content_type": content_type,
                    "byte_size": byte_size,
                    "width": width,
                    "height": height,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "skipped": True,
                    "skip_reason": "too_small_dims",
                })
                results.append({"id": row_id, "skipped": True, "skip_reason": "too_small_dims"})
                continue

            sha = hashlib.sha256(content).hexdigest()
            prior = find_crawl_image_by_sha(run_id, sha)
            if prior:
                row_id = insert_crawl_image({
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "source_url": source_url,
                    "local_path": prior.get("local_path"),
                    "served_url": prior.get("served_url"),
                    "content_type": content_type,
                    "sha256": sha,
                    "byte_size": byte_size,
                    "width": width,
                    "height": height,
                    "kind": kind,
                    "alt_text": alt,
                    "source_page_url": source_page,
                    "offering_name": offering_name,
                    "is_duplicate": True,
                    "duplicate_of": prior["id"],
                })
                results.append({
                    "id": row_id, "source_url": source_url, "is_duplicate": True,
                    "duplicate_of": prior["id"], "sha256": sha, "kind": kind,
                    "offering_name": offering_name,
                    "local_path": prior.get("local_path"),
                    "served_url": prior.get("served_url"),
                })
                continue

            ext = _guess_ext(content_type, source_url)
            label = slugify(offering_name or kind or "img")[:40]
            filename = f"{label}_{sha[:12]}{ext}"
            local_path = dest_root / filename
            with open(local_path, "wb") as fh:
                fh.write(content)

            # Staging-relative URL (not yet the served product-images path)
            served_url = f"/crawl_images/{tenant_id}/{run_id}/{filename}"
            row_id = insert_crawl_image({
                "run_id": run_id,
                "tenant_id": tenant_id,
                "source_url": source_url,
                "local_path": str(local_path),
                "served_url": served_url,
                "content_type": content_type,
                "sha256": sha,
                "byte_size": byte_size,
                "width": width,
                "height": height,
                "kind": kind,
                "alt_text": alt,
                "source_page_url": source_page,
                "offering_name": offering_name,
            })
            stored_count += 1
            url_to_id[source_url] = row_id
            results.append({
                "id": row_id,
                "source_url": source_url,
                "local_path": str(local_path),
                "served_url": served_url,
                "sha256": sha,
                "kind": kind,
                "offering_name": offering_name,
                "alt_text": alt,
                "source_page_url": source_page,
                "section": section,
                "width": width,
                "height": height,
                "byte_size": byte_size,
            })
            logger.info(
                "CRAWL_IMAGE_STORED | tenant=%s run=%s file=%s kind=%s",
                tenant_id, run_id, filename, kind,
            )

    return results


def delete_staged_image_files(tenant_id: str, run_id: int) -> int:
    """Remove the staged crawl_images directory for a run. Returns files deleted."""
    root = upload_dir() / "crawl_images" / str(tenant_id) / str(run_id)
    if not root.exists():
        return 0
    deleted = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                logger.warning("Failed deleting staged image %s: %s", path, exc)
    try:
        # remove empty dirs bottom-up
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_dir():
                path.rmdir()
        root.rmdir()
    except OSError:
        pass
    return deleted


def promote_images_to_product_dir(
    tenant_id: str,
    run_id: int,
    images: List[Dict[str, Any]],
) -> Dict[str, str]:
    """Copy non-duplicate stored images into product_images; return source_url→served_url map."""
    import shutil

    dest = product_images_dir()
    mapping: Dict[str, str] = {}
    seen_sha: Dict[str, str] = {}

    for img in images:
        if img.get("skipped") or not img.get("local_path"):
            continue
        src = Path(img["local_path"])
        if not src.is_file():
            continue
        sha = img.get("sha256") or ""
        if sha and sha in seen_sha:
            mapping[img["source_url"]] = seen_sha[sha]
            continue
        ext = src.suffix or ".jpg"
        label = slugify(img.get("offering_name") or img.get("kind") or "img")[:30]
        filename = f"{tenant_id}_{label}_{(sha or hashlib.md5(img['source_url'].encode()).hexdigest())[:10]}{ext}"
        target = dest / filename
        if not target.exists():
            shutil.copy2(src, target)
        served = f"/uploaded_files/product_images/{filename}"
        mapping[img["source_url"]] = served
        if sha:
            seen_sha[sha] = served
        # Also map by staging served_url if present
        if img.get("served_url"):
            mapping[img["served_url"]] = served
    return mapping
