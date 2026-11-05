"""
Parse catalogue / new-arrival PDFs into structured product records.

Runs at upload time (backend/routes/admin.py) for modules "catalogue" and
"new_arrival". Extraction heuristics are intentionally conservative: a line
only becomes a product record when it looks like "name ... price" with a
rupee amount or a decimal number, so random PDF prose is not turned into
products. Products already existing (by slug) are skipped.
"""

import logging
import re

logger = logging.getLogger("pdf_products")

# Matches a price like ₹50, `100/- (backtick rupee glyph emitted by many
# PDF fonts), Rs. 120, INR 250.00, 99/-, or a plain 45.00
_PRICE_RE = re.compile(
    r"(?:₹|`|´|rs\.?|inr)\s*(\d{1,7}(?:\.\d{1,2})?)|(\d{1,7}(?:\.\d{1,2})?)\s*(?:/-|rs\.?|₹|`|´)",
    re.IGNORECASE,
)

# Lines that are headings/prose, not products
_NOISE_WORDS = (
    "page", "catalogue", "catalog", "contents", "contact", "phone", "email",
    "address", "www", "http", "gst", "price list", "new arrivals", "our products",
    "order", "terms", "pack of", "mrp", "awards", "founder", "inside story",
    "this pouch", "this box", "this chikki", "this packet", "this coconut",
    "these packs", "the below", "following", "that’s", "that's", "high in",
    "rich in", "source of", "instant", "made with", "made of", "man of",
    "powering", "product of", "ceo of", "govt", "award", "poshak", "fortune",
    "best", "startup", "india", "troogood", "www.", "do good",
)

# A product name line in these catalogues reliably ends with a pack weight,
# e.g. "TOFFEE CHIKKIS 5g" or "CLASSIC MILLET CHIKKI 10g".
_WEIGHT_TAIL_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:g|kg|ml|gm)\s*$", re.IGNORECASE)


def _extract_price(text: str) -> str:
    m = _PRICE_RE.search(text)
    if m:
        return m.group(1) or m.group(2) or ""
    # Bare decimal like 249.00 surrounded by spaces
    m = re.search(r"\b(\d{2,6}\.\d{2})\b", text)
    return m.group(1) if m else ""


def _clean_name(line: str, price_str: str) -> str:
    name = line
    if price_str:
        idx = name.find(price_str)
        if idx > 0:
            name = name[:idx]
    name = re.sub(r"(?:₹|`|´|rs\.?|inr)\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"^mrp\s*", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\(.*?pack.*?\)", "", name, flags=re.IGNORECASE)
    name = name.strip(" .:;\t-–—|`()").strip()
    if name.isupper() and len(name) > 3:
        name = name.title()
    return name.strip()


def _is_product_line(line: str) -> bool:
    lower = line.lower().strip()
    if len(lower) < 3 or len(lower) > 120:
        return False
    if any(w in lower for w in _NOISE_WORDS):
        return False
    # Need at least two letters and a price marker to count
    if not re.search(r"[a-zA-Z]{3}", lower):
        return False
    return bool(_PRICE_RE.search(line) or re.search(r"\b\d{2,6}\.\d{2}\b", line))


def _looks_like_name(line: str) -> bool:
    """A short, letter-heavy line that could be a product name."""
    lower = line.lower().strip()
    if len(lower) < 3 or len(lower) > 80:
        return False
    if any(w in lower for w in _NOISE_WORDS):
        return False
    if _PRICE_RE.search(line) or re.search(r"\b\d{2,6}\.\d{2}\b", line):
        return False  # has a price → handled elsewhere
    letters = len(re.findall(r"[a-zA-Z]", lower))
    return letters >= 3 and letters >= len(lower) * 0.5


# Table-layout parsing (grid/table catalogues such as ReportLab price lists).
# Header cells we recognise, mapped to canonical roles.
_HEADER_ROLES = {
    "name": "name", "product": "name", "item": "name", "product name": "name",
    "item name": "name",
    "category": "cat",
    "weight": "weight", "pack": "weight", "pack size": "weight", "size": "weight",
    "reg. price": "price", "reg price": "price", "mrp": "price",
    "price": "price", "list price": "price",
    "sale price": "sale_price", "selling price": "sale_price",
    "description": "desc", "short description": "desc", "details": "desc",
}

# Status cell values that mean the row should be skipped.
_SOLD_OUT_MARKERS = ("sold out", "out of stock", "unavailable")


def _normalise_header(cell: str) -> str:
    return re.sub(r"\s+", " ", (cell or "").strip().lower())


def _extract_tables(pdf_path: str) -> list:
    """Extract all tables from a PDF via PyMuPDF. Returns [] on any failure."""
    try:
        import fitz
        tables = []
        with fitz.open(pdf_path) as doc:
            for page in doc:
                try:
                    for t in page.find_tables():
                        rows = t.extract()
                        if rows:
                            tables.append(rows)
                except Exception as e:
                    logger.warning(f"find_tables failed on a page: {e}")
        return tables
    except Exception as e:
        logger.warning(f"table extraction unavailable for {pdf_path}: {e}")
        return []


def _role_for_header(cell: str):
    h = _normalise_header(cell)
    if h in _HEADER_ROLES:
        return _HEADER_ROLES[h]
    for key, role in _HEADER_ROLES.items():
        if len(key) > 3 and h.startswith(key):
            return role
    return None


def _parse_table_rows(rows: list, seen_slugs: set, category: str,
                      source_file: str, page_number) -> list:
    """Map one extracted table to product dicts. Returns [] when no header
    row matches known column names."""
    if not rows or len(rows) < 2:
        return []
    header = rows[0]
    roles = [_role_for_header(c) for c in header]
    if not roles or roles.count("name") != 1 or "price" not in roles:
        return []

    out = []
    for row in rows[1:]:
        rec = {}
        for role, cell in zip(roles, row):
            if not role:
                continue
            cell = (cell or "").replace("\n", " ").strip()
            if cell:
                rec[role] = (rec[role] + " " + cell) if role in rec else cell
        name = (rec.get("name") or "").strip()
        if not name or len(name) < 3:
            continue
        if any(m in (rec.get("status", "") or "").lower() for m in _SOLD_OUT_MARKERS):
            continue
        price = rec.get("sale_price") or rec.get("price") or ""
        price = _extract_price(price) or price.replace("Rs.", "").strip()
        weight = (rec.get("weight") or "").strip()
        desc = (rec.get("desc") or "").strip()
        if weight:
            desc = (desc + " " + weight).strip() if desc else weight
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        out.append({
            "name": name[:100],
            "slug": slug,
            "category": category,
            "short_description": desc[:200],
            "price": price,
            "source_file": source_file,
            "page_number": page_number,
        })
    return out


def parse_catalogue_products(pages: list, source_file: str, category: str, pdf_path: str = None) -> list:
    """Turn extracted PDF pages into product dicts.

    pages: list of {"page": int, "text": str} (same shape as extract_pdf results).
    Handles both layouts:
      - "Millet Bars Rs. 50"  (name and price on one line)
      - "Millet Bars\\nRs. 50" (price on the following line)
    Returns list of dicts with name, slug, category, price, description.
    """
    products = []
    seen_slugs = set()

    # Try structured table extraction first (grid/table catalogues such as
    # ReportLab-generated price lists). Falls back to the line heuristics
    # below when no tables are found or nothing matches.
    if pdf_path:
        for rows in _extract_tables(pdf_path):
            products.extend(
                _parse_table_rows(rows, seen_slugs, category, source_file, None)
            )
        if products:
            logger.info(f"PDF_PRODUCTS_PARSED_TABLE | {source_file} | category={category} | found={len(products)}")
            return products

    for page in pages:
        text = (page.get("text") or "").strip()
        if not text:
            continue
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        pending_name = ""
        pending_desc = ""

        for idx, line in enumerate(lines):
            lower = line.lower()
            has_price = bool(_PRICE_RE.search(line) or re.search(r"\b\d{2,6}\.\d{2}\b", line))

            if has_price:
                # Case 1: price-only line following a name line
                price_only = re.fullmatch(
                    r"(?:₹|rs\.?|inr)?\s*\d{1,7}(?:\.\d{1,2})?\s*(?:/-|rs\.?|₹)?", lower
                )
                if price_only and pending_name:
                    name, price_str = pending_name, _extract_price(line)
                    pending_name = ""
                elif _is_product_line(line):
                    # Case 2: name and price on the same line
                    price_str = _extract_price(line)
                    name = _clean_name(line, price_str)
                else:
                    pending_name = ""
                    continue

                if not name or len(name) < 3:
                    continue
                slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80]
                if not slug or slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                products.append({
                    "name": name[:100],
                    "slug": slug,
                    "category": category,
                    "short_description": pending_desc[:200],
                    "price": price_str,
                    "source_file": source_file,
                    "page_number": page.get("page"),
                })
                pending_desc = ""
                continue

            # No price on this line
            if _looks_like_name(line):
                if pending_name:
                    # Two name-like lines in a row: first was probably a
                    # description or heading; keep it as description.
                    if not pending_desc:
                        pending_desc = pending_name
                pending_name = line
            elif 3 <= len(line) <= 100 and not pending_desc and not any(
                w in lower for w in _NOISE_WORDS
            ):
                pending_desc = line

    logger.info(f"PDF_PRODUCTS_PARSED | {source_file} | category={category} | found={len(products)}")
    return products


def save_parsed_products(products: list, media_url: str = None) -> int:
    """Insert parsed products into the products table. Returns count saved."""
    try:
        from database import get_product_by_slug, save_product
    except ImportError:
        from backend.database import get_product_by_slug, save_product

    saved = 0
    for p in products:
        try:
            if get_product_by_slug(p["slug"]):
                continue  # already exists, do not duplicate
            save_product(
                name=p["name"],
                slug=p["slug"],
                category=p["category"],
                short_description=p.get("short_description", ""),
                price=p.get("price", ""),
                media_url=media_url,
                media_type="document" if media_url else "image",
            )
            saved += 1
        except Exception as e:
            logger.error(f"PDF_PRODUCT_SAVE_FAILED | {p.get('slug')}: {e}")
    logger.info(f"PDF_PRODUCTS_SAVED | count={saved}")
    return saved
