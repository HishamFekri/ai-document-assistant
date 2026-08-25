from time import perf_counter

from pypdf import PdfReader


def extract_content_from_pdf(file_path):
    start = perf_counter()

    reader = PdfReader(
        file_path
    )

    blocks = []

    pages_with_text = 0
    total_characters = 0

    for page_number, page in enumerate(
        reader.pages,
        start=1,
    ):
        text = page.extract_text()

        if not text:
            continue

        text = text.strip()

        if not text:
            continue

        pages_with_text += 1
        total_characters += len(text)

        blocks.append({
            "type": "text",
            "content": text,
            "location": f"Page {page_number}",
            "metadata": {
                "page": page_number,
            },
        })

    elapsed = perf_counter() - start

    print(
        f"[PYPDF] Pages: {len(reader.pages)}"
    )

    print(
        f"[PYPDF] Pages with text: {pages_with_text}"
    )

    print(
        f"[PYPDF] Characters: {total_characters}"
    )

    print(
        f"[PYPDF] Extraction time: {elapsed:.2f}s"
    )

    return blocks