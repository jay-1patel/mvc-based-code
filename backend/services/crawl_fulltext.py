"""Full-text knowledge extraction for crawl v2.

Until now a crawled page contributed only what the LLM chose to summarise
(plus its one-line meta description in the export): the page's real body
text never reached the knowledge base. This module fixes that:

* each fetched page's main content is extracted (trafilatura when
  available, the crawler's own block text as fallback), boilerplate that
  repeats across pages is stripped, and the cleaned text is chunked
  (~1000 chars, heading-aware) into knowledge_base items that carry the
  source page URL;
* junk pages (privacy / terms / login / search-results style pages, and
  pages with almost no text) are not staged as knowledge — every skip is
  recorded with its reason;
* duplicate URLs (trailing-slash / www / default-port variants) are
  staged once.

The LLM bifurcation layer (articles / FAQs / offerings) is untouched and
stays on top of this grounded layer.
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("crawl_fulltext")

CHUNK_LIMIT = 1000
MIN_PAGE_WORDS = 30

# Conservative path-segment patterns for pages that must never become
# knowledge. Matched against individual URL path segments (lowercased).
_JUNK_SEGMENTS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"^privacy(-policy)?$"), "junk:privacy_page"),
    (re.compile(r"^terms(-of-service|-and-conditions|-conditions)?$"), "junk:terms_page"),
    (re.compile(r"^(log-?in|signin|sign-?in|wp-login)$"), "junk:login_page"),
    (re.compile(r"^my-account$"), "junk:login_page"),
    (re.compile(r"^(search|search-results|listing-results|results)$"), "junk:search_results_page"),
    (re.compile(r"^(cart|checkout)$"), "junk:commerce_flow_page"),
]


def junk_reason_for_url(url: str) -> Optional[str]:
    """Return a skip reason when ``url`` is a junk page, else None."""
    try:
        path = urllib.parse.urlparse(url or "").path or ""
    except Exception:
        return None
    segments = [s for s in path.lower().split("/") if s]
    for segment in segments:
        for pattern, reason in _JUNK_SEGMENTS:
            if pattern.search(segment):
                return reason
    return None


def url_dedupe_key(url: str) -> str:
    """Identity key for "same page" checks.

    The crawler's canonical form keeps trailing slashes and the www
    prefix, so /about, /about/ and www.example.com/about could each be
    staged as if they were different pages. This key folds those
    variants (plus default ports and fragments) into one identity.
    """
    parsed = urllib.parse.urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    scheme = (parsed.scheme or "https").lower()
    port = parsed.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urllib.parse.urlunparse((scheme, netloc, path, "", "", ""))


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text or ""))


def extract_main_text(
    html: Optional[str],
    url: str,
    fallback_text: str = "",
) -> Tuple[str, str]:
    """Extract a page's main content as markdown-ish text.

    Returns (text, extractor) where extractor is "trafilatura" or
    "fallback". Trafilatura is optional: any import/extract failure
    falls back to the crawler's own block text for the page.
    """
    if html:
        try:
            import trafilatura

            extracted = trafilatura.extract(
                html,
                url=url,
                output_format="markdown",
                include_links=False,
                include_images=False,
            )
            if extracted and _word_count(extracted) >= 10:
                return extracted.strip(), "trafilatura"
        except Exception as exc:  # pragma: no cover - exercised when lib missing
            logger.debug("trafilatura unavailable/failed for %s: %s", url, exc)
    return (fallback_text or "").strip(), "fallback"


def _drop_repeated_lines(texts: List[str]) -> List[str]:
    """Remove lines that repeat across most pages (nav/footer boilerplate).

    A line (<=300 chars) appearing on more than half of the pages is
    boilerplate, not knowledge. Headings are always kept. Needs >= 3
    pages before anything is dropped.
    """
    if len(texts) < 3:
        return texts
    counts: Dict[str, int] = {}
    for text in texts:
        seen_here = set()
        for line in text.splitlines():
            norm = line.strip()
            if norm and len(norm) <= 300 and norm not in seen_here:
                seen_here.add(norm)
                counts[norm] = counts.get(norm, 0) + 1
    threshold = len(texts) / 2
    boiler = {line for line, n in counts.items() if n > threshold}
    if not boiler:
        return texts
    out: List[str] = []
    for text in texts:
        kept_lines = []
        for line in text.splitlines():
            norm = line.strip()
            if norm in boiler and not line.lstrip().startswith("#"):
                continue
            kept_lines.append(line)
        out.append("\n".join(kept_lines).strip())
    return out


def _split_blocks(text: str) -> List[Tuple[str, str]]:
    """Split markdown-ish text into (heading, paragraph) blocks."""
    blocks: List[Tuple[str, str]] = []
    heading = ""
    para_lines: List[str] = []

    def flush() -> None:
        para = "\n".join(para_lines).strip()
        if para:
            blocks.append((heading, para))
        para_lines.clear()

    for raw_line in (text or "").splitlines():
        line = raw_line.rstrip()
        if re.match(r"^#{1,6}\s+\S", line.strip()):
            flush()
            heading = line.strip().lstrip("#").strip()
            blocks.append((heading, ""))  # heading marker block
            continue
        if not line.strip():
            flush()
            continue
        para_lines.append(line.strip())
    flush()
    return blocks


def _hard_split(paragraph: str, limit: int) -> List[str]:
    parts: List[str] = []
    rest = paragraph
    while len(rest) > limit:
        cut = rest.rfind(". ", 0, limit)
        cut = cut + 1 if cut > 200 else limit
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


def chunk_page(title: str, text: str, limit: int = CHUNK_LIMIT) -> List[Dict[str, Any]]:
    """Chunk one page's cleaned text into knowledge items (~limit chars).

    Heading-aware: a chunk carries its section heading at the top, and a
    new heading starts a new chunk. Titles follow the approved sample
    format: "<Page title> (part n)".
    """
    blocks = _split_blocks(text)
    raw_chunks: List[str] = []
    current = ""
    current_heading = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            raw_chunks.append(current.strip())
        current = ""

    for heading, para in blocks:
        if para == "":  # heading marker
            flush()
            current_heading = heading
            current = f"## {heading}" if heading else ""
            continue
        pieces = _hard_split(para, limit) if len(para) > limit else [para]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                flush()
                prefix = f"## {current_heading}\n\n" if current_heading else ""
                current = prefix + piece
    flush()

    chunks: List[Dict[str, Any]] = []
    for i, content in enumerate(raw_chunks, start=1):
        if not content.strip():
            continue
        chunks.append({
            "title": f"{title} (part {i})",
            "category": "Website page",
            "content": content,
        })
    # Re-number cleanly in case any chunk was dropped above.
    for i, chunk in enumerate(chunks, start=1):
        chunk["title"] = f"{title} (part {i})"
    return chunks


def build_page_knowledge(
    pages: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build grounded KB chunks + skip records from crawled page payloads.

    Returns {
        "chunks": [{title, category, content, url, page_title}],
        "skipped": [{url, reason}],
        "kept_urls": [url, ...],   # pages that produced chunks
        "excluded_urls": [url, ...],  # junk/duplicate pages: excluded
            from every stage (no chunks, no AI input, no images). A page
            skipped only as "too_short" is NOT excluded — it yields no
            chunks but still feeds the AI layer and image capture.
        "per_page": {url: chunk_count},
        "extractors": {url: "trafilatura"|"fallback"},
    }
    """
    skipped: List[Dict[str, str]] = []
    kept: List[Dict[str, Any]] = []
    seen_keys: Dict[str, str] = {}

    for page in pages or []:
        url = page.get("url") or ""
        if not url:
            continue
        key = url_dedupe_key(url)
        if key in seen_keys:
            skipped.append({"url": url, "reason": "duplicate_url"})
            continue
        reason = junk_reason_for_url(url)
        if reason:
            skipped.append({"url": url, "reason": reason})
            continue
        seen_keys[key] = url
        kept.append(page)

    texts: List[str] = []
    extractors: Dict[str, str] = {}
    survivors: List[Dict[str, Any]] = []
    for page in kept:
        url = page.get("url") or ""
        text, extractor = extract_main_text(
            page.get("html"), url, fallback_text=page.get("content") or ""
        )
        if _word_count(text) < MIN_PAGE_WORDS:
            skipped.append({"url": url, "reason": "too_short"})
            continue
        texts.append(text)
        extractors[url] = extractor
        survivors.append(page)

    texts = _drop_repeated_lines(texts)

    chunks: List[Dict[str, Any]] = []
    per_page: Dict[str, int] = {}
    for page, text in zip(survivors, texts):
        url = page.get("url") or ""
        title = (page.get("title") or url).strip()
        page_chunks = chunk_page(title, text)
        per_page[url] = len(page_chunks)
        for chunk in page_chunks:
            chunks.append({**chunk, "url": url, "page_title": title})

    return {
        "chunks": chunks,
        "skipped": skipped,
        "kept_urls": [p.get("url") or "" for p in survivors],
        "excluded_urls": [
            s["url"] for s in skipped if s["reason"] != "too_short"
        ],
        "per_page": per_page,
        "extractors": extractors,
    }
