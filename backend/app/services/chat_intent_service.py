import json
import os

from openai import OpenAI


DEEPSEEK_API_KEY = os.getenv(
    "DEEPSEEK_API_KEY"
)

DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL",
    "https://api.deepseek.com",
)

DEEPSEEK_MODEL = os.getenv(
    "DEEPSEEK_MODEL",
    "deepseek-chat",
)


client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


def detect_chat_intent(
    question: str,
    documents: list,
) -> dict:
    document_list = [
        {
            "id": document.id,
            "filename": document.filename,
        }
        for document in documents
    ]

    system_prompt = """
You are an intent router for an AI Document Assistant.

Your ONLY job is to determine what the user wants to do.

Possible actions:

1. "chat"
The user is asking a normal question about the document
or having a normal conversation.

2. "generate_summary"
The user wants a document summary to be generated now.

Examples:
- summarize it
- summarize the document
- generate a summary
- make me a summary
- لخصه
- لخص الملف
- اعمل ملخص
- اعمل السمري
- اختصرلي الملف
- يلا اعمله

3. "summary_preferences"
The user is only describing how a FUTURE summary should
be generated, without asking to generate it now.

Examples:
- make the summary Arabic
- focus on equations
- make the next summary shorter
- خلي السمري بالعربي
- ركز على الجداول
- خليه مختصر

IMPORTANT:

Understand intent semantically.
Do not depend on keywords alone.

If the user says:
"Don't summarize it now, just make the next summary Arabic"
the action is "summary_preferences".

If the user says:
"Make it Arabic and summarize it now"
the action is "generate_summary".

DOCUMENT SELECTION:

You will receive the documents attached to the chat.

If exactly one document is attached and the user asks
for a summary without naming a document, select it.

If multiple documents exist and the user clearly names
one of them, select that document.

If multiple documents exist and the user asks to summarize
all documents, return all matching document IDs.

If multiple documents exist and it is unclear which one
the user means, set needs_document_selection to true.

For normal chat and summary_preferences,
document_ids may be empty.

Return valid JSON only:

{
  "action": "chat" | "generate_summary" | "summary_preferences",
  "document_ids": [],
  "needs_document_selection": false
}
""".strip()

    response = (
        client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "ATTACHED DOCUMENTS:\n"
                        + json.dumps(
                            document_list,
                            ensure_ascii=False,
                        )
                        + "\n\nUSER MESSAGE:\n"
                        + question
                    ),
                },
            ],
            temperature=0,
            max_tokens=150,
            response_format={
                "type": "json_object"
            },
        )
    )

    content = (
        response
        .choices[0]
        .message
        .content
    )

    if not content:
        return {
            "action": "chat",
            "document_ids": [],
            "needs_document_selection": False,
        }

    try:
        result = json.loads(
            content
        )

    except json.JSONDecodeError:
        return {
            "action": "chat",
            "document_ids": [],
            "needs_document_selection": False,
        }

    action = result.get(
        "action",
        "chat",
    )

    if action not in {
        "chat",
        "generate_summary",
        "summary_preferences",
    }:
        action = "chat"

    valid_document_ids = {
        document.id
        for document in documents
    }

    document_ids = []

    for document_id in result.get(
        "document_ids",
        [],
    ):
        try:
            document_id = int(
                document_id
            )

        except (
            TypeError,
            ValueError,
        ):
            continue

        if (
            document_id
            in valid_document_ids
            and document_id
            not in document_ids
        ):
            document_ids.append(
                document_id
            )

    needs_document_selection = bool(
        result.get(
            "needs_document_selection",
            False,
        )
    )

    if (
        action == "generate_summary"
        and len(documents) == 1
        and not document_ids
    ):
        document_ids = [
            documents[0].id
        ]

        needs_document_selection = False

    return {
        "action":
            action,

        "document_ids":
            document_ids,

        "needs_document_selection":
            needs_document_selection,
    }