from docx import Document

from app.services.content_budget import ContentBudget
from app.services.upload_validation import validate_document_source


def extract_content_from_word(file_path):
    validate_document_source(file_path, ".docx")
    document = Document(file_path)
    budget = ContentBudget()
    blocks = []
    for paragraph_number, paragraph in enumerate(document.paragraphs, start=1):
        text = paragraph.text.strip()
        if text:
            blocks.append(budget.block({
                "type": "text", "content": text,
                "location": f"Paragraph {paragraph_number}",
                "metadata": {"paragraph": paragraph_number},
            }))
    return blocks
