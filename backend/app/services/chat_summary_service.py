from sqlalchemy.orm import Session

from app.database.models import (
    Document,
)

from app.services.summaries.summary_assistant_service import (
    create_summary_assistant_message,
)

from app.services.summaries.summary_generation_service import (
    generate_summary_for_record,
)

from app.services.summaries.summary_service import (
    create_summary_record,
)


def save_summary_instruction(
    db: Session,
    chat_id: int,
    document_id: int,
    content: str,
):
    return (
        create_summary_assistant_message(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
            role="user",
            content=content,
        )
    )


def generate_summary_from_chat(
    db: Session,
    chat_id: int,
    document: Document,
    instruction: str,
):
    save_summary_instruction(
        db=db,
        chat_id=chat_id,
        document_id=document.id,
        content=instruction,
    )

    summary = (
        create_summary_record(
            db=db,
            chat_id=chat_id,
            document_id=document.id,
        )
    )

    return (
        generate_summary_for_record(
            db=db,
            document=document,
            summary=summary,
        )
    )