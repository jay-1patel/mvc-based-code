"""SiteCrawler v2 — sitemap-first discovery + same-domain BFS.

Keeps ``url_scraper.WebScraperCrawler`` untouched for legacy endpoints.
Reuses UniversalWebExtractor / RobotsTxtPolicy / normalize helpers from
url_scraper. No Playwright hard dependency (optional guarded import only).
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
import socket
import time
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx
from bs4 import BeautifulSoup

from services.url_scraper import (
    DEFAULT_CRAWL_DELAY,
    USER_AGENT,
    RobotsTxtPolicy,
    UniversalWebExtractor,
    clean_text,
    looks_like_js_shell,
    normalize_url,
    render_with_playwright,
    _js_render_enabled,
)

logger = logging.getLogger("site_crawler")


# ─────────────────────────────────────────────
# M2: SSRF guard — crawl/image targets must be public addresses
# ─────────────────────────────────────────────
class BlockedUrlError(ValueError):
    """Raised when a URL fails the SSRF guard (non-public target)."""


def _crawl_allowlist() -> Set[str]:
    """Hosts/IPs exempt from the SSRF guard (dev fixtures only).

    Read from CRAWL_URL_ALLOWLIST (comma-separated, empty by default).
    Must never be set in production.
    """
    raw = os.environ.get("CRAWL_URL_ALLOWLIST", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def assert_public_url(url: str) -> None:
    """Raise BlockedUrlError unless ``url`` is http(s) and resolves public.

    Every resolved IP is checked: any private / loopback / link-local /
    reserved / multicast / unspecified address rejects the URL. Hosts (or
    fully-allowlisted resolutions) in CRAWL_URL_ALLOWLIST skip the check.
    """
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise BlockedUrlError(f"blocked: unsupported scheme {parsed.scheme!r}")
    host = (parsed.hostname or "").strip()
    if not host:
        raise BlockedUrlError("blocked: missing host")
    allowlist = _crawl_allowlist()
    if host.lower() in allowlist:
        return
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        raise BlockedUrlError(f"blocked: cannot resolve host {host}: {exc}") from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise BlockedUrlError(f"blocked: cannot resolve host {host}")
    if all(ip.lower() in allowlist for ip in ips):
        return
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise BlockedUrlError(
                f"blocked: non-public address {ip} for host {host}"
            )


_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
MAX_REDIRECT_HOPS = 5


def _get_guarded(
    client: httpx.Client,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[float] = None,
) -> httpx.Response:
    """GET ``url`` with SSRF validation of the URL and every redirect hop.

    Redirects are followed manually (max 5 hops); each Location target is
    re-validated with assert_public_url before it is fetched.
    """
    current = url
    kwargs: Dict[str, Any] = {"headers": headers, "follow_redirects": False}
    if timeout is not None:
        kwargs["timeout"] = timeout
    resp: Optional[httpx.Response] = None
    for _hop in range(MAX_REDIRECT_HOPS + 1):
        assert_public_url(current)
        resp = client.get(current, **kwargs)
        if resp.status_code not in _REDIRECT_STATUSES:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        current = urllib.parse.urljoin(current, location)
    assert resp is not None
    return resp

NON_HTML_EXTENSIONS = re.compile(
    r"\.(pdf|png|jpe?g|gif|webp|svg|ico|css|js|mjs|map|zip|rar|gz|tgz|mp[34]|wav|"
    r"avi|mov|wmv|mkv|woff2?|ttf|eot|otf|docx?|xlsx?|pptx?|csv|json|xml|rss|atom|"
    r"txt|md|yml|yaml|gz|7z|exe|dmg|apk)(?:$|\?)",
    re.I,
)

def same_domain(base_netloc: str, target_url: str) -> bool:
    """True when target shares the registrable host (www-tolerant)."""
    parsed = urllib.parse.urlparse(target_url)
    host = (parsed.netloc or "").lower()
    base = (base_netloc or "").lower()
    if not host or not base:
        return False
    strip = lambda h: h[4:] if h.startswith("www.") else h
    return strip(host) == strip(base)


def canonicalize_url(raw_url: str) -> str:
    """Normalize scheme/host/path; drop fragment and query for crawl identity."""
    url = normalize_url(raw_url)
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path or "/"
    # collapse duplicate slashes; keep trailing slash distinction only for root
    path = re.sub(r"/{2,}", "/", path)
    return urllib.parse.urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", "")
    )


def is_probably_html_url(url: str) -> bool:
    if not url:
        return False
    if NON_HTML_EXTENSIONS.search(url):
        return False
    return True


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_sitemap_xml(xml_text: str) -> Tuple[List[str], List[str]]:
    """Parse a sitemap document into (page_urls, nested_sitemap_urls)."""
    page_urls: List[str] = []
    nested: List[str] = []
    if not xml_text or not xml_text.strip():
        return page_urls, nested
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        # Some servers serve HTML error pages; ignore.
        return page_urls, nested

    root_name = _local_name(root.tag).lower()
    if root_name == "sitemapindex":
        for child in root:
            if _local_name(child.tag).lower() != "sitemap":
                continue
            for el in child:
                if _local_name(el.tag).lower() == "loc" and el.text:
                    nested.append(el.text.strip())
    elif root_name == "urlset":
        for child in root:
            if _local_name(child.tag).lower() != "url":
                continue
            for el in child:
                if _local_name(el.tag).lower() == "loc" and el.text:
                    page_urls.append(el.text.strip())
    else:
        # Fallback: any <loc>
        for el in root.iter():
            if _local_name(el.tag).lower() == "loc" and el.text:
                page_urls.append(el.text.strip())
    return page_urls, nested


class SiteCrawler:
    """Sitemap-first + BFS website crawler with full URL inventory."""

    def __init__(
        self,
        base_url: str,
        max_pages: int = 50,
        max_depth: int = 4,
        page_timeout: float = 12.0,
        total_timeout: float = 600.0,
        respect_robots: bool = True,
        request_delay: float = DEFAULT_CRAWL_DELAY,
        enable_js_render: Optional[bool] = None,
    ):
        self.root_url = canonicalize_url(base_url)
        self.max_pages = max(1, int(max_pages or 50))
        self.max_depth = max(0, int(max_depth if max_depth is not None else 4))
        self.page_timeout = float(page_timeout or 12.0)
        self.total_timeout = float(total_timeout or 600.0)
        self.respect_robots = respect_robots
        self.request_delay = request_delay
        self.enable_js_render = _js_render_enabled(enable_js_render)
        parsed = urllib.parse.urlparse(self.root_url)
        self.domain = parsed.netloc.lower()
        self.robots: Optional[RobotsTxtPolicy] = None
        self._robots_sitemaps: List[str] = []
        self._last_fetch_ts: float = 0.0
        self.inventory: List[Dict[str, Any]] = []
        self.js_rendered_urls: List[str] = []

    def _polite_delay(self) -> None:
        delay = self.request_delay
        if self.robots and self.robots.crawl_delay:
            delay = max(delay, float(self.robots.crawl_delay))
        elapsed = time.monotonic() - self._last_fetch_ts
        if elapsed < delay:
            time.sleep(delay - elapsed)

    def _load_robots(self, client: httpx.Client) -> None:
        if not self.respect_robots:
            return
        self.robots = RobotsTxtPolicy(self.root_url, timeout=self.page_timeout)
        # Also harvest Sitemap: directives from robots.txt body
        parsed = urllib.parse.urlparse(self.root_url)
        robots_url = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, "/robots.txt", "", "", "")
        )
        try:
            self._polite_delay()
            resp = _get_guarded(client, robots_url, headers={"User-Agent": USER_AGENT})
            self._last_fetch_ts = time.monotonic()
            if resp.status_code == 200:
                for line in resp.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        loc = line.split(":", 1)[1].strip()
                        if loc:
                            self._robots_sitemaps.append(loc)
        except Exception as exc:
            logger.debug("robots.txt sitemap harvest failed: %s", exc)

    def _discover_sitemap_urls(self, client: httpx.Client) -> List[str]:
        """Collect same-domain page URLs from robots Sitemap: lines + /sitemap.xml."""
        seeds: List[str] = list(self._robots_sitemaps)
        parsed = urllib.parse.urlparse(self.root_url)
        seeds.append(
            urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/sitemap.xml", "", "", ""))
        )
        # de-dupe preserving order
        seen_sitemaps: Set[str] = set()
        queue: List[str] = []
        for s in seeds:
            canon = s.strip()
            if canon and canon not in seen_sitemaps:
                seen_sitemaps.add(canon)
                queue.append(canon)

        page_urls: List[str] = []
        seen_pages: Set[str] = set()
        max_sitemaps = 25

        while queue and len(seen_sitemaps) <= max_sitemaps:
            sm_url = queue.pop(0)
            try:
                assert_public_url(sm_url)
                self._polite_delay()
                resp = _get_guarded(
                    client,
                    sm_url,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml,*/*"},
                    timeout=self.page_timeout,
                )
                self._last_fetch_ts = time.monotonic()
                if resp.status_code != 200:
                    continue
                ctype = (resp.headers.get("content-type") or "").lower()
                body = resp.text
                if "html" in ctype and "<urlset" not in body and "<sitemapindex" not in body:
                    continue
                pages, nested = parse_sitemap_xml(body)
                for n in nested:
                    if n not in seen_sitemaps and same_domain(self.domain, n):
                        seen_sitemaps.add(n)
                        queue.append(n)
                for p in pages:
                    canon = canonicalize_url(p)
                    if not canon or canon in seen_pages:
                        continue
                    if not same_domain(self.domain, canon):
                        continue
                    if not is_probably_html_url(canon):
                        continue
                    seen_pages.add(canon)
                    page_urls.append(canon)
            except Exception as exc:
                logger.debug("sitemap fetch failed for %s: %s", sm_url, exc)
        return page_urls

    def _record(
        self,
        url: str,
        *,
        source: str,
        http_status: Optional[int] = None,
        success: bool = False,
        error: Optional[str] = None,
        word_count: int = 0,
        final_url: Optional[str] = None,
        page_data: Optional[Dict[str, Any]] = None,
        depth: int = 0,
    ) -> None:
        self.inventory.append({
            "url": final_url or url,
            "requested_url": url,
            "source": source,
            "http_status": http_status,
            "success": success,
            "error": error,
            "word_count": word_count,
            "depth": depth,
            "page_data": page_data,
        })

    def _fetch(
        self, client: httpx.Client, url: str
    ) -> Tuple[Optional[str], Optional[int], Optional[str], Optional[str]]:
        """Return (html, http_status, final_url, error)."""
        self._polite_delay()
        try:
            resp = _get_guarded(
                client,
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=self.page_timeout,
            )
            self._last_fetch_ts = time.monotonic()
            if resp.status_code in (429, 503):
                retry_after = resp.headers.get("Retry-After", "")
                backoff = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 5.0
                time.sleep(min(backoff, 30.0))
                resp = _get_guarded(
                    client,
                    url,
                    headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                    timeout=self.page_timeout,
                )
                self._last_fetch_ts = time.monotonic()

            final_url = canonicalize_url(str(resp.url)) or url
            ctype = (resp.headers.get("content-type") or "").lower()
            if resp.status_code != 200:
                return None, resp.status_code, final_url, f"HTTP {resp.status_code}"
            if "text/html" not in ctype and "application/xhtml" not in ctype:
                return None, resp.status_code, final_url, f"non-html content-type: {ctype[:80]}"

            html = resp.text
            if self.enable_js_render and looks_like_js_shell(html):
                rendered = render_with_playwright(url, timeout=self.page_timeout)
                if rendered:
                    self.js_rendered_urls.append(url)
                    html = rendered
            return html, resp.status_code, final_url, None
        except Exception as exc:
            self._last_fetch_ts = time.monotonic()
            return None, None, url, str(exc)[:300]

    def _extract_page(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        meta_desc = ""
        meta_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
        if not meta_tag:
            meta_tag = soup.find("meta", attrs={"property": "og:description"})
        if meta_tag and meta_tag.get("content"):
            meta_desc = str(meta_tag["content"]).strip()

        logo_url = UniversalWebExtractor.extract_brand_logo(soup, url)
        json_ld_offerings = UniversalWebExtractor.extract_json_ld(soup, url)
        visual_cards = UniversalWebExtractor.extract_visual_cards(soup, url)

        # Raw JSON-LD blobs for image collector / AI context
        json_ld_raw: List[Any] = []
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                import json as _json
                json_ld_raw.append(_json.loads(script.string.strip()))
            except Exception:
                continue

        full_html_text = soup.get_text()
        emails = list(set(re.findall(
            r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", full_html_text
        )))
        phones = list(set(re.findall(
            r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3,5}\)?[-.\s]?\d{3,5}[-.\s]?\d{3,5}",
            full_html_text,
        )))
        phones = [
            p.strip() for p in phones
            if 10 <= len(re.sub(r"\D", "", p)) <= 14
        ]

        headings: List[str] = []
        for h in soup.find_all(["h1", "h2", "h3"]):
            txt = clean_text(h.get_text())
            if txt and len(txt) > 3 and txt not in headings:
                headings.append(txt)

        clean_soup = BeautifulSoup(str(soup), "html.parser")
        for tag in clean_soup(["script", "style", "noscript", "svg", "header", "footer"]):
            tag.decompose()
        text_blocks: List[str] = []
        for elem in clean_soup.find_all(["p", "li", "article", "section", "table"]):
            txt = clean_text(elem.get_text())
            if len(txt) > 35 and txt not in text_blocks:
                text_blocks.append(txt)
        combined_text = "\n\n".join(text_blocks[:40])
        word_count = len(re.findall(r"\w+", combined_text)) if combined_text else 0

        # og / twitter images kept for image collector
        og_image = None
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            og_image = urllib.parse.urljoin(url, str(og["content"]).strip())
        tw_image = None
        tw = soup.find("meta", attrs={"name": "twitter:image"})
        if tw and tw.get("content"):
            tw_image = urllib.parse.urljoin(url, str(tw["content"]).strip())

        return {
            "url": url,
            "title": title,
            "meta_description": meta_desc,
            "logo_url": logo_url,
            "emails": emails[:3],
            "phones": phones[:3],
            "headings": headings[:15],
            "content": combined_text[:6000],
            "word_count": word_count,
            "json_ld_offerings": json_ld_offerings,
            "visual_cards": visual_cards,
            "json_ld_raw": json_ld_raw,
            "og_image": og_image,
            "twitter_image": tw_image,
            "html": str(soup),  # image collector needs markup; trimmed later
        }

    def _discover_links(
        self, soup: BeautifulSoup, current_url: str
    ) -> List[str]:
        found: List[str] = []
        seen: Set[str] = set()
        for a in soup.find_all("a", href=True):
            raw_href = (a.get("href") or "").strip()
            if not raw_href or raw_href.startswith("#") or raw_href.lower().startswith("javascript:"):
                continue
            if raw_href.lower().startswith(("mailto:", "tel:", "sms:")):
                continue
            target = canonicalize_url(urllib.parse.urljoin(current_url, raw_href))
            if not target or target in seen:
                continue
            if not same_domain(self.domain, target):
                continue
            if not is_probably_html_url(target):
                continue
            seen.add(target)
            found.append(target)
        return found

    def crawl(self) -> Dict[str, Any]:
        """Execute sitemap-first discovery then BFS until caps hit."""
        started = time.monotonic()
        crawled_pages: List[Dict[str, Any]] = []
        visited: Set[str] = set()
        # queue entries: (url, source, depth)
        queue: List[Tuple[str, str, int]] = []

        with httpx.Client(trust_env=False, timeout=self.page_timeout, follow_redirects=True) as client:
            # M2: the root target itself must be a public address.
            try:
                assert_public_url(self.root_url)
            except BlockedUrlError:
                self._record(
                    self.root_url, source="link", success=False,
                    error="blocked: non-public address", http_status=None,
                )
                return {
                    "root_url": self.root_url,
                    "domain": self.domain,
                    "pages_found": 1,
                    "pages_crawled_count": 0,
                    "pages": [],
                    "inventory": self.inventory,
                    "js_rendered_urls": self.js_rendered_urls,
                    "blocked_by_robots": False,
                    "blocked_non_public": True,
                }

            self._load_robots(client)

            if self.respect_robots and self.robots and not self.robots.can_fetch(self.root_url):
                self._record(
                    self.root_url, source="link", success=False,
                    error="robots.txt disallow", http_status=None,
                )
                return {
                    "root_url": self.root_url,
                    "domain": self.domain,
                    "pages_found": 1,
                    "pages_crawled_count": 0,
                    "pages": [],
                    "inventory": self.inventory,
                    "js_rendered_urls": self.js_rendered_urls,
                    "blocked_by_robots": True,
                }

            # 1) Sitemap seeds
            sitemap_urls = self._discover_sitemap_urls(client)
            for u in sitemap_urls:
                queue.append((u, "sitemap", 0))

            # 2) Always seed the root for BFS (link source) if not already queued
            if self.root_url not in {u for u, _, _ in queue}:
                queue.insert(0, (self.root_url, "link", 0))
            elif all(u != self.root_url for u, _, _ in queue):
                queue.insert(0, (self.root_url, "link", 0))
            else:
                # Prefer crawling root early even if sitemap listed it
                queue = [(u, s, d) for u, s, d in queue if u != self.root_url]
                queue.insert(0, (self.root_url, "link", 0))

            while queue and len(crawled_pages) < self.max_pages:
                if time.monotonic() - started > self.total_timeout:
                    logger.warning("SITE_CRAWL_TOTAL_TIMEOUT | root=%s", self.root_url)
                    break

                curr_url, source, depth = queue.pop(0)
                if curr_url in visited:
                    continue
                visited.add(curr_url)

                # M2: never fetch a non-public address mid-crawl either.
                try:
                    assert_public_url(curr_url)
                except BlockedUrlError:
                    self._record(
                        curr_url, source=source, success=False,
                        error="blocked: non-public address", depth=depth,
                    )
                    continue

                if self.respect_robots and self.robots and not self.robots.can_fetch(curr_url):
                    self._record(
                        curr_url, source=source, success=False,
                        error="robots.txt disallow", depth=depth,
                    )
                    continue

                if not is_probably_html_url(curr_url):
                    self._record(
                        curr_url, source=source, success=False,
                        error="non-html extension", depth=depth,
                    )
                    continue

                html, status, final_url, err = self._fetch(client, curr_url)
                if final_url and final_url != curr_url:
                    visited.add(final_url)

                if not html:
                    self._record(
                        curr_url, source=source, http_status=status,
                        success=False, error=err or "fetch failed",
                        final_url=final_url, depth=depth,
                    )
                    continue

                soup = BeautifulSoup(html, "html.parser")
                page_data = self._extract_page(soup, final_url or curr_url)
                # Drop heavy HTML from the returned pages list but keep a flag;
                # image collector re-parses from page_data["html"] when present.
                page_data["category"] = "home" if (final_url or curr_url) == self.root_url else "other"
                page_data["discovery_source"] = source
                word_count = int(page_data.get("word_count") or 0)

                self._record(
                    curr_url, source=source, http_status=status,
                    success=True, word_count=word_count,
                    final_url=final_url, page_data=page_data, depth=depth,
                )
                crawled_pages.append(page_data)

                if depth < self.max_depth:
                    for link in self._discover_links(soup, final_url or curr_url):
                        if link in visited:
                            continue
                        if any(link == q[0] for q in queue):
                            continue
                        if self.respect_robots and self.robots and not self.robots.can_fetch(link):
                            continue
                        queue.append((link, "link", depth + 1))

            # Record remaining queued URLs that were discovered but never fetched
            # (capped by max_pages) so inventory reflects discovery completeness.
            for u, source, depth in queue:
                if u in visited:
                    continue
                visited.add(u)
                self._record(
                    u, source=source, success=False,
                    error="not_fetched_max_pages", depth=depth,
                )

        successful = [p for p in self.inventory if p.get("success")]
        return {
            "root_url": self.root_url,
            "domain": self.domain,
            "pages_found": len(self.inventory),
            "pages_crawled_count": len(crawled_pages),
            "pages": crawled_pages,
            "inventory": self.inventory,
            "js_rendered_urls": self.js_rendered_urls,
            "blocked_by_robots": False,
            "successful_urls": [p["url"] for p in successful],
        }
