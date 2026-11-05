"""PDF Section Extractor Service.

When a document is uploaded through the Uploads section in the Admin Console:
1. AI & Structural Analysis identifies the FAQ section and creates a small PDF
   containing only the FAQ-related content (e.g. <basename>_faq.pdf).
   This is placed into the FAQ section (module='faq', Screenshot 1).

2. AI & Structural Analysis identifies the Knowledge Base article section
   (headings with large characters, articles, policies, company overview)
   and creates a small PDF containing only the KB-related content (e.g. <basename>_kb.pdf).
   This is placed into the Knowledge Base section (module='kb', Screenshot 2).

3. The unbifurcated original parent entry is cleaned up so ONLY the extracted
   parts appear in their respective sections in the Admin Console.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("pdf_section_extractor")


# ── Page Classification ───────────────────────────────────────────────────────

def _compute_page_scores(page, pno: int) -> Tuple[int, int]:
    """Compute (faq_score, kb_score) for a single page of a PDF document."""
    text = page.get_text()
    lower_text = text.lower()

    faq_score = 0
    kb_score = 0

    # 1. Direct explicit section headers
    if re.search(r"\[SECTION:\s*FAQS?[^\]]*\]", text, re.I):
        faq_score += 25
    if re.search(r"^\s*(?:#+\s*)?(?:FAQ|FAQS|Frequently Asked Questions|Questions\s*&\s*Answers)\b", text, re.I | re.M):
        faq_score += 20
    if "customer question" in lower_text and "official verified answer" in lower_text:
        faq_score += 20

    # Count Q&A pairs on page
    qa_patterns = len(re.findall(r"(?:^|\n)\s*(?:Q(?:uestion)?\s*[\d.:–-]|Customer Question\b)", text, re.I))
    if qa_patterns >= 2:
        faq_score += qa_patterns * 3

    # Check for KB explicit section headers
    if re.search(r"\[SECTION:\s*(?:KNOWLEDGE\s*BASE|INFO\s*PAGES?|ARTICLES?|POLICIES)[^\]]*\]", text, re.I):
        kb_score += 25
    if "article title" in lower_text and "article content" in lower_text:
        kb_score += 25
    if re.search(r"\b(home\s*buying\s*journey|booking\s*process|policies|company\s*overview|about\s+us|terms\s*&\s*conditions)\b", lower_text):
        kb_score += 10

    # 2. Inspect font sizes and headings ("large character" analysis)
    page_dict = page.get_text("dict")
    span_sizes = []
    for b in page_dict.get("blocks", []):
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                t = s.get("text", "").strip()
                if len(t) >= 15:
                    span_sizes.append(s.get("size", 0))

    body_font_size = sorted(span_sizes)[len(span_sizes) // 2] if span_sizes else 9.0

    for b in page_dict.get("blocks", []):
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                t = s.get("text", "").strip()
                size = s.get("size", 0)
                flags = s.get("flags", 0)
                is_bold = bool(flags & 16 or flags & 2)

                # Heading detection: font size larger than body or bold title
                is_heading = (size >= body_font_size * 1.15) or (is_bold and size >= body_font_size)
                if is_heading and 3 < len(t) < 100:
                    t_lower = t.lower()
                    if any(w in t_lower for w in ("faq", "frequently asked", "q&a", "question")):
                        faq_score += 5
                    if any(w in t_lower for w in ("article", "journey", "overview", "about", "policy", "policies", "terms", "guide", "technology", "technologies", "benefits", "projects", "info")):
                        kb_score += 5

    # 3. Suppress non-applicable pages
    # Catalog / Offerings pages are not FAQ or KB articles
    if re.search(r"\[SECTION:\s*CATALOG\s*&\s*OFFERINGS\]", text, re.I) or "catalog images" in lower_text:
        faq_score = 0
        kb_score = 0

    # Brand & profile page is onboarding intro
    if re.search(r"\[SECTION:\s*BRAND\s*&\s*PROFILE\]", text, re.I) and "customer question" not in lower_text:
        faq_score = 0
        if kb_score < 10:
            kb_score = 0

    # Internal bot configurations, strict guardrails, and parameters are NOT Knowledge Base
    if re.search(r"\[SECTION:\s*(?:BOT\s*PERSONA|GUARDRAILS?|SYSTEM\s*PROMPT)[^\]]*\]", text, re.I):
        if "article title" not in lower_text and "[section: info" not in lower_text:
            faq_score = 0
            kb_score = 0

    if ("strict guardrails" in lower_text or "rule & guideline" in lower_text) and "article title" not in lower_text:
        kb_score = 0

    return faq_score, kb_score


def _strip_persona_and_guardrails(doc) -> None:
    """Remove bot persona, guardrails, conflict tables, and internal parameter tables
    from pages of the Knowledge Base PDF so it strictly contains knowledge base
    articles, company guides, policies, and info pages."""
    import fitz
    markers = [
        "[SECTION: BOT PERSONA",
        "[SECTION: GUARDRAIL",
        "[SECTION: PROMPT",
        "[SECTION: SYSTEM PROMPT",
        "[SECTION: ONBOARDING CONFIG",
        "Strict Guardrails",
        "Rule & Guideline",
        "Tone & Style",
        "Welcome Greeting",
    ]
    for page in doc:
        # 1. Redact non-KB internal tables/notes above [SECTION: KNOWLEDGE BASE ARTICLES
        kb_rects = page.search_for("[SECTION: KNOWLEDGE BASE ARTICLES")
        if kb_rects:
            r = kb_rects[0]
            if r.y0 > 80:
                redact_box = fitz.Rect(0, 45, page.rect.x1, r.y0 - 5)
                page.add_redact_annot(redact_box, fill=(1, 1, 1))
                page.apply_redactions()
                logger.info("STRIPPED_PRE_KB_INTERNAL_TABLES | rect=%s", redact_box)

        # 2. Redact bot persona & guardrails
        for marker in markers:
            rects = page.search_for(marker)
            if rects:
                r = rects[0]
                redact_box = fitz.Rect(0, max(0, r.y0 - 6), page.rect.x1, page.rect.y1)
                page.add_redact_annot(redact_box, fill=(1, 1, 1))
                page.apply_redactions()
                logger.info("STRIPPED_NON_KB_SECTION | marker='%s' | rect=%s", marker, redact_box)
                break


def extract_sections_from_pdf(
    source_path: str,
    output_dir: str,
    base_name: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Analyze source PDF and produce small derivative PDFs for FAQ and KB sections."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF (fitz) not installed – cannot extract PDF sections.")
        return None, None

    try:
        doc = fitz.open(source_path)
    except Exception as exc:
        logger.error("Cannot open PDF %s: %s", source_path, exc)
        return None, None

    faq_pages: List[int] = []
    kb_pages: List[int] = []

    for pno in range(len(doc)):
        try:
            page = doc[pno]
            faq_s, kb_s = _compute_page_scores(page, pno)

            if faq_s >= 8 and faq_s > kb_s:
                faq_pages.append(pno)
            elif kb_s >= 8 and kb_s >= faq_s:
                kb_pages.append(pno)
        except Exception as exc:
            logger.warning("Error evaluating page %d in %s: %s", pno, source_path, exc)

    doc.close()

    faq_pages = sorted(set(faq_pages))
    kb_pages = sorted(set(kb_pages))

    logger.info("PDF_PAGES_CLASSIFIED | file=%s | faq_pages=%s | kb_pages=%s", base_name, faq_pages, kb_pages)

    os.makedirs(output_dir, exist_ok=True)
    faq_path: Optional[str] = None
    kb_path: Optional[str] = None

    # Write FAQ PDF
    if faq_pages:
        faq_filename = f"{base_name}_faq.pdf"
        faq_path = os.path.join(output_dir, faq_filename)
        try:
            src = fitz.open(source_path)
            out = fitz.open()
            for pno in faq_pages:
                out.insert_pdf(src, from_page=pno, to_page=pno)
            out.save(faq_path)
            out.close()
            src.close()
            logger.info("FAQ_PDF_CREATED | file=%s | pages=%s | bytes=%d", faq_filename, faq_pages, os.path.getsize(faq_path))
        except Exception as exc:
            logger.error("Failed writing FAQ PDF for %s: %s", base_name, exc)
            faq_path = None

    # Write KB PDF
    if kb_pages:
        kb_filename = f"{base_name}_kb.pdf"
        kb_path = os.path.join(output_dir, kb_filename)
        try:
            src = fitz.open(source_path)
            out = fitz.open()
            for pno in kb_pages:
                out.insert_pdf(src, from_page=pno, to_page=pno)
            # Remove any bot persona / guardrails / parameters so KB PDF is 100% clean
            _strip_persona_and_guardrails(out)
            out.save(kb_path)
            out.close()
            src.close()
            logger.info("KB_PDF_CREATED | file=%s | pages=%s | bytes=%d", kb_filename, kb_pages, os.path.getsize(kb_path))
        except Exception as exc:
            logger.error("Failed writing KB PDF for %s: %s", base_name, exc)
            kb_path = None

    return faq_path, kb_path


def auto_extract_and_register(
    source_filepath: str,
    original_filename: str,
    tenant_id: Optional[str],
    actor: dict,
    cleanup_original: bool = True,
) -> dict:
    """Extract FAQ & KB PDFs from *source_filepath* and register them in admin_files.

    The extracted FAQ PDF is placed in the FAQ section (module='faq').
    The extracted KB PDF is placed in the Knowledge base section (module='kb').
    The unbifurcated original parent entry is removed from admin_files when
    derivatives are successfully produced.
    """
    result = {"faq_pdf": None, "kb_pdf": None}

    ext = Path(original_filename).suffix.lower()
    if ext != ".pdf":
        return result

    upload_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "uploaded_files")
    )
    base_name = Path(original_filename).stem
    # Remove any existing suffix if re-run
    base_name = re.sub(r"_(?:faq|kb)(?:_extracted)?$", "", base_name)

    faq_dir = os.path.join(upload_dir, "faq")
    kb_dir = os.path.join(upload_dir, "kb")

    faq_path, kb_path = extract_sections_from_pdf(
        source_path=source_filepath,
        output_dir=upload_dir,
        base_name=base_name,
    )

    # 1. Register FAQ PDF in FAQ module (Screenshot 1)
    if faq_path and os.path.exists(faq_path):
        try:
            os.makedirs(faq_dir, exist_ok=True)
            faq_filename = os.path.basename(faq_path)
            faq_dest = os.path.join(faq_dir, faq_filename)
            if os.path.abspath(faq_path) != os.path.abspath(faq_dest):
                os.replace(faq_path, faq_dest)

            from database import save_admin_file, save_chunks, update_admin_file_status
            save_admin_file(
                faq_filename,
                "pdf",
                "faq",
                os.path.getsize(faq_dest),
                file_path=faq_dest,
                url=f"/uploaded_files/faq/{faq_filename}",
                tenant_id=tenant_id,
                actor=actor,
                status="processing",
                chunks_count=0,
            )

            # Extract chunks for FAQ
            try:
                from extract import process_file, results_to_chunks
                results = process_file(faq_dest)
                chunks_count = 0
                if results:
                    chunks = results_to_chunks(results)
                    if chunks:
                        save_chunks(
                            chunks,
                            module="faq",
                            media_url=f"/uploaded_files/faq/{faq_filename}",
                            media_type="pdf",
                            tenant_id=tenant_id,
                        )
                        chunks_count = len(chunks)
                update_admin_file_status(
                    faq_filename,
                    status="ready",
                    chunks_count=chunks_count,
                    tenant_id=tenant_id,
                )
                logger.info("FAQ_INDEXED | file=%s | chunks=%d", faq_filename, chunks_count)
            except Exception as idx_err:
                logger.warning("FAQ indexing error for %s: %s", faq_filename, idx_err)
                update_admin_file_status(faq_filename, status="ready", chunks_count=0, tenant_id=tenant_id)

            result["faq_pdf"] = faq_filename
        except Exception as exc:
            logger.error("Failed registering FAQ PDF: %s", exc)

    # 2. Register KB PDF in Knowledge Base module (Screenshot 2)
    if kb_path and os.path.exists(kb_path):
        try:
            os.makedirs(kb_dir, exist_ok=True)
            kb_filename = os.path.basename(kb_path)
            kb_dest = os.path.join(kb_dir, kb_filename)
            if os.path.abspath(kb_path) != os.path.abspath(kb_dest):
                os.replace(kb_path, kb_dest)

            from database import save_admin_file, save_knowledge_base_chunks, update_admin_file_status
            save_admin_file(
                kb_filename,
                "pdf",
                "kb",
                os.path.getsize(kb_dest),
                file_path=kb_dest,
                url=f"/uploaded_files/kb/{kb_filename}",
                tenant_id=tenant_id,
                actor=actor,
                status="processing",
                chunks_count=0,
            )

            # Extract chunks for KB
            try:
                from extract import process_file, results_to_chunks
                results = process_file(kb_dest)
                kb_chunks = []
                if results:
                    raw_chunks = results_to_chunks(results)
                    kb_chunks = [
                        {"content": c, "source_file": kb_filename} if isinstance(c, str) else c
                        for c in raw_chunks
                    ]
                    if kb_chunks:
                        save_knowledge_base_chunks(
                            chunks=kb_chunks,
                            source_file=kb_filename,
                            tenant_id=tenant_id,
                        )
                        try:
                            from kb.services.rag import build_chunks_for_kb, generate_missing_embeddings, rebuild_faiss_index
                            build_chunks_for_kb()
                            generate_missing_embeddings()
                            rebuild_faiss_index()
                        except Exception as rag_err:
                            logger.warning("KB FAISS rebuild warning: %s", rag_err)

                update_admin_file_status(
                    kb_filename,
                    status="ready",
                    chunks_count=len(kb_chunks),
                    tenant_id=tenant_id,
                )
                logger.info("KB_INDEXED | file=%s | chunks=%d", kb_filename, len(kb_chunks))
            except Exception as idx_err:
                logger.warning("KB indexing error for %s: %s", kb_filename, idx_err)
                update_admin_file_status(kb_filename, status="ready", chunks_count=0, tenant_id=tenant_id)

            result["kb_pdf"] = kb_filename
        except Exception as exc:
            logger.error("Failed registering KB PDF: %s", exc)

    # 3. Clean up the unbifurcated raw original entry from admin_files
    # so ONLY the extracted FAQ and KB small PDFs appear in their respective tabs
    if cleanup_original and (result["faq_pdf"] or result["kb_pdf"]):
        try:
            from database import delete_admin_file, delete_file_chunks, delete_knowledge_base_file
            if original_filename != result.get("faq_pdf") and original_filename != result.get("kb_pdf"):
                delete_admin_file(original_filename, tenant_id=tenant_id, actor=actor)
                delete_file_chunks(original_filename, tenant_id=tenant_id)
                delete_knowledge_base_file(original_filename, tenant_id=tenant_id)
                logger.info("CLEANED_UP_RAW_PARENT_FILE | file=%s | tenant=%s", original_filename, tenant_id)
        except Exception as cln_err:
            logger.debug("Cleanup of parent file error: %s", cln_err)

    return result
