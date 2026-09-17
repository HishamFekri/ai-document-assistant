import logging
from app.services.observability import log_event
from app.services.database_queries import iter_query_batches
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.services.resource_admission import user_operation

from app.services.error_service import log_generation_failure

from app.database.database import (
    SessionLocal,
)

from app.database.models import (
    Chat,
    Document,
    Message,
    chat_documents,
)

from app.services.rag_service import (
    answer_question,
)


def chat_has_failed_documents(
    chat: Chat,
) -> bool:
    return any(
        document.processing_status == "failed"
        for document in chat.documents
    )


def chat_documents_are_ready(
    chat: Chat,
) -> bool:
    if not chat.documents:
        return False

    return all(
        document.processing_status == "ready"
        for document in chat.documents
    )


def mark_message_failed(
    db: Session,
    message: Message,
    error: str,
):
    message.status = "failed"
    message.error = error

    db.commit()


def claim_waiting_message(
    db: Session,
    message_id: int,
) -> bool:
    updated_rows = (
        db.query(Message)
        .filter(
            Message.id == message_id,
            Message.status == "waiting",
        )
        .update(
            {
                Message.status: "processing",
                Message.error: None,
            },
            synchronize_session=False,
        )
    )

    db.commit()

    return updated_rows == 1


def process_waiting_message(
    db: Session,
    message: Message,
):
    if not claim_waiting_message(
        db=db,
        message_id=message.id,
    ):
        return

    message = db.get(
        Message,
        message.id,
    )

    if not message:
        return

    # Keep context available even if rollback expires or deletes the record.
    message_id = message.id
    chat_id = message.chat_id
    try:
        owner_id = db.scalar(select(Chat.user_id).where(Chat.id == chat_id))
        with user_operation(owner_id, "chat"):
            result = answer_question(
                db=db,
                chat_id=message.chat_id,
                question=message.content,
            )

            assistant_message = Message(
                chat_id=message.chat_id,
                role="assistant",
                content=result["answer"],
                status="completed",
                error=None,
                sources=result["sources"],
            )

            db.add(
                assistant_message
            )

            message.status = "completed"
            message.error = None

            db.commit()

            log_event(logging.getLogger(__name__), logging.INFO, "queued_message_completed", message_id=message.id)

    except Exception as error:
        public_error = log_generation_failure(
            error,
            "message",
            chat_id=chat_id,
            message_id=message_id,
        )
        db.rollback()

        message = db.get(
            Message,
            message_id,
        )

        if message:
            message.status = "failed"
            message.error = public_error

            db.commit()


def process_waiting_messages_for_document(document_id: int):
    db = SessionLocal()
    try:
        query = (db.query(Message.id, Message.chat_id, Message.created_at)
                 .join(Chat, Chat.id == Message.chat_id)
                 .filter(Chat.documents.any(id=document_id), Message.role == 'user', Message.status == 'waiting'))
        for batch in iter_query_batches(query, [Message.chat_id, Message.created_at, Message.id]):
            # Project readiness once per batch. Plain values survive processing
            # commits without reloading expired ORM attachment relationships.
            readiness = {}
            statuses = (db.query(chat_documents.c.chat_id, Document.processing_status)
                        .join(Document, Document.id == chat_documents.c.document_id)
                        .filter(chat_documents.c.chat_id.in_({row.chat_id for row in batch})).all())
            for chat_id, status in statuses:
                failed, ready = readiness.get(chat_id, (False, True))
                readiness[chat_id] = (failed or status == 'failed', ready and status == 'ready')
            for row in batch:
                failed, ready = readiness.get(row.chat_id, (False, False))
                if not failed and not ready:
                    continue
                message = db.get(Message, row.id)
                if message is None or message.status != 'waiting':
                    continue
                if failed:
                    mark_message_failed(db=db, message=message, error='One or more documents failed to process.')
                elif ready:
                    process_waiting_message(db=db, message=message)
    finally:
        db.close()
