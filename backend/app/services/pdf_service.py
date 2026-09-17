from pypdf import PdfReader

from app.services.upload_validation import validate_document_source, PDFTextBudget
from app.services.content_budget import ContentBudget


def extract_content_from_pdf(file_path):
    validate_document_source(file_path, ".pdf")
    reader = PdfReader(file_path)
    pdf_budget, budget = PDFTextBudget(), ContentBudget()
    blocks = []
    try:
        for page_number, page in enumerate(reader.pages, start=1):
            text = pdf_budget.extract(page)
            if text:
                blocks.append(budget.block({"type": "text", "content": text,
                                            "location": f"Page {page_number}",
                                            "metadata": {"page": page_number}}))
    finally:
        reader.close()
    return blocks
