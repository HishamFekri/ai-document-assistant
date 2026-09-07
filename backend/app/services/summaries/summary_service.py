from typing import Literal

from sqlalchemy import delete, func, or_, select, Text, update
from sqlalchemy.orm import Session

from app.database.summary_models import (
    DocumentSummary,
)


from app.services.error_service import public_generation_error
from app.services.summaries.summary_claim import (
    cleanup_is_safe,
    lock_summary_context,
    require_generation_owner,
    SummaryGenerationBusy,
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


def _context(chat_id, document_id, mode):
    return (
        DocumentSummary.chat_id == chat_id,
        DocumentSummary.document_id == document_id,
        DocumentSummary.mode == validate_summary_mode(mode),
    )


def _finish(db, summary_id):
    """Return a current detached snapshot without leaving a refresh transaction."""
    summary = db.get(DocumentSummary, summary_id, populate_existing=True)
    if summary is not None:
        db.expunge(summary)
    db.commit()
    return summary


def get_next_summary_version(db, chat_id, document_id, mode):
    # The lock remains held until the INSERT commits. The existing unique
    # constraint is an additional backstop; allocation never runs unlocked.
    lock_summary_context(db, chat_id, document_id, mode)
    current = db.scalar(select(func.max(DocumentSummary.version)).where(
        *_context(chat_id, document_id, mode)
    ))
    return int(current or 0) + 1


def cleanup_old_summaries(db, chat_id, document_id, mode, keep_summary_id):
    lock_summary_context(db, chat_id, document_id, mode)
    if not cleanup_is_safe(db, chat_id, document_id, mode):
        return
    keep = db.scalar(select(DocumentSummary).where(
        DocumentSummary.id == keep_summary_id,
        *_context(chat_id, document_id, mode),
        DocumentSummary.status.in_(("completed", "cancelled")),
    ).execution_options(populate_existing=True))
    if keep is None:
        return
    db.execute(delete(DocumentSummary).where(
        *_context(chat_id, document_id, mode),
        DocumentSummary.version < keep.version,
        DocumentSummary.status.in_(("completed", "failed", "cancelled")),
        DocumentSummary.is_selected.is_(False),
    ).execution_options(synchronize_session=False))


def create_summary_record(db, chat_id, document_id, mode="summary"):
    require_generation_owner(db, chat_id, document_id, mode)
    lock_summary_context(db, chat_id, document_id, mode)
    active = db.scalar(select(DocumentSummary.id).where(
        *_context(chat_id, document_id, mode),
        DocumentSummary.status.in_(("pending", "generating")),
    ).limit(1))
    if active is not None:
        # A crashed request is recovered by the existing cancel + regenerate
        # flow. Never silently restart a provider request of uncertain outcome.
        db.rollback()
        raise SummaryGenerationBusy()
    summary = DocumentSummary(
        chat_id=chat_id, document_id=document_id, mode=mode,
        version=get_next_summary_version(db, chat_id, document_id, mode),
        status="pending", content=None, is_selected=False, error=None,
    )
    db.add(summary)
    db.flush()
    return _finish(db, summary.id)


def _lock_record_context(db, summary):
    lock_summary_context(db, summary.chat_id, summary.document_id, summary.mode)
    return _context(summary.chat_id, summary.document_id, summary.mode)


def mark_summary_generating(db, summary):
    require_generation_owner(db, summary.chat_id, summary.document_id, summary.mode)
    context = _lock_record_context(db, summary)
    changed = db.execute(update(DocumentSummary).where(
        DocumentSummary.id == summary.id, *context,
        DocumentSummary.status == "pending",
    ).values(status="generating", error=None, is_selected=False)
      .execution_options(synchronize_session=False)).rowcount
    db.commit()
    return changed == 1


def _select_only(db, summary):
    db.execute(update(DocumentSummary).where(
        *_context(summary.chat_id, summary.document_id, summary.mode),
    ).values(is_selected=False).execution_options(synchronize_session=False))
    db.execute(update(DocumentSummary).where(
        DocumentSummary.id == summary.id,
    ).values(is_selected=True).execution_options(synchronize_session=False))


def mark_summary_completed(db, summary, content):
    require_generation_owner(db, summary.chat_id, summary.document_id, summary.mode)
    context = _lock_record_context(db, summary)
    changed = db.execute(update(DocumentSummary).where(
        DocumentSummary.id == summary.id, *context,
        DocumentSummary.status == "generating",
    ).values(status="completed", content=content, error=None)
      .execution_options(synchronize_session=False)).rowcount
    if changed == 1:
        newer = db.scalar(select(DocumentSummary.id).where(
            *context, DocumentSummary.version > summary.version,
            DocumentSummary.status.in_(("completed", "cancelled")),
        ).limit(1))
        if newer is None:
            _select_only(db, summary)
            cleanup_old_summaries(
                db, summary.chat_id, summary.document_id, summary.mode, summary.id,
            )
    return _finish(db, summary.id)


def mark_summary_failed(db, summary, error):
    context = _lock_record_context(db, summary)
    db.execute(update(DocumentSummary).where(
        DocumentSummary.id == summary.id, *context,
        DocumentSummary.status.in_(("pending", "generating")),
    ).values(status="failed", error=public_generation_error(error, "summary"),
             is_selected=False).execution_options(synchronize_session=False))
    return _finish(db, summary.id)


def mark_summary_cancelled(db, summary, content=None):
    context = _lock_record_context(db, summary)
    values = dict(status="cancelled", error=None, is_selected=False)
    if content is not None:
        values["content"] = content
    changed = db.execute(update(DocumentSummary).where(
        DocumentSummary.id == summary.id, *context,
        DocumentSummary.status.in_(("pending", "generating")),
    ).values(**values).execution_options(synchronize_session=False)).rowcount
    if changed == 1:
        newer = db.scalar(select(DocumentSummary.id).where(
            *context, DocumentSummary.version > summary.version,
            DocumentSummary.status.in_(("completed", "cancelled")),
        ).limit(1))
        if newer is None:
            _select_only(db, summary)
    elif content is not None:
        # Only the still-owning stream may finish saving its cancelled partial.
        # Never reselect it, replace an existing partial, or accept a final result.
        require_generation_owner(db, summary.chat_id, summary.document_id, summary.mode)
        db.execute(update(DocumentSummary).where(
            DocumentSummary.id == summary.id, *context,
            DocumentSummary.status == "cancelled",
            or_(DocumentSummary.content.is_(None),
                DocumentSummary.content.cast(Text) == "null"),
        ).values(content=content).execution_options(synchronize_session=False))
    return _finish(db, summary.id)


def select_summary(db, chat_id, document_id, summary_id):
    summary = db.scalar(select(DocumentSummary).where(
        DocumentSummary.id == summary_id,
        DocumentSummary.chat_id == chat_id,
        DocumentSummary.document_id == document_id,
    ))
    if summary is None:
        db.rollback()
        return None
    context = _lock_record_context(db, summary)
    summary = db.scalar(select(DocumentSummary).where(
        DocumentSummary.id == summary_id, *context,
        DocumentSummary.status == "completed",
    ).execution_options(populate_existing=True))
    if summary is None:
        db.rollback()
        return None
    _select_only(db, summary)
    cleanup_old_summaries(db, chat_id, document_id, summary.mode, summary_id)
    return _finish(db, summary_id)


def delete_summary(db, summary):
    context = _lock_record_context(db, summary)
    # Deletion is explicit user intent. A later worker update cannot recreate it.
    db.execute(delete(DocumentSummary).where(
        DocumentSummary.id == summary.id, *context,
    ).execution_options(synchronize_session=False))
    db.commit()
