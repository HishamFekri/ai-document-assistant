from sqlalchemy.orm import Session

from app.database.database import (
    SessionLocal,
)

from app.database.models import (
    Chat,
    Message,
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

    try:
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

        print(
            "[QUEUE] Message "
            f"{message.id} completed"
        )

    except Exception as error:
        db.rollback()

        message = db.get(
            Message,
            message.id,
        )

        if message:
            message.status = "failed"
            message.error = str(
                error
            )

            db.commit()

        print(
            "[QUEUE] Message "
            f"{message.id if message else 'unknown'} "
            f"failed: {error}"
        )


def process_waiting_messages_for_document(
    document_id: int,
):
    db = SessionLocal()

    try:
        chats = (
            db.query(Chat)
            .filter(
                Chat.documents.any(
                    id=document_id
                )
            )
            .all()
        )

        for chat in chats:
            waiting_messages = (
                db.query(Message)
                .filter(
                    Message.chat_id == chat.id,
                    Message.role == "user",
                    Message.status == "waiting",
                )
                .order_by(
                    Message.created_at
                )
                .all()
            )

            if not waiting_messages:
                continue

            if chat_has_failed_documents(
                chat
            ):
                for message in waiting_messages:
                    mark_message_failed(
                        db=db,
                        message=message,
                        error=(
                            "One or more documents "
                            "failed to process."
                        ),
                    )

                continue

            if not chat_documents_are_ready(
                chat
            ):
                continue

            for message in waiting_messages:
                process_waiting_message(
                    db=db,
                    message=message,
                )

    finally:
        db.close()