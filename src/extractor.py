def extract_facts(document_text: str) -> list[dict]:
    return [{"text": document_text[:40], "confidence": 0.91}]
