import json
import os

from openai import OpenAI

from sqlalchemy.orm import Session

from app.database.summary_assistant_models import (
    SummaryAssistantMessage,
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


MAX_HISTORY_MESSAGES = 20


client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


def get_summary_assistant_messages(
    db: Session,
    chat_id: int,
    document_id: int,
    limit: int | None = None,
    offset: int = 0,
) -> list[SummaryAssistantMessage]:
    query = (
        db.query(
            SummaryAssistantMessage
        )
        .filter(
            SummaryAssistantMessage.chat_id
            == chat_id,
            SummaryAssistantMessage.document_id
            == document_id,
        )
        .order_by(
            SummaryAssistantMessage.created_at.asc(),
            SummaryAssistantMessage.id.asc(),
        )
    )

    if limit is not None:
        query = query.offset(offset).limit(limit)

    return query.all()


def get_recent_summary_assistant_messages(
    db: Session,
    chat_id: int,
    document_id: int,
    limit: int = MAX_HISTORY_MESSAGES,
) -> list[SummaryAssistantMessage]:
    messages = (
        db.query(
            SummaryAssistantMessage
        )
        .filter(
            SummaryAssistantMessage.chat_id
            == chat_id,
            SummaryAssistantMessage.document_id
            == document_id,
        )
        .order_by(
            SummaryAssistantMessage.created_at.desc(),
            SummaryAssistantMessage.id.desc(),
        )
        .limit(
            limit
        )
        .all()
    )

    messages.reverse()

    return messages


def create_summary_assistant_message(
    db: Session,
    chat_id: int,
    document_id: int,
    role: str,
    content: str,
) -> SummaryAssistantMessage:
    cleaned_content = (
        content.strip()
    )

    if not cleaned_content:
        raise ValueError(
            "Message cannot be empty"
        )

    if role not in {
        "user",
        "assistant",
    }:
        raise ValueError(
            "Invalid summary assistant role"
        )

    message = SummaryAssistantMessage(
        chat_id=chat_id,
        document_id=document_id,
        role=role,
        content=cleaned_content,
    )

    db.add(
        message
    )

    db.commit()

    db.refresh(
        message
    )

    return message


def build_summary_assistant_system_prompt() -> str:
    return """
You are the Summary Assistant inside an AI Document Assistant.

The user is working directly inside a document summary workspace.

Your job is to understand how the user wants the summary changed.

Return valid JSON only.

The required JSON format is:

{
  "action": "update_preferences" | "generate_summary",
  "reply": "short natural response"
}

ACTION RULES

Use "generate_summary" when the user asks for any change that should be applied to the current summary now.

Examples:

"Make it Arabic."
"Make it shorter."
"Make it more detailed."
"Focus more on the tables."
"Keep the equations."
"Use an academic style."
"Make it English."
"ركز على النتائج"
"خليه بالعربي"
"اختصره أكثر"
"خليه مفصل"
"غير الأسلوب"
"لخصه"
"اعمل السمري"
"يلا اعمله"
"Do it."
"Generate it now."

A direct request to change the current summary should normally use "generate_summary".

Use "update_preferences" only when the user explicitly says the instruction is for later or explicitly says not to regenerate now.

Examples:

"For future summaries, use Arabic."
"Remember that I prefer short summaries."
"Don't regenerate it yet, just remember this."
"لا تعمله هسا، بس تذكر إني بدي السمري مختصر."
"بالمرات الجاية خليه بالإنجليزي."

Understand intent from the full conversation, not from keywords alone.

If the user gives a direct editing instruction without saying to wait, apply it now using "generate_summary".

LANGUAGE BEHAVIOR

Reply in the language the user is currently using unless they explicitly request otherwise.

For Arabic:

- Write natural, fluent Arabic.
- Preserve correct Arabic sentence order.
- Use clean punctuation.
- Avoid unnecessary English.
- Keep technical terms in English when translating them would be awkward.
- Keep equations, formulas, filenames, model names, code, and identifiers intact.
- Keep the response suitable for RTL rendering.
- Be concise.

For English:

- Use clean natural English.
- Be concise.

Do not claim that the summary has already been generated.

If action is "generate_summary", briefly acknowledge that the requested change will be applied.

If action is "update_preferences", briefly acknowledge that the preference was saved.

Return JSON only.
""".strip()


def build_llm_history(
    messages: list[
        SummaryAssistantMessage
    ],
) -> list[dict]:
    history = []

    for message in messages:
        if message.role not in {
            "user",
            "assistant",
        }:
            continue

        history.append(
            {
                "role":
                    message.role,

                "content":
                    message.content,
            }
        )

    return history


def parse_assistant_decision(
    response_text: str,
) -> dict:
    cleaned = (
        response_text
        .strip()
    )

    if cleaned.startswith(
        "```json"
    ):
        cleaned = cleaned[
            len("```json"):
        ]

    elif cleaned.startswith(
        "```"
    ):
        cleaned = cleaned[
            len("```"):
        ]

    if cleaned.endswith(
        "```"
    ):
        cleaned = cleaned[
            :-3
        ]

    cleaned = cleaned.strip()

    result = json.loads(
        cleaned
    )

    if not isinstance(
        result,
        dict,
    ):
        raise ValueError(
            "Invalid summary assistant response"
        )

    action = result.get(
        "action"
    )

    reply = result.get(
        "reply"
    )

    if action not in {
        "update_preferences",
        "generate_summary",
    }:
        raise ValueError(
            "Invalid summary assistant action"
        )

    if (
        not isinstance(
            reply,
            str,
        )
        or not reply.strip()
    ):
        raise ValueError(
            "Invalid summary assistant reply"
        )

    return {
        "action":
            action,

        "reply":
            reply.strip(),
    }


def generate_summary_assistant_decision(
    history: list[
        SummaryAssistantMessage
    ],
) -> dict:
    messages = [
        {
            "role": "system",
            "content":
                build_summary_assistant_system_prompt(),
        }
    ]

    messages.extend(
        build_llm_history(
            history
        )
    )

    response = (
        client.chat.completions.create(
            model=DEEPSEEK_MODEL,

            messages=messages,

            temperature=0.1,

            max_tokens=250,

            response_format={
                "type": "json_object"
            },
        )
    )

    response_text = (
        response
        .choices[0]
        .message
        .content
    )

    if not response_text:
        raise ValueError(
            "Summary assistant returned an empty response"
        )

    return parse_assistant_decision(
        response_text
    )


def send_summary_assistant_message(
    db: Session,
    chat_id: int,
    document_id: int,
    content: str,
) -> tuple[
    SummaryAssistantMessage,
    SummaryAssistantMessage,
    str,
]:
    user_message = (
        create_summary_assistant_message(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
            role="user",
            content=content,
        )
    )

    history = (
        get_recent_summary_assistant_messages(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    decision = (
        generate_summary_assistant_decision(
            history
        )
    )

    assistant_message = (
        create_summary_assistant_message(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
            role="assistant",
            content=decision[
                "reply"
            ],
        )
    )

    return (
        user_message,
        assistant_message,
        decision[
            "action"
        ],
    )


def clear_summary_assistant_messages(
    db: Session,
    chat_id: int,
    document_id: int,
) -> int:
    deleted_count = (
        db.query(
            SummaryAssistantMessage
        )
        .filter(
            SummaryAssistantMessage.chat_id
            == chat_id,
            SummaryAssistantMessage.document_id
            == document_id,
        )
        .delete(
            synchronize_session=False
        )
    )

    db.commit()

    return deleted_count