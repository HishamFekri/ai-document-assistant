import os
import re
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from sqlalchemy.orm import Session

from app.database.models import (
    Chat,
    Document,
    DocumentChunk,
    Message,
)


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


DEFAULT_CHAT_TITLES = {
    "",
    "new chat",
    "new conversation",
    "untitled chat",
    "محادثة جديدة",
    "دردشة جديدة",
    "yeni sohbet",
}


MAX_DOCUMENT_CONTEXT_CHARS = 800
MAX_RECENT_USER_MESSAGES = 4
MAX_TITLE_WORDS = 7
MAX_TITLE_CHARS = 80


ARABIC_PATTERN = re.compile(
    r"[\u0600-\u06FF]"
)

LATIN_PATTERN = re.compile(
    r"[A-Za-z]"
)

TURKISH_PATTERN = re.compile(
    r"[çğıöşüÇĞİÖŞÜ]"
)


@lru_cache(maxsize=1)
def get_deepseek_client():
    if not DEEPSEEK_API_KEY:
        return None

    return OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )


def is_default_chat_title(
    title: str | None,
) -> bool:
    if title is None:
        return True

    normalized = " ".join(
        title.strip().split()
    ).casefold()

    return (
        normalized
        in DEFAULT_CHAT_TITLES
    )


def clean_filename(
    filename: str,
) -> str:
    stem = Path(
        filename
    ).stem

    stem = re.sub(
        r"[_\-]+",
        " ",
        stem,
    )

    stem = re.sub(
        r"\s+",
        " ",
        stem,
    )

    return stem.strip()


def get_title_documents(
    chat: Chat,
    document_ids: list[int],
) -> list[Document]:
    normalized_ids = {
        document_id
        for document_id
        in document_ids
        if (
            isinstance(
                document_id,
                int,
            )
            and document_id > 0
        )
    }

    if not normalized_ids:
        return []

    return [
        document
        for document
        in chat.documents
        if (
            document.id
            in normalized_ids
        )
    ]


def get_recent_user_messages(
    db: Session,
    chat_id: int,
) -> list[str]:
    messages = (
        db.query(Message)
        .filter(
            Message.chat_id
            == chat_id,
            Message.role
            == "user",
        )
        .order_by(
            Message.created_at.desc(),
            Message.id.desc(),
        )
        .limit(
            MAX_RECENT_USER_MESSAGES
        )
        .all()
    )

    messages.reverse()

    return [
        message.content.strip()
        for message
        in messages
        if message.content.strip()
    ]


def detect_text_language(
    text: str,
) -> str | None:
    value = text.strip()

    if not value:
        return None

    arabic_count = len(
        ARABIC_PATTERN.findall(
            value
        )
    )

    latin_count = len(
        LATIN_PATTERN.findall(
            value
        )
    )

    if (
        TURKISH_PATTERN.search(
            value
        )
        and latin_count > 0
    ):
        return "Turkish"

    if (
        arabic_count > 0
        and latin_count == 0
    ):
        return "Arabic"

    if (
        latin_count > 0
        and arabic_count == 0
    ):
        return "English"

    if (
        arabic_count > 0
        and latin_count > 0
    ):
        first_arabic = (
            ARABIC_PATTERN.search(
                value
            )
        )

        first_latin = (
            LATIN_PATTERN.search(
                value
            )
        )

        if (
            first_arabic
            and first_latin
        ):
            if (
                first_arabic.start()
                < first_latin.start()
            ):
                return "Arabic"

            return "English"

        if (
            arabic_count
            >= latin_count
        ):
            return "Arabic"

        return "English"

    return None


def detect_title_language(
    question: str,
    recent_messages: list[str],
) -> str:
    question_language = (
        detect_text_language(
            question
        )
    )

    if question_language:
        return question_language

    for message in reversed(
        recent_messages
    ):
        language = (
            detect_text_language(
                message
            )
        )

        if language:
            return language

    return "English"


def get_document_title_context(
    db: Session,
    documents: list[Document],
) -> str:
    if not documents:
        return ""

    document_ids = [
        document.id
        for document
        in documents
    ]

    chunks = (
        db.query(DocumentChunk)
        .filter(
            DocumentChunk.document_id.in_(
                document_ids
            )
        )
        .order_by(
            DocumentChunk.document_id,
            DocumentChunk.id,
        )
        .limit(6)
        .all()
    )

    context_parts = []

    total_chars = 0

    for chunk in chunks:
        if (
            chunk.content_type
            == "image"
        ):
            continue

        content = (
            chunk.content
            or ""
        ).strip()

        if not content:
            continue

        remaining = (
            MAX_DOCUMENT_CONTEXT_CHARS
            - total_chars
        )

        if remaining <= 0:
            break

        content = content[
            :remaining
        ]

        context_parts.append(
            content
        )

        total_chars += len(
            content
        )

    return "\n\n".join(
        context_parts
    )


def clean_generated_title(
    value: str,
) -> str | None:
    title = (
        value
        .strip()
        .replace(
            "\n",
            " ",
        )
    )

    title = re.sub(
        r"\s+",
        " ",
        title,
    )

    title = re.sub(
        (
            r"^(title|chat title|"
            r"العنوان|عنوان المحادثة|"
            r"başlık)\s*[:：\-]\s*"
        ),
        "",
        title,
        flags=re.IGNORECASE,
    )

    title = title.strip(
        "\"'`“”‘’*# "
    )

    if not title:
        return None

    words = title.split()

    if (
        len(words)
        > MAX_TITLE_WORDS
    ):
        title = " ".join(
            words[
                :MAX_TITLE_WORDS
            ]
        )

    if (
        len(title)
        > MAX_TITLE_CHARS
    ):
        title = title[
            :MAX_TITLE_CHARS
        ].rstrip()

    return (
        title
        or None
    )


def build_fallback_title(
    question: str,
) -> str:
    question_text = re.sub(
        r"\s+",
        " ",
        question.strip(),
    )

    words = (
        question_text.split()
    )

    title = " ".join(
        words[
            :MAX_TITLE_WORDS
        ]
    ).strip()

    title = title.strip(
        "\"'`.,!?؟:;؛-_ "
    )

    if (
        len(title)
        > MAX_TITLE_CHARS
    ):
        title = title[
            :MAX_TITLE_CHARS
        ].rstrip()

    return (
        title
        or "New chat"
    )


def generate_ai_chat_title(
    db: Session,
    chat_id: int,
    question: str,
    documents: list[Document],
) -> str | None:
    client = (
        get_deepseek_client()
    )

    if not client:
        return None

    recent_messages = (
        get_recent_user_messages(
            db=db,
            chat_id=chat_id,
        )
    )

    target_language = (
        detect_title_language(
            question=question,
            recent_messages=(
                recent_messages
            ),
        )
    )

    filenames = "\n".join(
        (
            f"- {clean_filename(document.filename)}"
        )
        for document
        in documents
    )

    recent_context = "\n".join(
        f"- {message}"
        for message
        in recent_messages
    )

    document_context = (
        get_document_title_context(
            db=db,
            documents=documents,
        )
    )

    if documents:
        context_instruction = """
Use the user's question as the main topic.
Use the document names and document context only to better understand what the conversation is about.
""".strip()

    else:
        context_instruction = """
There is no attached document.
Generate the title only from the user's conversation topic.
Do not mention documents, files, PDFs, or attachments.
""".strip()

    system_prompt = f"""
You generate short chat titles for an AI assistant.

TARGET LANGUAGE: {target_language}

The title MUST be written in {target_language}.

Rules:

- Return ONLY the title.
- No quotes.
- No explanation.
- Use 3 to 7 words when possible.
- Match the topic of the user's conversation.
- The newest user question is the strongest signal for both topic and language.
- Do not translate the title into another language.
- If TARGET LANGUAGE is English, the title must be English.
- If TARGET LANGUAGE is Arabic, the title must be Arabic.
- If TARGET LANGUAGE is Turkish, the title must be Turkish.
- The title should describe the subject, not the action.
- Avoid generic titles such as "New Chat", "Document Question", "File Analysis", or "General Question".
- Do not invent unsupported facts.

{context_instruction}
""".strip()

    user_prompt = f"""
CURRENT USER QUESTION:
{question}

RECENT USER MESSAGES:
{recent_context or question}

DOCUMENTS:
{filenames or "No attached documents"}

DOCUMENT CONTEXT:
{document_context or "No document context"}

TARGET LANGUAGE:
{target_language}

Generate the title now.
""".strip()

    try:
        response = (
            client.chat.completions.create(
                model=(
                    DEEPSEEK_MODEL
                ),
                messages=[
                    {
                        "role":
                            "system",
                        "content":
                            system_prompt,
                    },
                    {
                        "role":
                            "user",
                        "content":
                            user_prompt,
                    },
                ],
                temperature=0.1,
                max_tokens=40,
            )
        )

        content = (
            response
            .choices[0]
            .message
            .content
            or ""
        )

        return (
            clean_generated_title(
                content
            )
        )

    except Exception as error:
        print(
            "[CHAT TITLE AI ERROR] "
            f"{error}"
        )

        return None


def maybe_generate_chat_title(
    db: Session,
    chat_id: int,
    question: str,
    document_ids: list[int],
) -> str | None:
    chat = (
        db.query(Chat)
        .filter(
            Chat.id
            == chat_id
        )
        .first()
    )

    if not chat:
        return None

    if not is_default_chat_title(
        chat.title
    ):
        return None

    documents = (
        get_title_documents(
            chat=chat,
            document_ids=(
                document_ids
            ),
        )
    )

    generated_title = (
        generate_ai_chat_title(
            db=db,
            chat_id=chat_id,
            question=question,
            documents=documents,
        )
    )

    title = (
        generated_title
        or build_fallback_title(
            question=question,
        )
    )

    try:
        db.refresh(
            chat
        )

        if not is_default_chat_title(
            chat.title
        ):
            return None

        chat.title = title

        db.commit()

        db.refresh(
            chat
        )

        return chat.title

    except Exception as error:
        db.rollback()

        print(
            "[CHAT TITLE SAVE ERROR] "
            f"{error}"
        )

        return None