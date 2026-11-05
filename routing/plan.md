Good question — and since we're in Plan mode, I'll lay out the analysis of which files would need to change without touching anything yet.
Short answer
The main file that needs to change is:
backend/services/auto_bifurcator.py → AutoBifurcator.extract_file_content() (line ~768)
Why that's the primary file
- extract_file_content() currently handles PDFs with only PyMuPDF text + table extraction (lines ~775–795):
- page.get_text("text")
- page.find_tables()
- No Tesseract OCR, no image extraction.
- So today, for files coming through the Uploads & Auto-Configurator, only text/tables are extracted — not product images via OCR.
The good news: the OCR + image machinery already exists
backend/extract.py already has everything needed:
- ocr_page(page) (line ~151) — renders PDF page via PyMuPDF → PIL → pytesseract → text ✓
- _extract_page_images(doc, page, page_num, 
stem) (line ~180) — extracts embedded PDF images → saves to EXTRACTED_IMAGES_DIR → returns image dicts ✓
- extract_pdf(filepath) (line ~252) — combines pages (text/OCR) + tables + images ✓
- results_to_chunks (line ~465) / format_output (line ~561) — image chunks/markers ✓
Files that would need changes for "product images + text via Tesseract OCR"
File	Change needed
backend/services/auto_bifurcator.py	Primary — extract_file_content() PDF branch: add OCR + image extraction (reuse/extend backend/extract.py logic) and feed images into product creation (save_product / apply_bifurcation).
backend/extract.py	Supporting — expose/consume image data so auto-bifurcator can attach product images (mostly already present).
Likely supporting files
- backend/services/brochure_service.py — brochure PDF hosting (catbox) if product-level PDFs are involved.
- backend/routes/admin.py — upload/hosting wiring for extracted images.
⚠️ Plan mode reminder: I can't make these edits myself right now — if you want me to implement the change, you'll need to switch agents.
Want me to turn this into a concrete implementation plan?