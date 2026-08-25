from docx import Document


def extract_content_from_word(file_path):
    document = Document(file_path)

    blocks = []

    for paragraph_number, paragraph in enumerate(
        document.paragraphs,
        start=1
    ):
        text = paragraph.text.strip()

        if text:
            blocks.append({
                "type": "text",
                "content": text,
                "location": f"Paragraph {paragraph_number}",
                "metadata": {
                    "paragraph": paragraph_number
                }
            })

    return blocks