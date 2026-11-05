"""
📄 Brochure Management Service

This service provides intelligent brochure selection based on:
- User queries and intent detection
- Button interactions
- Content relevance matching
- Metadata-based filtering

Features:
- Multiple brochure support per tenant
- Query-based relevance matching
- Metadata extraction from PDFs/documents
- Fallback to most recent brochure when no match found
"""

import os
import logging
import re
from typing import List, Dict, Optional, Any
from datetime import datetime
import difflib
from contextlib import contextmanager

logger = logging.getLogger("brochure_service")


# 🎯 Brochure types/modules that are supported
BROCHURE_MODULES = [
    "catalogue",           # Main product/service brochure
    "new_arrival",        # New arrivals brochure
    "product_brochure",   # Specific product brochures
    "service_brochure",   # Service-specific brochures
    "company_profile",    # Company profile/overview
    "technical_brochure", # Technical specifications
    "pricing",            # Pricing information
    "customer_case_study", # Case studies & testimonials
    "industry_brochure", # Industry-specific brochures
    "general",            # General brochure (fallback)
]


class Brochure:
    """Represents a brochure with metadata for relevance matching."""
    
    def __init__(self, file_id: int, tenant_id: str, module: str, 
                 name: str, filename: str, url: str, file_path: str = None,
                 ext: str = None, size: int = 0, doc_id: int = None,
                 created_at: str = None, metadata: Dict = None):
        self.file_id = file_id
        self.tenant_id = tenant_id
        self.module = module.lower()
        self.name = name
        self.filename = filename
        self.url = url
        self.file_path = file_path
        self.ext = ext
        self.size = size
        self.doc_id = doc_id
        self.created_at = created_at
        
        # Extract metadata from filename and name if not provided
        self.metadata = metadata or self._extract_metadata_from_filename()
        self.metadata.update(self._extract_metadata_from_name(name))
        
        # Set relevance keywords
        self.keywords = self._generate_keywords()
        
    def _extract_metadata_from_filename(self) -> Dict:
        """Extract metadata from the filename."""
        metadata = {}
        
        # Remove extension
        filename_no_ext = os.path.splitext(self.filename)[0]
        
        # Extract potential metadata from filename patterns
        patterns = [
            # "ProductName_Brochure_2024.pdf" -> ProductName
            r'([A-Za-z\s]+)_?Brochure',
            # "Company_Profile_2024.pdf" -> Company Profile
            r'([A-Za-z\s]+)_?Profile', 
            # "Technical_Specs_v2.pdf" -> Technical Specs
            r'([A-Za-z\s]+)_?Specs',
            # "Pricing_Guide.pdf" -> Pricing Guide
            r'([A-Za-z\s]+)_?Guide',
            # "IndustryName_Catalog.pdf" -> IndustryName
            r'([A-Za-z\s]+)_?Catalog',
        ]
        
        for pattern in patterns:
            match = re.match(pattern, filename_no_ext, re.IGNORECASE)
            if match:
                metadata['title'] = match.group(1)
                break
        
        # Try to extract year from filename
        year_match = re.search(r'(19|20)\d{2}', filename_no_ext)
        if year_match:
            metadata['year'] = year_match.group(0)
        
        return metadata
    
    def _extract_metadata_from_name(self, name: str) -> Dict:
        """Extract metadata from the display name."""
        metadata = {}
        
        # If name contains metadata-like content
        if name:
            # Check for common patterns
            name_lower = name.lower()
            
            if any(word in name_lower for word in ['catalogue', 'catalog']):
                metadata['type'] = 'catalogue'
            elif any(word in name_lower for word in ['brochure']):
                metadata['type'] = 'brochure'
            elif any(word in name_lower for word in ['profile']):
                metadata['type'] = 'profile'
            elif any(word in name_lower for word in ['pricing', 'price']):
                metadata['type'] = 'pricing'
            elif any(word in name_lower for word in ['technical', 'specs', 'specification']):
                metadata['type'] = 'technical'
            elif any(word in name_lower for word in ['case study', 'testimonial']):
                metadata['type'] = 'case_study'
        
        return metadata
    
    def _generate_keywords(self) -> List[str]:
        """Generate keywords for relevance matching."""
        keywords = []
        
        # Add module-based keywords
        module_keywords = {
            'catalogue': ['catalog', 'catalogue', 'products', 'product list', 'offerings'],
            'new_arrival': ['new arrivals', 'new products', 'latest', 'recent', 'new'],
            'product_brochure': ['product brochure', 'product info', 'product details'],
            'service_brochure': ['services', 'service info', 'service details'],
            'company_profile': ['company', 'profile', 'about us', 'overview'],
            'technical_brochure': ['technical', 'specifications', 'specs', 'tech'],
            'pricing': ['pricing', 'price list', 'rates', 'cost'],
            'customer_case_study': ['case study', 'testimonial', 'customer story'],
            'industry_brochure': ['industry', 'sector', 'vertical'],
            'general': ['brochure', 'information', 'details'],
        }
        
        if self.module in module_keywords:
            keywords.extend(module_keywords[self.module])
        
        # Add filename-based keywords
        if self.filename:
            filename_no_ext = os.path.splitext(self.filename)[0]
            filename_parts = re.split(r'[\-_\s]', filename_no_ext)
            for part in filename_parts:
                if part and len(part) > 2:  # Skip very short parts
                    keywords.append(part.lower())
        
        # Add name-based keywords
        if self.name:
            name_parts = re.split(r'[\-_\s]', self.name)
            for part in name_parts:
                if part and len(part) > 2:
                    keywords.append(part.lower())
        
        # Add metadata-based keywords
        if self.metadata:
            for key, value in self.metadata.items():
                if isinstance(value, str):
                    keywords.append(value.lower())
        
        # Deduplicate keywords
        keywords = list(set(keywords))
        
        return keywords
    
    def matches_query(self, query: str, threshold: float = 0.6) -> float:
        """
        Calculate how well this brochure matches the given query.
        Returns a score between 0 and 1.
        """
        if not query or not query.strip():
            return 0.0
        
        query_lower = query.lower().strip()
        
        # Perfect match scenarios
        if self.module in query_lower:
            return 1.0
        
        if self.name and self.name.lower() in query_lower:
            return 1.0
        
        if self.filename and self.filename.lower() in query_lower:
            return 1.0
        
        # Keyword-based matching
        query_words = set(re.split(r'[\s\-_]+', query_lower))
        query_words.discard('')  # Remove empty strings
        
        # Entity-level project matching (e.g. Arbour, Dahlias, Privana, Midtown)
        filename_tokens = set(re.split(r'[\-_\s.]+', (self.filename or self.name or "").lower()))
        generic_tokens = {'dlf', 'brochure', 'pdf', 'project', 'deck', 'luxury', 'residences', 'the', 'portfolio', 'super', 'new', 'general', 'info'}
        specific_tokens = filename_tokens - generic_tokens
        specific_hits = query_words.intersection(specific_tokens)
        if specific_hits:
            return 0.95

        matching_keywords = query_words.intersection(set(self.keywords))
        
        if matching_keywords:
            # Score based on percentage of query words matched
            match_ratio = len(matching_keywords) / len(query_words)
            # Boost based on module relevance
            module_boost = 0.2 if self.module in BROCHURE_MODULES else 0.0
            return min(1.0, match_ratio + module_boost)
        
        # Fallback: Use sequence matching for partial matches
        for keyword in self.keywords:
            if keyword in query_lower:
                return 0.4
        
        return 0.0


class BrochureService:
    """
    Main service for managing and retrieving relevant brochures.
    """
    
    def __init__(self):
        self.brochure_cache = {}
    
    def clear_cache(self, tenant_id: str = None) -> None:
        """Drop cached brochure lists so a fresh upload is visible immediately."""
        if tenant_id:
            self.brochure_cache.pop(tenant_id, None)
        else:
            self.brochure_cache.clear()

    def get_all_brochures(self, tenant_id: str = None) -> List[Brochure]:
        """Get all brochures for a specific tenant or globally."""
        try:
            cache_key = tenant_id or "global"
            if cache_key in self.brochure_cache and self.brochure_cache[cache_key]:
                return self.brochure_cache[cache_key]
            
            brochures = []
            seen_ids = set()

            def _append_row(row) -> None:
                rid = row["id"] if "id" in row.keys() else None
                if rid is not None and rid in seen_ids:
                    return
                if rid is not None:
                    seen_ids.add(rid)
                raw_name = row["name"] or ""
                raw_filename = ""
                try:
                    raw_filename = row["filename"] or ""
                except Exception:
                    raw_filename = ""
                display = raw_name or raw_filename or "Brochure.pdf"
                brochures.append(Brochure(
                    file_id=row["id"],
                    tenant_id=row["tenant_id"] or "",
                    module=row["module"] or "general",
                    name=raw_name or display,
                    filename=display,
                    url=row["url"] or "",
                    file_path=row["file_path"] or "",
                    ext=row["ext"] or "",
                    size=row["size"] or 0,
                    doc_id=row["doc_id"] or None,
                    created_at=row["created_at"] or "",
                ))
            
            # Only dedicated brochure modules (Uploads → brochure /brochures/upload).
            # Option A/B onboarding kit PDFs are stored as module=kb and must never
            # be returned here — even if the filename contains "brochure".
            brochure_modules = tuple(BROCHURE_MODULES) + ("brochure",)
            with self._get_db_context() as conn:
                placeholders = ", ".join(["?"] * len(brochure_modules))
                
                if tenant_id:
                    sql = (
                        f"SELECT * FROM admin_files WHERE tenant_id = ? "
                        f"AND module IN ({placeholders}) ORDER BY created_at DESC"
                    )
                    rows = conn.execute(sql, (tenant_id,) + brochure_modules).fetchall()
                else:
                    sql = (
                        f"SELECT * FROM admin_files WHERE tenant_id IS NULL "
                        f"AND module IN ({placeholders}) ORDER BY created_at DESC"
                    )
                    rows = conn.execute(sql, brochure_modules).fetchall()
                
                for row in rows:
                    _append_row(row)
            
            # Profile-configured brochure URL (admin setting), still a real upload/link.
            if not brochures and tenant_id:
                try:
                    from shared.tenancy.loader import get_tenant_profile
                    from shared.tenancy.resolver import resolve_tenant_for_user
                    tid = resolve_tenant_for_user(tenant_id) or tenant_id
                    profile = get_tenant_profile(tid)
                    url = (getattr(getattr(profile, "notifications", None), "brochure_url", "") or "").strip()
                    label = (getattr(getattr(profile, "notifications", None), "brochure_label", "") or "").strip() or "Brochure.pdf"
                    if url:
                        brochures.append(Brochure(
                            file_id=0,
                            tenant_id=tid,
                            module="catalogue",
                            name=label,
                            filename=label if label.endswith(".pdf") else f"{label}.pdf",
                            url=url,
                        ))
                except Exception as exc:
                    logger.debug(f"profile brochure fallback failed: {exc}")

            # Intentionally NO product-row fallback: Explore Offerings sends
            # product image+text cards; brochure asks must only send uploaded PDFs.

            # Cache the result
            self.brochure_cache[cache_key] = brochures
            return brochures
            
        except Exception as e:
            logger.error(f"Failed to get brochures for tenant {tenant_id}: {e}")
            return []
    
    def get_relevant_brochure(self, query: str, tenant_id: str = None, 
                           button_id: str = None) -> Optional[Brochure]:
        """
        Get the most relevant brochure for a query or button press.
        
        Args:
            query: The user's query/text
            tenant_id: The tenant ID to filter brochures
            button_id: The button press ID (if applicable)
            
        Returns:
            The most relevant Brochure or None
        """
        # If button press, try to match button ID to module
        if button_id:
            module = self._button_id_to_module(button_id)
            if module:
                brochure = self.get_brochure_by_module(module, tenant_id)
                if brochure:
                    return brochure
        
        # Get all brochures for this tenant
        brochures = self.get_all_brochures(tenant_id)
        
        if not brochures:
            # Fallback to global brochures
            brochures = self.get_all_brochures()
        
        if not brochures:
            logger.warning(f"No brochures found for tenant {tenant_id}")
            return None
        
        # Find the best match based on query
        best_brochure = None
        best_score = 0.0
        
        for brochure in brochures:
            score = brochure.matches_query(query)
            if score > best_score:
                best_score = score
                best_brochure = brochure
        
        # If we have a good match, return it
        if best_score >= 0.4:
            return best_brochure
        
        # If no good match found, return the most recent brochure
        return brochures[0] if brochures else None

    @staticmethod
    def categorize_query(query: str) -> str:
        """Categorize a query to determine the most likely brochure type needed."""
        return categorize_query(query)
    
    def get_brochure_by_module(self, module: str, tenant_id: str = None) -> Optional[Brochure]:
        """Get a specific brochure by its module type."""
        module = module.lower()
        
        try:
            with self._get_db_context() as conn:
                if tenant_id:
                    row = conn.execute(
                        """SELECT * FROM admin_files 
                           WHERE tenant_id = ? AND module = ?
                           ORDER BY created_at DESC LIMIT 1""",
                        (tenant_id, module)
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT * FROM admin_files 
                           WHERE tenant_id IS NULL AND module = ?
                           ORDER BY created_at DESC LIMIT 1""",
                        (module,)
                    ).fetchone()
                
                if row:
                    display = row["name"] or f"{module}_brochure.pdf"
                    return Brochure(
                        file_id=row["id"],
                        tenant_id=row["tenant_id"] or "",
                        module=row["module"] or module,
                        name=row["name"] or display,
                        filename=display,
                        url=row["url"] or "",
                        file_path=row["file_path"] or "",
                        ext=row["ext"] or "",
                        size=row["size"] or 0,
                        doc_id=row["doc_id"] or None,
                        created_at=row["created_at"] or "",
                    )
        
        except Exception as e:
            logger.error(f"Failed to get brochure by module {module} for tenant {tenant_id}: {e}")
        
        return None
    
    def get_brochures_by_category(self, category: str, tenant_id: str = None) -> List[Brochure]:
        """Get brochures that match a specific category or topic."""
        category = category.lower()
        all_brochures = self.get_all_brochures(tenant_id)
        
        relevant_brochures = []
        
        for brochure in all_brochures:
            # Check if category matches module
            if category in brochure.module:
                relevant_brochures.append(brochure)
            # Check if category matches any keywords
            elif any(category in keyword for keyword in brochure.keywords):
                relevant_brochures.append(brochure)
            # Check if category matches filename or name
            elif (brochure.filename and category in brochure.filename.lower()) or \
                 (brochure.name and category in brochure.name.lower()):
                relevant_brochures.append(brochure)
        
        # Sort by relevance (exact module match first, then keyword matches)
        relevant_brochures.sort(
            key=lambda b: (0 if category in b.module else 1, -len(b.keywords))
        )
        
        return relevant_brochures
    
    def _button_id_to_module(self, button_id: str) -> Optional[str]:
        """Convert brochure menu button IDs to modules.

        Browse buttons (menu_products / menu_services / …) must NOT map here —
        those open image+text offering cards, not the PDF.
        """
        button_module_map = {
            "menu_brochure": "catalogue",
            "menu_catalogue": "catalogue",
            "menu_new_arrivals": "new_arrival",
            "menu_company_profile": "company_profile",
            "menu_technical": "technical_brochure",
            "menu_pricing": "pricing",
            "menu_case_studies": "customer_case_study",
            "menu_pdf": "catalogue",
        }
        
        return button_module_map.get(button_id.lower())
    
    def _get_db_context(self):
        """Get database context."""
        try:
            from backend.database import get_db_context
            return get_db_context()
        except ImportError:
            try:
                from database import get_db_context
                return get_db_context()
            except ImportError:
                # Fallback to direct import
                import sqlite3
                from pathlib import Path
                db_path = Path(__file__).parent.parent / "faq.db"
                
                def temp_db_context():
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    try:
                        yield conn
                        conn.commit()
                    except:
                        conn.rollback()
                        raise
                    finally:
                        conn.close()
                
                return temp_db_context()


# 🎯 Query categorization for better brochure matching
QUERY_CATEGORIES = {
    # Catalogue/Product related queries
    'catalogue': [
        'catalog', 'catalogue', 'products', 'product list', 'offerings',
        'what do you offer', 'what products', 'show products', 'brochure'
    ],
    
    # New arrivals
    'new_arrival': [
        'new arrivals', 'new products', 'latest products', 'recent additions',
        'what is new', 'new releases', 'latest', 'recent'
    ],
    
    # Company information
    'company_profile': [
        'company profile', 'about us', 'about the company', 'who are you',
        'company information', 'company overview', 'company details',
        'about your company', 'tell me about', 'your company', 'company history'
    ],
    
    # Technical information
    'technical_brochure': [
        'technical specs', 'specs', 'specifications', 'technical details',
        'technical information', 'product specifications', 'features',
        'technical specifications', 'detailed specs', 'technical features'
    ],
    
    # Pricing information
    'pricing': [
        'pricing', 'price list', 'prices', 'cost', 'rates',
        'how much', 'pricing information', 'rate card', 'price guide',
        'cost list', 'pricing details'
    ],
    
    # Service information
    'service_brochure': [
        'services', 'service list', 'what services', 'service offerings',
        'service details', 'service information', 'your services',
        'service brochures', 'service catalog'
    ],
    
    # Case studies
    'customer_case_study': [
        'case studies', 'case study', 'success stories', 'testimonials',
        'customer stories', 'customer examples', 'use cases',
        'customer case study', 'case studies examples'
    ],
}


def categorize_query(query: str) -> str:
    """Categorize a query to determine the most likely brochure type needed."""
    query_lower = query.lower()
    
    for category, keywords in QUERY_CATEGORIES.items():
        for keyword in keywords:
            if keyword in query_lower:
                return category
    
    # Default fallback
    return "catalogue"  # Most common request


def extract_file_metadata(file_path: str) -> Dict[str, str]:
    """
    Extract metadata from an uploaded file.
    Currently supports basic filename analysis - can be extended for PDF parsing.
    """
    metadata = {
        'title': '',
        'description': '',
        'keywords': [],
        'category': '',
        'language': 'en',
    }
    
    # Extract from filename
    filename = os.path.basename(file_path)
    filename_no_ext = os.path.splitext(filename)[0]
    
    # Try to extract meaningful parts
    parts = re.split(r'[\-_\s]', filename_no_ext)
    
    if len(parts) >= 1:
        metadata['title'] = ' '.join(parts[:2]).title()
    
    if len(parts) >= 3:
        metadata['description'] = ' '.join(parts[2:]).title()
    
    # Detect category from filename
    category_keywords = {
        'catalogue': ['catalog', 'catalogue', 'products'],
        'profile': ['profile', 'about', 'company'],
        'technical': ['technical', 'specs', 'specification'],
        'pricing': ['pricing', 'price', 'rates'],
        'services': ['services', 'service'],
        'new': ['new', 'arrivals', 'releases'],
    }
    
    filename_lower = filename_no_ext.lower()
    for category, keywords in category_keywords.items():
        for keyword in keywords:
            if keyword in filename_lower:
                metadata['category'] = category
                break
    
    metadata['keywords'] = parts
    
    return metadata


# 🚀 Global service instance
brochure_service = BrochureService()


# Menu / interactive ids that mean "send the uploaded brochure PDF".
_BROCHURE_MENU_IDS = frozenset({
    "menu_brochure",
    "menu_catalogue",
    "menu_catalog",
    "menu_pdf",
    "menu_new_arrivals",
    "menu_company_profile",
})

_BROCHURE_INTENTS = frozenset({
    "brochure_request",
    "catalogue_request",
    "new_arrivals",
})

_BROCHURE_FEATURES = frozenset({
    "brochure_pdf",
    "catalogue_pdf",
})


def _normalize_ask(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _brochure_button_keys(tenant_id: str = None) -> set:
    """Ids and titles of profile menu rows that should send the brochure PDF."""
    keys: set = set(_BROCHURE_MENU_IDS)
    if not tenant_id:
        return keys
    try:
        from shared.tenancy.loader import get_tenant_profile
        profile = get_tenant_profile(tenant_id)
        buttons = getattr(getattr(profile, "menu", None), "buttons", None) or []
    except Exception as exc:
        logger.debug("brochure button keys unavailable for %s: %s", tenant_id, exc)
        return keys
    for button in buttons:
        intent = (getattr(button, "intent", None) or "").strip()
        feature = (getattr(button, "requires_feature", None) or "").strip()
        bid = (getattr(button, "id", None) or "").strip()
        title = (getattr(button, "title", None) or "").strip()
        if (
            bid in _BROCHURE_MENU_IDS
            or intent in _BROCHURE_INTENTS
            or feature in _BROCHURE_FEATURES
        ):
            if bid:
                keys.add(bid)
                keys.add(_normalize_ask(bid))
            if title:
                keys.add(title)
                keys.add(_normalize_ask(title))
    return keys


def is_brochure_ask(text: str = None, button_id: str = None, tenant_id: str = None) -> bool:
    """True when the user tapped/typed a request for the uploaded brochure/PDF.

    Explore Offerings / product browse must NOT match — those send image cards.
    """
    raw_id = (button_id or "").strip()
    raw_text = (text or "").strip()

    # send2 often delivers the row id as plain text
    if not raw_id and raw_text.startswith("menu_"):
        raw_id = raw_text

    keys = _brochure_button_keys(tenant_id)
    if raw_id and (raw_id in keys or _normalize_ask(raw_id) in keys):
        return True
    if raw_text and (raw_text in keys or _normalize_ask(raw_text) in keys):
        return True

    try:
        from routing.message import _wants_catalogue_pdf
        if _wants_catalogue_pdf(raw_text):
            return True
    except Exception:
        # deliberate: the catalogue-PDF cross-check is best-effort — the
        # local checks above already ran.
        logger.debug("CATALOGUE_PDF_CHECK_SKIPPED", exc_info=True)

    # Narrow typed fallback (avoid stealing "products" / "offerings" browse).
    needle = _normalize_ask(raw_text)
    if not needle or len(needle) > 60:
        return False
    exact = {
        "pdf", "brochure", "brochures", "catalogue", "catalog",
        "brochure pdf", "catalogue pdf", "catalog pdf", "send brochure",
        "share brochure", "send pdf", "project brochure", "price list",
    }
    if needle in exact:
        return True
    if any(tok in needle for tok in ("brochure", "catalogue pdf", "catalog pdf")) and not any(
        bad in needle for bad in ("offering", "product", "package", "propert", "service")
    ):
        # "send me the brochure" / "need brochure" — but not "brochure of products explore"
        if any(v in needle for v in ("send", "share", "give", "get", "need", "want", "download", "pdf")):
            return True
        if needle.count(" ") <= 2 and "brochure" in needle:
            return True
    return False


# 🎯 Convenience Functions

def get_relevant_brochure(query: str = None, tenant_id: str = None, 
                        button_id: str = None) -> Optional[Brochure]:
    """
    Get the most relevant brochure for a query, tenant, or button press.
    """
    return brochure_service.get_relevant_brochure(query, tenant_id, button_id)


def get_brochure_url(query: str = None, tenant_id: str = None, 
                    button_id: str = None) -> Optional[str]:
    """
    Get the URL of the most relevant brochure.
    """
    brochure = get_relevant_brochure(query, tenant_id, button_id)
    return brochure.url if brochure else None


def get_brochures_by_category(category: str, tenant_id: str = None) -> List[Brochure]:
    """
    Get brochures for a specific category.
    """
    return brochure_service.get_brochures_by_category(category, tenant_id)


def send_relevant_brochure(wa_id: str, query: str = None, tenant_id: str = None,
                          button_id: str = None) -> bool:
    """
    Send the most relevant brochure to a WhatsApp contact.
    Returns True if brochure was sent successfully.
    """
    # Always re-read after uploads; empty caches used to hide new PDFs.
    brochure_service.clear_cache(tenant_id)
    brochure = get_relevant_brochure(query, tenant_id, button_id)

    # Last-resort fallback used by older catalogue helpers.
    if (not brochure or not brochure.url) and tenant_id:
        try:
            from kb.services.whatsapp import get_catalogue_doc
            doc = get_catalogue_doc(tenant_id=tenant_id)
            if doc and doc.get("url"):
                brochure = Brochure(
                    file_id=0,
                    tenant_id=tenant_id,
                    module="catalogue",
                    name=doc.get("filename") or "Catalogue.pdf",
                    filename=doc.get("filename") or "Catalogue.pdf",
                    url=doc["url"],
                )
        except Exception as exc:
            logger.debug("get_catalogue_doc fallback failed: %s", exc)
    
    if brochure and brochure.url:
        try:
            from kb.services.whatsapp import send_document_with_caption
            caption = f"Here is the {brochure.name or 'requested brochure'}."
            ok = send_document_with_caption(
                to=wa_id,
                document_url=brochure.url,
                filename=brochure.filename,
                caption=caption
            )
            logger.info(
                "BROCHURE_SENT | wa_id=%s | tenant=%s | file=%s | ok=%s",
                wa_id, tenant_id, brochure.filename, ok,
            )
            return bool(ok)
        except Exception as e:
            logger.error(f"Failed to send brochure to {wa_id}: {e}")
            return False
    
    logger.warning("BROCHURE_NOT_FOUND | wa_id=%s | tenant=%s | query=%r", wa_id, tenant_id, query)
    return False
