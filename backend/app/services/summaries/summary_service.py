from typing import Literal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.summary_models import (
    DocumentSummary,
)


SummaryMode = Literal[
    "summary",
    "transcription",
]


def validate_summary_mode(
    mode: str,
) -> SummaryMode:
    if mode not in {
        "summary",
        "transcription",
    }:
        raise ValueError(
            "Invalid summary mode"
        )

    return mode


def get_document_summaries(
    db: Session,
    chat_id: int,
    document_id: int,
    mode: SummaryMode | None = None,
) -> list[DocumentSummary]:
    query = (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.chat_id
            == chat_id,
            DocumentSummary.document_id
            == document_id,
        )
    )

    if mode is not None:
        validated_mode = (
            validate_summary_mode(
                mode
            )
        )

        query = query.filter(
            DocumentSummary.mode
            == validated_mode
        )

    return (
        query
        .order_by(
            DocumentSummary.is_selected.desc(),
            DocumentSummary.created_at.desc(),
            DocumentSummary.id.desc(),
        )
        .all()
    )


def get_selected_summary(
    db: Session,
    chat_id: int,
    document_id: int,
    mode: SummaryMode = "summary",
) -> DocumentSummary | None:
    validated_mode = (
        validate_summary_mode(
            mode
        )
    )

    visible_statuses = (
        "completed",
        "cancelled",
    )

    summary = (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.chat_id
            == chat_id,
            DocumentSummary.document_id
            == document_id,
            DocumentSummary.mode
            == validated_mode,
            DocumentSummary.is_selected
            .is_(True),
            DocumentSummary.status
            .in_(
                visible_statuses
            ),
        )
        .order_by(
            DocumentSummary.created_at.desc(),
            DocumentSummary.id.desc(),
        )
        .first()
    )

    if summary is not None:
        return summary

    return (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.chat_id
            == chat_id,
            DocumentSummary.document_id
            == document_id,
            DocumentSummary.mode
            == validated_mode,
            DocumentSummary.status
            .in_(
                visible_statuses
            ),
        )
        .order_by(
            DocumentSummary.created_at.desc(),
            DocumentSummary.id.desc(),
        )
        .first()
    )


def get_summary_by_id(
    db: Session,
    summary_id: int,
) -> DocumentSummary | None:
    return (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.id
            == summary_id
        )
        .first()
    )


def get_next_summary_version(
    db: Session,
    chat_id: int,
    document_id: int,
    mode: SummaryMode,
) -> int:
    validated_mode = (
        validate_summary_mode(
            mode
        )
    )

    current_max = (
        db.query(
            func.max(
                DocumentSummary.version
            )
        )
        .filter(
            DocumentSummary.chat_id
            == chat_id,
            DocumentSummary.document_id
            == document_id,
            DocumentSummary.mode
            == validated_mode,
        )
        .scalar()
    )

    return int(
        current_max or 0
    ) + 1


def cleanup_old_summaries(
    db: Session,
    chat_id: int,
    document_id: int,
    mode: SummaryMode,
    keep_summary_id: int,
) -> None:
    validated_mode = (
        validate_summary_mode(
            mode
        )
    )

    old_summaries = (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.chat_id
            == chat_id,
            DocumentSummary.document_id
            == document_id,
            DocumentSummary.mode
            == validated_mode,
            DocumentSummary.id
            != keep_summary_id,
        )
        .all()
    )

    for old_summary in old_summaries:
        db.delete(
            old_summary
        )


def create_summary_record(
    db: Session,
    chat_id: int,
    document_id: int,
    mode: SummaryMode = "summary",
) -> DocumentSummary:
    validated_mode = (
        validate_summary_mode(
            mode
        )
    )

    next_version = (
        get_next_summary_version(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
            mode=validated_mode,
        )
    )

    summary = DocumentSummary(
        chat_id=chat_id,
        document_id=document_id,
        mode=validated_mode,
        version=next_version,
        status="pending",
        content=None,
        is_selected=False,
        error=None,
    )

    db.add(
        summary
    )

    db.commit()

    db.refresh(
        summary
    )

    return summary


def mark_summary_generating(
    db: Session,
    summary: DocumentSummary,
) -> DocumentSummary:
    summary.status = (
        "generating"
    )

    summary.error = None

    summary.is_selected = False

    db.commit()

    db.refresh(
        summary
    )

    return summary


def mark_summary_completed(
    db: Session,
    summary: DocumentSummary,
    content: dict,
) -> DocumentSummary:
    validated_mode = (
        validate_summary_mode(
            summary.mode
        )
    )

    db.refresh(
        summary
    )

    if (
        summary.status
        == "cancelled"
    ):
        return summary

    summary.status = (
        "completed"
    )

    summary.content = content

    summary.error = None

    summary.is_selected = True

    (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.chat_id
            == summary.chat_id,
            DocumentSummary.document_id
            == summary.document_id,
            DocumentSummary.mode
            == validated_mode,
            DocumentSummary.id
            != summary.id,
        )
        .update(
            {
                DocumentSummary
                .is_selected:
                    False
            },
            synchronize_session=False,
        )
    )

    cleanup_old_summaries(
        db=db,
        chat_id=summary.chat_id,
        document_id=(
            summary.document_id
        ),
        mode=validated_mode,
        keep_summary_id=summary.id,
    )

    db.commit()

    db.refresh(
        summary
    )

    return summary


def mark_summary_failed(
    db: Session,
    summary: DocumentSummary,
    error: str,
) -> DocumentSummary:
    db.refresh(
        summary
    )

    if (
        summary.status
        == "cancelled"
    ):
        return summary

    summary.status = (
        "failed"
    )

    summary.error = error

    summary.is_selected = False

    db.commit()

    db.refresh(
        summary
    )

    return summary


def mark_summary_cancelled(
    db: Session,
    summary: DocumentSummary,
    content: dict | None = None,
) -> DocumentSummary:
    validated_mode = (
        validate_summary_mode(
            summary.mode
        )
    )

    summary.status = (
        "cancelled"
    )

    if content is not None:
        summary.content = content

    summary.error = None

    summary.is_selected = True

    (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.chat_id
            == summary.chat_id,
            DocumentSummary.document_id
            == summary.document_id,
            DocumentSummary.mode
            == validated_mode,
            DocumentSummary.id
            != summary.id,
        )
        .update(
            {
                DocumentSummary
                .is_selected:
                    False
            },
            synchronize_session=False,
        )
    )

    db.commit()

    db.refresh(
        summary
    )

    return summary


def select_summary(
    db: Session,
    chat_id: int,
    document_id: int,
    summary_id: int,
) -> DocumentSummary | None:
    summary = (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.id
            == summary_id,
            DocumentSummary.chat_id
            == chat_id,
            DocumentSummary.document_id
            == document_id,
            DocumentSummary.status
            == "completed",
        )
        .first()
    )

    if summary is None:
        return None

    validated_mode = (
        validate_summary_mode(
            summary.mode
        )
    )

    (
        db.query(
            DocumentSummary
        )
        .filter(
            DocumentSummary.chat_id
            == chat_id,
            DocumentSummary.document_id
            == document_id,
            DocumentSummary.mode
            == validated_mode,
        )
        .update(
            {
                DocumentSummary
                .is_selected:
                    False
            },
            synchronize_session=False,
        )
    )

    summary.is_selected = True

    cleanup_old_summaries(
        db=db,
        chat_id=chat_id,
        document_id=document_id,
        mode=validated_mode,
        keep_summary_id=(
            summary.id
        ),
    )

    db.commit()

    db.refresh(
        summary
    )

    return summary


def delete_summary(
    db: Session,
    summary: DocumentSummary,
) -> None:
    db.delete(
        summary
    )

    db.commit()