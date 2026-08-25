def extract_content_from_text(file_path):
    with open(
        file_path,
        "r",
        encoding="utf-8"
    ) as file:
        text = file.read()

    text = text.strip()

    if not text:
        return []

    return [
        {
            "type": "text",
            "content": text,
            "location": "Text file",
            "metadata": {}
        }
    ]