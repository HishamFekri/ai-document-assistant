from app.services.content_budget import ContentBudget
from app.services.upload_validation import validate_document_source


def extract_content_from_text(file_path):
    validate_document_source(file_path, ".txt")
    budget = ContentBudget()
    parts = []
    with open(file_path, "r", encoding="utf-8-sig") as source:
        while part := source.read(64 * 1024):
            budget.text(part)
            parts.append(part)
    text = "".join(parts).strip()
    if not text:
        return []
    return [{"type": "text", "content": text, "location": "Text file", "metadata": {}}]
