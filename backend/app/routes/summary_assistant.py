from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from sqlalchemy.orm import Session

from app.database.database import (
    get_db,
)

from app.database.models import (
    Chat,
    Document,
    User,
)

from app.routes.auth import (
    get_current_user,
)

from app.schemas.summary_assistant_schemas import (
    SummaryAssistantChatResponse,
    SummaryAssistantMessageCreate,
    SummaryAssistantReplyResponse,
)

from app.services.summaries.summary_assistant_service import (
    clear_summary_assistant_messages,
    get_summary_assistant_messages,
    send_summary_assistant_message,
)


router = APIRouter(
    prefix="/documents",
    tags=["Summary Assistant"],
)


def get_owned_chat(
    chat_id: int,
    current_user: User,
    db: Session,
) -> Chat:
    chat = (
        db.query(
            Chat
        )
        .filter(
            Chat.id
            == chat_id,
            Chat.user_id
            == current_user.id,
        )
        .first()
    )

    if chat is None:
        raise HTTPException(
            status_code=404,
            detail="Chat not found",
        )

    return chat


def get_owned_document(
    document_id: int,
    current_user: User,
    db: Session,
) -> Document:
    document = (
        db.query(
            Document
        )
        .filter(
            Document.id
            == document_id,
            Document.user_id
            == current_user.id,
        )
        .first()
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return document


def get_chat_document(
    chat_id: int,
    document_id: int,
    current_user: User,
    db: Session,
) -> tuple[
    Chat,
    Document,
]:
    chat = get_owned_chat(
        chat_id=chat_id,
        current_user=current_user,
        db=db,
    )

    document = get_owned_document(
        document_id=document_id,
        current_user=current_user,
        db=db,
    )

    document_in_chat = next(
        (
            existing_document
            for existing_document
            in chat.documents
            if existing_document.id
            == document_id
        ),
        None,
    )

    if document_in_chat is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Document is not attached "
                "to this chat"
            ),
        )

    return (
        chat,
        document,
    )


@router.get(
    "/{document_id}/summary-assistant/messages",
    response_model=SummaryAssistantChatResponse,
)
def read_summary_assistant_messages(
    document_id: int,
    chat_id: int,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    get_chat_document(
        chat_id=chat_id,
        document_id=document_id,
        current_user=current_user,
        db=db,
    )

    messages = (
        get_summary_assistant_messages(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
            limit=limit,
            offset=offset,
        )
    )

    return {
        "messages":
            messages
    }


@router.post(
    "/{document_id}/summary-assistant/messages",
    response_model=SummaryAssistantReplyResponse,
)
def create_summary_assistant_message(
    document_id: int,
    data: SummaryAssistantMessageCreate,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    _, document = get_chat_document(
        chat_id=data.chat_id,
        document_id=document_id,
        current_user=current_user,
        db=db,
    )

    if (
        document.processing_status
        != "ready"
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Document must be ready "
                "before using the summary assistant"
            ),
        )

    try:
        (
            user_message,
            assistant_message,
            _detected_action,
        ) = (
            send_summary_assistant_message(
                db=db,
                chat_id=data.chat_id,
                document_id=document_id,
                content=data.content,
            )
        )

        # The Summary Assistant endpoint now stores/updates
        # instructions only.
        #
        # It must never generate a summary by itself.
        # Actual generation is triggered explicitly from the
        # Summary UI's Generate / Regenerate button after this
        # request completes.

        return {
            "user_message":
                user_message,

            "assistant_message":
                assistant_message,

            "action":
                "update_preferences",

            "generated_summary":
                None,
        }

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(
                error
            ),
        )

    except Exception as error:
        print(
            "[SUMMARY ASSISTANT ERROR]",
            error,
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Could not process "
                "summary instructions"
            ),
        )


@router.delete(
    "/{document_id}/summary-assistant/messages",
)
def reset_summary_assistant(
    document_id: int,
    chat_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    get_chat_document(
        chat_id=chat_id,
        document_id=document_id,
        current_user=current_user,
        db=db,
    )

    deleted_count = (
        clear_summary_assistant_messages(
            db=db,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    return {
        "message":
            "Summary assistant reset to default",

        "deleted_messages":
            deleted_count,
    }