"""Intelligent Document Auto-Bifurcation & Auto-Configuration Engine.

Accepts multi-format uploaded documents (PDF, DOCX, XLSX, CSV, TXT, MD), parses their
contents, semantically bifurcates the information into:
  1. Brand & Profile Data (Name, Bot persona, Tagline, Tone, Contact, Hours, Vertical)
  2. WhatsApp Interactive Navigation Menu (Buttons/Drawers <= 24 title / 72 desc chars)
  3. Conversational Intents & Flow Triggers (Keywords, Utterances, Action Mappings)
  4. Offerings & Catalog Data (Products, Services, Packages, Properties with Pricing)
  5. Knowledge Base & FAQ Documents (Markdown articles, Chunks, Vector RAG Embeddings)

Automatically configures and publishes all components into the tenant configuration,
database tables, client pack (clients/<tenant_id>/config.json), and FAISS vector index,
eliminating manual data entry and configuration work.
"""

import csv
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import string
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # PyMuPDF
from docx import Document
import openpyxl

from database import (
    get_db_context,
    list_products,
    product_slug_taken,
    record_admin_audit_event,
    save_admin_file,
    save_chunks,
    save_knowledge_base_chunks,
    save_product,
    update_product,
)  # save_chunks used for FAQ Q&A persistence
from shared.tenancy import cache as tenancy_cache
from shared.tenancy import loader as tenancy_loader
from shared.tenancy import schemas as tenancy_schemas
from shared.tenancy import store as tenancy_store
from shared.tenancy.defaults import get_vertical_defaults
from shared.tenancy.paths import BACKEND_CLIENTS_DIR
from services.kit_profile_extract import (
    fields_to_brand_bundle,
    merge_operations_into_draft,
    merge_profile_extras_into_draft,
    parse_brand_kit_tables,
    parse_guardrails_kit_tables,
    parse_operations_domain_tables,
)
from services.bifurcation_guards import (
    filter_faqs_for_publish,
    filter_offerings_for_tenant,
    is_placeholder_brand_value,
    merge_brand_fill_empty,
    normalise_qa_key,
    placeholder_warnings,
    qa_key_from_content,
    resolve_locked_vertical,
)

logger = logging.getLogger("auto_bifurcator")

# Target vertical options supported by tenancy schema
VALID_VERTICALS = set(tenancy_schemas.VERTICALS)

# Brochure modules recognized by brochure_service (destination for
# brochure/profile/menu/price-list documents)
BROCHURE_MODULES = {
    "catalogue",
    "new_arrival",
    "product_brochure",
    "service_brochure",
    "company_profile",
    "technical_brochure",
    "pricing",
    "customer_case_study",
    "industry_brochure",
    "general",
}

# Document types the LLM/heuristics may assign; brochure-ish types are
# auto-registered as sendable brochure PDFs for the tenant.
BROCHURE_DOC_TYPES = {
    "brochure": "catalogue",
    "company_profile": "company_profile",
    "menu": "pricing",
    "price_list": "pricing",
    "catalog": "catalogue",
    "technical_brochure": "technical_brochure",
    "case_study": "customer_case_study",
}

# Public file host used to expose brochure PDFs over WhatsApp
CATBOX_API_URL = os.environ.get("CATBOX_API_URL", "https://catbox.moe/user/api.php")

# ──────────────────────────────────────────────────────────────────────────
# OCR setup (scanned / image-only PDFs and image files)
# ──────────────────────────────────────────────────────────────────────────
try:
    import pytesseract
    from PIL import Image
except ImportError:  # pragma: no cover - OCR optional
    pytesseract = None
    Image = None

TESSERACT_CMD = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
if not TESSERACT_CMD:
    _default_windows_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if os.path.exists(_default_windows_path):
        TESSERACT_CMD = _default_windows_path

if TESSERACT_CMD and pytesseract:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    logger.info("OCR enabled | tesseract=%s", TESSERACT_CMD)
else:
    logger.warning(
        "OCR unavailable - scanned/image-only PDFs will yield little or no text. "
        "Set TESSERACT_CMD to the tesseract executable to enable OCR."
    )

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp")
MAX_OCR_PAGES = 20


# ──────────────────────────────────────────────────────────────────────────
# Pure text-extraction helpers were split to services/bifurcation_extract.py;
# re-imported here so existing `from services.auto_bifurcator import ...`
# (and monkeypatching of this module's globals) keeps working unchanged.
# ──────────────────────────────────────────────────────────────────────────
from services.bifurcation_extract import (  # noqa: E402,F401
    VALID_VERTICALS,
    _CHUNK_CHARS,
    _CHUNK_OVERLAP,
    _bifurcation_prompt_template,
    _chunk_text,
    _clean_text,
    _dedupe_key_article,
    _dedupe_key_faq,
    _dedupe_key_intent,
    _dedupe_key_offering,
    _ensure_free_slug,
    _extract_markdown_table_rows,
    _guess_vertical,
    _is_placeholder_brand_value,
    _menu_button_id,
    _menu_items_from_vertical_defaults,
    _normalize_menu_button_key,
    _normalize_phone,
    _sanitize_business_hours,
    _sanitize_menu_buttons,
    _section_body,
    _slugify,
    heuristic_extract_corpus,
    merge_bifurcation_results,
)


def _menu_replacement_tombstones(
    menu_buttons: List[Dict[str, Any]],
    previous_buttons: List[Dict[str, Any]],
    defaults: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Remove stale menu entries when an uploaded menu adds PDF-specific options."""
    default_ids = {
        _normalize_menu_button_key(str(button.get("id") or ""))
        for button in defaults
        if isinstance(button, dict) and button.get("id")
    }
    default_titles = {
        _normalize_menu_button_key(str(button.get("title") or ""))
        for button in defaults
        if isinstance(button, dict) and button.get("title")
    }
    has_pdf_specific_options = any(
        _normalize_menu_button_key(str(button.get("id") or "")) not in default_ids
        and _normalize_menu_button_key(str(button.get("title") or "")) not in default_titles
        for button in menu_buttons
        if isinstance(button, dict)
    )
    if not has_pdf_specific_options:
        return []

    active_ids = {
        _normalize_menu_button_key(str(button.get("id") or ""))
        for button in menu_buttons
        if isinstance(button, dict) and button.get("id")
    }
    removals: List[Dict[str, Any]] = []
    seen_ids = set(active_ids)
    for button in [*defaults, *previous_buttons]:
        if not isinstance(button, dict):
            continue
        button_id = str(button.get("id") or "").strip()
        normalized_id = _normalize_menu_button_key(button_id)
        if not button_id or normalized_id in seen_ids:
            continue
        seen_ids.add(normalized_id)
        removals.append({"id": button_id, "__remove__": True})
    return removals


def _attach_pdf_images_to_offerings(
    offerings: List[Dict[str, Any]],
    extracted_docs: List[Dict[str, Any]],
) -> None:
    """Host embedded PDF product images and attach them to offering records.

    Matching rules (in order):
      1. Exact 1 image + 1 named offering on the page
      2. Same count of page images and named offerings → zip by visual
         reading order (top→bottom, left→right) — covers catalog pages
         like MakeMyTrip kits with 6 packages + 6 photos
      3. Otherwise skip with a warning (ambiguous layout)
    """
    if not offerings:
        return
    try:
        from services.image_upload import _upload_to_imghippo
    except ImportError:
        from backend.services.image_upload import _upload_to_imghippo

    for source in extracted_docs:
        content = source.get("_content")
        if not content or str(source.get("ext") or "").lower() not in (".pdf", "pdf"):
            continue

        doc = fitz.open(stream=content, filetype="pdf")
        uploaded_by_xref: Dict[int, str] = {}
        try:
            for page_number, page in enumerate(doc, start=1):
                page_text = (page.get_text("text") or "").casefold()
                page_offerings = [
                    offering
                    for offering in offerings
                    if isinstance(offering, dict)
                    and str(offering.get("name") or "").strip()
                    and str(offering["name"]).strip().casefold() in page_text
                ]
                page_offerings.sort(
                    key=lambda offering: page_text.find(
                        str(offering.get("name") or "").strip().casefold()
                    )
                )
                if not page_offerings:
                    continue

                page_images = []
                seen_xrefs = set()
                for image_index, image_info in enumerate(page.get_images(full=True)):
                    xref = int(image_info[0])
                    if xref in seen_xrefs:
                        continue
                    seen_xrefs.add(xref)
                    extracted = doc.extract_image(xref)
                    if not extracted:
                        continue
                    if int(extracted.get("width") or 0) < 120 or int(extracted.get("height") or 0) < 120:
                        continue
                    try:
                        rects = page.get_image_rects(xref) or []
                    except Exception:
                        rects = []
                    if rects:
                        y0 = min(float(r.y0) for r in rects)
                        x0 = min(float(r.x0) for r in rects)
                    else:
                        y0, x0 = float(image_index), 0.0
                    page_images.append((y0, x0, xref, image_index, extracted))

                if not page_images:
                    continue
                # Visual reading order so catalog grids zip correctly.
                page_images.sort(key=lambda row: (round(row[0] / 20), row[1], row[3]))
                ordered_images = [
                    (xref, image_index, extracted)
                    for _y, _x, xref, image_index, extracted in page_images
                ]

                if len(page_offerings) == 1 and len(ordered_images) == 1:
                    matches = [(page_offerings[0], ordered_images[0])]
                elif len(page_offerings) == len(ordered_images) and len(ordered_images) > 0:
                    matches = list(zip(page_offerings, ordered_images))
                    logger.info(
                        "PDF_IMAGE_ZIP_MATCH | file=%s | page=%d | pairs=%d",
                        source.get("filename"),
                        page_number,
                        len(matches),
                    )
                else:
                    logger.warning(
                        "PDF_IMAGE_MATCH_SKIPPED | file=%s | page=%d | images=%d | products=%d",
                        source.get("filename"),
                        page_number,
                        len(ordered_images),
                        len(page_offerings),
                    )
                    continue

                for offering, (xref, image_index, extracted) in matches:
                    current_url = str(offering.get("media_url") or "").strip()
                    if current_url.startswith(("http://", "https://")):
                        continue
                    url = uploaded_by_xref.get(xref)
                    if not url:
                        extension = str(extracted.get("ext") or "jpg").lower()
                        if extension == "jpeg":
                            extension = "jpg"
                        filename = (
                            f"{Path(str(source.get('filename') or 'document')).stem}"
                            f"_p{page_number}_{image_index}.{extension}"
                        )
                        try:
                            uploaded_url = _upload_to_imghippo(extracted["image"], filename)
                        except Exception as upload_err:
                            logger.warning(
                                "PDF_IMAGE_UPLOAD_FAILED | file=%s | err=%s",
                                filename,
                                upload_err,
                            )
                            continue
                        url = tenancy_schemas.extract_first_url(uploaded_url or "")
                        if not url:
                            raise RuntimeError(
                                f"Extracted PDF image {filename} has no public URL "
                                "(ImageHipo / PUBLIC_BASE_URL)"
                            )
                        uploaded_by_xref[xref] = url
                        logger.info(
                            "PDF_IMAGE_HOSTED | offering=%r | url=%s",
                            offering.get("name"),
                            url[:120],
                        )
                    offering["media_url"] = url
                    offering["media_type"] = "image"
        finally:
            doc.close()


class AutoBifurcator:
    """Core auto-bifurcation and auto-configuration pipeline."""

    def __init__(self, tenant_id: str, actor: Optional[dict] = None):
        self.tenant_id = tenant_id.strip().lower()
        self.actor = actor or {
            "username": "auto_bifurcator",
            "role": "super_admin",
            "tenant_id": self.tenant_id,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Multi-Format Text & Table Extraction
    # ──────────────────────────────────────────────────────────────────────────

    def extract_file_content(self, filename: str, content: bytes) -> Dict[str, Any]:
        """Extract text and tables from PDF, DOCX, XLSX, CSV, TXT, MD."""
        ext = Path(filename).suffix.lower()
        extracted_text = ""
        tables_found: List[List[List[str]]] = []

        try:
            if ext == ".pdf":
                doc = fitz.open(stream=content, filetype="pdf")
                pages_text = []
                ocr_pages_used = 0
                for pno in range(len(doc)):
                    page = doc[pno]
                    txt = page.get_text("text") or ""
                    # Scanned / image-only pages: fall back to OCR when native text is thin.
                    if len(txt.strip()) < 30 and pytesseract and Image and ocr_pages_used < MAX_OCR_PAGES:
                        try:
                            pix = page.get_pixmap(dpi=200)
                            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                            ocr_txt = (pytesseract.image_to_string(img) or "").strip()
                            if len(ocr_txt) > len(txt.strip()):
                                txt = ocr_txt
                                ocr_pages_used += 1
                        except Exception as ocr_err:
                            logger.debug("OCR skipped on page %d of %s: %s", pno + 1, filename, ocr_err)
                    if txt.strip():
                        pages_text.append(f"--- Page {pno + 1} ---\n{txt}")
                    try:
                        tabs = page.find_tables()
                        for tab in tabs:
                            extracted_tbl = tab.extract()
                            if extracted_tbl and len(extracted_tbl) > 1:
                                clean_tbl = [
                                    [str(c or "").strip().replace("\n", " ") for c in r]
                                    for r in extracted_tbl
                                ]
                                tables_found.append(clean_tbl)
                    except Exception as tab_err:
                        logger.debug("PDF find_tables page %d skipped: %s", pno, tab_err)
                extracted_text = "\n\n".join(pages_text)
                if ocr_pages_used:
                    logger.info("OCR used on %d page(s) for %s", ocr_pages_used, filename)

            elif ext in (".docx", ".doc"):
                doc_file = io.BytesIO(content)
                doc = Document(doc_file)
                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                # Extract tables
                table_texts = []
                for table in doc.tables:
                    t_rows = []
                    for row in table.rows:
                        row_cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                        t_rows.append(row_cells)
                    if t_rows:
                        tables_found.append(t_rows)
                        table_texts.append("\n".join([" | ".join(r) for r in t_rows]))
                extracted_text = "\n\n".join(paragraphs + table_texts)

            elif ext in (".xlsx", ".xls"):
                wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
                sheet_texts = []
                for sheetname in wb.sheetnames:
                    sheet = wb[sheetname]
                    rows = []
                    for row in sheet.iter_rows(values_only=True):
                        cells = [str(c).strip() if c is not None else "" for c in row]
                        if any(cells):
                            rows.append(" | ".join(cells))
                    if rows:
                        sheet_texts.append(f"=== Sheet: {sheetname} ===\n" + "\n".join(rows))
                extracted_text = "\n\n".join(sheet_texts)

            elif ext == ".csv":
                text_stream = io.StringIO(content.decode("utf-8", errors="ignore"))
                reader = csv.reader(text_stream)
                rows = [" | ".join(row) for row in reader if any(row)]
                extracted_text = "\n".join(rows)

            else:
                # Text or Markdown fallback
                try:
                    extracted_text = content.decode("utf-8")
                except UnicodeDecodeError:
                    extracted_text = content.decode("latin-1", errors="ignore")

        except Exception as exc:
            logger.error("Failed extracting %s: %s", filename, exc)
            extracted_text = f"[Text extraction error: {exc}]"

        extracted_text = _clean_text(extracted_text)
        return {
            "filename": filename,
            "ext": ext,
            "size": len(content),
            "char_count": len(extracted_text),
            "text": extracted_text,
            "tables": tables_found,
            "has_tables": len(tables_found) > 0,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # 2. LLM Semantic Bifurcation & Synthesis
    # ──────────────────────────────────────────────────────────────────────────

    def _bifurcation_system_prompt(self, focus: str = "full") -> str:
        """Shared JSON schema prompt; ``focus`` narrows extraction for multi-pass."""
        focus_note = {
            "full": "Extract EVERYTHING: brand, menu, intents, offerings, faqs, knowledge_articles.",
            "brand": "Focus on brand profile, vertical, business_hours, and WhatsApp menu_items. Leave offerings/faqs thin if needed.",
            "catalog": "Focus on offerings (every product/service/package) and knowledge_articles. Keep brand minimal.",
            "faq_info": "Focus on faqs (every Q&A) and intents (technologies/projects/careers/benefits with full answer text). Keep brand minimal.",
        }.get(focus, "Extract all fields you can from this chunk.")

        return (
            _bifurcation_prompt_template()
            .replace("<<TENANT_ID>>", self.tenant_id)
            .replace("<<FOCUS_NOTE>>", focus_note)
        )

    def bifurcate_with_llm(self, doc_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Bifurcate any document type via heuristics + multi-pass LLM (full corpus)."""
        combined_chunks = []
        for doc in doc_summaries:
            txt = doc.get("text") or ""
            combined_chunks.append(
                f"================ DOCUMENT: {doc.get('filename', 'doc')} ================\n{txt}"
            )
        full_corpus = "\n\n".join(combined_chunks)

        # Always run deterministic heuristics on the COMPLETE text (no truncation)
        heuristic = heuristic_extract_corpus(full_corpus, tenant_id=self.tenant_id)
        logger.info(
            "HEURISTIC_EXTRACT | tenant=%s | brand_keys=%s | offerings=%s | faqs=%s | intents=%s | articles=%s",
            self.tenant_id,
            list((heuristic.get("brand") or {}).keys()),
            len(heuristic.get("offerings") or []),
            len(heuristic.get("faqs") or []),
            len(heuristic.get("intents") or []),
            len(heuristic.get("knowledge_articles") or []),
        )

        windows = _chunk_text(full_corpus)
        if not windows:
            return merge_bifurcation_results(heuristic, tenant_id=self.tenant_id)

        llm_parts: List[Dict[str, Any]] = []
        # One full pass per window. The old brand+catalog+faq_info triple pass
        # made local Ollama take ~3–5+ minutes and looked like a hang.
        logger.info(
            "OLLAMA_BIFURCATION_PLAN | tenant=%s | windows=%s | passes=%s",
            self.tenant_id,
            len(windows),
            len(windows),
        )
        for idx, window in enumerate(windows):
            system_prompt = self._bifurcation_system_prompt(focus="full")
            user_prompt = (
                f"This is chunk {idx + 1} of {len(windows)} from uploaded client collateral.\n"
                f"Extract per focus='full'. Merge-safe partial JSON is OK.\n\n{window}"
            )
            logger.info(
                "OLLAMA_BIFURCATION_START | tenant=%s | chunk=%s/%s | chars=%s",
                self.tenant_id,
                idx + 1,
                len(windows),
                len(window),
            )
            try:
                part = self._call_llm_json(system_prompt, user_prompt)
            except Exception as exc:
                logger.warning("LLM bifurcate chunk=%s focus=full failed: %s", idx, exc)
                part = None
            if part:
                llm_parts.append(part)

        if not llm_parts:
            logger.warning(
                "Bifurcation LLM returned no parts — using heuristics only | tenant=%s",
                self.tenant_id,
            )
            return merge_bifurcation_results(heuristic, tenant_id=self.tenant_id)

        merged = merge_bifurcation_results(heuristic, *llm_parts, tenant_id=self.tenant_id)
        # Prefer heuristic vertical when LLM is generic but heuristics are specific
        if merged.get("vertical") == "generic" and heuristic.get("vertical") in VALID_VERTICALS:
            merged["vertical"] = heuristic["vertical"]
        return merged

    def _call_llm_json(self, system_prompt: str, user_prompt: str) -> Optional[Dict[str, Any]]:
        """Call local Ollama only (no Groq/Mistral).

        Bifurcation always uses the configured Ollama model. Heavy work must
        run in a thread pool (see tenant_files auto-bifurcate) so a slow
        generation does not freeze port 9000.
        """
        import requests

        try:
            from routing import config as _cfg
            ollama_base = getattr(_cfg, "OLLAMA_BASE_URL", None) or os.getenv(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            )
            ollama_url = getattr(_cfg, "OLLAMA_API_URL", None) or (
                ollama_base.rstrip("/") + "/api/chat"
            )
            ollama_mdl = getattr(_cfg, "OLLAMA_MODEL", None) or "qwen3:8b"
        except Exception:
            ollama_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/chat")
            ollama_mdl = os.getenv("OLLAMA_MODEL", "qwen3:8b")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Allow long local generations; defaults match chat/RAG OLLAMA_TIMEOUT.
        ollama_timeout = float(
            os.getenv(
                "BIFURCATE_OLLAMA_TIMEOUT",
                os.getenv("OLLAMA_TIMEOUT", "300"),
            )
        )
        # qwen3 "thinking" models often leave content empty unless think is off.
        payload = {
            "model": ollama_mdl,
            "messages": messages,
            "format": "json",
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": 8192,
                "num_predict": 4096,
            },
        }
        ollama_headers = {
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
        }
        try:
            resp = requests.post(
                ollama_url,
                json=payload,
                headers=ollama_headers,
                timeout=ollama_timeout,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Ollama bifurcation HTTP %s | model=%s | body=%s",
                    resp.status_code,
                    ollama_mdl,
                    (resp.text or "")[:200],
                )
                return None
            message = resp.json().get("message") or {}
            content = (
                (message.get("content") or "").strip()
                or (message.get("thinking") or "").strip()
                or (message.get("reasoning") or "").strip()
            )
            parsed = self._extract_json_block(content)
            if parsed:
                logger.info("OLLAMA_BIFURCATION_SUCCESS | model=%s", ollama_mdl)
                return parsed
            logger.warning(
                "Ollama returned no usable JSON | model=%s | preview=%s",
                ollama_mdl,
                (content or "")[:200],
            )
            return None
        except Exception as e:
            logger.warning(
                "Ollama bifurcation failed | model=%s | error=%s",
                ollama_mdl,
                e,
            )
            return None

    def _extract_json_block(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract and parse JSON safely from LLM output."""
        if not text:
            return None
        text = text.strip()
        # Direct parse
        try:
            return json.loads(text)
        except Exception:
            # deliberate: parse strategy chain — try the next strategy.
            logger.debug("JSON_EXTRACT_ATTEMPT_FAILED | strategy=direct", exc_info=True)

        # Regex search for ```json ... ```
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                # deliberate: parse strategy chain — try the next strategy.
                logger.debug("JSON_EXTRACT_ATTEMPT_FAILED | strategy=fenced_block", exc_info=True)

        # Search for first { to last }
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            try:
                return json.loads(text[first : last + 1])
            except Exception:
                # deliberate: last parse strategy — caller handles None.
                logger.debug("JSON_EXTRACT_ATTEMPT_FAILED | strategy=brace_slice", exc_info=True)

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # 2b. Section Partitioning & Kit Staging (deterministic [SECTION:] parse)
    # ──────────────────────────────────────────────────────────────────────────

    def partition_sections(self, extracted_docs: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:

        """Identify sections (Brand, Menu, Catalog, FAQs, Guardrails) across files."""

        combined_text = "\n\n".join(d["text"] for d in extracted_docs)

        all_tables = []

        for d in extracted_docs:

            all_tables.extend(d.get("tables", []))



        # Standard Section Patterns

        section_patterns = {

            "brand": r"\[SECTION:\s*BRAND(?:\s*&\s*PROFILE)?\]",

            "operations": r"\[SECTION:\s*OPERATIONS(?:\s*&\s*DOMAIN)?\]",

            "menu": r"\[SECTION:\s*WHATSAPP(?:\s*MENU)?\]",

            "catalog": r"\[SECTION:\s*CATALOG(?:\s*&\s*OFFERINGS)?\]",

            "faqs": r"\[SECTION:\s*FAQS(?:\s*&\s*POLICIES)?\]",

            "info_pages": r"\[SECTION:\s*INFO\s*PAGES(?:\s*&\s*INTENTS)?\]",

            "guardrails": r"\[SECTION:\s*BOT\s*PERSONA(?:\s*&\s*GUARDRAILS)?\]",

        }



        # Check if standardized section markers exist

        has_markers = any(re.search(pat, combined_text, re.IGNORECASE) for pat in section_patterns.values())



        sections: Dict[str, Dict[str, Any]] = {

            "brand": {"text": "", "tables": []},

            "operations": {"text": "", "tables": []},

            "menu": {"text": "", "tables": []},

            "catalog": {"text": "", "tables": []},

            "faqs": {"text": "", "tables": []},

            "info_pages": {"text": "", "tables": []},

            "guardrails": {"text": "", "tables": []},

        }



        if has_markers:

            # Split text along section headers

            splits = re.split(

                r"(\[SECTION:\s*[^\]]+\])",

                combined_text,

                flags=re.IGNORECASE,

            )

            current_sec = "brand"

            for part in splits:

                part = part.strip()

                if not part:

                    continue

                match_header = re.match(r"\[SECTION:\s*([^\]]+)\]", part, re.IGNORECASE)

                if match_header:

                    tag = match_header.group(1).upper()

                    if "BRAND" in tag:

                        current_sec = "brand"

                    elif "OPERATION" in tag or "DOMAIN" in tag:

                        current_sec = "operations"

                    elif "MENU" in tag:

                        current_sec = "menu"

                    elif "CATALOG" in tag or "OFFERING" in tag:

                        current_sec = "catalog"

                    elif "FAQ" in tag or "POLIC" in tag:

                        current_sec = "faqs"

                    elif "INFO PAGE" in tag or "INFO PAGES" in tag or tag.strip() in (
                        "TECHNOLOGIES", "PROJECTS", "CAREERS", "BENEFITS",
                    ):
                        current_sec = "info_pages"

                    elif "PERSONA" in tag or "GUARD" in tag:

                        current_sec = "guardrails"

                else:

                    sections[current_sec]["text"] += f"\n{part}"



            # Distribute tables to matching sections based on table headers

            for tbl in all_tables:

                if not tbl or not tbl[0]:

                    continue

                section_tag = None
                for table_row in tbl:
                    for cell in table_row:
                        section_match = re.search(
                            r"\[SECTION:\s*([^\]]+)\]",
                            str(cell or ""),
                            re.IGNORECASE,
                        )
                        if section_match:
                            section_tag = section_match.group(1).upper()
                            break
                    if section_tag:
                        break

                if section_tag:
                    if "BRAND" in section_tag:
                        target_section = "brand"
                    elif "OPERATION" in section_tag or "DOMAIN" in section_tag:
                        target_section = "operations"
                    elif "MENU" in section_tag or "WHATSAPP" in section_tag:
                        target_section = "menu"
                    elif "CATALOG" in section_tag or "OFFERING" in section_tag:
                        target_section = "catalog"
                    elif "FAQ" in section_tag or "POLIC" in section_tag:
                        target_section = "faqs"
                    elif "INFO PAGE" in section_tag or "INTENT" in section_tag:
                        target_section = "info_pages"
                    elif "PERSONA" in section_tag or "GUARD" in section_tag:
                        target_section = "guardrails"
                    else:
                        target_section = None
                    if target_section:
                        sections[target_section]["tables"].append(tbl)
                        continue

                first_row_str = " ".join(str(c).lower() for c in tbl[0])

                if any(k in first_row_str for k in ("domain field", "why we need it", "your answer")) and (
                    "domain" in first_row_str or "why we need" in first_row_str
                ):

                    sections["operations"]["tables"].append(tbl)

                elif any(
                    k in first_row_str
                    for k in (
                        "configuration parameter",
                        "bot behavior",
                        "tone & style",
                        "never-do",
                        "guardrail",
                        "rule & guideline",
                    )
                ):

                    sections["guardrails"]["tables"].append(tbl)

                elif any(
                    k in first_row_str
                    for k in (
                        "company legal",
                        "tenant id",
                        "bot name",
                        "features enabled",
                        "support email",
                        "your company input",
                    )
                ) or (
                    "parameter" in first_row_str
                    and "configuration parameter" not in first_row_str
                    and "bot behavior" not in first_row_str
                ):

                    sections["brand"]["tables"].append(tbl)

                elif any(k in first_row_str for k in ("button title", "action mapping", "category section")):

                    sections["menu"]["tables"].append(tbl)

                elif any(k in first_row_str for k in ("property", "product", "sku", "service name", "package name", "bhk", "carpet area")):

                    sections["catalog"]["tables"].append(tbl)

                elif any(
                    k in first_row_str
                    for k in (
                        "knowledge article",
                        "article title",
                        "article content",
                        "markdown content",
                    )
                ):
                    sections["faqs"]["tables"].append(tbl)

                elif any(
                    k in first_row_str
                    for k in (
                        "info page",
                        "page name",
                        "intent name",
                        "technologies",
                        "projects",
                        "careers",
                        "benefits",
                        "bot answer",
                    )
                ) and any(
                    k in first_row_str
                    for k in ("page", "intent", "answer", "content", "technologies", "projects", "careers", "benefits")
                ):
                    sections["info_pages"]["tables"].append(tbl)

                elif (

                    any(k in first_row_str for k in ("question", "frequently asked", "official answer", "verified answer"))

                    or str(tbl[0][0]).strip().endswith("?")

                    or (len(tbl[0]) == 3 and any(str(r[0]).strip().endswith("?") for r in tbl))

                ):

                    sections["faqs"]["tables"].append(tbl)

                else:

                    # Generic fallback table

                    sections["catalog"]["tables"].append(tbl)

        else:

            # Fallback for unformatted/raw collateral — feed full text to every
            # extractor so kit-less uploads still have something to parse.
            sections["brand"]["text"] = combined_text
            sections["catalog"]["text"] = combined_text
            sections["catalog"]["tables"] = all_tables
            sections["faqs"]["text"] = combined_text
            sections["info_pages"]["text"] = combined_text
            sections["menu"]["text"] = combined_text
            sections["guardrails"]["text"] = combined_text

        return sections



    # ──────────────────────────────────────────────────────────────────────────

    # 4. Section-Targeted Extractors

    # ──────────────────────────────────────────────────────────────────────────



    def extract_brand_section(
        self, sec_data: Dict[str, Any], default_vertical: str = "generic"
    ) -> Tuple[Dict[str, Any], Dict[str, Any], str, Dict[str, Any]]:
        """Extract Brand profile, hours, vertical, and profile extras from Section 1.

        Extras include notifications, vocabulary, features, greeting, logo/WABA hints
        so a complete kit can fill the live Profile — not only the legacy 6 brand keys.
        """
        text = sec_data.get("text", "")
        tables = sec_data.get("tables", [])

        parsed_fields = parse_brand_kit_tables(tables, text=text)

        # Free-text contact scrape when kit tables are missing fields
        if text:
            heur_brand = (heuristic_extract_corpus(text, tenant_id=self.tenant_id).get("brand") or {})
            for src_key, dst_key in (
                ("name", "brand_name"),
                ("tagline", "tagline"),
                ("website", "website"),
                ("support_email", "support_email"),
                ("support_phone", "support_phone"),
                ("bot_name", "bot_name"),
                ("signature", "signature"),
            ):
                if heur_brand.get(src_key) and not parsed_fields.get(dst_key):
                    parsed_fields[dst_key] = heur_brand[src_key]

        brand, business_hours, vertical, extras = fields_to_brand_bundle(
            parsed_fields,
            tenant_id=self.tenant_id,
            default_vertical=default_vertical,
        )

        # Keyword vertical detection when table hint missing
        full_sample = (text + " " + json.dumps(parsed_fields)).lower()
        if not vertical or vertical == default_vertical:
            slug_hints = (
                ("it_software", "it_software"),
                ("software & it", "it_software"),
                ("real_estate", "real_estate"),
                ("real estate", "real_estate"),
                ("tours_travel", "tours_travel"),
                ("tours & travel", "tours_travel"),
                ("ecommerce", "ecommerce"),
                ("e-commerce", "ecommerce"),
                ("healthcare", "healthcare"),
                ("banking", "banking"),
                ("finance", "finance"),
                ("generic", "generic"),
            )
            for needle, slug in slug_hints:
                if needle in full_sample:
                    vertical = slug
                    break
            else:
                scores = {
                    "real_estate": sum(1 for w in ("property", "bhk", "villa", "penthouse", "rera", "carpet area") if w in full_sample),
                    "tours_travel": sum(1 for w in ("flight", "tour", "itinerary", "visa", "holiday") if w in full_sample),
                    "it_software": sum(1 for w in ("software", "devops", "tech stack", "fastapi", "react", "kubernetes", "flutter") if w in full_sample),
                    "ecommerce": sum(1 for w in ("sku", "cart", "warranty", "cod", "chikki") if w in full_sample),
                    "healthcare": sum(1 for w in ("clinic", "doctor", "hospital", "patient", "consultation fee") if w in full_sample),
                    "banking": sum(1 for w in ("credit card", "atm", "ifsc", "savings account") if w in full_sample),
                    "finance": sum(1 for w in ("gst", "itr", "gold loan", "two-wheeler loan", "nbfc") if w in full_sample),
                }
                best = max(scores.items(), key=lambda kv: kv[1])
                if best[1] > 0:
                    vertical = best[0]
                elif text:
                    guessed = _guess_vertical(text)
                    if guessed != "generic":
                        vertical = guessed

        return brand, business_hours, vertical, extras



    def extract_menu_section(self, sec_data: Dict[str, Any], vertical: str) -> List[Dict[str, Any]]:

        """Extract and validate WhatsApp interactive menu items (Meta 24/72 char limits)."""

        tables = sec_data.get("tables", [])

        menu_items = []



        for tbl in tables:

            # Check if this table has menu headers

            if len(tbl) > 1 and len(tbl[0]) >= 3:

                for row in tbl[1:]:

                    if not row or not row[0].strip():
                        continue

                    row_header = " ".join(str(cell or "") for cell in row).casefold()
                    if "[section:" in row_header or (
                        "button title" in row_header
                        or ("description" in row_header and "section" in row_header and "action" in row_header)
                    ):
                        continue

                    title = row[0].strip()[:24]
                    desc = (row[1].strip() if len(row) > 1 else title)[:72]
                    sec = (row[2].strip() if len(row) > 2 else "Explore")[:24]
                    raw_action = (row[3].strip() if len(row) > 3 else "offerings")
                    id_part = re.split(r"\b(?:intent|flow)\s*:", raw_action, maxsplit=1, flags=re.IGNORECASE)[0]
                    id_match = re.search(
                        r"\bmenu(?:[_\s-]+[a-z0-9]+)+",
                        id_part,
                        flags=re.IGNORECASE,
                    )
                    action_match = re.search(
                        r"\b(intent|flow)\s*:\s*([a-z][a-z0-9 _-]*)",
                        raw_action,
                        flags=re.IGNORECASE,
                    )
                    action = (
                        re.sub(r"[-\s]+", "_", action_match.group(2)).strip("_").lower()
                        if action_match
                        else raw_action.lower()
                    )
                    action = action.replace(" ", "_").replace("-", "_")
                    # E2E D-E2E-1: PDF table extraction can split a trailing
                    # underscore off as its own token ("track order _" →
                    # "track_order__"), which missed the exact keep-list
                    # below and fell through to the silent "offerings"
                    # rewrite. Collapse repeated separators and trim edge
                    # underscores so valid actions pass through unchanged.
                    action = re.sub(r"_+", "_", action).strip("_")

                    action_warning = None

                    # Preserve IT info-page intents and known flows; don't collapse them to offerings
                    info_page_actions = {
                        "technologies", "technology", "tech_stack", "projects", "project",
                        "careers", "career", "benefits", "benefit", "services", "service_enquiry",
                    }
                    if action in info_page_actions or action in (
                        "technologies", "projects", "careers", "benefits", "offerings",
                        "offering_details", "faq", "quote", "get_quote", "book_callback",
                        "raise_ticket", "track_order", "returns",
                    ):
                        if action in ("technology", "tech_stack"):
                            action = "technologies"
                        elif action == "project":
                            action = "projects"
                        elif action == "career":
                            action = "careers"
                        elif action == "benefit":
                            action = "benefits"
                    elif "quote" in action or "price" in action:
                        action = "quote"
                    elif "call" in action or "visit" in action or "appoint" in action:
                        action = "book_callback"
                    elif "ticket" in action or "support" in action or "help" in action:
                        action = "raise_ticket"
                    elif "faq" in action:
                        action = "faq"
                    elif "technolog" in action or "stack" in action:
                        action = "technologies"
                    elif "project" in action or "portfolio" in action or "case" in action:
                        action = "projects"
                    elif "career" in action or "hiring" in action or "job" in action:
                        action = "careers"
                    elif "benefit" in action or "why" in action:
                        action = "benefits"
                    elif "detail" in action:
                        action = "offering_details"
                    elif action_match and action_match.group(1).lower() == "intent":
                        pass
                    else:
                        # E2E D-E2E-1: never silently rewrite an action we
                        # do not recognise to "offerings" — keep the
                        # author's action and flag it in the staged payload
                        # so the mismatch is visible at review time.
                        action_warning = (
                            f"Unrecognised menu action '{raw_action}' kept as-is"
                        )



                    menu_item = {

                        "id": _menu_button_id(id_match.group(0)) if id_match else _menu_button_id(title),

                        "title": title,

                        "description": desc,

                        "section": sec,

                        "action": action,

                    }
                    if action_match:
                        menu_item[action_match.group(1).lower()] = action
                    if action_warning:
                        menu_item["action_warning"] = action_warning
                    menu_items.append(menu_item)



        # If no items found, load vertical defaults (full WhatsApp list, up to 10)

        if len(menu_items) < 3:
            menu_items.extend(_menu_items_from_vertical_defaults(vertical, limit=10))

        # WhatsApp list menus support up to 10 rows; preserve all extracted
        # items within that limit instead of silently dropping the last two.
        return menu_items[:10]



    def extract_catalog_section(self, sec_data: Dict[str, Any], vertical: str) -> List[Dict[str, Any]]:

        """Extract structured catalog offerings/properties with vertical-specific attributes."""

        tables = sec_data.get("tables", [])

        text = sec_data.get("text", "")

        offerings: List[Dict[str, Any]] = []



        for tbl in tables:

            if len(tbl) < 2:

                continue

            headers = [str(c).strip().lower() for c in tbl[0]]



            # Filter out brand or menu tables accidentally routed here

            if any(h in headers for h in ("button title", "parameter", "verified answer", "knowledge article")):

                continue



            for row in tbl[1:]:

                if not row or not any(row):

                    continue

                first_cell = str(row[0]).strip()

                if not first_cell or first_cell.startswith("[") or first_cell.lower() == "enter item":

                    continue



                row_dict = {}

                for idx, cell in enumerate(row):

                    if idx < len(headers):

                        row_dict[headers[idx]] = str(cell).strip()

                def _pick_media_url(rd: Dict[str, str]) -> str:
                    for key, val in rd.items():
                        kl = key.lower()
                        if any(tok in kl for tok in ("image url", "image", "media url", "media_url", "photo", "thumbnail")):
                            v = (val or "").strip()
                            if v and not v.startswith("["):
                                url = tenancy_schemas.extract_first_url(v)
                                if url:
                                    return url
                    return ""



                # Vertical-specific mapping

                list_price = ""

                if vertical == "real_estate":

                    name = row_dict.get("property name") or first_cell

                    prop_type = row_dict.get("type") or "Residential"

                    bhk = row_dict.get("configuration") or "2 & 3 BHK"

                    carpet = row_dict.get("carpet area (sqft)") or row_dict.get("carpet area") or ""

                    price = row_dict.get("price range") or row_dict.get("price") or "Price on Request"

                    loc = row_dict.get("location") or "Prime Location"

                    amenities = row_dict.get("key amenities") or row_dict.get("amenities") or ""

                    possession = row_dict.get("possession") or "Ready to Move"

                    rera = row_dict.get("rera id") or row_dict.get("rera number") or ""



                    short_desc = f"{bhk} in {loc}"

                    full_desc = f"{name}: {prop_type} with {bhk} ({carpet} sqft) located at {loc}. Amenities: {amenities}. Possession: {possession}. RERA: {rera}."

                    attrs = {

                        "type": prop_type,

                        "bhk": bhk,

                        "carpet_area": carpet,

                        "location": loc,

                        "amenities": amenities,

                        "possession": possession,

                        "rera_id": rera,

                    }



                elif vertical == "ecommerce":

                    name = row_dict.get("product name") or first_cell

                    sku = row_dict.get("sku") or _slugify(name).upper()

                    prop_type = row_dict.get("category") or "Accessories"

                    price = row_dict.get("discount price") or row_dict.get("price (₹)") or row_dict.get("price") or ""

                    # E2E D-E2E-2: catalogs carry BOTH a list price and a
                    # discount price (₹3,499 → ₹2,999). Preserve the list
                    # price as the product's MRP instead of dropping it.
                    list_price = row_dict.get("price (₹)") or row_dict.get("list price") or row_dict.get("mrp") or row_dict.get("price") or ""

                    specs = row_dict.get("key specifications / size") or row_dict.get("specifications") or ""

                    stock = row_dict.get("stock status") or "In Stock"

                    warranty = row_dict.get("warranty / returns") or row_dict.get("warranty") or "7-Day Return"



                    short_desc = specs[:100] or name

                    full_desc = f"{name} (SKU: {sku}) - {specs}. Stock: {stock}. Warranty: {warranty}."

                    attrs = {

                        "sku": sku,

                        "specifications": specs,

                        "stock_status": stock,

                        "warranty": warranty,

                    }



                else:

                    # General / IT / Travel / Healthcare

                    name = row_dict.get("service / offering") or row_dict.get("package name") or row_dict.get("specialty / department") or first_cell

                    prop_type = row_dict.get("category") or "Services"

                    price = row_dict.get("starting price / model") or row_dict.get("price per person") or row_dict.get("consultation fee") or "On Request"

                    desc = row_dict.get("scope & deliverables") or row_dict.get("highlights") or row_dict.get("services & treatments") or name

                    short_desc = desc[:90]

                    full_desc = desc

                    attrs = {
                        k: v for k, v in row_dict.items()
                        if k not in ("property name", "product name", "service / offering", "image url", "media url", "image", "photo")
                    }

                media_url = _pick_media_url(row_dict)
                media_type = "image"
                if media_url and media_url.lower().endswith(".pdf"):
                    media_type = "pdf"

                offering: Dict[str, Any] = {

                    "name": name,

                    "category": prop_type,

                    "price": price,

                    "short_description": short_desc,

                    "description": full_desc,

                    "unit": "unit",

                    "attrs": attrs,

                }
                if media_url:
                    offering["media_url"] = media_url
                    offering["media_type"] = media_type
                if list_price and str(list_price).strip() and str(list_price).strip() != str(price).strip():
                    offering["mrp"] = str(list_price).strip()

                offerings.append(offering)



        # Fallback if no tables: regex extract lines with prices or bullets

        if not offerings and text:

            for line in text.splitlines():

                line = line.strip()

                if (line.startswith("•") or line.startswith("*") or line.startswith("-")) and len(line) > 10:

                    clean_l = line.lstrip("•*- ").strip()

                    parts = clean_l.split(":")

                    item_name = parts[0].strip()[:60]

                    item_desc = parts[1].strip() if len(parts) > 1 else clean_l

                    offerings.append({

                        "name": item_name,

                        "category": "General",

                        "price": "On Request",

                        "short_description": item_name,

                        "description": item_desc,

                        "unit": "unit",

                        "attrs": {},

                    })

                if len(offerings) >= 8:

                    break



        return offerings



    def extract_faq_section(self, sec_data: Dict[str, Any], brand_name: str, vertical: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract FAQs and knowledge articles from FAQS & POLICIES tables."""
        tables = sec_data.get("tables", [])
        text = sec_data.get("text", "")
        faqs: List[Dict[str, Any]] = []
        articles: List[Dict[str, Any]] = []

        def _header_blob(tbl: List[List[str]]) -> str:
            if not tbl or not tbl[0]:
                return ""
            return " ".join(str(c or "").lower() for c in tbl[0])

        def _is_kb_table(tbl: List[List[str]]) -> bool:
            h = _header_blob(tbl)
            return (
                "knowledge article" in h
                or ("article title" in h and "content" in h)
                or ("article content" in h)
                or (h.startswith("knowledge") and "title" in h)
            )

        def _is_faq_table(tbl: List[List[str]]) -> bool:
            if _is_kb_table(tbl):
                return False
            h = _header_blob(tbl)
            if "question" in h or "faq" in h or "answer" in h:
                return True
            # Data rows that look like Q&A
            sample = tbl[1][0].strip() if len(tbl) > 1 and tbl[1] else (tbl[0][0].strip() if tbl[0] else "")
            return sample.endswith("?") or "?" in sample

        for tbl in tables:
            if not tbl or len(tbl[0]) < 2:
                continue

            if _is_kb_table(tbl):
                for row in tbl[1:]:
                    if not row or not str(row[0]).strip():
                        continue
                    title = str(row[0]).strip()
                    if title.startswith("[") or title.lower().startswith("add "):
                        continue
                    # Title | Category | Content  OR  Title | Content
                    if len(row) >= 3:
                        cat = str(row[1]).strip() or "Knowledge"
                        body = str(row[2]).strip()
                    else:
                        cat = "Knowledge"
                        body = str(row[1]).strip()
                    if title and body:
                        articles.append({
                            "title": title,
                            "category": cat,
                            "markdown_content": body if body.lstrip().startswith("#") else f"# {title}\n\n{body}",
                        })
                continue

            if not _is_faq_table(tbl):
                continue

            is_q = str(tbl[0][0]).strip().endswith("?") or "?" in str(tbl[0][0])
            start_idx = 0 if is_q else 1
            for row in tbl[start_idx:]:
                if not row or not str(row[0]).strip():
                    continue
                q = str(row[0]).strip()
                if q.startswith("[") or q.lower().startswith("add question") or q.lower() == "customer question":
                    continue
                a = str(row[1]).strip() if len(row) > 1 else ""
                cat = str(row[2]).strip() if len(row) > 2 else "General"
                if q and a:
                    faqs.append({"question": q, "answer": a, "category": cat})

        if not faqs and text:
            qa_matches = re.findall(
                r"(?:Q|Question):\s*(.+?)\n+(?:A|Answer):\s*(.+?)(?=\n+(?:Q|Question):|\Z)",
                text,
                re.DOTALL | re.IGNORECASE,
            )
            for q, a in qa_matches:
                faqs.append({"question": q.strip(), "answer": a.strip(), "category": "General"})

        # Always include a compact company stub + rolled-up FAQ article for retrieval.
        articles.insert(0, {
            "title": f"About {brand_name}",
            "category": "Company",
            "markdown_content": (
                f"# About {brand_name}\n\n"
                f"Welcome to {brand_name}. We provide industry-leading {vertical.replace('_', ' ')} solutions.\n\n"
                "Our team is committed to transparency, excellence, and customer satisfaction. "
                "Use the FAQs and knowledge articles below for policies, processes, and support guidance."
            ),
        })

        if faqs:
            faq_md_lines = [f"# Frequently Asked Questions — {brand_name}\n"]
            for f in faqs:
                faq_md_lines.append(
                    f"### Q: {f['question']}\n**A:** {f['answer']}\n*(Category: {f['category']})*\n"
                )
            articles.append({
                "title": "Frequently Asked Questions & Customer Support",
                "category": "FAQ",
                "markdown_content": "\n".join(faq_md_lines),
            })

        return faqs, articles



    def extract_guardrails_section(self, sec_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract tone, never-state, forbidden/handoff/escalate keywords, escalation message."""
        return parse_guardrails_kit_tables(sec_data.get("tables") or [])

    def extract_operations_domain_section(self, sec_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract vertical domain answers (wizard questions) from OPERATIONS & DOMAIN."""
        return parse_operations_domain_tables(
            sec_data.get("tables") or [],
            text=sec_data.get("text") or "",
        )



    def extract_info_pages_section(self, sec_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract Technologies / Projects / Careers / Benefits intent answers."""
        INFO_DEFAULTS = {
            "technologies": {
                "keywords": ["technologies", "technology", "tech stack", "stack", "framework", "tools"],
                "examples": ["what technologies do you work with", "what is your tech stack"],
            },
            "projects": {
                "keywords": ["projects", "project", "portfolio", "case study", "case studies", "clients"],
                "examples": ["show me your work", "who have you built for"],
            },
            "careers": {
                "keywords": ["career", "careers", "job", "hiring", "vacancy", "internship", "open role"],
                "examples": ["are you hiring", "job openings at your company"],
            },
            "benefits": {
                "keywords": ["benefits", "why choose you", "why work with you", "advantages", "perks"],
                "examples": ["why should we work with you", "what are the benefits"],
            },
        }
        alias = {
            "technology": "technologies",
            "tech stack": "technologies",
            "tech_stack": "technologies",
            "stack": "technologies",
            "project": "projects",
            "portfolio": "projects",
            "case studies": "projects",
            "career": "careers",
            "hiring": "careers",
            "jobs": "careers",
            "benefit": "benefits",
            "why us": "benefits",
            "why choose us": "benefits",
        }
        found: Dict[str, Dict[str, Any]] = {}
        tables = sec_data.get("tables", []) or []
        text = sec_data.get("text", "") or ""

        for tbl in tables:
            if not tbl or len(tbl[0]) < 2:
                continue
            header = " ".join(str(c or "").lower() for c in tbl[0])
            start = 1
            # Single-page tables whose first header cell is the page name
            first_h = str(tbl[0][0]).strip().lower()
            if first_h in INFO_DEFAULTS or alias.get(first_h) in INFO_DEFAULTS:
                page = alias.get(first_h, first_h)
                body_parts = []
                for row in tbl[1:]:
                    cells = [str(c).strip() for c in row if str(c).strip()]
                    if cells:
                        body_parts.append(" • ".join(cells) if len(cells) > 1 else cells[0])
                answer = "\n".join(f"• {p}" if not p.startswith("•") else p for p in body_parts if p)
                if answer:
                    meta = INFO_DEFAULTS[page]
                    found[page] = {
                        "name": page,
                        "answer": answer,
                        "keywords": list(meta["keywords"]),
                        "examples": list(meta["examples"]),
                        "enabled": True,
                        "flow": None,
                    }
                continue

            if "page" in header or "intent" in header or "answer" in header or "content" in header or "keyword" in header:
                for row in tbl[start:]:
                    if not row or not str(row[0]).strip():
                        continue
                    raw_name = str(row[0]).strip().lower()
                    page = alias.get(raw_name, raw_name.replace(" ", "_"))
                    if page not in INFO_DEFAULTS:
                        continue
                    answer = str(row[1]).strip() if len(row) > 1 else ""
                    # New 4-col kit: Page | Answer | Keywords | Examples
                    # Legacy 3-col: Page | Answer | extra answer text
                    keywords = list(INFO_DEFAULTS[page]["keywords"])
                    examples = list(INFO_DEFAULTS[page]["examples"])
                    if len(row) >= 4:
                        kw_cell = str(row[2] or "").strip()
                        ex_cell = str(row[3] or "").strip()
                        if kw_cell and not kw_cell.startswith("["):
                            keywords = [k.strip() for k in re.split(r"[,;]", kw_cell) if k.strip()] or keywords
                        if ex_cell and not ex_cell.startswith("["):
                            examples = [e.strip() for e in re.split(r"[|;\n]", ex_cell) if e.strip()] or examples
                    elif len(row) > 2 and str(row[2]).strip():
                        extra = str(row[2]).strip()
                        # If it looks like keywords (short, comma-ish) don't append to answer
                        if "," in extra and len(extra) < 220 and "?" not in extra:
                            keywords = [k.strip() for k in extra.split(",") if k.strip()] or keywords
                        else:
                            answer = (answer + "\n" + extra).strip()
                    if not answer or answer.startswith("["):
                        continue
                    found[page] = {
                        "name": page,
                        "description": page.replace("_", " ").title(),
                        "answer": answer,
                        "keywords": keywords[:12],
                        "examples": examples[:6],
                        "enabled": True,
                        "flow": None,
                    }

        # Text blocks like ### Technologies\n...
        if text and len(found) < 4:
            for page in INFO_DEFAULTS:
                if page in found:
                    continue
                m = re.search(
                    rf"(?:^|\n)\s*(?:#+?\s*)?{page}\s*[:\n]+(.+?)(?=\n\s*(?:#+?\s*)?(?:technologies|projects|careers|benefits)\b|\Z)",
                    text,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                if m and m.group(1).strip():
                    meta = INFO_DEFAULTS[page]
                    found[page] = {
                        "name": page,
                        "answer": m.group(1).strip()[:4000],
                        "keywords": list(meta["keywords"]),
                        "examples": list(meta["examples"]),
                        "enabled": True,
                        "flow": None,
                    }

        return list(found.values())

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Staging Pipeline (Review & Edit Before Publish)
    # ──────────────────────────────────────────────────────────────────────────

    _CORE_KIT_SECTIONS = ("BRAND", "WHATSAPP", "CATALOG", "FAQS", "BOT PERSONA")

    def _kit_section_presence(self, text: str) -> Dict[str, bool]:
        """Which core (and optional) kit section markers appear in the corpus."""
        checks = {
            "BRAND": r"\[SECTION:\s*BRAND",
            "WHATSAPP": r"\[SECTION:\s*WHATSAPP",
            "CATALOG": r"\[SECTION:\s*CATALOG",
            "FAQS": r"\[SECTION:\s*FAQS",
            "BOT PERSONA": r"\[SECTION:\s*BOT\s*PERSONA",
            "INFO PAGES": r"\[SECTION:\s*INFO\s*PAGES",
            "OPERATIONS": r"\[SECTION:\s*OPERATIONS",
        }
        return {name: bool(re.search(pat, text or "", re.I)) for name, pat in checks.items()}

    def _is_complete_kit(self, text: str) -> bool:
        presence = self._kit_section_presence(text)
        return all(presence.get(name) for name in self._CORE_KIT_SECTIONS)

    def _default_guardrails(self) -> Dict[str, Any]:
        return self.extract_guardrails_section({"text": "", "tables": []})

    def _build_offering_linked_intents(
        self,
        offerings: List[Dict[str, Any]],
        brand_name: str = "",
    ) -> List[Dict[str, Any]]:
        """Fixed-template intents so Intents Editor matches the catalog (Q10=C)."""
        brand = (brand_name or "us").strip() or "us"
        top = [o for o in (offerings or []) if isinstance(o, dict) and (o.get("name") or "").strip()][:8]
        if top:
            lines = []
            for o in top:
                name = str(o.get("name") or "").strip()
                short = str(o.get("short_description") or o.get("description") or "").strip()
                price = str(o.get("price") or "").strip()
                bit = f"• *{name}*"
                if short:
                    bit += f" — {short[:120]}"
                if price:
                    bit += f" ({price})"
                lines.append(bit)
            catalog_block = "\n".join(lines)
        else:
            catalog_block = f"• Browse our live catalog for {brand} offerings, or ask for a specific service."

        service_answer = (
            f"• Here's what {brand} offers:\n"
            f"{catalog_block}\n"
            "• Ask for details on any item, or tap *Get a Quote* / *Request a Callback*."
        )
        pricing_answer = (
            "• Pricing depends on scope, volume, and packaging.\n"
            f"{catalog_block}\n"
            "• Share your requirements and we'll send a tailored quote — tap *Get a Quote*."
        )
        return [
            {
                "name": "service_enquiry",
                "description": "What services / offerings we provide",
                "keywords": ["service", "services", "what do you do", "offerings", "catalog"],
                "examples": ["what services do you offer", "tell me about your offerings"],
                "answer": service_answer,
                "enabled": True,
                "flow": None,
            },
            {
                "name": "pricing_enquiry",
                "description": "Pricing and packages",
                "keywords": ["price", "pricing", "cost", "rate", "package", "quote"],
                "examples": ["what are your prices", "how much does it cost"],
                "answer": pricing_answer,
                "enabled": True,
                "flow": None,
            },
        ]

    def _enrich_intents_with_offerings(
        self,
        intents: List[Dict[str, Any]],
        offerings: List[Dict[str, Any]],
        brand_name: str = "",
    ) -> List[Dict[str, Any]]:
        linked = self._build_offering_linked_intents(offerings, brand_name=brand_name)
        by_name = {str(i.get("name") or "").lower(): dict(i) for i in (intents or []) if isinstance(i, dict)}
        for item in linked:
            name = item["name"]
            prev = by_name.get(name) or {}
            merged = {**prev, **item}
            # Always refresh answer from catalog template when offerings exist
            if offerings:
                merged["answer"] = item["answer"]
            by_name[name] = merged
        return list(by_name.values())

    # ── Section provenance (staged payloads) ───────────────────────────
    # The universal extractor merges genuinely document-derived values
    # with vertical-template stand-ins and, historically, did not say
    # which was which. ``section_sources`` labels each staged section so
    # consumers (the onboarding wizard) can trust a section only when it
    # really came out of the document:
    #   "extracted" — the values came from the document (LLM parts or
    #                 deterministic heuristic/regex extraction);
    #   "template"  — a fallback/default supplied the values;
    #   "mixed"     — the section carries a document signal, but its
    #                 headline values still include defaults.
    # The labels describe the pipeline exactly as it behaves today; no
    # extraction behaviour changes here.

    @staticmethod
    def _business_hours_source(raw_hours: Any) -> str:
        """Provenance of the staged business hours, judged on the
        PRE-sanitize draft hours.

        The sanitizer fills open/close with hard-coded 09:30/18:30
        whenever they are missing, and the heuristic extractor seeds
        those same defaults unconditionally — so the mere presence of
        open/close in the payload proves nothing. The trustworthy
        document signals are the ones the merge actually lets through:
        a free-text ``raw`` hours label (regex-found in the document
        and parsed by the sanitizer) or an ``always_open`` claim.
        """
        if not isinstance(raw_hours, dict):
            return "template"
        raw_label = str(raw_hours.get("raw") or "").strip()
        if raw_label:
            times = re.findall(
                r"(\d{1,2}:\d{2})\s*(AM|PM)?", raw_label, flags=re.IGNORECASE
            )
            if len(times) >= 2:
                return "extracted"
            low = raw_label.lower()
            if "24/7" in low or "always" in low:
                return "extracted"
            # A labelled hours line exists in the document but its times
            # could not be parsed — open/close are still the defaults.
            return "mixed"
        if raw_hours.get("always_open"):
            return "extracted"
        return "template"

    @staticmethod
    def _menu_section_source(menu_items: List[Dict[str, Any]], vertical: str) -> str:
        """Provenance of the staged menu.

        The merge (and, for very short menus, ``_stage_universal``
        itself) tops a short extracted menu up with the vertical's
        default buttons, so the payload alone cannot be trusted. Each
        item is classified against the vertical defaults by normalized
        id/title key: an item whose keys all match a default counts as
        template. That errs conservative — a document item that happens
        to share a default's id/title counts as template too — which is
        the safe direction for a consumer deciding whether to trust it.
        """
        items = [i for i in (menu_items or []) if isinstance(i, dict)]
        if not items:
            return "template"
        default_keys = set()
        for d in _menu_items_from_vertical_defaults(vertical, limit=10):
            for field in ("id", "title"):
                key = _normalize_menu_button_key(str(d.get(field) or ""))
                if key:
                    default_keys.add(key)

        def _is_default(item: Dict[str, Any]) -> bool:
            keys = {
                _normalize_menu_button_key(str(item.get(f) or ""))
                for f in ("id", "title")
            }
            keys.discard("")
            return bool(keys) and keys <= default_keys

        flags = [_is_default(i) for i in items]
        if all(flags):
            return "template"
        if any(flags):
            return "mixed"
        return "extracted"

    def _universal_section_sources(
        self,
        draft: Dict[str, Any],
        *,
        brand_name_fabricated: bool,
        guardrails_from_template: bool,
        menu_items: List[Dict[str, Any]],
        vertical: str,
        offerings: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """Assemble the ``section_sources`` map for a universal staging."""
        draft_brand = draft.get("brand") if isinstance(draft.get("brand"), dict) else {}
        if not brand_name_fabricated:
            brand_source = "extracted"
        elif any(
            str(draft_brand.get(k) or "").strip()
            for k in ("support_email", "support_phone", "website", "tagline")
        ):
            # The name is the tenant-id stand-in, but real contact
            # details were extracted from the document.
            brand_source = "mixed"
        else:
            brand_source = "template"
        return {
            "brand": brand_source,
            "business_hours": self._business_hours_source(draft.get("business_hours")),
            "guardrails": "template" if guardrails_from_template else "extracted",
            "menu": self._menu_section_source(menu_items, vertical),
            # Offerings are never filled from vertical templates in this
            # path (heuristic tables/headings or LLM only); an empty
            # list therefore means nothing document-derived was found.
            "offerings": "extracted" if offerings else "template",
        }

    def _stage_universal(
        self,
        extracted_docs: List[Dict[str, Any]],
        combined_text: str,
        *,
        warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            draft = self.bifurcate_with_llm(extracted_docs)
        except Exception as exc:
            logger.warning("stage_files LLM path failed (%s); heuristics-only fallback", exc)
            draft = merge_bifurcation_results(
                heuristic_extract_corpus(combined_text, tenant_id=self.tenant_id),
                tenant_id=self.tenant_id,
            )
        brand = dict(draft.get("brand") or {})
        brand_name_fabricated = not brand.get("name")
        if brand_name_fabricated:
            brand["name"] = self.tenant_id.replace("-", " ").replace("_", " ").title()
        business_hours = _sanitize_business_hours(draft.get("business_hours") or {})
        vertical = draft.get("vertical") or _guess_vertical(combined_text)
        menu_items = draft.get("menu_items") or []
        if len(menu_items) < 3:
            menu_items = self.extract_menu_section({"text": "", "tables": []}, vertical=vertical)
        offerings = draft.get("offerings") or []
        _attach_pdf_images_to_offerings(offerings, extracted_docs)
        faqs = draft.get("faqs") or []
        articles = draft.get("knowledge_articles") or []
        intents = draft.get("intents") or []
        extra_info = self.extract_info_pages_section({"text": combined_text, "tables": []})
        if extra_info:
            by_name = {i.get("name"): i for i in intents if isinstance(i, dict)}
            for item in extra_info:
                name = item.get("name")
                if not name:
                    continue
                if name not in by_name or len(str(item.get("answer") or "")) > len(
                    str(by_name[name].get("answer") or "")
                ):
                    by_name[name] = item
            intents = list(by_name.values())
        intents = self._enrich_intents_with_offerings(intents, offerings, brand.get("name") or "")
        guardrails = draft.get("guardrails") if isinstance(draft.get("guardrails"), dict) else {}
        # NOTE: merge_bifurcation_results does not carry a guardrails
        # key, so in universal mode this fallback is currently ALWAYS
        # taken — the staged guardrails are the template defaults. The
        # provenance label follows the branch actually taken, so it
        # stays truthful if the merge ever carries extracted rails.
        guardrails_from_template = not guardrails or not guardrails.get("tone")
        if guardrails_from_template:
            guardrails = self._default_guardrails()
        staged = {
            "status": "staged",
            "tenant_id": self.tenant_id,
            "vertical": vertical,
            "brand": brand,
            "business_hours": business_hours,
            "menu_items": menu_items,
            "offerings": offerings,
            "faqs": faqs,
            "knowledge_articles": articles,
            "intents": intents,
            "guardrails": guardrails,
            "source_files": [d["filename"] for d in extracted_docs],
            "extraction_mode": "universal",
            "section_sources": self._universal_section_sources(
                draft,
                brand_name_fabricated=brand_name_fabricated,
                guardrails_from_template=guardrails_from_template,
                menu_items=menu_items,
                vertical=vertical,
                offerings=offerings,
            ),
            "stats": {
                "offerings_count": len(offerings),
                "menu_items_count": len(menu_items),
                "faqs_count": len(faqs),
                "articles_count": len(articles),
                "intents_count": len(intents),
            },
        }
        if warning:
            staged["warning"] = warning
        return staged

    def _persist_option_b_as_kb(self, files_data: List[Dict[str, Any]]) -> None:
        """Option B (filled industry kit): store source files as module=kb only.

        Never catalogue/brochure — WhatsApp brochure asks must ignore these.
        """
        if not self.tenant_id or self.tenant_id == "draft_tenant":
            return
        for f in files_data:
            filename = f.get("filename") or "onboarding_kit"
            content = f.get("content") or b""
            if isinstance(content, str):
                import base64
                try:
                    content = base64.b64decode(content)
                except Exception:
                    content = content.encode("utf-8", errors="ignore")
            if not content:
                continue
            ext = Path(filename).suffix.lstrip(".") or "bin"
            module = "kb"
            from routing.config import UPLOAD_DIR as _UPLOAD_DIR
            upload_dir = os.path.join(_UPLOAD_DIR, module)
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, filename)
            public_url = None
            try:
                with open(filepath, "wb") as disk_file:
                    disk_file.write(content)
                if ext.lower() == "pdf":
                    try:
                        from services.media_host import host_via_public_base
                    except ImportError:
                        from backend.services.media_host import host_via_public_base
                    public_url = host_via_public_base(content, filename, subdir=module)
                save_admin_file(
                    filename,
                    ext,
                    module,
                    len(content),
                    file_path=filepath,
                    url=public_url or f"/uploaded_files/{module}/{filename}",
                    tenant_id=self.tenant_id,
                    actor=self.actor,
                )
                logger.info(
                    "OPTION_B_KB_STORED | tenant=%s | file=%s | module=kb",
                    self.tenant_id, filename,
                )
            except Exception as write_err:
                logger.warning(
                    "Option B kb store failed for %s: %s", filename, write_err,
                )

    def stage_files(
        self,
        files_data: List[Dict[str, Any]],
        *,
        lane: str = "any",
    ) -> Dict[str, Any]:
        """Parse files into a reviewable staging draft without committing.

        Complete kits (all 5 core ``[SECTION:]`` markers) use deterministic parsers
        plus heuristic/LLM gap-fill. Incomplete kits and unstructured uploads use
        the universal path (warn + continue for incomplete kits).

        Option B (``lane='kit'``) always persists source files as ``module=kb``.
        """
        if (lane or "").strip().lower() == "kit":
            self._persist_option_b_as_kb(files_data)

        extracted_docs = []
        for f in files_data:
            fname = f.get("filename") or "onboarding_doc.docx"
            content = f.get("content") or b""
            if isinstance(content, str):
                import base64
                content = base64.b64decode(content)
            extracted = self.extract_file_content(fname, content)
            if Path(fname).suffix.lower() == ".pdf":
                extracted["_content"] = content
            extracted_docs.append(extracted)

        combined_text = "\n\n".join(d.get("text") or "" for d in extracted_docs)
        presence = self._kit_section_presence(combined_text)
        any_marker = any(presence.values())
        complete = self._is_complete_kit(combined_text)

        if any_marker and not complete:
            missing = [n for n in self._CORE_KIT_SECTIONS if not presence.get(n)]
            warning = (
                "Incomplete kit — used general extraction. "
                f"Missing sections: {', '.join(missing)}."
            )
            return self._stage_universal(extracted_docs, combined_text, warning=warning)

        if not any_marker:
            return self._stage_universal(extracted_docs, combined_text)

        # ── Complete kit path: deterministic + gap-fill ──────────────────────
        sections = self.partition_sections(extracted_docs)
        brand, business_hours, vertical, profile_extras = self.extract_brand_section(sections["brand"])
        operations = self.extract_operations_domain_section(
            sections.get("operations") or {"text": "", "tables": []}
        )
        heur = heuristic_extract_corpus(combined_text, tenant_id=self.tenant_id)
        for key in ("support_email", "support_phone", "website", "tagline", "name", "bot_name", "signature"):
            hv = (heur.get("brand") or {}).get(key)
            if hv and (not brand.get(key) or _is_placeholder_brand_value(key, brand.get(key), self.tenant_id)):
                brand[key] = hv
        if heur.get("vertical") in VALID_VERTICALS and (
            not vertical or vertical == "generic"
        ):
            vertical = heur["vertical"]

        menu_items = self.extract_menu_section(sections["menu"], vertical=vertical)
        offerings = self.extract_catalog_section(sections["catalog"], vertical=vertical)
        faqs, articles = self.extract_faq_section(
            sections["faqs"], brand_name=brand["name"], vertical=vertical
        )
        intents = self.extract_info_pages_section(
            sections.get("info_pages") or {"text": "", "tables": []}
        )
        guardrails = self.extract_guardrails_section(sections["guardrails"])
        # Greeting may live under guardrails table in older kits
        if guardrails.get("greeting") and not profile_extras.get("greeting"):
            profile_extras["greeting"] = guardrails["greeting"]

        # Gap-fill empty buckets from heuristics
        if heur.get("offerings") and len(offerings) < 3:
            offerings = merge_bifurcation_results(
                {"offerings": offerings},
                {"offerings": heur["offerings"]},
                tenant_id=self.tenant_id,
            ).get("offerings") or offerings
        if heur.get("faqs"):
            faqs = merge_bifurcation_results(
                {"faqs": faqs}, {"faqs": heur["faqs"]}, tenant_id=self.tenant_id
            ).get("faqs") or faqs
        if heur.get("knowledge_articles") and not articles:
            articles = heur["knowledge_articles"]
        # Gap-fill missing info pages only — never overwrite kit answers with
        # longer heuristic/LLM dumps (frontend Technologies/Careers/etc. need kit text).
        def _merge_missing_intents(base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            by_name = {
                str(i.get("name") or "").lower(): dict(i)
                for i in (base or [])
                if isinstance(i, dict) and i.get("name")
            }
            for item in extra or []:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                name = str(item["name"]).lower()
                cur = by_name.get(name)
                if not cur or not str(cur.get("answer") or "").strip():
                    by_name[name] = dict(item)
                    continue
                # Prefer kit keywords/examples if extra has richer lists and kit left defaults only
                if len(item.get("keywords") or []) > len(cur.get("keywords") or []):
                    cur["keywords"] = item["keywords"]
                if len(item.get("examples") or []) > len(cur.get("examples") or []):
                    cur["examples"] = item["examples"]
                by_name[name] = cur
            return list(by_name.values())

        if heur.get("intents"):
            intents = _merge_missing_intents(intents, heur["intents"])

        # Targeted LLM only when core buckets are still empty after heuristics
        need_llm = len(offerings) < 1 or len(faqs) < 1 or (
            vertical == "it_software" and len(intents) < 2
        )
        if need_llm:
            try:
                llm_draft = self.bifurcate_with_llm(extracted_docs)
                offerings = merge_bifurcation_results(
                    {"offerings": offerings},
                    {"offerings": llm_draft.get("offerings") or []},
                    tenant_id=self.tenant_id,
                ).get("offerings") or offerings
                faqs = merge_bifurcation_results(
                    {"faqs": faqs},
                    {"faqs": llm_draft.get("faqs") or []},
                    tenant_id=self.tenant_id,
                ).get("faqs") or faqs
                intents = _merge_missing_intents(intents, llm_draft.get("intents") or [])
                articles = merge_bifurcation_results(
                    {"knowledge_articles": articles},
                    {"knowledge_articles": llm_draft.get("knowledge_articles") or []},
                    tenant_id=self.tenant_id,
                ).get("knowledge_articles") or articles
            except Exception as exc:
                logger.warning("Kit gap-fill LLM failed: %s", exc)

        intents = self._enrich_intents_with_offerings(
            intents, offerings, brand.get("name") or ""
        )
        _attach_pdf_images_to_offerings(offerings, extracted_docs)

        return {
            "status": "staged",
            "tenant_id": self.tenant_id,
            "vertical": vertical,
            "brand": brand,
            "business_hours": business_hours,
            "profile_extras": profile_extras,
            "operations": operations,
            "menu_items": menu_items,
            "offerings": offerings,
            "faqs": faqs,
            "knowledge_articles": articles,
            "intents": intents,
            "guardrails": guardrails,
            "source_files": [d["filename"] for d in extracted_docs],
            # E2E D-E2E-7: stage-time flags for schema-placeholder content;
            # publish rejects these items outright (see apply_bifurcation).
            "placeholder_warnings": placeholder_warnings(
                {"faqs": faqs, "offerings": offerings}
            ),
            "extraction_mode": "kit",
            # Kit documents are parsed section-by-section out of the
            # document itself, so every section is document-derived.
            "section_sources": {
                "brand": "extracted",
                "business_hours": "extracted",
                "guardrails": "extracted",
                "menu": "extracted",
                "offerings": "extracted",
            },
            "kit_sections": presence,
            "stats": {
                "offerings_count": len(offerings),
                "menu_items_count": len(menu_items),
                "faqs_count": len(faqs),
                "articles_count": len(articles),
                "intents_count": len(intents),
                "domain_answers_count": len((operations or {}).get("domain_answers") or []),
            },
        }



    # ──────────────────────────────────────────────────────────────────────────
    # 3. Auto-Configuration & Application Engine
    # ──────────────────────────────────────────────────────────────────────────

    def apply_bifurcation(
        self,
        bifurcation: Dict[str, Any],
        raw_files: List[Dict[str, Any]],
        auto_publish: bool = False,
        careful_brand_merge: bool = True,
        source_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply extracted data across database tables, profile draft, and knowledge base.

        Safety defaults (prevent cross-tenant / cross-vertical contamination):
          * ``auto_publish=False`` — offerings stage as ``is_active=0`` until review
          * ``careful_brand_merge=True`` — only fill empty/placeholder brand fields
          * Tenant vertical is locked; PDF-guessed vertical cannot flip it
          * Off-vertical and junk offering names are rejected
        """
        tenant_id = self.tenant_id
        kb_source = source_file or "auto_bifurcated_docs.md"
        summary: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "vertical": bifurcation.get("vertical", "generic"),
            "brand": {},
            "menu_buttons_count": 0,
            "intents_count": 0,
            "offerings_created": 0,
            "offerings_sample": [],
            "offerings_rejected": 0,
            "offerings_rejected_sample": [],
            "faqs_saved": 0,
            "kb_chunks_indexed": 0,
            "brand_updated": False,
            "version": 0,
            "status": "configured",
            "staged": not bool(auto_publish),
        }

        # 3.1 Sanitize + LOCK vertical to the tenant's registered vertical
        extracted_vertical = bifurcation.get("vertical", "generic")
        if extracted_vertical not in VALID_VERTICALS:
            extracted_vertical = "generic"
        vertical = resolve_locked_vertical(tenant_id, extracted_vertical)
        summary["vertical"] = vertical
        summary["vertical_extracted"] = extracted_vertical
        summary["vertical_locked"] = vertical != extracted_vertical and vertical != "generic"

        # 3.2 Sanitize Brand — keep raw signals separate from filled defaults
        brand_raw = bifurcation.get("brand", {}) or {}
        real_brand: Dict[str, str] = {}
        for key in ("name", "bot_name", "tagline", "website", "support_email", "support_phone", "signature", "logo_url"):
            raw_val = brand_raw.get(key)
            if raw_val and not _is_placeholder_brand_value(key, raw_val, tenant_id):
                val = str(raw_val).strip()
                # PDF/LLM extracts paste notes around contacts
                # ("https://x.co (rates: https://y)", "1800 … · +91 … (overseas)").
                # Keep one clean value so the published profile stays loadable.
                if key == "website":
                    val = tenancy_schemas.extract_first_url(val) or val
                elif key == "support_phone":
                    val = tenancy_schemas.extract_first_valid_phone(val) or val
                real_brand[key] = val

        clean_brand = {
            "name": real_brand.get("name") or tenant_id.replace("-", " ").title(),
            "bot_name": real_brand.get("bot_name") or f"{tenant_id.title()} Assistant",
            "tagline": real_brand.get("tagline") or "",
            "website": real_brand.get("website") or f"https://www.{tenant_id}.com",
            "support_email": real_brand.get("support_email") or f"support@{tenant_id}.com",
            "support_phone": real_brand.get("support_phone") or "+91 9800000000",
            "signature": real_brand.get("signature") or f"{tenant_id.title()} — At your service.",
        }
        if real_brand.get("logo_url"):
            clean_brand["logo_url"] = real_brand["logo_url"]
        if clean_brand["website"] and not clean_brand["website"].startswith("http"):
            clean_brand["website"] = "https://" + clean_brand["website"].lstrip("/")
        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", clean_brand["support_email"]):
            clean_brand["support_email"] = f"support@{tenant_id}.com"

        summary["brand"] = clean_brand if not careful_brand_merge else (real_brand or clean_brand)
        has_real_brand = bool(real_brand.get("name") or real_brand.get("bot_name") or real_brand.get("support_email") or real_brand.get("support_phone"))

        # 3.3 Sanitize WhatsApp Menu Buttons (Meta Character Limitations)
        # Underscore ids + skip duplicates vs vertical defaults / within the extract
        # so PDF uploads do not create menu-products alongside menu_products.
        menu_items_raw = bifurcation.get("menu_items", []) or []
        menu_buttons = _sanitize_menu_buttons(menu_items_raw, vertical=vertical)

        has_real_menu = len(menu_buttons) > 0
        if not menu_buttons and not careful_brand_merge:
            menu_buttons = [
                {"id": "offerings", "title": "Explore Offerings"[:24], "description": "Browse catalog"[:72], "section": "Main"[:24], "intent": "offerings", "sort_order": 1, "enabled": True},
                {"id": "callback", "title": "Book Consultation"[:24], "description": "Request a callback"[:72], "section": "Main"[:24], "flow": "book_callback", "sort_order": 2, "enabled": True},
                {"id": "support", "title": "Customer Help"[:24], "description": "Ask questions"[:72], "section": "Main"[:24], "flow": "raise_ticket", "sort_order": 3, "enabled": True},
            ]
        summary["menu_buttons_count"] = len(menu_buttons)

        # 3.4 Sanitize Intents (preserve answer text for Technologies/Projects/Careers/Benefits)
        intents_raw = bifurcation.get("intents", []) or []
        # Prefill offering-linked intents from catalog in the same draft
        intents_raw = self._enrich_intents_with_offerings(
            list(intents_raw),
            bifurcation.get("offerings") or [],
            (bifurcation.get("brand") or {}).get("name") or clean_brand.get("name") or "",
        )
        clean_intents = []
        for intent in intents_raw:
            i_name = _slugify(intent.get("name") or "custom_intent")
            if i_name in ("technology", "tech_stack"):
                i_name = "technologies"
            elif i_name == "project":
                i_name = "projects"
            elif i_name == "career":
                i_name = "careers"
            elif i_name == "benefit":
                i_name = "benefits"
            examples = [ex.strip() for ex in intent.get("examples", []) if ex.strip()][:5]
            keywords = [kw.strip() for kw in intent.get("keywords", []) if kw.strip()][:10]
            answer = (intent.get("answer") or "").strip()
            entry = {
                "name": i_name,
                "examples": examples,
                "keywords": keywords,
                "flow": intent.get("flow") or None,
                "enabled": True if intent.get("enabled", True) else False,
            }
            if intent.get("description"):
                entry["description"] = str(intent.get("description"))
            if answer:
                entry["answer"] = answer
            clean_intents.append(entry)
        summary["intents_count"] = len(clean_intents)
        summary["info_pages_updated"] = [
            i["name"] for i in clean_intents
            if i["name"] in ("technologies", "projects", "careers", "benefits") and i.get("answer")
        ]

        # 3.5 Update Tenant Profile Draft (fill-empty brand; never wipe real profile)
        draft = tenancy_store.get_draft(tenant_id) or {}
        existing_brand = dict(draft.get("brand") or {})
        should_touch_profile = (
            not careful_brand_merge
            or has_real_brand
            or has_real_menu
            or bool(clean_intents)
            or ("business_hours" in bifurcation and bifurcation.get("business_hours"))
        )

        if should_touch_profile:
            draft["tenant_id"] = tenant_id
            # Never let a random PDF flip an already-set tenant vertical.
            existing_vertical = str(draft.get("vertical") or "").strip().lower()
            if existing_vertical not in VALID_VERTICALS or existing_vertical == "generic":
                draft["vertical"] = vertical
            else:
                draft["vertical"] = existing_vertical
                vertical = existing_vertical

            if careful_brand_merge:
                merged_brand = merge_brand_fill_empty(existing_brand, real_brand, tenant_id)
                if merged_brand.get("website") and not str(merged_brand["website"]).startswith("http"):
                    merged_brand["website"] = "https://" + str(merged_brand["website"]).lstrip("/")
                draft["brand"] = merged_brand or existing_brand
                # Only replace display_name when the tenant has none / placeholder
                incoming_name = real_brand.get("name")
                if incoming_name and (
                    not draft.get("display_name")
                    or is_placeholder_brand_value("name", draft.get("display_name"), tenant_id)
                ):
                    draft["display_name"] = incoming_name
                summary["brand"] = draft.get("brand") or {}
                summary["brand_updated"] = bool(
                    any(
                        (draft.get("brand") or {}).get(k) != existing_brand.get(k)
                        for k in ("name", "bot_name", "tagline", "website", "support_email", "support_phone", "signature", "logo_url")
                    )
                )
            else:
                draft["display_name"] = clean_brand["name"]
                draft["brand"] = clean_brand
                summary["brand_updated"] = True

            # Keep an existing menu unless this extract truly brought buttons
            # (or caller opted into a full non-careful reconfigure).
            if has_real_menu or (not careful_brand_merge and not draft.get("menu")):
                brand_for_menu = draft.get("brand") or clean_brand
                previous_menu = draft.get("menu") or {}
                previous_buttons = (
                    previous_menu.get("buttons") or []
                    if isinstance(previous_menu, dict)
                    else []
                )
                vertical_menu_defaults = (
                    (get_vertical_defaults(vertical) or {}).get("menu", {}).get("buttons") or []
                )
                menu_removals = _menu_replacement_tombstones(
                    menu_buttons,
                    previous_buttons if isinstance(previous_buttons, list) else [],
                    vertical_menu_defaults if isinstance(vertical_menu_defaults, list) else [],
                )
                draft["menu"] = {
                    "key": "kb_main",
                    "header": brand_for_menu.get("name") or clean_brand["name"],
                    "body": "Welcome! How can we assist you today?",
                    "footer": "Select an option or type your query directly",
                    "button_text": "Select Option",
                    "buttons": menu_buttons + menu_removals,
                }

            # Company info-page intents (technologies/projects/careers/benefits)
            # are IT-only — never inject them into banking / ecommerce / etc.
            existing_intents = {
                i["name"]: dict(i)
                for i in draft.get("intents", [])
                if isinstance(i, dict) and i.get("name")
            }
            _IT_INFO = ("technologies", "projects", "careers", "benefits")
            try:
                if vertical == "it_software":
                    for di in (get_vertical_defaults(vertical).get("intents") or []):
                        name = di.get("name")
                        if name in _IT_INFO and name not in existing_intents:
                            existing_intents[name] = dict(di)
                else:
                    for name in _IT_INFO:
                        existing_intents.pop(name, None)
            except Exception:
                # deliberate: vertical-default intents are best-effort
                # enrichment — the extracted intents remain as-is.
                logger.debug("VERTICAL_DEFAULT_INTENTS_SKIPPED | vertical=%s tenant=%s", vertical, tenant_id, exc_info=True)

            if clean_intents:
                for ci in clean_intents:
                    # Skip IT info pages when uploading kits for non-IT verticals
                    if vertical != "it_software" and ci.get("name") in _IT_INFO:
                        continue
                    prev = existing_intents.get(ci["name"], {})
                    merged = {**prev, **{k: v for k, v in ci.items() if v not in (None, "", [], {})}}
                    # Never wipe a prior answer with an empty one
                    if not ci.get("answer") and prev.get("answer"):
                        merged["answer"] = prev["answer"]
                    if not ci.get("examples") and prev.get("examples"):
                        merged["examples"] = prev["examples"]
                    if not ci.get("keywords") and prev.get("keywords"):
                        merged["keywords"] = prev["keywords"]
                    existing_intents[ci["name"]] = merged

            if clean_intents or existing_intents:
                try:
                    from shared.tenancy.defaults import (
                        ensure_company_info_page_intents,
                        strip_company_info_page_intents,
                    )
                    intent_list = list(existing_intents.values())
                    if vertical == "it_software":
                        draft["intents"] = ensure_company_info_page_intents(intent_list)
                    else:
                        draft["intents"] = strip_company_info_page_intents(intent_list)
                except Exception:
                    draft["intents"] = list(existing_intents.values())

            if "business_hours" in bifurcation and bifurcation.get("business_hours"):
                draft["business_hours"] = _sanitize_business_hours(bifurcation["business_hours"])

            # Brand-table extras: notifications, vocabulary, features, greeting, WABA hint
            extras = bifurcation.get("profile_extras") if isinstance(bifurcation.get("profile_extras"), dict) else {}
            extra_flags = merge_profile_extras_into_draft(draft, extras)
            if extra_flags:
                summary["profile_extras_updated"] = list(extra_flags.keys())

            # OPERATIONS & DOMAIN → prompt keywords / domain topics
            operations = bifurcation.get("operations") if isinstance(bifurcation.get("operations"), dict) else {}
            if merge_operations_into_draft(draft, operations):
                summary["operations_updated"] = True
                summary["domain_answers_count"] = len(operations.get("domain_answers") or [])

            # Persist guardrails from kit / review modal (previously dropped)
            rails = bifurcation.get("guardrails")
            if isinstance(rails, dict) and (
                rails.get("tone")
                or rails.get("never_state")
                or rails.get("escalate_keywords")
                or rails.get("forbidden_terms")
                or rails.get("handoff_keywords")
            ):
                existing_rails = dict(draft.get("guardrails") or {})
                if rails.get("tone"):
                    existing_rails["tone"] = str(rails["tone"])
                if isinstance(rails.get("never_state"), list) and rails["never_state"]:
                    existing_rails["never_state"] = [str(x).strip() for x in rails["never_state"] if str(x).strip()]
                if isinstance(rails.get("escalation_triggers"), list) and rails["escalation_triggers"]:
                    existing_rails["escalate_keywords"] = [
                        str(x).strip() for x in rails["escalation_triggers"] if str(x).strip()
                    ]
                if isinstance(rails.get("escalate_keywords"), list) and rails["escalate_keywords"]:
                    existing_rails["escalate_keywords"] = [
                        str(x).strip() for x in rails["escalate_keywords"] if str(x).strip()
                    ]
                if isinstance(rails.get("forbidden_terms"), list) and rails["forbidden_terms"]:
                    existing_rails["forbidden_terms"] = [
                        str(x).strip() for x in rails["forbidden_terms"] if str(x).strip()
                    ]
                if isinstance(rails.get("handoff_keywords"), list) and rails["handoff_keywords"]:
                    existing_rails["handoff_keywords"] = [
                        str(x).strip().lower() for x in rails["handoff_keywords"] if str(x).strip()
                    ]
                if rails.get("escalation_rules"):
                    existing_rails["escalation_message"] = str(rails["escalation_rules"])
                if rails.get("escalation_message"):
                    existing_rails["escalation_message"] = str(rails["escalation_message"])
                draft["guardrails"] = existing_rails
                summary["guardrails_updated"] = True

            # Keep menu greeting when kit brand extras set body before menu overwrite
            if extras.get("greeting") and isinstance(draft.get("menu"), dict):
                draft["menu"]["body"] = str(extras["greeting"])

            bot_name = (draft.get("brand") or {}).get("bot_name") or clean_brand["bot_name"]
            if "flows" not in draft or not draft["flows"]:
                draft["flows"] = [
                    {"name": "book_callback", "success_message": f"Thank you! Your consultation request has been scheduled with {bot_name}."},
                    {"name": "get_quote", "success_message": "Thank you! Our specialist will prepare a customized quote and follow up promptly."},
                    {"name": "raise_ticket", "success_message": "Your request has been logged. A support ticket has been created and an executive will assist you."},
                ]

        # 3.6 Insert Offerings into Catalog — staged unless auto_publish
        offerings_raw = bifurcation.get("offerings", []) or []
        accepted_offerings, rejected_offerings = filter_offerings_for_tenant(
            offerings_raw, locked_vertical=vertical
        )
        summary["offerings_rejected"] = len(rejected_offerings)
        summary["offerings_rejected_sample"] = [
            f"{r.get('name')} ({r.get('reason')})" for r in rejected_offerings[:8]
        ]
        if rejected_offerings:
            logger.info(
                "BIFURCATION_OFFERINGS_REJECTED | tenant=%s | vertical=%s | count=%s | sample=%s",
                tenant_id, vertical, len(rejected_offerings), summary["offerings_rejected_sample"],
            )

        existing_products = {p.get("name", "").lower(): p for p in list_products(tenant_id=tenant_id, active_only=False)}
        # Slugs already held by this tenant's rows; other tenants are checked
        # live in _ensure_free_slug because products.slug is UNIQUE globally.
        used_slugs = {
            (p.get("slug") or "").lower() for p in existing_products.values() if p.get("slug")
        }
        inserted_count = 0
        active_flag = 1 if auto_publish else 0

        for off in accepted_offerings:
            name = (off.get("name") or "").strip()
            if not name:
                continue
            cat = (off.get("category") or "General").strip()
            price = (str(off.get("price") or "")).strip() or None
            # E2E D-E2E-2: carry the staged list price through as MRP.
            mrp = (str(off.get("mrp") or "")).strip() or None
            short_desc = (off.get("short_description") or name).strip()
            desc = (off.get("description") or short_desc).strip()
            attrs = off.get("attrs") or {}
            slug = _slugify(name)
            media_url = (off.get("media_url") or "").strip() or None
            if not media_url and isinstance(attrs, dict):
                for k in ("media_url", "image_url", "image", "photo"):
                    v = attrs.get(k)
                    if isinstance(v, str) and v.strip():
                        media_url = v.strip()
                        break
            media_type = (off.get("media_type") or "image").strip() or "image"
            if media_url and media_url.lower().endswith(".pdf"):
                media_type = "pdf"

            if name.lower() in existing_products and existing_products[name.lower()].get("id"):
                prod_id = existing_products[name.lower()]["id"]
                update_kwargs = dict(
                    tenant_id=tenant_id,
                    price=price,
                    description=desc,
                    short_description=short_desc,
                    category=cat,
                    attrs_json=attrs,
                    is_active=active_flag,
                )
                if mrp:
                    update_kwargs["mrp"] = mrp
                if media_url:
                    update_kwargs["media_url"] = media_url
                    update_kwargs["media_type"] = media_type
                update_product(prod_id, **update_kwargs)
            else:
                slug = _ensure_free_slug(slug, used_slugs, tenant_id)
                try:
                    prod_id = save_product(
                        name=name,
                        slug=slug,
                        category=cat,
                        price=price,
                        mrp=mrp or "",
                        short_description=short_desc,
                        description=desc,
                        attrs_json=attrs,
                        media_url=media_url,
                        media_type=media_type,
                        tenant_id=tenant_id,
                        actor=self.actor,
                        is_active=active_flag,
                    )
                except sqlite3.IntegrityError as slug_exc:
                    # Same-name/same-slug races must not abort the whole
                    # publish after the profile version is already out.
                    logger.warning(
                        "OFFERING_INSERT_SKIPPED | tenant=%s | name=%s | slug=%s | %s",
                        tenant_id, name, slug, slug_exc,
                    )
                    continue
                existing_products[name.lower()] = {"id": prod_id, "slug": slug}
                inserted_count += 1
                if len(summary["offerings_sample"]) < 5:
                    summary["offerings_sample"].append(name)

        summary["offerings_created"] = inserted_count

        # 3.6b Persist FAQ Q&A pairs into faq_dataset (live index)
        faqs_raw = bifurcation.get("faqs", []) or []
        # E2E D-E2E-7: extraction-schema placeholder echoes must never be
        # written live — reject them here (both publish doors funnel through
        # apply_bifurcation) and surface the rejection in the summary.
        faqs_publishable, faqs_rejected = filter_faqs_for_publish(faqs_raw)
        summary["faqs_rejected"] = len(faqs_rejected)
        summary["faqs_rejected_sample"] = [
            f"{r.get('question')} ({r.get('reason')})" for r in faqs_rejected[:8]
        ]
        if faqs_rejected:
            logger.info(
                "BIFURCATION_FAQS_REJECTED | tenant=%s | count=%s | sample=%s",
                tenant_id, len(faqs_rejected), summary["faqs_rejected_sample"],
            )
        faqs_saved = self._save_faq_pairs(faqs_publishable, source_file=kb_source)
        summary["faqs_saved"] = faqs_saved

        # 3.7 Write Knowledge Base Markdown Files & Build Vector Embeddings
        knowledge_dir = BACKEND_CLIENTS_DIR / tenant_id / "knowledge"
        knowledge_dir.mkdir(parents=True, exist_ok=True)

        articles = bifurcation.get("knowledge_articles", []) or []
        all_chunks_to_index: List[str] = []

        for idx, art in enumerate(articles):
            title = art.get("title", f"Knowledge Article {idx+1}")
            md_content = art.get("markdown_content", "")
            file_slug = f"{idx+1:02d}_{_slugify(title)}.md"
            target_path = knowledge_dir / file_slug
            try:
                target_path.write_text(md_content, encoding="utf-8")
            except Exception as e:
                logger.warning("Could not write knowledge file %s: %s", target_path, e)

            sections = md_content.split("\n## ")
            for sec in sections:
                if sec.strip():
                    all_chunks_to_index.append(sec.strip())

        for rf in raw_files:
            txt = rf.get("text", "")
            if txt:
                paragraphs = [p.strip() for p in txt.split("\n\n") if len(p.strip()) > 80]
                all_chunks_to_index.extend(paragraphs[:50])

        if all_chunks_to_index:
            try:
                formatted_chunks = [
                    {"content": ch, "source_file": kb_source}
                    for ch in all_chunks_to_index
                ]
                save_knowledge_base_chunks(
                    chunks=formatted_chunks,
                    source_file=kb_source,
                    tenant_id=tenant_id,
                )
                try:
                    from kb.services.rag import (
                        build_chunks_for_kb,
                        generate_missing_embeddings,
                        rebuild_faiss_index,
                        rebuild_product_faiss_index,
                    )
                    build_chunks_for_kb()
                    generate_missing_embeddings()
                    rebuild_faiss_index()
                    try:
                        rebuild_product_faiss_index()
                    except Exception as prod_err:
                        logger.warning("Product FAISS rebuild error: %s", prod_err)
                    logger.info("VECTOR_RAG_INDEX_REBUILT | tenant=%s | chunks=%d", tenant_id, len(all_chunks_to_index))
                    try:
                        from kb.services.brain import clear_response_cache
                        clear_response_cache()
                    except Exception:
                        logger.exception("RESPONSE_CACHE_CLEAR_FAILED | tenant=%s", tenant_id)
                except Exception as rag_err:
                    logger.warning("RAG vector rebuild error: %s", rag_err)

                summary["kb_chunks_indexed"] = len(all_chunks_to_index)
            except Exception as chunk_err:
                logger.error("Failed storing knowledge base chunks: %s", chunk_err)

        # Offerings may have been written even when no KB chunks were produced —
        # always refresh the product vector index after a live catalog write.
        if inserted_count > 0 and not all_chunks_to_index:
            try:
                from kb.services.rag import rebuild_product_faiss_index
                rebuild_product_faiss_index()
            except Exception as prod_err:
                logger.warning("Product FAISS rebuild (offerings-only) failed: %s", prod_err)

        # 3.8 Save + publish the profile LAST. Draft/catalog/KB failures above
        # must never leave a published-but-half-configured tenant behind —
        # publishing first is exactly what stranded sbi on an invalid v2 while
        # the catalog INSERT crashed.
        if should_touch_profile:
            with get_db_context() as conn:
                tenancy_store.save_draft(tenant_id, draft, updated_by=self.actor.get("username", "admin"), conn=conn)
                if auto_publish:
                    # Gate before writing a version: an invalid profile must
                    # stay a draft, never become the loader's current version
                    # (store.publish_version enforces this too, since 2026-10).
                    try:
                        tenancy_loader.validate_merged_profile(tenant_id, draft)
                    except Exception as val_exc:
                        summary["publish_blocked"] = str(val_exc)[:400]
                        logger.error(
                            "AUTO_BIFURCATION_PUBLISH_BLOCKED | tenant=%s | draft kept unpublished: %s",
                            tenant_id, val_exc,
                        )
                    else:
                        try:
                            version = tenancy_store.publish_version(
                                tenant_id, draft, published_by=self.actor.get("username", "admin"), conn=conn
                            )
                            summary["version"] = version
                            tenancy_cache.purge(tenant_id)
                            logger.info("AUTO_BIFURCATION_PUBLISHED | tenant=%s | v=%s", tenant_id, version)
                        except Exception as pub_exc:
                            logger.warning("Could not auto-publish version directly: %s", pub_exc)

            try:
                tenancy_loader.write_client_config_file(tenant_id, draft)
            except Exception as file_exc:
                logger.warning("Could not sync client config file: %s", file_exc)

        try:
            with get_db_context() as conn:
                record_admin_audit_event(
                    conn,
                    action="tenant_auto_bifurcated",
                    actor=self.actor,
                    resource_type="tenant",
                    resource_id=tenant_id,
                    tenant_id=tenant_id,
                    details={
                        "vertical": vertical,
                        "brand": (summary.get("brand") or {}).get("name"),
                        "offerings_count": inserted_count,
                        "faqs_saved": faqs_saved,
                        "menu_buttons_count": len(menu_buttons),
                        "intents_count": len(clean_intents),
                        "chunks_count": summary["kb_chunks_indexed"],
                        "careful_brand_merge": careful_brand_merge,
                        "source_file": kb_source,
                    },
                )
        except Exception:
            logger.exception("AUDIT_EVENT_RECORD_FAILED | action=tenant_auto_bifurcated tenant=%s", tenant_id)

        return summary

    def _save_faq_pairs(self, faqs: List[Dict[str, Any]], source_file: str) -> int:
        """Persist extracted Q&A into faq_dataset and rebuild the FAQ index."""
        if not faqs:
            return 0
        saved = 0
        try:
            chunks = []
            for f in faqs:
                q = (f.get("question") or "").strip()
                a = (f.get("answer") or "").strip()
                if not q or not a:
                    continue
                chunks.append({
                    "source_file": source_file,
                    "content_type": "qa",
                    "content": f"Q: {q}\nA: {a}",
                    "page_number": 1,
                })
            if not chunks:
                return 0
            # Drop prior auto-extracted QAs for this source so re-process does not
            # duplicate FAQ index rows (leave non-qa raw extract chunks intact).
            try:
                with get_db_context() as conn:
                    if self.tenant_id is not None:
                        conn.execute(
                            "DELETE FROM faq_dataset WHERE source_file = ? AND tenant_id = ? AND content_type = 'qa'",
                            (source_file, self.tenant_id),
                        )
                    else:
                        conn.execute(
                            "DELETE FROM faq_dataset WHERE source_file = ? AND content_type = 'qa'",
                            (source_file,),
                        )
            except Exception as purge_err:
                logger.warning("FAQ QA purge before save failed: %s", purge_err)
            # E2E D-E2E-6 (known D-08): a Q&A already live for this tenant
            # under a DIFFERENT source file must not be written again — the
            # purge above only covers this same source. Key on the parsed,
            # normalised question+answer so the two writers' formats
            # ("Q:/A:" vs "Question:/Answer:") collide correctly; also
            # dedupe repeats inside this batch.
            try:
                with get_db_context() as conn:
                    if self.tenant_id is not None:
                        rows = conn.execute(
                            "SELECT content FROM faq_dataset WHERE tenant_id = ? AND content_type = 'qa'",
                            (self.tenant_id,),
                        ).fetchall()
                    else:
                        rows = conn.execute(
                            "SELECT content FROM faq_dataset WHERE content_type = 'qa'"
                        ).fetchall()
                seen_keys = set()
                for r in rows:
                    key = qa_key_from_content(r[0])
                    if key:
                        seen_keys.add(key)
                unique_chunks = []
                for ch in chunks:
                    key = qa_key_from_content(ch["content"])
                    if key and key in seen_keys:
                        continue
                    if key:
                        seen_keys.add(key)
                    unique_chunks.append(ch)
                if len(unique_chunks) < len(chunks):
                    logger.info(
                        "FAQ_PAIRS_DEDUPED | tenant=%s | skipped=%d | source=%s",
                        self.tenant_id, len(chunks) - len(unique_chunks), source_file,
                    )
                chunks = unique_chunks
            except Exception as dedupe_err:
                logger.warning("FAQ dedupe check failed (saving all): %s", dedupe_err)
            if not chunks:
                return 0
            save_chunks(chunks, module="faq", tenant_id=self.tenant_id, replace_existing=False)
            saved = len(chunks)
            try:
                from faq.service import faq_index
                faq_index.build(force=True)
            except Exception as idx_err:
                logger.warning("FAQ index rebuild after bifurcation failed: %s", idx_err)
            logger.info("FAQ_PAIRS_SAVED | tenant=%s | count=%d | source=%s", self.tenant_id, saved, source_file)
        except Exception as exc:
            logger.error("Failed saving FAQ pairs: %s", exc)
        return saved

    def process_document_bytes(
        self,
        filename: str,
        content: bytes,
        auto_publish: bool = False,
        careful_brand_merge: bool = True,
    ) -> Dict[str, Any]:
        """Extract + bifurcate a single already-saved upload for this tenant."""
        doc_data = self.extract_file_content(filename, content)
        if Path(filename).suffix.lower() == ".pdf":
            doc_data["_content"] = content
        bifurcation = self.bifurcate_with_llm([doc_data])
        _attach_pdf_images_to_offerings(
            bifurcation.get("offerings") or [],
            [doc_data],
        )
        locked_vertical = resolve_locked_vertical(
            self.tenant_id, bifurcation.get("vertical") or "generic"
        )
        bifurcation["vertical"] = locked_vertical
        # Guarantee vertical-aware menu when LLM/heuristics left it thin
        if len(bifurcation.get("menu_items") or []) < 3:
            bifurcation["menu_items"] = self.extract_menu_section(
                {"text": "", "tables": []},
                vertical=locked_vertical,
            )
        result = self.apply_bifurcation(
            bifurcation=bifurcation,
            raw_files=[doc_data],
            auto_publish=auto_publish,
            careful_brand_merge=careful_brand_merge,
            source_file=filename,
        )
        result["files_processed"] = [filename]
        result["tenant_id"] = self.tenant_id
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # 4. High-Level Coordinator
    # ──────────────────────────────────────────────────────────────────────────

    def process_files(
        self,
        files_data: List[Dict[str, Any]],
        auto_publish: bool = False,
        careful_brand_merge: bool = True,
    ) -> Dict[str, Any]:
        """End-to-end extraction, bifurcation, and system configuration.

        Defaults to staged offerings + careful brand merge so a wrong PDF cannot
        go live or overwrite an existing tenant profile.
        """
        extracted_docs = []

        for f in files_data:
            filename = f.get("filename") or "document.pdf"
            content = f.get("content") or b""
            if isinstance(content, str):
                import base64
                content = base64.b64decode(content)

            ext = Path(filename).suffix.lstrip(".")
            # Default process_files path: keep as kb so extraction sources are never
            # auto-sent as WhatsApp brochures. Option B also uses stage_files(lane=kit)
            # which always persists as kb via _persist_option_b_as_kb.
            module = "kb"
            from routing.config import UPLOAD_DIR as _UPLOAD_DIR
            upload_dir = os.path.join(_UPLOAD_DIR, module)
            os.makedirs(upload_dir, exist_ok=True)
            filepath = os.path.join(upload_dir, filename)
            public_url = None
            try:
                with open(filepath, "wb") as disk_file:
                    disk_file.write(content)
                if ext.lower() == "pdf":
                    try:
                        from services.media_host import host_via_public_base
                    except ImportError:
                        from backend.services.media_host import host_via_public_base
                    public_url = host_via_public_base(
                        content, filename, subdir=module
                    )
                    # Split FAQ/KB pages into derivative PDFs; keep the parent
                    # for bifurcation (cleanup_original=False).
                    try:
                        from services.pdf_section_extractor import auto_extract_and_register
                        ext_res = auto_extract_and_register(
                            source_filepath=filepath,
                            original_filename=filename,
                            tenant_id=self.tenant_id,
                            actor=self.actor,
                            cleanup_original=False,
                        )
                        if ext_res.get("faq_pdf") or ext_res.get("kb_pdf"):
                            logger.info(
                                "AUTO_BIFURCATOR_PDF_EXTRACTED | %s | faq=%s | kb=%s",
                                filename,
                                ext_res.get("faq_pdf"),
                                ext_res.get("kb_pdf"),
                            )
                    except Exception as sec_err:
                        logger.warning(
                            "Auto-extraction of PDF sections failed in auto_bifurcator: %s",
                            sec_err,
                        )
                save_admin_file(
                    filename,
                    ext,
                    module,
                    len(content),
                    file_path=filepath,
                    url=public_url or f"/uploaded_files/{module}/{filename}",
                    tenant_id=self.tenant_id,
                    actor=self.actor,
                )
            except Exception as write_err:
                logger.warning("Could not save admin file %s: %s", filename, write_err)

            # Extract content
            doc_data = self.extract_file_content(filename, content)
            if Path(filename).suffix.lower() == ".pdf":
                doc_data["_content"] = content
            extracted_docs.append(doc_data)

        # Run LLM semantic bifurcation
        bifurcation = self.bifurcate_with_llm(extracted_docs)
        _attach_pdf_images_to_offerings(
            bifurcation.get("offerings") or [],
            extracted_docs,
        )
        locked_vertical = resolve_locked_vertical(
            self.tenant_id, bifurcation.get("vertical") or "generic"
        )
        bifurcation["vertical"] = locked_vertical

        primary_source = extracted_docs[0]["filename"] if extracted_docs else "auto_bifurcated_docs.md"
        result_summary = self.apply_bifurcation(
            bifurcation=bifurcation,
            raw_files=extracted_docs,
            auto_publish=auto_publish,
            careful_brand_merge=careful_brand_merge,
            source_file=primary_source,
        )

        result_summary["files_processed"] = [d["filename"] for d in extracted_docs]
        return result_summary
