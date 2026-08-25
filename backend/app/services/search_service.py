import logging
import os
import re
from collections import defaultdict

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.database.models import (
    DocumentChunk,
)

from app.services.embedding_service import (
    create_query_embedding,
)


load_dotenv()


logger = logging.getLogger(__name__)


RAG_MIN_SIMILARITY = float(
    os.getenv(
        "RAG_MIN_SIMILARITY",
        "0.45",
    )
)

RAG_MAX_CHUNKS = int(
    os.getenv(
        "RAG_MAX_CHUNKS",
        "8",
    )
)

RAG_CANDIDATE_MULTIPLIER = int(
    os.getenv(
        "RAG_CANDIDATE_MULTIPLIER",
        "5",
    )
)

RAG_MAX_SCORE_DROP = float(
    os.getenv(
        "RAG_MAX_SCORE_DROP",
        "0.20",
    )
)

RAG_MAX_CHUNKS_PER_DOCUMENT = int(
    os.getenv(
        "RAG_MAX_CHUNKS_PER_DOCUMENT",
        "6",
    )
)

RAG_MAX_CHUNKS_PER_LOCATION = int(
    os.getenv(
        "RAG_MAX_CHUNKS_PER_LOCATION",
        "3",
    )
)

RAG_PAGE_MAX_CHUNKS = int(
    os.getenv(
        "RAG_PAGE_MAX_CHUNKS",
        "24",
    )
)

RAG_VISUAL_MAX_CHUNKS = int(
    os.getenv(
        "RAG_VISUAL_MAX_CHUNKS",
        "8",
    )
)

RAG_COMPANION_MAX_CHUNKS = int(
    os.getenv(
        "RAG_COMPANION_MAX_CHUNKS",
        "10",
    )
)

RAG_COMPANION_PAGE_RADIUS = int(
    os.getenv(
        "RAG_COMPANION_PAGE_RADIUS",
        "1",
    )
)


GENERIC_CONTENT_TYPES = [
    "text",
    "table",
    "equation",
]


def get_chunk_page(
    chunk: DocumentChunk,
) -> int | None:
    metadata = (
        chunk.chunk_metadata
        or {}
    )

    raw_page = metadata.get(
        "page"
    )

    if raw_page is not None:
        try:
            page = int(
                raw_page
            )

            if page > 0:
                return page

        except (
            TypeError,
            ValueError,
        ):
            pass

    location = (
        chunk.location
        or ""
    )

    match = re.search(
        r"\bpage\s+(\d+)\b",
        location,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    return int(
        match.group(1)
    )


def normalize_chunk_text(
    content: str | None,
) -> str:
    if not content:
        return ""

    return (
        re.sub(
            r"\s+",
            " ",
            content,
        )
        .strip()
        .lower()
    )


def normalize_query_text(
    query: str,
) -> str:
    return (
        re.sub(
            r"\s+",
            " ",
            query,
        )
        .strip()
        .lower()
    )


def chunk_signature(
    chunk: DocumentChunk,
) -> str:
    normalized = (
        normalize_chunk_text(
            chunk.content
        )
    )

    if not normalized:
        return (
            f"chunk:{chunk.id}"
        )

    return normalized[:700]


def location_key(
    chunk: DocumentChunk,
) -> str:
    if chunk.location:
        return (
            str(
                chunk.location
            )
            .strip()
            .lower()
        )

    page = get_chunk_page(
        chunk
    )

    if page is not None:
        return (
            f"page:{page}"
        )

    return (
        f"chunk:{chunk.id}"
    )


def lexical_bonus(
    query: str,
    chunk: DocumentChunk,
) -> float:
    normalized_query = (
        normalize_query_text(
            query
        )
    )

    normalized_content = (
        normalize_chunk_text(
            chunk.content
        )
    )

    if (
        not normalized_query
        or not normalized_content
    ):
        return 0.0

    if (
        normalized_query
        in normalized_content
    ):
        return 0.18

    query_tokens = {
        token
        for token in re.findall(
            r"[\w.-]+",
            normalized_query,
            flags=re.UNICODE,
        )
        if len(token) >= 3
    }

    if not query_tokens:
        return 0.0

    content_tokens = set(
        re.findall(
            r"[\w.-]+",
            normalized_content,
            flags=re.UNICODE,
        )
    )

    overlap = (
        len(
            query_tokens
            & content_tokens
        )
        / len(
            query_tokens
        )
    )

    if overlap >= 0.8:
        return 0.12

    if overlap >= 0.6:
        return 0.08

    if overlap >= 0.4:
        return 0.04

    return 0.0


def search_chunks_by_page(
    db: Session,
    document_ids: list[int],
    page_number: int,
    content_types:
        list[str] | None = None,
    limit: int | None = None,
):
    if not document_ids:
        return []

    if page_number <= 0:
        return []

    if limit is None:
        limit = RAG_PAGE_MAX_CHUNKS

    query = (
        db.query(
            DocumentChunk
        )
        .filter(
            DocumentChunk.document_id.in_(
                document_ids
            )
        )
    )

    if content_types:
        query = query.filter(
            DocumentChunk.content_type.in_(
                content_types
            )
        )

    chunks = (
        query
        .order_by(
            DocumentChunk.document_id,
            DocumentChunk.id,
        )
        .all()
    )

    results = []
    seen_signatures = set()

    for chunk in chunks:
        chunk_page = (
            get_chunk_page(
                chunk
            )
        )

        if (
            chunk_page
            != page_number
        ):
            continue

        signature = (
            chunk_signature(
                chunk
            )
        )

        if (
            signature
            in seen_signatures
        ):
            continue

        seen_signatures.add(
            signature
        )

        results.append(
            {
                "chunk": chunk,
                "similarity": 1.0,
                "match_type":
                    "exact_page",
            }
        )

        if (
            len(results)
            >= limit
        ):
            break

    logger.debug(
        "Exact page search page=%s chunks=%s",
        page_number,
        len(results),
    )

    return results


def get_companion_chunks(
    db: Session,
    anchor_results,
    document_ids: list[int],
    limit: int,
):
    if (
        not anchor_results
        or limit <= 0
    ):
        return []

    anchor_pages = []

    for result in anchor_results[:3]:
        chunk = result["chunk"]

        page = get_chunk_page(
            chunk
        )

        if page is None:
            continue

        anchor_pages.append(
            (
                chunk.document_id,
                page,
                result["similarity"],
            )
        )

    if not anchor_pages:
        return []

    wanted_pages = set()

    for (
        document_id,
        page,
        _,
    ) in anchor_pages:
        for offset in range(
            -RAG_COMPANION_PAGE_RADIUS,
            RAG_COMPANION_PAGE_RADIUS + 1,
        ):
            candidate_page = (
                page
                + offset
            )

            if candidate_page <= 0:
                continue

            wanted_pages.add(
                (
                    document_id,
                    candidate_page,
                )
            )

    chunks = (
        db.query(
            DocumentChunk
        )
        .filter(
            DocumentChunk.document_id.in_(
                document_ids
            )
        )
        .filter(
            DocumentChunk.content_type.in_(
                GENERIC_CONTENT_TYPES
            )
        )
        .order_by(
            DocumentChunk.document_id,
            DocumentChunk.id,
        )
        .all()
    )

    companions = []
    seen_signatures = set()

    anchor_similarity_by_document = (
        defaultdict(float)
    )

    for (
        document_id,
        _,
        similarity,
    ) in anchor_pages:
        anchor_similarity_by_document[
            document_id
        ] = max(
            anchor_similarity_by_document[
                document_id
            ],
            similarity,
        )

    for chunk in chunks:
        page = get_chunk_page(
            chunk
        )

        if page is None:
            continue

        if (
            chunk.document_id,
            page,
        ) not in wanted_pages:
            continue

        signature = (
            chunk_signature(
                chunk
            )
        )

        if (
            signature
            in seen_signatures
        ):
            continue

        seen_signatures.add(
            signature
        )

        base_similarity = (
            anchor_similarity_by_document[
                chunk.document_id
            ]
        )

        companions.append(
            {
                "chunk": chunk,
                "similarity": max(
                    base_similarity
                    - 0.03,
                    0.0,
                ),
                "match_type":
                    "semantic_companion",
            }
        )

    companions.sort(
        key=lambda result: (
            0
            if (
                result["chunk"]
                .content_type
                == "table"
            )
            else (
                1
                if (
                    result["chunk"]
                    .content_type
                    == "equation"
                )
                else 2
            ),
            abs(
                (
                    get_chunk_page(
                        result["chunk"]
                    )
                    or 0
                )
                - min(
                    (
                        page
                        for (
                            document_id,
                            page,
                            _,
                        )
                        in anchor_pages
                        if (
                            document_id
                            == result["chunk"]
                            .document_id
                        )
                    ),
                    default=0,
                )
            ),
            result["chunk"].id,
        )
    )

    return companions[:limit]


def search_similar_chunks(
    db: Session,
    query: str,
    document_ids: list[int],
    limit: int | None = None,
    min_similarity:
        float | None = None,
    content_types:
        list[str] | None = None,
):
    if (
        not query
        or not query.strip()
    ):
        raise ValueError(
            "Query cannot be empty"
        )

    if not document_ids:
        return []

    if limit is None:
        limit = RAG_MAX_CHUNKS

    if min_similarity is None:
        min_similarity = (
            RAG_MIN_SIMILARITY
        )

    effective_content_types = (
        content_types
        if content_types
        else GENERIC_CONTENT_TYPES
    )

    candidate_limit = max(
        limit,
        limit
        * RAG_CANDIDATE_MULTIPLIER,
    )

    query_embedding = (
        create_query_embedding(
            query
        )
    )

    distance = (
        DocumentChunk
        .embedding
        .cosine_distance(
            query_embedding
        )
    )

    db_query = (
        db.query(
            DocumentChunk,
            distance.label(
                "distance"
            ),
        )
        .filter(
            DocumentChunk.document_id.in_(
                document_ids
            )
        )
        .filter(
            DocumentChunk.embedding.isnot(
                None
            )
        )
        .filter(
            DocumentChunk.content_type.in_(
                effective_content_types
            )
        )
    )

    rows = (
        db_query
        .order_by(
            distance
        )
        .limit(
            candidate_limit
        )
        .all()
    )

    candidates = []

    for (
        chunk,
        distance_value,
    ) in rows:
        similarity = (
            1
            - float(
                distance_value
            )
        )

        bonus = lexical_bonus(
            query=query,
            chunk=chunk,
        )

        ranking_score = (
            similarity
            + bonus
        )

        logger.debug(
            (
                "Search candidate chunk=%s document=%s "
                "type=%s location=%s similarity=%.4f "
                "lexical_bonus=%.4f score=%.4f"
            ),
            chunk.id,
            chunk.document_id,
            chunk.content_type,
            chunk.location,
            similarity,
            bonus,
            ranking_score,
        )

        if (
            similarity
            < min_similarity
            and bonus <= 0
        ):
            continue

        candidates.append(
            {
                "chunk": chunk,
                "similarity":
                    similarity,
                "ranking_score":
                    ranking_score,
                "match_type":
                    (
                        "hybrid"
                        if bonus > 0
                        else "semantic"
                    ),
            }
        )

    if not candidates:
        logger.debug(
            "No relevant chunks found candidates=%s threshold=%.2f",
            len(rows),
            min_similarity,
        )

        return []

    candidates.sort(
        key=lambda result:
            result[
                "ranking_score"
            ],
        reverse=True,
    )

    best_score = (
        candidates[0][
            "ranking_score"
        ]
    )

    dynamic_threshold = max(
        min_similarity,
        best_score
        - RAG_MAX_SCORE_DROP,
    )

    filtered_candidates = [
        result
        for result
        in candidates
        if (
            result[
                "ranking_score"
            ]
            >= dynamic_threshold
        )
    ]

    selected = []
    seen_signatures = set()

    per_document_count = (
        defaultdict(int)
    )

    per_location_count = (
        defaultdict(int)
    )

    multiple_documents = (
        len(
            set(
                document_ids
            )
        )
        > 1
    )

    for result in (
        filtered_candidates
    ):
        chunk = (
            result[
                "chunk"
            ]
        )

        signature = (
            chunk_signature(
                chunk
            )
        )

        if (
            signature
            in seen_signatures
        ):
            continue

        document_id = (
            chunk.document_id
        )

        location = (
            location_key(
                chunk
            )
        )

        if (
            multiple_documents
            and per_document_count[
                document_id
            ]
            >= RAG_MAX_CHUNKS_PER_DOCUMENT
        ):
            continue

        if (
            per_location_count[
                (
                    document_id,
                    location,
                )
            ]
            >= RAG_MAX_CHUNKS_PER_LOCATION
        ):
            continue

        seen_signatures.add(
            signature
        )

        per_document_count[
            document_id
        ] += 1

        per_location_count[
            (
                document_id,
                location,
            )
        ] += 1

        selected.append(
            result
        )

        if (
            len(selected)
            >= limit
        ):
            break

    companion_limit = max(
        0,
        RAG_COMPANION_MAX_CHUNKS,
    )

    companions = (
        get_companion_chunks(
            db=db,
            anchor_results=selected,
            document_ids=document_ids,
            limit=companion_limit,
        )
    )

    merged = []
    merged_signatures = set()

    for result in (
        selected
        + companions
    ):
        chunk = (
            result[
                "chunk"
            ]
        )

        signature = (
            chunk_signature(
                chunk
            )
        )

        if (
            signature
            in merged_signatures
        ):
            continue

        merged_signatures.add(
            signature
        )

        merged.append(
            result
        )

    logger.debug(
        (
            "Search selection anchors=%s companions=%s "
            "total_context=%s"
        ),
        len(selected),
        len(merged) - len(selected),
        len(merged),
    )

    return merged


def search_visual_chunks(
    db: Session,
    query: str,
    document_ids: list[int],
    limit: int | None = None,
    min_similarity:
        float | None = None,
):
    if limit is None:
        limit = (
            RAG_VISUAL_MAX_CHUNKS
        )

    return search_similar_chunks(
        db=db,
        query=query,
        document_ids=document_ids,
        limit=limit,
        min_similarity=(
            min_similarity
        ),
        content_types=[
            "image",
        ],
    )