import re

def chunk_text(text, max_chars=800):
    text = text or ""
    # split by blank lines / headings
    parts = re.split(r"\n\s*\n", text)
    chunks = []
    cur = ""
    for p in parts:
        if len(cur) + len(p) + 2 <= max_chars:
            cur = (cur + "\n\n" + p).strip() if cur else p.strip()
        else:
            if cur:
                chunks.append(cur)
            cur = p.strip()
    if cur:
        chunks.append(cur)
    if not chunks:
        chunks = [text[:max_chars]] if text else []
    return chunks

def chunk_pages(pages):
    out = []
    for i, pg in enumerate(pages):
        for c in chunk_text(pg):
            out.append({"page": i+1, "text": c})
    return out
PY