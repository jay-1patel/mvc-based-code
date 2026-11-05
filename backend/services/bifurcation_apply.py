import hashlib, json
from . import database as db
from .ollama_json import call_json
from .bifurcation_prompt import SYSTEM_PROMPT
from .document_chunker import chunk_text
from .bifurcation_validation import validate_result, dedupe

def _hash(s):
    return hashlib.sha256(s.encode("utf-8", "ignore")).hexdigest()

def stage_chunks(text):
    results = []
    for c in chunk_text(text):
        try:
            res = call_json(c, system=SYSTEM_PROMPT)
            items = dedupe(validate_result(res))
        except Exception:
            items = [{"bucket": "general", "fields": {"content": c}, "confidence": 0.2}]
        for it in items:
            it["source_hash"] = _hash(c)
        results.append({"chunk": c, "items": items})
    return results

def apply_bifurcation(conn, tenant_id, source_file, staged):
    # delete prior auto rows
    conn.execute("DELETE FROM faq_dataset WHERE origin='auto_bifurcation' AND source_file=? AND tenant_id=?", (source_file, tenant_id))
    conn.execute("DELETE FROM knowledge_base WHERE origin='auto_bifurcation' AND source=? AND tenant_id=?", (source_file, tenant_id))
    # products slug handling omitted for brevity
    counts = {"faq":0,"policy":0,"kb":0,"product":0,"profile":0,"general":0}
    for s in staged:
        for it in s["items"]:
            b = it["bucket"]
            counts[b] = counts.get(b,0)+1
            fields = it["fields"]
            if b in ("faq","policy"):
                conn.execute(
                    "INSERT INTO faq_dataset (tenant_id,source_file,question,answer,category,origin,source_hash,metadata_json,content_type,content) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (tenant_id, source_file, fields.get("question",""), fields.get("answer",""), fields.get("category",b), "auto_bifurcation", it["source_hash"], json.dumps(fields), b, fields.get("answer",""))
                )
            elif b in ("kb","general"):
                conn.execute(
                    "INSERT INTO knowledge_base (tenant_id,title,category,content,source,origin,source_hash,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
                    (tenant_id, fields.get("title",b), fields.get("category",b), fields.get("content",""), source_file, "auto_bifurcation", it["source_hash"], json.dumps(fields))
                )
    return counts
PY