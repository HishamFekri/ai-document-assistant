import logging
import os
import re

from sqlalchemy.orm import Session

from app.database.models import (
    Chat,
    Message,
)

from app.services.search_service import (
    search_chunks_by_page,
    search_similar_chunks,
    search_visual_chunks,
)

from app.services.llm_service import (
    generate_answer,
)


logger = logging.getLogger(__name__)


MAX_HISTORY_MESSAGES = 10
MAX_SOURCE_SNIPPET_CHARS = 420

RAG_MIN_CONFIDENCE_SCORE = float(
    os.getenv(
        "RAG_MIN_CONFIDENCE_SCORE",
        "0.50",
    )
)


ARABIC_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)

PAGE_PATTERNS = [
    r"\bpage\s*(?:number|no\.?|#)?\s*(\d+)\b",
    r"\bp\.?\s*(\d+)\b",
    r"(?:الصفحة|الصفحه|صفحة|صفحه)\s*(?:رقم)?\s*(\d+)",
]

PAGE_COUNT_PATTERNS = [
    r"كم\s+(?:عدد\s+)?(?:الصفحات|صفحة|صفحه)",
    r"(?:ما|شو)\s+(?:هو\s+)?عدد\s+(?:الصفحات|الصفحات)",
    r"عدد\s+(?:صفحات|الصفحات)\s+(?:المستند|الملف|الوثيقة|الوثيقه)",
    r"how\s+many\s+pages",
    r"\bpage\s+count\b",
    r"\bnumber\s+of\s+pages\b",
    r"\btotal\s+pages\b",
]

VISUAL_KEYWORDS = {
    "image",
    "images",
    "picture",
    "pictures",
    "photo",
    "photos",
    "figure",
    "figures",
    "chart",
    "charts",
    "diagram",
    "diagrams",
    "graph",
    "graphs",
    "plot",
    "plots",
    "visual",
    "visuals",
    "صورة",
    "صور",
    "الصورة",
    "الصور",
    "شكل",
    "أشكال",
    "اشكال",
    "الشكل",
    "الأشكال",
    "الاشكال",
    "مخطط",
    "مخططات",
    "المخطط",
    "المخططات",
    "رسم",
    "رسمة",
    "رسومات",
    "الرسم",
    "الرسومات",
    "جراف",
    "جرافات",
    "بياني",
    "بيانية",
    "بيانيه",
}


def normalize_digits(
    text: str,
) -> str:
    return text.translate(
        ARABIC_DIGIT_TRANSLATION
    )


def detect_question_language(
    question: str,
) -> str:
    arabic_count = len(
        re.findall(
            r"[\u0600-\u06FF]",
            question,
        )
    )

    latin_count = len(
        re.findall(
            r"[A-Za-z]",
            question,
        )
    )

    if arabic_count > latin_count:
        return "ar"

    return "en"


def extract_page_number(
    question: str,
) -> int | None:
    normalized = normalize_digits(
        question
    )

    for pattern in PAGE_PATTERNS:
        match = re.search(
            pattern,
            normalized,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        try:
            page_number = int(
                match.group(1)
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if page_number > 0:
            return page_number

    return None


def is_page_count_question(
    question: str,
) -> bool:
    normalized = normalize_digits(
        question
    )

    return any(
        re.search(
            pattern,
            normalized,
            flags=re.IGNORECASE,
        )
        is not None
        for pattern in PAGE_COUNT_PATTERNS
    )


def is_visual_question(
    question: str,
) -> bool:
    lowered = (
        normalize_digits(
            question
        )
        .lower()
    )

    return any(
        keyword in lowered
        for keyword in VISUAL_KEYWORDS
    )


def resolve_target_documents(
    chat: Chat,
    requested_document_ids:
        list[int] | None = None,
):
    chat_documents = list(
        chat.documents
    )

    if not requested_document_ids:
        return chat_documents

    requested_ids = []

    for document_id in (
        requested_document_ids
    ):
        if (
            document_id
            not in requested_ids
        ):
            requested_ids.append(
                document_id
            )

    documents_by_id = {
        document.id: document
        for document
        in chat_documents
    }

    missing_ids = [
        document_id
        for document_id
        in requested_ids
        if (
            document_id
            not in documents_by_id
        )
    ]

    if missing_ids:
        raise ValueError(
            "One or more selected documents "
            "are not attached to this chat"
        )

    return [
        documents_by_id[
            document_id
        ]
        for document_id
        in requested_ids
    ]


def build_source_snippet(
    content: str | None,
) -> str | None:
    if not content:
        return None

    cleaned = re.sub(
        r"\s+",
        " ",
        str(content),
    ).strip()

    if not cleaned:
        return None

    if (
        len(cleaned)
        <= MAX_SOURCE_SNIPPET_CHARS
    ):
        return cleaned

    return (
        cleaned[
            :MAX_SOURCE_SNIPPET_CHARS - 3
        ].rstrip()
        + "..."
    )


def build_context(
    search_results,
) -> str:
    context_parts = []

    for index, result in enumerate(
        search_results,
        start=1,
    ):
        chunk = result["chunk"]

        similarity = result.get(
            "similarity",
            1.0,
        )

        ranking_score = result.get(
            "ranking_score"
        )

        match_type = result.get(
            "match_type",
            "semantic",
        )

        document = chunk.document

        metadata = (
            chunk.chunk_metadata
            or {}
        )

        source_id = f"S{index}"

        context_lines = [
            f"[{source_id}]",
            f"Document: {document.filename}",
            f"Document ID: {document.id}",
            f"Type: {chunk.content_type}",
            f"Location: {chunk.location}",
            f"Match type: {match_type}",
        ]

        if (
            match_type
            in {
                "semantic",
                "hybrid",
            }
        ):
            context_lines.append(
                (
                    "Similarity: "
                    f"{similarity:.4f}"
                )
            )

        if ranking_score is not None:
            context_lines.append(
                (
                    "Ranking score: "
                    f"{ranking_score:.4f}"
                )
            )

        asset_filename = (
            metadata.get(
                "asset_filename"
            )
        )

        if asset_filename:
            context_lines.append(
                (
                    "Asset filename: "
                    f"{asset_filename}"
                )
            )

        for key in (
            "caption",
            "description",
            "alt_text",
            "title",
        ):
            value = metadata.get(
                key
            )

            if value:
                context_lines.append(
                    (
                        f"{key.replace('_', ' ').title()}: "
                        f"{value}"
                    )
                )

        context_lines.append(
            "Content:"
        )

        context_lines.append(
            chunk.content
            or ""
        )

        context_parts.append(
            "\n".join(
                context_lines
            )
        )

    return "\n\n---\n\n".join(
        context_parts
    )


def build_sources(
    search_results,
):
    sources = []

    for index, result in enumerate(
        search_results,
        start=1,
    ):
        chunk = result["chunk"]

        similarity = result.get(
            "similarity",
            1.0,
        )

        document = chunk.document

        metadata = (
            chunk.chunk_metadata
            or {}
        )

        snippet = (
            build_source_snippet(
                chunk.content
            )
        )

        source = {
            "source_id": f"S{index}",
            "document_id": document.id,
            "filename": document.filename,
            "content_type": (
                chunk.content_type
            ),
            "location": (
                chunk.location
            ),
            "chunk_id": chunk.id,
            "similarity": round(
                similarity,
                4,
            ),
            "match_type": result.get(
                "match_type",
                "semantic",
            ),
        }

        ranking_score = result.get(
            "ranking_score"
        )

        if ranking_score is not None:
            source[
                "ranking_score"
            ] = round(
                ranking_score,
                4,
            )

        if snippet:
            source[
                "snippet"
            ] = snippet

        asset_filename = (
            metadata.get(
                "asset_filename"
            )
        )

        if (
            chunk.content_type
            == "image"
            and asset_filename
        ):
            source[
                "asset_filename"
            ] = asset_filename

            source[
                "asset_url"
            ] = (
                f"/documents/"
                f"{document.id}"
                f"/assets/"
                f"{asset_filename}"
            )

        sources.append(
            source
        )

    return sources


def extract_cited_source_ids(
    answer: str,
) -> set[str]:
    matches = re.findall(
        r"\[(S\d+)\]",
        answer,
        flags=re.IGNORECASE,
    )

    return {
        match.upper()
        for match in matches
    }


def filter_sources_by_citations(
    sources: list[dict],
    answer: str,
):
    visual_sources = []
    seen_assets = set()

    for source in sources:
        asset_url = source.get(
            "asset_url"
        )

        if not asset_url:
            continue

        asset_key = (
            source.get(
                "document_id"
            ),
            source.get(
                "asset_filename"
            )
            or asset_url,
        )

        if asset_key in seen_assets:
            continue

        seen_assets.add(
            asset_key
        )

        visual_sources.append(
            source
        )

    return visual_sources


def get_conversation_history(
    db: Session,
    chat_id: int,
):
    messages = (
        db.query(Message)
        .filter(
            Message.chat_id
            == chat_id,
            Message.status
            == "completed",
        )
        .order_by(
            Message.created_at.desc(),
            Message.id.desc(),
        )
        .limit(
            MAX_HISTORY_MESSAGES
        )
        .all()
    )

    messages.reverse()

    history = []

    for message in messages:
        if message.role not in {
            "user",
            "assistant",
        }:
            continue

        if not message.content:
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


def build_page_count_answer(
    documents,
    question: str,
) -> str:
    language = (
        detect_question_language(
            question
        )
    )

    if not documents:
        if language == "ar":
            return (
                "لا يوجد مستند محدد لهذا السؤال."
            )

        return (
            "There is no selected document "
            "for this question."
        )

    if len(documents) == 1:
        document = documents[0]

        if (
            document.pages_count
            is None
        ):
            if language == "ar":
                return (
                    "عدد صفحات المستند غير متوفر "
                    "في بيانات الملف."
                )

            return (
                "The document page count is "
                "not available in its metadata."
            )

        if language == "ar":
            return (
                f"المستند يحتوي على "
                f"{document.pages_count} صفحة."
            )

        return (
            f"The document has "
            f"{document.pages_count} pages."
        )

    known_documents = [
        document
        for document in documents
        if (
            document.pages_count
            is not None
        )
    ]

    unknown_documents = [
        document
        for document in documents
        if (
            document.pages_count
            is None
        )
    ]

    lines = []

    if language == "ar":
        lines.append(
            "عدد الصفحات في المستندات المحددة:"
        )

        for document in known_documents:
            lines.append(
                (
                    f"- {document.filename}: "
                    f"{document.pages_count} صفحة"
                )
            )

        for document in unknown_documents:
            lines.append(
                (
                    f"- {document.filename}: "
                    "عدد الصفحات غير متوفر"
                )
            )

    else:
        lines.append(
            "Page counts for the selected documents:"
        )

        for document in known_documents:
            lines.append(
                (
                    f"- {document.filename}: "
                    f"{document.pages_count} pages"
                )
            )

        for document in unknown_documents:
            lines.append(
                (
                    f"- {document.filename}: "
                    "page count unavailable"
                )
            )

    return "\n".join(
        lines
    )


def validate_page_number(
    documents,
    page_number: int,
) -> bool:
    for document in documents:
        pages_count = (
            document.pages_count
        )

        if pages_count is None:
            return True

        if (
            page_number
            <= pages_count
        ):
            return True

    return False


def build_missing_page_answer(
    documents,
    question: str,
    page_number: int,
) -> str:
    language = (
        detect_question_language(
            question
        )
    )

    counts = [
        document.pages_count
        for document in documents
        if (
            document.pages_count
            is not None
        )
    ]

    if counts and all(
        page_number > count
        for count in counts
    ):
        if language == "ar":
            if (
                len(documents) == 1
                and len(counts) == 1
            ):
                return (
                    f"الصفحة {page_number} غير موجودة "
                    f"في المستند؛ عدد صفحاته "
                    f"{counts[0]} صفحة."
                )

            return (
                f"الصفحة {page_number} تتجاوز عدد "
                "الصفحات في المستندات المحددة."
            )

        if (
            len(documents) == 1
            and len(counts) == 1
        ):
            return (
                f"Page {page_number} does not exist "
                f"in this document; it has "
                f"{counts[0]} pages."
            )

        return (
            f"Page {page_number} is outside the "
            "page range of the selected documents."
        )

    if language == "ar":
        return (
            f"الصفحة {page_number} موجودة ضمن نطاق "
            "المستند، لكن لم أجد محتوى قابلًا "
            "للاستخراج منها."
        )

    return (
        f"Page {page_number} is within the document "
        "range, but I could not find extractable "
        "content for that page."
    )


def build_no_answer(
    question: str,
) -> str:
    language = (
        detect_question_language(
            question
        )
    )

    if language == "ar":
        return (
            "ما لقيت معلومات كافية وموثوقة "
            "عن هذا السؤال داخل الملفات المحددة."
        )

    return (
        "I couldn't find enough reliable "
        "information about this question "
        "in the selected files."
    )


def merge_search_results(
    *result_groups,
):
    merged = []
    seen_chunk_ids = set()

    for group in result_groups:
        for result in group:
            chunk = result["chunk"]

            if chunk.id in seen_chunk_ids:
                continue

            seen_chunk_ids.add(
                chunk.id
            )

            merged.append(
                result
            )

    return merged


def get_primary_results(
    search_results,
):
    return [
        result
        for result in search_results
        if (
            result.get(
                "match_type"
            )
            != "semantic_companion"
        )
    ]


def get_retrieval_confidence(
    search_results,
) -> float:
    primary_results = (
        get_primary_results(
            search_results
        )
    )

    if not primary_results:
        return 0.0

    scores = []

    for result in primary_results:
        ranking_score = (
            result.get(
                "ranking_score"
            )
        )

        if ranking_score is not None:
            scores.append(
                float(
                    ranking_score
                )
            )
            continue

        scores.append(
            float(
                result.get(
                    "similarity",
                    0.0,
                )
            )
        )

    if not scores:
        return 0.0

    return max(
        scores
    )


def has_confident_retrieval(
    search_results,
) -> bool:
    confidence = (
        get_retrieval_confidence(
            search_results
        )
    )

    logger.debug(
        "RAG retrieval confidence=%.4f minimum=%.2f",
        confidence,
        RAG_MIN_CONFIDENCE_SCORE,
    )

    return (
        confidence
        >= RAG_MIN_CONFIDENCE_SCORE
    )


def build_retrieval_guidance(
    search_results,
    documents,
) -> str:
    result_document_ids = {
        result["chunk"].document_id
        for result in search_results
    }

    selected_document_ids = {
        document.id
        for document in documents
    }

    multiple_documents = (
        len(
            result_document_ids
        )
        > 1
    )

    targeting_note = (
        "The user explicitly selected a subset "
        "of chat documents for this question."
        if (
            selected_document_ids
            and len(selected_document_ids)
            < len(
                {
                    document.id
                    for document in documents
                }
            )
        )
        else (
            "Use only the document evidence "
            "included below."
        )
    )

    conflict_note = (
        "Evidence comes from multiple documents. "
        "Keep each document's claims separate. "
        "If two documents provide different values, "
        "instructions, specifications, dates, or conclusions, "
        "state the difference clearly instead of merging them "
        "into one value or silently choosing one."
        if multiple_documents
        else (
            "If retrieved passages within the document "
            "appear inconsistent, do not silently reconcile "
            "them. Mention the discrepancy when it affects "
            "the answer."
        )
    )

    return (
        "RETRIEVAL GUIDANCE\n"
        f"{targeting_note}\n"
        f"{conflict_note}\n"
        "Prefer exact values from tables, equations, "
        "and directly matching sections over vague nearby text.\n"
        "Companion chunks provide surrounding page context; "
        "they are supporting context and may be less directly "
        "matched to the question."
    )


def prepare_answer_context(
    db: Session,
    chat_id: int,
    question: str,
    allow_general_knowledge:
        bool = False,
    document_ids:
        list[int] | None = None,
):
    chat = db.get(
        Chat,
        chat_id,
    )

    if not chat:
        raise ValueError(
            "Chat not found"
        )

    conversation_history = (
        get_conversation_history(
            db=db,
            chat_id=chat_id,
        )
    )

    documents = (
        resolve_target_documents(
            chat=chat,
            requested_document_ids=(
                document_ids
            ),
        )
    )

    target_document_ids = [
        document.id
        for document in documents
    ]

    logger.debug(
        "RAG conversation history messages=%s",
        len(conversation_history),
    )

    logger.debug(
        "RAG target documents=%s",
        target_document_ids,
    )

    if not target_document_ids:
        if allow_general_knowledge:
            return {
                "immediate_answer":
                    None,
                "context":
                    "",
                "conversation_history":
                    conversation_history,
                "candidate_sources":
                    [],
                "mode":
                    "files_and_general",
                "retrieval_mode":
                    "general_only",
                "target_document_ids":
                    [],
            }

        return {
            "immediate_answer":
                (
                    "This chat has no attached "
                    "documents. Attach a document "
                    "or enable general knowledge."
                ),
            "context":
                "",
            "conversation_history":
                conversation_history,
            "candidate_sources":
                [],
            "mode":
                "files_only",
            "retrieval_mode":
                "no_documents",
            "target_document_ids":
                [],
        }

    if is_page_count_question(
        question
    ):
        logger.debug(
            "RAG retrieval mode=document_metadata"
        )

        return {
            "immediate_answer":
                build_page_count_answer(
                    documents=documents,
                    question=question,
                ),
            "context":
                "",
            "conversation_history":
                conversation_history,
            "candidate_sources":
                [],
            "mode":
                "files_only",
            "retrieval_mode":
                "document_metadata",
            "target_document_ids":
                target_document_ids,
        }

    page_number = (
        extract_page_number(
            question
        )
    )

    visual_question = (
        is_visual_question(
            question
        )
    )

    if page_number is not None:
        logger.debug(
            "RAG retrieval mode=exact_page page=%s",
            page_number,
        )

        page_is_possible = (
            validate_page_number(
                documents=documents,
                page_number=page_number,
            )
        )

        if not page_is_possible:
            return {
                "immediate_answer":
                    build_missing_page_answer(
                        documents=documents,
                        question=question,
                        page_number=page_number,
                    ),
                "context":
                    "",
                "conversation_history":
                    conversation_history,
                "candidate_sources":
                    [],
                "mode":
                    "files_only",
                "retrieval_mode":
                    "exact_page",
                "target_document_ids":
                    target_document_ids,
            }

        page_results = (
            search_chunks_by_page(
                db=db,
                document_ids=(
                    target_document_ids
                ),
                page_number=(
                    page_number
                ),
            )
        )

        if not page_results:
            return {
                "immediate_answer":
                    build_missing_page_answer(
                        documents=documents,
                        question=question,
                        page_number=page_number,
                    ),
                "context":
                    "",
                "conversation_history":
                    conversation_history,
                "candidate_sources":
                    [],
                "mode":
                    "files_only",
                "retrieval_mode":
                    "exact_page",
                "target_document_ids":
                    target_document_ids,
            }

        if visual_question:
            visual_page_results = [
                result
                for result in page_results
                if (
                    result["chunk"]
                    .content_type
                    in {
                        "image",
                        "table",
                        "equation",
                    }
                )
            ]

            page_results = (
                merge_search_results(
                    visual_page_results,
                    page_results,
                )
            )

        page_context = (
            build_context(
                page_results
            )
        )

        context = (
            "EXACT PAGE REQUEST\n"
            f"The user explicitly asked about page {page_number}.\n"
            "The evidence below was retrieved from that exact page "
            "within the selected document scope.\n"
            "Give a faithful and detailed walkthrough of the page.\n"
            "Cover the visible section/title structure, important prose, "
            "technical details, numbers, tables, equations, figures, "
            "charts, diagrams, and images whenever those items are present "
            "in the extracted evidence.\n"
            "If the selected scope contains multiple documents and more "
            "than one has this page number, keep their contents separate "
            "by document instead of mixing them.\n"
            "If different documents disagree, explicitly describe the "
            "difference rather than choosing one silently.\n"
            "Do not mention source IDs or a Sources section.\n"
            "Do not say that an image is unavailable if an image chunk "
            "is present; describe the extracted image evidence and the "
            "frontend will render the image separately.\n\n"
            + page_context
        )

        candidate_sources = (
            build_sources(
                page_results
            )
        )

        return {
            "immediate_answer":
                None,
            "context":
                context,
            "conversation_history":
                conversation_history,
            "candidate_sources":
                candidate_sources,
            "mode":
                "files_only",
            "retrieval_mode":
                (
                    "exact_page_visual"
                    if visual_question
                    else "exact_page"
                ),
            "target_document_ids":
                target_document_ids,
        }

    if visual_question:
        logger.debug(
            "RAG retrieval mode=visual_semantic"
        )

        visual_results = (
            search_visual_chunks(
                db=db,
                query=question,
                document_ids=(
                    target_document_ids
                ),
            )
        )

        if not visual_results:
            language = (
                detect_question_language(
                    question
                )
            )

            if language == "ar":
                immediate_answer = (
                    "لم أجد صورة أو مخططًا مرتبطًا "
                    "بهذا السؤال داخل الملفات المحددة."
                )
            else:
                immediate_answer = (
                    "I could not find an image, "
                    "chart, or diagram related to "
                    "this question in the selected files."
                )

            return {
                "immediate_answer":
                    immediate_answer,
                "context":
                    "",
                "conversation_history":
                    conversation_history,
                "candidate_sources":
                    [],
                "mode":
                    "files_only",
                "retrieval_mode":
                    "visual_semantic",
                "target_document_ids":
                    target_document_ids,
            }

        visual_context = (
            build_context(
                visual_results
            )
        )

        context = (
            build_retrieval_guidance(
                search_results=(
                    visual_results
                ),
                documents=documents,
            )
            + "\n\n"
            + visual_context
        )

        candidate_sources = (
            build_sources(
                visual_results
            )
        )

        return {
            "immediate_answer":
                None,
            "context":
                context,
            "conversation_history":
                conversation_history,
            "candidate_sources":
                candidate_sources,
            "mode":
                "files_only",
            "retrieval_mode":
                "visual_semantic",
            "target_document_ids":
                target_document_ids,
        }

    search_results = (
        search_similar_chunks(
            db=db,
            query=question,
            document_ids=(
                target_document_ids
            ),
        )
    )

    logger.debug(
        "RAG retrieval mode=hybrid_semantic"
    )

    logger.debug(
        "RAG relevant search results=%s",
        len(search_results),
    )

    if not search_results:
        if allow_general_knowledge:
            return {
                "immediate_answer":
                    None,
                "context":
                    "",
                "conversation_history":
                    conversation_history,
                "candidate_sources":
                    [],
                "mode":
                    "files_and_general",
                "retrieval_mode":
                    "general_fallback",
                "target_document_ids":
                    target_document_ids,
            }

        return {
            "immediate_answer":
                build_no_answer(
                    question
                ),
            "context":
                "",
            "conversation_history":
                conversation_history,
            "candidate_sources":
                [],
            "mode":
                "files_only",
            "retrieval_mode":
                "no_answer",
            "target_document_ids":
                target_document_ids,
        }

    if not has_confident_retrieval(
        search_results
    ):
        if allow_general_knowledge:
            logger.info(
                "RAG weak document match; using general knowledge fallback"
            )

            return {
                "immediate_answer":
                    None,
                "context":
                    "",
                "conversation_history":
                    conversation_history,
                "candidate_sources":
                    [],
                "mode":
                    "files_and_general",
                "retrieval_mode":
                    "general_fallback_low_confidence",
                "target_document_ids":
                    target_document_ids,
            }

        logger.info(
            "RAG weak document match; returning no answer"
        )

        return {
            "immediate_answer":
                build_no_answer(
                    question
                ),
            "context":
                "",
            "conversation_history":
                conversation_history,
            "candidate_sources":
                [],
            "mode":
                "files_only",
            "retrieval_mode":
                "no_answer_low_confidence",
            "target_document_ids":
                target_document_ids,
        }

    raw_context = (
        build_context(
            search_results
        )
    )

    context = (
        build_retrieval_guidance(
            search_results=(
                search_results
            ),
            documents=documents,
        )
        + "\n\n"
        + raw_context
    )

    candidate_sources = (
        build_sources(
            search_results
        )
    )

    return {
        "immediate_answer":
            None,
        "context":
            context,
        "conversation_history":
            conversation_history,
        "candidate_sources":
            candidate_sources,
        "mode":
            (
                "files_and_general"
                if allow_general_knowledge
                else "files_only"
            ),
        "retrieval_mode":
            "hybrid_semantic",
        "target_document_ids":
            target_document_ids,
    }


def answer_question(
    db: Session,
    chat_id: int,
    question: str,
    allow_general_knowledge:
        bool = False,
    document_ids:
        list[int] | None = None,
):
    prepared = (
        prepare_answer_context(
            db=db,
            chat_id=chat_id,
            question=question,
            allow_general_knowledge=(
                allow_general_knowledge
            ),
            document_ids=(
                document_ids
            ),
        )
    )

    immediate_answer = (
        prepared[
            "immediate_answer"
        ]
    )

    if immediate_answer:
        return {
            "answer":
                immediate_answer,
            "sources":
                [],
            "mode":
                prepared[
                    "mode"
                ],
        }

    answer = generate_answer(
        question=question,
        context=(
            prepared[
                "context"
            ]
        ),
        conversation_history=(
            prepared[
                "conversation_history"
            ]
        ),
        allow_general_knowledge=(
            allow_general_knowledge
        ),
    )

    candidate_sources = (
        prepared[
            "candidate_sources"
        ]
    )

    sources = (
        filter_sources_by_citations(
            sources=(
                candidate_sources
            ),
            answer=answer,
        )
    )

    logger.debug(
        "RAG retrieval mode=%s",
        prepared.get("retrieval_mode"),
    )

    logger.debug(
        "RAG candidate sources=%s",
        len(candidate_sources),
    )

    logger.debug(
        "RAG visual assets returned=%s",
        len(sources),
    )

    return {
        "answer":
            answer,
        "sources":
            sources,
        "mode":
            prepared[
                "mode"
            ],
    }