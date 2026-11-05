def bucket_to_table(bucket):
    if bucket in ("faq","policy"):
        return "faq_dataset"
    if bucket in ("kb","general"):
        return "knowledge_base"
    if bucket == "product":
        return "products"
    if bucket == "profile":
        return "tenant_profile_draft"
    return "knowledge_base"
PY