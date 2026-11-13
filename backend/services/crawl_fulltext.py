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


_DEFAULT_DOC_SUFFIXES = ("/index.html", "/index.php", "/index.htm", "/default.aspx")


def url_dedupe_key(url: str) -> str:
    """Identity key for "same page" checks.

    The crawler's canonical form keeps trailing slashes and the www
    prefix, so /about, /about/ and www.example.com/about could each be
    staged as if they were different pages. This key folds those
    variants into one identity, and additionally (health check
    2026-11-12):

    * folds the scheme — http and https spellings of the same host+path
      are one page (a login page was fetched under both schemes);
    * strips default-document suffixes — /index.html IS the home page
      (identical chunks were staged twice before this fold).
    """
    parsed = urllib.parse.urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    # Scheme is folded for identity: http is treated as its https twin.
    port = parsed.port
    netloc = host
    if port and port not in (80, 443):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    lowered = path.lower()
    for suffix in _DEFAULT_DOC_SUFFIXES:
        if lowered.endswith(suffix):
            path = path[: -len(suffix)] + "/"
            break
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urllib.parse.urlunparse(("https", netloc, path, "", "", ""))


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


# Short lines that carry facts, not boilerplate: availability, prices,
# ratings. The cross-page stripper must never eat them — the health
# check found a standalone "In stock" line stripped from every listing
# chunk precisely because it repeated on most pages.
_FACTUAL_LINE_RE = re.compile(
    r"\b(?:in stock|out of stock|available now|currently unavailable)\b"
    r"|(?:[£$€₹]\s?\d|\b(?:USD|GBP|EUR|INR|Rs\.?)\s?\d|\d\s?(?:USD|GBP|EUR|INR)\b)"
    r"|\b\d(?:\.\d)?\s*(?:out of 5|/ ?5|stars?)\b",
    re.I,
)


def _is_factual_line(line: str) -> bool:
    return bool(_FACTUAL_LINE_RE.search(line or ""))


def _drop_repeated_lines(texts: List[str]) -> List[str]:
    """Remove lines that repeat across most pages (nav/footer boilerplate).

    A line (<=300 chars) appearing on more than half of the pages is
    boilerplate, not knowledge. Headings are always kept, and so are
    short factual lines (availability / price / rating) even when they
    repeat everywhere. Needs >= 3 pages before anything is dropped.
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
    boiler = {
        line for line, n in counts.items()
        if n > threshold and not _is_factual_line(line)
    }
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


def _normalize_words(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def supplement_blocks(extracted_text: str, block_text: str) -> List[str]:
    """Blocks from the crawler's own block text that the main extractor
    missed — the card-grid loss (health check 2026-11-12: a services
    page kept only 1,269 of 2,702 visible words; feature-card text was
    dropped wholesale). Presence is tested with an 8-word shingle from
    each block's opening words against the extracted text.
    """
    haystack = " " + _normalize_words(extracted_text) + " "
    missing: List[str] = []
    for block in (block_text or "").split("\n\n"):
        block = block.strip()
        if len(block) < 36:
            continue
        words = _normalize_words(block).split()
        if len(words) < 6:
            continue
        shingle = " ".join(words[:8])
        if (" " + shingle + " ") not in haystack:
            missing.append(block)
    return missing


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
        "supplements": {url: added_words},  # card-grid supplement applied
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

    # Card-grid supplement: blocks the crawler's own simple extraction
    # saw but the main extractor dropped are appended to the page text
    # when they add substantial content (>= 25% extra words), so card /
    # feature-list pages stop losing half their text at extraction.
    supplements: Dict[str, int] = {}
    enriched: List[str] = []
    for page, text in zip(survivors, texts):
        url = page.get("url") or ""
        if extractors.get(url) != "fallback":
            missing = supplement_blocks(text, page.get("content") or "")
            added_words = sum(_word_count(b) for b in missing)
            if missing and added_words >= max(20, int(0.25 * _word_count(text))):
                text = text.rstrip() + "\n\n" + "\n\n".join(missing)
                supplements[url] = added_words
        enriched.append(text)
    texts = enriched

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
        "supplements": supplements,
    }
