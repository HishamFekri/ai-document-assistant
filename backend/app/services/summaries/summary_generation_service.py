import json
import os
import re
from typing import Literal

from openai import OpenAI
from sqlalchemy.orm import Session

from app.database.models import (
    Document,
    Message,
)

from app.database.summary_assistant_models import (
    SummaryAssistantMessage,
)

from app.services.summaries.summary_context_service import (
    find_scope_page_numbers,
    get_document_summary_context,
    get_document_transcription_context,
)

from app.services.summaries.summary_service import (
    mark_summary_completed,
    mark_summary_failed,
    mark_summary_generating,
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


MAX_SUMMARY_INSTRUCTION_MESSAGES = 20

MAX_CHAT_LANGUAGE_MESSAGES = 8

TRANSCRIPTION_MAX_OUTPUT_TOKENS_PER_PAGE = int(
    os.getenv(
        "TRANSCRIPTION_MAX_OUTPUT_TOKENS_PER_PAGE",
        "900",
    )
)


SummaryMode = Literal[
    "summary",
    "transcription",
]


client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


def get_summary_instruction_history(
    db: Session,
    chat_id: int,
    document_id: int,
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
            MAX_SUMMARY_INSTRUCTION_MESSAGES
        )
        .all()
    )

    messages.reverse()

    return messages


def build_summary_instruction_context(
    db: Session,
    chat_id: int,
    document_id: int,
) -> str:
    messages = (
        get_summary_instruction_history(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    user_messages = [
        message
        for message in messages
        if message.role == "user"
    ]

    if not user_messages:
        return """
No custom summary instructions were provided.

- Use the current chat language as the default output language.
- If the chat language cannot be determined, use the dominant language of the document.
- Follow the selected generation mode exactly.
""".strip()

    lines = []

    for message in user_messages:
        lines.append(
            f"User instruction: {message.content}"
        )

    return "\n".join(
        lines
    )


def detect_text_language(
    text: str,
) -> str | None:
    arabic_count = 0

    latin_count = 0

    for character in text:
        if (
            "\u0600"
            <= character
            <= "\u06ff"
            or "\u0750"
            <= character
            <= "\u077f"
            or "\u08a0"
            <= character
            <= "\u08ff"
        ):
            arabic_count += 1

        elif (
            "A"
            <= character
            <= "Z"
            or "a"
            <= character
            <= "z"
        ):
            latin_count += 1

    total_letters = (
        arabic_count
        + latin_count
    )

    if total_letters < 3:
        return None

    if (
        arabic_count
        > latin_count
    ):
        return "Arabic"

    if (
        latin_count
        > arabic_count
    ):
        return "English"

    if arabic_count > 0:
        return "Arabic"

    return None


def get_chat_default_language(
    db: Session,
    chat_id: int,
) -> str | None:
    messages = (
        db.query(
            Message
        )
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
            MAX_CHAT_LANGUAGE_MESSAGES
        )
        .all()
    )

    # The language of the newest meaningful user message
    # represents the current chat language better than a
    # long aggregate of old messages.
    for message in messages:
        detected = (
            detect_text_language(
                message.content
                or ""
            )
        )

        if detected:
            return detected

    return None


def detect_explicit_language_request(
    text: str,
) -> str | None:
    normalized = (
        text
        .strip()
        .casefold()
    )

    if not normalized:
        return None

    english_phrases = (
        "make it english",
        "write it in english",
        "write in english",
        "in english",
        "english please",
        "بالانجليزي",
        "بالإنجليزي",
        "بالإنكليزي",
        "خليه انجليزي",
        "خليه إنجليزي",
        "اكتبه بالانجليزي",
        "اكتبه بالإنجليزي",
    )

    arabic_phrases = (
        "make it arabic",
        "write it in arabic",
        "write in arabic",
        "in arabic",
        "arabic please",
        "بالعربي",
        "بالعربية",
        "خليه عربي",
        "اكتبه بالعربي",
        "اكتبه بالعربية",
    )

    for phrase in english_phrases:
        if phrase.casefold() in normalized:
            return "English"

    for phrase in arabic_phrases:
        if phrase.casefold() in normalized:
            return "Arabic"

    return None


def get_explicit_summary_language(
    db: Session,
    chat_id: int,
    document_id: int,
) -> str | None:
    messages = (
        get_summary_instruction_history(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    for message in reversed(
        messages
    ):
        if message.role != "user":
            continue

        detected = (
            detect_explicit_language_request(
                message.content
                or ""
            )
        )

        if detected:
            return detected

    return None


def resolve_output_language(
    db: Session,
    chat_id: int,
    document_id: int,
) -> str | None:
    explicit_language = (
        get_explicit_summary_language(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    if explicit_language:
        return explicit_language

    return get_chat_default_language(
        db=db,
        chat_id=chat_id,
    )


def build_generation_request_system_prompt() -> str:
    return """
You convert summary-workspace instructions into a structured request.

Return valid JSON only.

Schema:
{
  "operation": "summarize" | "explain" | "translate",
  "scope_type": "whole_document" | "pages" | "topic",
  "scope_query": "string or null",
  "start_page": 1 or null,
  "end_page": 5 or null,
  "target_language": "Arabic" | "English" | null
}

Rules:

- Read the FULL instruction history in order.
- Newer instructions override only the fields they explicitly change.
- Preserve an earlier scope if the newest message only changes language/style.
- "Make it Arabic" changes target_language only; it does NOT reset scope.
- "Explain only the engine section" means:
  operation=explain, scope_type=topic, scope_query="engine".
- "Summarize the engine section" means:
  operation=summarize, scope_type=topic, scope_query="engine".
- "Translate only pages 15-20 to Arabic" means:
  operation=translate, scope_type=pages,
  start_page=15, end_page=20, target_language=Arabic.
- "Translate the engine section to Arabic" means:
  operation=translate, scope_type=topic,
  scope_query="engine", target_language=Arabic.
- "Do the whole document" or an explicit reset to the whole file means
  scope_type=whole_document and clears page/topic scope.
- If the operation is not explicitly changed, infer it from the selected
  product mode:
  summary mode -> summarize
  transcription mode -> explain
- If scope is not specified anywhere, use whole_document.
- Do not invent page numbers.
- scope_query should contain only the requested subject/section/topic,
  not the whole user sentence.
""".strip()


def resolve_generation_request(
    db: Session,
    chat_id: int,
    document_id: int,
    mode: SummaryMode,
) -> dict:
    history = (
        get_summary_instruction_history(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    user_instructions = [
        message.content.strip()
        for message in history
        if (
            message.role == "user"
            and message.content
            and message.content.strip()
        )
    ]

    default_operation = (
        "summarize"
        if mode == "summary"
        else "explain"
    )

    if not user_instructions:
        return {
            "operation":
                default_operation,
            "scope_type":
                "whole_document",
            "scope_query":
                None,
            "start_page":
                None,
            "end_page":
                None,
            "target_language":
                None,
        }

    response = (
        client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content":
                        build_generation_request_system_prompt(),
                },
                {
                    "role": "user",
                    "content":
                        (
                            "SELECTED PRODUCT MODE:\n"
                            f"{mode}\n\n"
                            "INSTRUCTION HISTORY:\n"
                            + "\n".join(
                                f"- {instruction}"
                                for instruction
                                in user_instructions
                            )
                        ),
                },
            ],
            temperature=0.0,
            max_tokens=300,
            response_format={
                "type":
                    "json_object"
            },
        )
    )

    raw = (
        response
        .choices[0]
        .message
        .content
        or ""
    ).strip()

    try:
        parsed = json.loads(
            raw
        )

    except json.JSONDecodeError:
        parsed = {}

    operation = parsed.get(
        "operation"
    )

    if operation not in {
        "summarize",
        "explain",
        "translate",
    }:
        operation = (
            default_operation
        )

    scope_type = parsed.get(
        "scope_type"
    )

    if scope_type not in {
        "whole_document",
        "pages",
        "topic",
    }:
        scope_type = (
            "whole_document"
        )

    scope_query = parsed.get(
        "scope_query"
    )

    if not isinstance(
        scope_query,
        str,
    ):
        scope_query = None

    elif not scope_query.strip():
        scope_query = None

    else:
        scope_query = (
            scope_query.strip()
        )

    start_page = parsed.get(
        "start_page"
    )

    end_page = parsed.get(
        "end_page"
    )

    if not isinstance(
        start_page,
        int,
    ):
        start_page = None

    if not isinstance(
        end_page,
        int,
    ):
        end_page = None

    target_language = parsed.get(
        "target_language"
    )

    if target_language not in {
        "Arabic",
        "English",
    }:
        target_language = None

    return {
        "operation":
            operation,
        "scope_type":
            scope_type,
        "scope_query":
            scope_query,
        "start_page":
            start_page,
        "end_page":
            end_page,
        "target_language":
            target_language,
    }


def resolve_generation_pages(
    db: Session,
    document: Document,
    request: dict,
) -> list[int] | None:
    scope_type = request.get(
        "scope_type"
    )

    if scope_type == "whole_document":
        return None

    if scope_type == "pages":
        start_page = request.get(
            "start_page"
        )

        end_page = request.get(
            "end_page"
        )

        if (
            not isinstance(
                start_page,
                int,
            )
            or not isinstance(
                end_page,
                int,
            )
        ):
            return None

        if start_page > end_page:
            start_page, end_page = (
                end_page,
                start_page,
            )

        if document.pages_count:
            start_page = max(
                1,
                min(
                    start_page,
                    document.pages_count,
                ),
            )

            end_page = max(
                1,
                min(
                    end_page,
                    document.pages_count,
                ),
            )

        return list(
            range(
                start_page,
                end_page + 1,
            )
        )

    scope_query = request.get(
        "scope_query"
    )

    if (
        scope_type == "topic"
        and isinstance(
            scope_query,
            str,
        )
        and scope_query.strip()
    ):
        pages = (
            find_scope_page_numbers(
                db=db,
                document=document,
                scope_query=(
                    scope_query
                ),
            )
        )

        return pages or None

    return None


def build_request_guidance(
    request: dict,
    selected_pages:
        list[int] | None,
) -> str:
    operation = request.get(
        "operation",
        "summarize",
    )

    scope_type = request.get(
        "scope_type",
        "whole_document",
    )

    scope_query = request.get(
        "scope_query"
    )

    lines = [
        "STRUCTURED USER REQUEST",
        f"Operation: {operation}",
        f"Scope type: {scope_type}",
    ]

    if scope_query:
        lines.append(
            f"Requested topic/section: {scope_query}"
        )

    if selected_pages:
        lines.append(
            "Selected pages: "
            + ", ".join(
                str(page)
                for page
                in selected_pages
            )
        )

    lines.extend(
        [
            (
                "Honor this scope strictly. "
                "Do not cover unrelated parts "
                "of the document."
            ),
            (
                "If the selected scope does not "
                "contain enough information, say so "
                "instead of silently switching back "
                "to the whole document."
            ),
        ]
    )

    return "\n".join(
        lines
    )


def build_summary_system_prompt(
    mode: SummaryMode,
    request: dict | None = None,
) -> str:
    if mode == "transcription":
        mode_rules = """
SELECTED MODE: TRANSCRIPTION

This mode is a detailed page-by-page document analysis.

- Process the document in page order.
- Cover every page represented in the supplied context.
- Do not collapse the whole document into a short overview.
- For each page, explain the meaningful text, headings, lists, definitions, examples, findings, and conclusions.
- Analyze important images, figures, diagrams, charts, tables, and equations when they are present.
- Preserve important equations and mathematical meaning accurately.
- Preserve important table values, relationships, and conclusions.
- Use supplied visual assets near the page/content they belong to.
- If a page contains multiple important visual assets, include all useful non-duplicate assets.
- Never invent content for a page or asset that is not present in the supplied context.
- Keep page order clear in section titles or locations whenever page information is available.
- This is intentionally detailed. Prefer completeness over brevity.
""".strip()

        output_format = """
The FIRST line must be:
{"type":"title","title":"..."}

Every following line must be one transcription section:
{"type":"section","section":{"type":"text","title":"string or null","content":"string or null","asset_id":null,"caption":null,"location":"string or null"}}

or:
{"type":"section","section":{"type":"image","title":"string or null","content":null,"asset_id":123,"caption":"string or null","location":"string or null"}}

or:
{"type":"section","section":{"type":"table","title":"string or null","content":"string or null","asset_id":123,"caption":"string or null","location":"string or null"}}

or:
{"type":"section","section":{"type":"equation","title":"string or null","content":"string or null","asset_id":123,"caption":"string or null","location":"string or null"}}
""".strip()

    else:
        mode_rules = """
SELECTED MODE: SUMMARY

This mode creates a concise high-value response for the SELECTED DOCUMENT SCOPE.

- Give the reader the overall purpose and main message of the document.
- Identify the document's real sections/topics and preserve their logical order.
- For every meaningful section/topic, state its key takeaway, result, conclusion, or most important idea.
- Focus on what the reader should remember after reading each section.
- Combine repetitive details instead of reproducing them page by page.
- Do not create a page-by-page transcription.
- Do not output image blocks.
- Do not output table blocks.
- Do not output equation blocks.
- Important information from images, charts, tables, and equations may influence the written summary, but express only the important takeaway in text.
- Preserve important numbers, findings, decisions, definitions, and conclusions when they materially affect the summary.
- Prefer clarity and useful compression over exhaustive detail.
""".strip()

        output_format = """
The FIRST line must be:
{"type":"title","title":"..."}

Every following line must be one text summary section:
{"type":"section","section":{"type":"text","title":"string or null","content":"string or null","asset_id":null,"caption":null,"location":"string or null"}}

For SUMMARY mode, never emit image, table, or equation blocks.
""".strip()

    return f"""
You are an expert document analyst.

Generate output grounded only in the supplied document.

The SELECTED MODE is a product-level constraint.
Summary Assistant instructions may change language, scope, emphasis, tone, level of detail, and whether the selected scope should be summarized, explained, or translated. The selected product mode still controls the output format.

SUMMARY LANGUAGE PRIORITY

Choose the output language using this exact priority:

1. The newest explicit language instruction in SUMMARY ASSISTANT PREFERENCES.
2. The DEFAULT SUMMARY LANGUAGE derived from the user's current chat.
3. The dominant language of the document only when no chat language can be determined.

An explicit Summary Assistant request such as:
"Make it English"
"Make it Arabic"
"خليه بالعربي"
"اكتبه بالإنجليزي"

must override the default chat language.

Follow the newest explicit Summary Assistant preference when preferences conflict.

Do not use chat language information as factual document context.
It is only a language preference.

LANGUAGE

For Arabic:
- Write fluent, natural Arabic.
- Keep technical English terms when clearer.
- Preserve equations, code, abbreviations, model names and identifiers.
- Structure prose naturally for RTL interfaces.

For English:
- Write clean, natural English.
- Preserve technical terminology accurately.

GROUNDING RULES

- Never invent information.
- Never invent pages.
- Never invent visual assets.
- Never invent, alter, or guess an asset ID.
- Use only asset IDs explicitly present in DOCUMENT ASSETS.
- Never use the same asset twice.
- Explain difficult concepts clearly when useful.

{mode_rules}

STREAMING OUTPUT FORMAT

Return NDJSON only: exactly one valid JSON object per line.
Do not use markdown fences.
Do not wrap the lines in an array.
Do not pretty-print a JSON object across multiple lines.

{output_format}

Emit sections in final display order.
Each line must be independently parseable JSON.
""".strip()

def build_summary_user_prompt(
    context: dict,
    instructions: str,
    default_language: str | None,
    mode: SummaryMode,
    request: dict,
    selected_pages:
        list[int] | None,
) -> str:
    document = context[
        "document"
    ]

    text_context = context.get(
        "text_context",
        "",
    )

    asset_context = context.get(
        "asset_context",
        "",
    )

    language_text = (
        default_language
        if default_language
        else (
            "Unknown. Infer the dominant "
            "language from the document."
        )
    )

    if mode == "transcription":
        task = """
Create a detailed page-by-page transcription and analysis.

Work through the supplied document context in page order.
For every represented page, explain all meaningful content.
Include and analyze important supplied images, diagrams, charts, tables, and equations.
Preserve page ordering and page/location information whenever available.
Prefer completeness and detail.
""".strip()

    else:
        operation = request.get(
            "operation",
            "summarize",
        )

        if operation == "explain":
            task = """
Explain the selected document scope clearly and thoroughly.

Focus only on the selected scope.
Teach the important concepts, relationships, procedures, specifications,
warnings, and conclusions found there.
Do not switch back to summarizing unrelated parts of the document.
Do not transcribe page by page unless page order is necessary to explain
a procedure.
""".strip()

        elif operation == "translate":
            task = """
Translate the meaningful content of the selected document scope while
preserving its technical meaning.

Focus only on the selected scope.
Preserve important numbers, units, identifiers, equations, and technical
terminology accurately.
Do not add unrelated document sections.
""".strip()

        else:
            task = """
Create a high-value summary of the selected document scope.

Do not transcribe page by page.
Identify the main ideas inside the selected scope and give the key takeaway
from each meaningful part.
Emphasize the conclusions, findings, specifications, and what the reader
should remember.
Do not include unrelated parts of the document.
""".strip()

    return f"""
DOCUMENT INFORMATION

Filename:
{document.get("filename")}

File type:
{document.get("file_type")}

Pages:
{document.get("pages_count")}


SELECTED GENERATION MODE

{mode.upper()}

{build_request_guidance(
    request=request,
    selected_pages=selected_pages,
)}

{task}


DEFAULT SUMMARY LANGUAGE FROM CURRENT CHAT

{language_text}

This value is only the default language preference.

If SUMMARY ASSISTANT PREFERENCES contain a newer explicit language request, that request has higher priority.


SUMMARY ASSISTANT PREFERENCES

{instructions}


DOCUMENT TEXT

{text_context}


DOCUMENT ASSETS

{asset_context}


Generate the requested {mode} now as NDJSON.

Use only information grounded in the supplied document context.
Use only supplied asset IDs.
""".strip()

def normalize_section(
    section: dict,
    used_asset_ids: set,
    mode: SummaryMode,
) -> dict | None:
    if mode == "summary":
        valid_types = {
            "text",
        }

    else:
        valid_types = {
            "text",
            "image",
            "table",
            "equation",
        }

    section_type = section.get(
        "type"
    )

    if section_type not in valid_types:
        return None

    asset_id = section.get(
        "asset_id"
    )

    if mode == "summary":
        asset_id = None

    if (
        section_type
        in {
            "image",
            "table",
            "equation",
        }
    ):
        if asset_id is None:
            return None

        if asset_id in used_asset_ids:
            return None

        used_asset_ids.add(
            asset_id
        )

    return {
        "type": section_type,

        "title": section.get(
            "title"
        ),

        "content": section.get(
            "content"
        ),

        "asset_id": asset_id,

        "caption": (
            None
            if mode == "summary"
            else section.get(
                "caption"
            )
        ),

        "location": section.get(
            "location"
        ),
    }

def page_has_meaningful_content(
    page: dict,
) -> bool:
    assets = (
        page.get(
            "assets"
        )
        or []
    )

    if assets:
        return True

    text_context = (
        page.get(
            "text_context"
        )
        or ""
    )

    meaningful_lines = []

    for raw_line in (
        text_context.splitlines()
    ):
        line = raw_line.strip()

        if not line:
            continue

        lowered = (
            line.lower()
        )

        if (
            lowered.startswith(
                "location:"
            )
            or lowered.startswith(
                "type:"
            )
            or line == "---"
        ):
            continue

        meaningful_lines.append(
            line
        )

    meaningful_text = (
        " ".join(
            meaningful_lines
        )
        .strip()
    )

    return len(
        meaningful_text
    ) >= 3


def build_transcription_page_system_prompt(
    output_language: str | None,
) -> str:
    language_text = (
        output_language
        if output_language
        else "the dominant language of the document"
    )

    return f"""
You are an expert document transcription and analysis assistant.

You are processing exactly ONE document page at a time.

OUTPUT LANGUAGE

The visible output MUST be written in {language_text}.

This is a hard requirement.

- The source document language must NOT override the selected output language.
- Translate ordinary headings, descriptions, table headers, role names, labels, explanatory prose, and normal text into {language_text}.
- Keep proper names, company names, model names, technical identifiers, part numbers, phone numbers, equations, codes, and abbreviations unchanged when appropriate.
- If {language_text} is Arabic, write natural fluent Arabic suitable for RTL display.
- If {language_text} is English, write natural English.
- Do not randomly switch back to the source language.

GOAL

Create a clean, detailed, readable representation of the meaningful content on this page.

The final UI already preserves page order internally.

Therefore:

- NEVER write visible headings such as "Page 1", "Page 2", "الصفحة 1", or similar.
- Use the real document heading or section name when useful.
- If there is no meaningful content, do not invent anything.

CONTENTS / INDEX PAGES

If this page is a table of contents, contents page, or index:

- Do NOT return one long paragraph.
- Use one compact text segment containing a vertical list.
- Put one section entry per line.
- Preserve section numbers and referenced page numbers.
- Use bullets when helpful.
- Do NOT create a separate text segment for every single list item.

Example:

- 7.2 نظام الوقود — 296
- 7.2.1 الوقود — 296
- 7.2.2 مضخة الوقود — 296

GENERAL CONTENT RULES

- Preserve meaningful headings, paragraphs, lists, definitions, warnings, procedures, specifications, findings, examples, and conclusions.
- Explain technical material clearly without filler.
- Preserve important numbers, units, symbols, section numbers, terminology, and identifiers.
- Diagram callout numbers such as (1), (2), (3) are NOT useful visible prose. Do not repeat them unless the number itself has technical meaning.
- If the extracted page contains many numbered diagram labels, rewrite them as a coherent explanation of the system/components instead of producing a long sentence that maps every component to a number.
- Prefer explaining relationships and flow: what supplies what, what controls what, and how the components work together.
- Do not output raw HTML.
- Do not output raw <tr>, <td>, <div>, or <img> markup.
- Do not invent information.

TABLES FOUND IN PAGE TEXT

A real table may appear either as a TABLE asset or directly inside PAGE TEXT.
Spreadsheet/XLSX extraction commonly stores sheet data in PAGE TEXT instead of PAGE ASSETS.

When PAGE TEXT contains tabular data, for example:
- a block marked "Type: table",
- spreadsheet rows and columns,
- repeated records sharing the same fields,
- a header row followed by data rows,

then preserve the structure as a real table instead of rewriting the rows into prose.

Rules for tables that exist in PAGE TEXT but do NOT have a corresponding TABLE asset:

- Return the table as valid Markdown inside a normal text segment's "content" field.
- Use a header row and Markdown separator row.
- Keep meaningful rows and columns in their original logical order.
- Preserve numbers, currencies, quantities, dates, names, codes, identifiers, and calculated values accurately.
- Translate ordinary textual headers/cells into {language_text} when appropriate, while preserving proper names and identifiers.
- For XLSX/spreadsheet documents, the table is primary document content; prefer showing the actual table over merely describing it.
- Do not output the same table once as prose and again as a table.
- If the source table is too large for the response budget, preserve the header and as many meaningful rows as possible rather than replacing the whole table with a prose summary.

ASSETS

The supplied page assets already exist in the database.

You must place each useful asset near the text it belongs to.

For IMAGE assets:

- Return only the asset placement.
- Do not produce long visual descriptions for decorative images, cover photos, logos, or obvious photographs.
- Do not add captions unless the page itself has a meaningful caption that helps understanding.

For TABLE assets:

- Preserve the table as a table.
- Translate ordinary textual labels/cells into {language_text}.
- Preserve names, numbers, phone numbers, codes, model identifiers, and values accurately.
- Return the cleaned table content as a Markdown table in the asset segment's "content" field.
- Never return raw HTML table markup.

For EQUATION assets:

- Preserve the mathematical expression accurately.
- Translate only explanatory labels/captions when needed.

ASSET IDs

- Use only asset IDs supplied for this page.
- Never invent an asset ID.
- Use each asset ID at most once.
- Do not mention asset IDs in visible prose.

OUTPUT FORMAT

Return VALID JSON only.

Use this exact shape:

{{
  "segments": [
    {{
      "kind": "text",
      "content": "clean readable text"
    }},
    {{
      "kind": "asset",
      "asset_id": 123,
      "title": null,
      "caption": null,
      "content": null
    }}
  ]
}}

For a translated table asset, use:

{{
  "kind": "asset",
  "asset_id": 123,
  "title": "optional translated title",
  "caption": "optional translated caption",
  "content": "| العمود 1 | العمود 2 |\\n| --- | --- |\\n| ... | ... |"
}}

IMPORTANT:

- Group consecutive prose/list lines into one text segment.
- Do not create one text segment for every sentence or bullet.
- You may alternate text and assets when necessary for natural reading order.
- Do not output markdown fences.
- Do not output anything outside the JSON object.
""".strip()


def is_spreadsheet_document(
    document: Document,
) -> bool:
    file_type = (
        str(
            document.file_type
            or ""
        )
        .strip()
        .lower()
    )

    filename = (
        str(
            document.filename
            or ""
        )
        .strip()
        .lower()
    )

    return (
        file_type
        in {
            "xls",
            "xlsx",
            "excel",
            "spreadsheet",
        }
        or filename.endswith(
            (
                ".xls",
                ".xlsx",
            )
        )
    )


def clean_spreadsheet_transcription_text(
    value: str,
) -> str:
    """
    Remove internal XLSX extraction labels while preserving real values.

    Example:
    A6: 1 | B6: MIS-1 | H6: Formula==D6*G6, Value=9476

    becomes:
    1 | MIS-1 | 9476
    """
    if not value:
        return ""

    cleaned_lines: list[str] = []

    for raw_line in (
        value
        .replace("\x00", "")
        .replace("\r", "")
        .splitlines()
    ):
        line = raw_line.strip()

        if not line:
            cleaned_lines.append(
                ""
            )
            continue

        lowered = line.lower()

        # Keep prompt/context markers untouched.
        if (
            lowered.startswith(
                "location:"
            )
            or lowered.startswith(
                "type:"
            )
            or line == "---"
        ):
            cleaned_lines.append(
                line
            )
            continue

        cleaned_cells: list[str] = []

        for raw_cell in line.split("|"):
            cell = raw_cell.strip()

            if not cell:
                continue

            # Remove internal Excel coordinates such as A6:, B12:, AA27:.
            cell = re.sub(
                r"^\s*\$?[A-Z]{1,3}\$?\d+\s*:\s*",
                "",
                cell,
                flags=re.IGNORECASE,
            ).strip()

            if not cell:
                continue

            # Replace formula-debug extraction with the computed cell value.
            # Example: Formula==D6*G6, Value=9476 -> 9476
            formula_match = re.search(
                r"Formula\s*=+\s*.*?,\s*Value\s*=\s*(.+)$",
                cell,
                flags=re.IGNORECASE,
            )

            if formula_match:
                cell = (
                    formula_match
                    .group(1)
                    .strip()
                )

            if cell:
                cleaned_cells.append(
                    cell
                )

        if cleaned_cells:
            cleaned_lines.append(
                " | ".join(
                    cleaned_cells
                )
            )

    cleaned = "\n".join(
        cleaned_lines
    )

    cleaned = re.sub(
        r"\n{3,}",
        "\n\n",
        cleaned,
    )

    return cleaned.strip()


def build_transcription_page_user_prompt(
    document: Document,
    page: dict,
    instructions: str,
    output_language: str | None,
) -> str:
    page_number = (
        page[
            "page_number"
        ]
    )

    text_context = (
        page.get(
            "text_context"
        )
        or ""
    )

    if is_spreadsheet_document(
        document
    ):
        text_context = (
            clean_spreadsheet_transcription_text(
                text_context
            )
        )

    asset_context = (
        page.get(
            "asset_context"
        )
        or ""
    )

    if is_spreadsheet_document(
        document
    ):
        asset_context = (
            clean_spreadsheet_transcription_text(
                asset_context
            )
        )

    asset_ids = [
        asset.get(
            "id"
        )
        for asset
        in (
            page.get(
                "assets"
            )
            or []
        )
        if isinstance(
            asset.get(
                "id"
            ),
            int,
        )
    ]

    language_text = (
        output_language
        if output_language
        else "the document language"
    )

    return f"""
DOCUMENT

Filename:
{document.filename}

File type:
{document.file_type}

INTERNAL PAGE NUMBER

{page_number}

The page number is for internal ordering only.
Do not print it.


REQUIRED OUTPUT LANGUAGE

{language_text}


SUMMARY ASSISTANT PREFERENCES

{instructions}


PAGE TEXT

{text_context}


PAGE ASSETS

{asset_context}


ALLOWED ASSET IDS

{asset_ids}


Build the clean transcription segments for this page.

Requirements:

- Visible output must be in {language_text}.
- Do not print a page-number heading.
- Skip meaningless extraction noise.
- Remove visual callout markers like (1), (2), (3) from normal prose when they are merely labels from an image/diagram.
- Rewrite callout-heavy extracted text into a clear technical explanation instead of a numbered-label sentence.
- Keep contents/index entries vertical and compact.
- Group consecutive contents/list entries into one text segment.
- Translate table labels/text into {language_text} while preserving names, phone numbers, identifiers, numbers, and technical values.
- If a table exists as a TABLE asset, return its cleaned Markdown table inside that asset segment.
- If tabular/spreadsheet data exists only in PAGE TEXT and has no TABLE asset, reconstruct it as a valid Markdown table inside a normal text segment. Do NOT flatten spreadsheet rows into prose.
- For XLSX/spreadsheet content, preserve the actual row/column structure whenever the source provides it.
- Never expose internal spreadsheet cell coordinates such as A1, B6, C12 as visible labels merely because they were used during extraction.
- Never expose extraction/debug strings such as "Formula=..." or "Value=...". Show the meaningful computed value in the proper table cell instead.
- If PAGE TEXT contains standalone spreadsheet titles or notes, keep their human-readable text but do not prepend their source cell coordinate.
- Do not over-explain decorative images.
- Place each asset in the most natural location.
- Return JSON only.
""".strip()


def clean_transcription_prose(
    value: str,
) -> str:
    text = (
        value
        .replace("\x00", "")
        .replace("\r", "")
        .strip()
    )

    if not text:
        return ""

    # PDF diagrams often extract visual callout labels as
    # "(1) ... (2) ... (3) ...". These numbers are useful
    # inside the original drawing, but they make generated
    # prose difficult to read when the drawing itself is
    # already displayed separately.
    callout_matches = re.findall(
        r"\(\s*(?:[1-9]|[1-9]\d)\s*\)",
        text,
    )

    if len(
        callout_matches
    ) >= 2:
        text = re.sub(
            r"\s*\(\s*(?:[1-9]|[1-9]\d)\s*\)\s*",
            " ",
            text,
        )

    # Never let an accidental JSON wrapper become visible prose.
    text = re.sub(
        r'^\s*\{\s*"segments"\s*:\s*\[',
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = (
        text
        .replace("\\n", "\n")
    )

    text = re.sub(
        r"[ \t]{2,}",
        " ",
        text,
    )

    text = re.sub(
        r" *\n *",
        "\n",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    # Clean spaces before Arabic/Latin punctuation.
    text = re.sub(
        r"\s+([،,:;.!?؟])",
        r"\1",
        text,
    )

    return text.strip()


def clean_spreadsheet_visible_text(
    value: str,
    has_table_asset: bool,
) -> str:
    """
    Final UI guard for spreadsheet transcription text.

    If a real table asset is already rendered for the page, raw extraction
    rows such as "A6: ... | B6: ... | H6: Formula=..., Value=..." are
    duplicates and must not be shown as prose.

    Standalone spreadsheet titles/notes are preserved, but their internal
    cell coordinate is removed.
    """
    if not value:
        return ""

    kept_lines: list[str] = []

    for raw_line in (
        value
        .replace("\x00", "")
        .replace("\r", "")
        .splitlines()
    ):
        line = raw_line.strip()

        if not line:
            if kept_lines and kept_lines[-1] != "":
                kept_lines.append("")
            continue

        cell_markers = re.findall(
            r"(?:(?<=^)|(?<=\|))\s*\$?[A-Z]{1,3}\$?\d+\s*:",
            line,
            flags=re.IGNORECASE,
        )

        looks_like_raw_row = (
            len(cell_markers) >= 2
            or (
                "formula=" in line.casefold()
                and "value=" in line.casefold()
            )
        )

        # A clean table is already rendered separately. Do not duplicate
        # internal spreadsheet row extraction as visible prose.
        if (
            has_table_asset
            and looks_like_raw_row
        ):
            continue

        cleaned = (
            clean_spreadsheet_transcription_text(
                line
            )
        )

        if cleaned:
            kept_lines.append(
                cleaned
            )

    cleaned_text = "\n".join(
        kept_lines
    )

    cleaned_text = re.sub(
        r"\n{3,}",
        "\n\n",
        cleaned_text,
    )

    return cleaned_text.strip()


def extract_transcription_json(
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

    cleaned = (
        cleaned.strip()
    )

    # Prefer the full response first.
    try:
        parsed = json.loads(
            cleaned
        )

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

    except json.JSONDecodeError:
        pass

    # Some models occasionally prepend/append a short sentence
    # even when JSON was requested. Extract the outer JSON object
    # instead of exposing the raw response to the UI.
    first_brace = cleaned.find(
        "{"
    )

    last_brace = cleaned.rfind(
        "}"
    )

    if (
        first_brace >= 0
        and last_brace
        > first_brace
    ):
        candidate = cleaned[
            first_brace:
            last_brace + 1
        ]

        parsed = json.loads(
            candidate
        )

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

    raise ValueError(
        "Could not parse transcription JSON"
    )


def extract_visible_text_from_json_like_response(
    response_text: str,
) -> str | None:
    """
    Recover only user-visible prose from a transcription JSON response.

    This is intentionally defensive. The fallback model is asked for prose,
    but models can occasionally return the original {"segments": [...]} JSON
    shape again. That JSON must never be rendered directly in the UI.
    """
    raw = (
        response_text
        .replace("\x00", "")
        .strip()
    )

    if not raw:
        return ""

    parsed = None

    try:
        parsed = (
            extract_transcription_json(
                raw
            )
        )

    except (
        json.JSONDecodeError,
        ValueError,
    ):
        parsed = None

    if isinstance(
        parsed,
        dict,
    ):
        raw_segments = (
            parsed.get(
                "segments"
            )
        )

        if isinstance(
            raw_segments,
            list,
        ):
            text_parts = []

            for segment in raw_segments:
                if not isinstance(
                    segment,
                    dict,
                ):
                    continue

                if (
                    segment.get(
                        "kind"
                    )
                    != "text"
                ):
                    continue

                content = (
                    segment.get(
                        "content"
                    )
                )

                if (
                    isinstance(
                        content,
                        str,
                    )
                    and content.strip()
                ):
                    text_parts.append(
                        content.strip()
                    )

            return "\n\n".join(
                text_parts
            )

    # If the JSON was cut off because the model hit the token limit,
    # recover complete text-segment content strings that are still present.
    if (
        '"segments"'
        in raw
        and '"kind"'
        in raw
        and '"content"'
        in raw
    ):
        matches = re.finditer(
            (
                r'"kind"\s*:\s*"text"'
                r'\s*,\s*"content"\s*:\s*'
                r'("(?:\\.|[^"\\])*")'
            ),
            raw,
            flags=re.DOTALL,
        )

        recovered = []

        for match in matches:
            encoded_value = (
                match.group(1)
            )

            try:
                decoded_value = (
                    json.loads(
                        encoded_value
                    )
                )

            except json.JSONDecodeError:
                continue

            if (
                isinstance(
                    decoded_value,
                    str,
                )
                and decoded_value.strip()
            ):
                recovered.append(
                    decoded_value.strip()
                )

        if recovered:
            return "\n\n".join(
                recovered
            )

        # It clearly looks like our internal JSON protocol, but it is too
        # malformed to recover safely. Returning an empty string is better
        # than exposing protocol JSON to the user.
        return ""

    return None


def generate_transcription_fallback_text(
    document: Document,
    page: dict,
    instructions: str,
    output_language: str | None,
) -> str:
    language_text = (
        output_language
        if output_language
        else "the document language"
    )

    page_text = (
        page.get(
            "text_context"
        )
        or ""
    )

    if is_spreadsheet_document(
        document
    ):
        page_text = (
            clean_spreadsheet_transcription_text(
                page_text
            )
        )

    response = (
        client.chat.completions.create(
            model=DEEPSEEK_MODEL,

            messages=[
                {
                    "role":
                        "system",

                    "content":
                        f"""
Rewrite the supplied page into clean, understandable {language_text}.

Rules:

- Return readable visible content only.
- Do not return JSON.
- Do not return raw HTML.
- Markdown tables are allowed and required when the source contains real tabular/spreadsheet data.
- If PAGE TEXT contains a block marked "Type: table", spreadsheet rows/columns, or another clear table structure, preserve it as a valid Markdown table instead of paraphrasing the rows into prose.
- For XLSX/spreadsheet documents, keep the actual header and row/column structure whenever possible.
- Preserve numbers, currencies, dates, quantities, names, codes, identifiers, and calculated values accurately.
- Do not duplicate a table as both prose and a table.
- Do not write Page 1 / Page 2 headings.
- If the source contains diagram callout markers such as (1), (2), (3), remove those marker numbers and explain the components naturally.
- Do not chain many callout references into one sentence.
- Group related components by function and explain the flow or relationship between them.
- Keep proper names, model names, part numbers, units, codes, and technical identifiers when needed.
- If the page is a table of contents, use one compact vertical list.
- Do not invent information.
""".strip(),
                },
                {
                    "role":
                        "user",

                    "content":
                        f"""
DOCUMENT:
{document.filename}

USER PREFERENCES:
{instructions}

PAGE TEXT:
{page_text}
""".strip(),
                },
            ],

            temperature=0.05,

            max_tokens=(
                TRANSCRIPTION_MAX_OUTPUT_TOKENS_PER_PAGE
            ),
        )
    )

    content = (
        response
        .choices[0]
        .message
        .content
        or ""
    )

    recovered_json_text = (
        extract_visible_text_from_json_like_response(
            content
        )
    )

    if recovered_json_text is not None:
        return (
            clean_transcription_prose(
                recovered_json_text
            )
            if recovered_json_text
            else ""
        )

    return clean_transcription_prose(
        content
    )


def parse_transcription_page_segments(
    response_text: str,
    allowed_asset_ids: set[int],
) -> list[dict]:
    parsed = (
        extract_transcription_json(
            response_text
        )
    )

    if not isinstance(
        parsed,
        dict,
    ):
        raise ValueError(
            "Transcription page response "
            "must be a JSON object"
        )

    raw_segments = (
        parsed.get(
            "segments"
        )
    )

    if not isinstance(
        raw_segments,
        list,
    ):
        raise ValueError(
            "Transcription page response "
            "is missing segments"
        )

    segments = []

    used_asset_ids: set[int] = set()

    for raw_segment in raw_segments:
        if not isinstance(
            raw_segment,
            dict,
        ):
            continue

        kind = (
            raw_segment.get(
                "kind"
            )
        )

        if kind == "text":
            content = (
                raw_segment.get(
                    "content"
                )
            )

            if isinstance(
                content,
                str,
            ):
                content = (
                    clean_transcription_prose(
                        content
                    )
                )

            if (
                isinstance(
                    content,
                    str,
                )
                and content.strip()
            ):
                # Merge adjacent text segments so the frontend
                # does not create large gaps between every line.
                if (
                    segments
                    and segments[-1].get(
                        "kind"
                    )
                    == "text"
                ):
                    segments[-1][
                        "content"
                    ] = (
                        segments[-1][
                            "content"
                        ].rstrip()
                        + "\n"
                        + content.strip()
                    )

                else:
                    segments.append(
                        {
                            "kind":
                                "text",

                            "content":
                                content.strip(),
                        }
                    )

            continue

        if kind != "asset":
            continue

        asset_id = (
            raw_segment.get(
                "asset_id"
            )
        )

        if (
            not isinstance(
                asset_id,
                int,
            )
            or asset_id
            not in allowed_asset_ids
            or asset_id
            in used_asset_ids
        ):
            continue

        used_asset_ids.add(
            asset_id
        )

        segment = {
            "kind":
                "asset",

            "asset_id":
                asset_id,
        }

        for key in (
            "title",
            "caption",
            "content",
        ):
            value = (
                raw_segment.get(
                    key
                )
            )

            if (
                isinstance(
                    value,
                    str,
                )
                and value.strip()
            ):
                segment[
                    key
                ] = value.strip()

        segments.append(
            segment
        )

    # Never lose real assets because of a formatting miss.
    for asset_id in sorted(
        allowed_asset_ids
    ):
        if asset_id in used_asset_ids:
            continue

        segments.append(
            {
                "kind":
                    "asset",

                "asset_id":
                    asset_id,
            }
        )

    return segments


def generate_transcription_page_segments(
    document: Document,
    page: dict,
    instructions: str,
    output_language: str | None,
) -> list[dict]:
    allowed_asset_ids = {
        asset[
            "id"
        ]
        for asset
        in (
            page.get(
                "assets"
            )
            or []
        )
        if isinstance(
            asset.get(
                "id"
            ),
            int,
        )
    }

    response = (
        client.chat.completions.create(
            model=DEEPSEEK_MODEL,

            messages=[
                {
                    "role":
                        "system",

                    "content":
                        build_transcription_page_system_prompt(
                            output_language
                        ),
                },
                {
                    "role":
                        "user",

                    "content":
                        build_transcription_page_user_prompt(
                            document=document,
                            page=page,
                            instructions=instructions,
                            output_language=(
                                output_language
                            ),
                        ),
                },
            ],

            temperature=0.05,

            response_format={
                "type":
                    "json_object",
            },

            max_tokens=(
                TRANSCRIPTION_MAX_OUTPUT_TOKENS_PER_PAGE
            ),
        )
    )

    content = (
        response
        .choices[0]
        .message
        .content
        or ""
    ).strip()

    if not content:
        return [
            {
                "kind":
                    "asset",

                "asset_id":
                    asset_id,
            }
            for asset_id
            in sorted(
                allowed_asset_ids
            )
        ]

    try:
        return (
            parse_transcription_page_segments(
                response_text=content,
                allowed_asset_ids=(
                    allowed_asset_ids
                ),
            )
        )

    except (
        json.JSONDecodeError,
        ValueError,
    ):
        fallback_segments = []

        fallback_text = (
            generate_transcription_fallback_text(
                document=document,
                page=page,
                instructions=instructions,
                output_language=(
                    output_language
                ),
            )
        )

        if fallback_text:
            fallback_segments.append(
                {
                    "kind":
                        "text",

                    "content":
                        fallback_text,
                }
            )

        for asset_id in sorted(
            allowed_asset_ids
        ):
            fallback_segments.append(
                {
                    "kind":
                        "asset",

                    "asset_id":
                        asset_id,
                }
            )

        return fallback_segments


def build_asset_section(
    asset: dict,
    segment: dict,
) -> dict | None:
    asset_type = (
        asset.get(
            "type"
        )
    )

    asset_id = (
        asset.get(
            "id"
        )
    )

    if asset_type not in {
        "image",
        "table",
        "equation",
    }:
        return None

    if not isinstance(
        asset_id,
        int,
    ):
        return None

    if asset_type == "image":
        return {
            "type":
                "image",

            "title":
                None,

            "content":
                None,

            "asset_id":
                asset_id,

            "caption":
                None,

            "location":
                None,
        }

    translated_title = (
        segment.get(
            "title"
        )
    )

    translated_caption = (
        segment.get(
            "caption"
        )
    )

    translated_content = (
        segment.get(
            "content"
        )
    )

    return {
        "type":
            asset_type,

        "title":
            (
                translated_title
                if isinstance(
                    translated_title,
                    str,
                )
                and translated_title.strip()
                else asset.get(
                    "title"
                )
            ),

        "content":
            (
                translated_content
                if isinstance(
                    translated_content,
                    str,
                )
                and translated_content.strip()
                else asset.get(
                    "content"
                )
            ),

        "asset_id":
            asset_id,

        "caption":
            (
                translated_caption
                if isinstance(
                    translated_caption,
                    str,
                )
                and translated_caption.strip()
                else asset.get(
                    "caption"
                )
            ),

        "location":
            None,
    }


def stream_transcription_content(
    db: Session,
    document: Document,
    chat_id: int,
    request: dict | None = None,
    selected_pages:
        list[int] | None = None,
):
    if request is None:
        request = (
            resolve_generation_request(
                db=db,
                chat_id=chat_id,
                document_id=document.id,
                mode="transcription",
            )
        )

    if selected_pages is None:
        selected_pages = (
            resolve_generation_pages(
                db=db,
                document=document,
                request=request,
            )
        )

    context = (
        get_document_transcription_context(
            db=db,
            document=document,
            page_numbers=(
                selected_pages
            ),
        )
    )

    pages = (
        context.get(
            "pages"
        )
        or []
    )

    if not pages:
        raise ValueError(
            "Document has no pages available "
            "for transcription"
        )

    instructions = (
        build_summary_instruction_context(
            db=db,
            chat_id=chat_id,
            document_id=document.id,
        )
        + "\n\n"
        + build_request_guidance(
            request=request,
            selected_pages=(
                selected_pages
            ),
        )
    )

    output_language = (
        request.get(
            "target_language"
        )
        or resolve_output_language(
            db=db,
            chat_id=chat_id,
            document_id=document.id,
        )
    )

    title = (
        "تفريغ وتحليل المستند"
        if output_language
        == "Arabic"
        else "Document transcription"
    )

    yield {
        "type":
            "title",

        "title":
            title,
    }

    sections = []

    for page in pages:
        if not page_has_meaningful_content(
            page
        ):
            continue

        page_assets = (
            page.get(
                "assets"
            )
            or []
        )

        assets_by_id = {
            asset[
                "id"
            ]:
                asset
            for asset
            in page_assets
            if isinstance(
                asset.get(
                    "id"
                ),
                int,
            )
        }

        page_segments = (
            generate_transcription_page_segments(
                document=document,
                page=page,
                instructions=instructions,
                output_language=(
                    output_language
                ),
            )
        )

        for segment in page_segments:
            kind = (
                segment.get(
                    "kind"
                )
            )

            if kind == "text":
                content = (
                    segment.get(
                        "content"
                    )
                )

                if (
                    not isinstance(
                        content,
                        str,
                    )
                    or not content.strip()
                ):
                    continue

                if is_spreadsheet_document(
                    document
                ):
                    has_table_asset = any(
                        asset.get(
                            "type"
                        )
                        == "table"
                        for asset
                        in page_assets
                    )

                    content = (
                        clean_spreadsheet_visible_text(
                            value=content,
                            has_table_asset=(
                                has_table_asset
                            ),
                        )
                    )

                    if not content:
                        continue

                text_section = {
                    "type":
                        "text",

                    "title":
                        None,

                    "content":
                        content.strip(),

                    "asset_id":
                        None,

                    "caption":
                        None,

                    "location":
                        None,
                }

                sections.append(
                    text_section
                )

                yield {
                    "type":
                        "section",

                    "section":
                        text_section,
                }

                continue

            if kind != "asset":
                continue

            asset_id = (
                segment.get(
                    "asset_id"
                )
            )

            asset = (
                assets_by_id.get(
                    asset_id
                )
            )

            if asset is None:
                continue

            asset_section = (
                build_asset_section(
                    asset=asset,
                    segment=segment,
                )
            )

            if asset_section is None:
                continue

            sections.append(
                asset_section
            )

            yield {
                "type":
                    "section",

                "section":
                    asset_section,
            }

    if not sections:
        raise ValueError(
            "Document has no meaningful "
            "content for transcription"
        )

    return {
        "title":
            title,

        "sections":
            sections,
    }


def stream_summary_content(
    db: Session,
    document: Document,
    chat_id: int,
    mode: SummaryMode = "summary",
):
    if mode not in {
        "summary",
        "transcription",
    }:
        raise ValueError(
            "Invalid summary mode"
        )

    request = (
        resolve_generation_request(
            db=db,
            chat_id=chat_id,
            document_id=document.id,
            mode=mode,
        )
    )

    selected_pages = (
        resolve_generation_pages(
            db=db,
            document=document,
            request=request,
        )
    )

    if mode == "transcription":
        transcription_generator = (
            stream_transcription_content(
                db=db,
                document=document,
                chat_id=chat_id,
                request=request,
                selected_pages=(
                    selected_pages
                ),
            )
        )

        while True:
            try:
                event = next(
                    transcription_generator
                )

                yield event

            except StopIteration as stop:
                if stop.value:
                    return stop.value

                break

        raise ValueError(
            "Could not complete transcription"
        )

    context = (
        get_document_summary_context(
            db=db,
            document=document,
            page_numbers=(
                selected_pages
            ),
        )
    )

    if (
        not context.get(
            "text_context"
        )
        and not context.get(
            "asset_context"
        )
    ):
        raise ValueError(
            "Document has no usable content for summary generation"
        )

    instructions = (
        build_summary_instruction_context(
            db=db,
            chat_id=chat_id,
            document_id=document.id,
        )
        + "\n\n"
        + build_request_guidance(
            request=request,
            selected_pages=(
                selected_pages
            ),
        )
    )

    default_language = (
        request.get(
            "target_language"
        )
        or resolve_output_language(
            db=db,
            chat_id=chat_id,
            document_id=document.id,
        )
    )

    response = (
        client.chat.completions.create(
            model=DEEPSEEK_MODEL,

            messages=[
                {
                    "role":
                        "system",

                    "content":
                        build_summary_system_prompt(
                            mode=mode,
                            request=request,
                        ),
                },
                {
                    "role":
                        "user",

                    "content":
                        build_summary_user_prompt(
                            context=context,
                            instructions=instructions,
                            default_language=(
                                default_language
                            ),
                            mode=mode,
                            request=request,
                            selected_pages=(
                                selected_pages
                            ),
                        ),
                },
            ],

            temperature=0.2,

            stream=True,
        )
    )

    buffer = ""

    title = None

    sections = []

    used_asset_ids = set()

    for chunk in response:
        delta = (
            chunk
            .choices[0]
            .delta
            .content
            or ""
        )

        if not delta:
            continue

        buffer += delta

        while "\n" in buffer:
            line, buffer = (
                buffer.split(
                    "\n",
                    1,
                )
            )

            line = line.strip()

            if not line:
                continue

            try:
                item = json.loads(
                    line
                )

            except json.JSONDecodeError:
                continue

            if (
                item.get(
                    "type"
                )
                == "title"
            ):
                candidate_title = (
                    item.get(
                        "title"
                    )
                )

                if (
                    isinstance(
                        candidate_title,
                        str,
                    )
                    and candidate_title.strip()
                ):
                    title = (
                        candidate_title
                        .strip()
                    )

                    yield {
                        "type":
                            "title",

                        "title":
                            title,
                    }

                continue

            if (
                item.get(
                    "type"
                )
                != "section"
            ):
                continue

            raw_section = (
                item.get(
                    "section"
                )
            )

            if not isinstance(
                raw_section,
                dict,
            ):
                continue

            section = (
                normalize_section(
                    raw_section,
                    used_asset_ids,
                    mode,
                )
            )

            if section is None:
                continue

            sections.append(
                section
            )

            yield {
                "type":
                    "section",

                "section":
                    section,
            }

    remaining = (
        buffer.strip()
    )

    if remaining:
        try:
            item = json.loads(
                remaining
            )

            if (
                item.get(
                    "type"
                )
                == "title"
            ):
                candidate_title = (
                    item.get(
                        "title"
                    )
                )

                if (
                    isinstance(
                        candidate_title,
                        str,
                    )
                    and candidate_title.strip()
                ):
                    title = (
                        candidate_title
                        .strip()
                    )

                    yield {
                        "type":
                            "title",

                        "title":
                            title,
                    }

            elif (
                item.get(
                    "type"
                )
                == "section"
                and isinstance(
                    item.get(
                        "section"
                    ),
                    dict,
                )
            ):
                section = (
                    normalize_section(
                        item[
                            "section"
                        ],
                        used_asset_ids,
                        mode,
                    )
                )

                if section is not None:
                    sections.append(
                        section
                    )

                    yield {
                        "type":
                            "section",

                        "section":
                            section,
                    }

        except json.JSONDecodeError:
            pass

    if not title:
        title = (
            document.filename
            or "Document Summary"
        )

        yield {
            "type":
                "title",

            "title":
                title,
        }

    if not sections:
        raise ValueError(
            "AI returned no usable summary sections"
        )

    return {
        "title":
            title,

        "sections":
            sections,
    }


def generate_summary_content(
    db: Session,
    document: Document,
    chat_id: int,
    mode: SummaryMode = "summary",
) -> dict:
    generator = (
        stream_summary_content(
            db=db,
            document=document,
            chat_id=chat_id,
            mode=mode,
        )
    )

    title = None

    sections = []

    while True:
        try:
            event = next(
                generator
            )

            if (
                event.get(
                    "type"
                )
                == "title"
            ):
                title = (
                    event.get(
                        "title"
                    )
                )

            elif (
                event.get(
                    "type"
                )
                == "section"
            ):
                sections.append(
                    event[
                        "section"
                    ]
                )

        except StopIteration as stop:
            if stop.value:
                return stop.value

            break

    if (
        not title
        or not sections
    ):
        raise ValueError(
            "Could not generate summary"
        )

    return {
        "title":
            title,

        "sections":
            sections,
    }


def generate_summary_for_record(
    db: Session,
    document: Document,
    summary,
    mode: SummaryMode = "summary",
):
    try:
        if summary.chat_id is None:
            raise ValueError(
                "Summary chat context is missing"
            )

        mark_summary_generating(
            db=db,
            summary=summary,
        )

        content = (
            generate_summary_content(
                db=db,
                document=document,
                chat_id=summary.chat_id,
                mode=mode,
            )
        )

        return (
            mark_summary_completed(
                db=db,
                summary=summary,
                content=content,
            )
        )

    except Exception as error:
        db.rollback()

        return (
            mark_summary_failed(
                db=db,
                summary=summary,
                error=str(
                    error
                ),
            )
        )