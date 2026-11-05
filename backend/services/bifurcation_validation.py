VALID_BUCKETS = {"faq","policy","kb","product","profile","general"}

def validate_item(item):
    if not isinstance(item, dict):
        return None
    b = item.get("bucket")
    if b not in VALID_BUCKETS:
        b = "general"
    fields = item.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
    conf = item.get("confidence", 0.5)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.5
    if conf < 0.3:
        b = "general"
    return {"bucket": b, "fields": fields, "confidence": conf}

def validate_result(res):
    items = []
    raw = res.get("items", []) if isinstance(res, dict) else []
    for it in raw:
        v = validate_item(it)
        if v:
            items.append(v)
    if not items:
        items = [{"bucket": "general", "fields": {"content": ""}, "confidence": 0.3}]
    return items

def dedupe(items):
    seen = set()
    out = []
    for it in items:
        key = (it["bucket"], json.dumps(it["fields"], sort_keys=True) if False else str(it["fields"]))
        import json
        key = (it["bucket"], json.dumps(it["fields"], sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out
PY