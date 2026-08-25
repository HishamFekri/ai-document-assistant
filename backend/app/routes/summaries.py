import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from fastapi.responses import (
    StreamingResponse,
)

from sqlalchemy.orm import Session

from app.database.database import (
    SessionLocal,
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

from app.schemas.summary_schemas import (
    DocumentSummaryResponse,
    SummaryGenerateRequest,
)

from app.services.summaries.summary_generation_service import (
    generate_summary_for_record,
    stream_summary_content,
)

from app.services.summaries.summary_service import (
    SummaryMode,
    create_summary_record,
    delete_summary,
    get_document_summaries,
    get_selected_summary,
    get_summary_by_id,
    mark_summary_cancelled,
    mark_summary_completed,
    mark_summary_failed,
    mark_summary_generating,
    select_summary,
)


router = APIRouter(
    prefix="/documents",
    tags=["Summaries"],
)


def get_owned_document(
    document_id: int,
    current_user: User,
    db: Session,
) -> Document:
    document = (
        db.query(Document)
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


def get_owned_chat(
    chat_id: int,
    current_user: User,
    db: Session,
) -> Chat:
    chat = (
        db.query(Chat)
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
            item
            for item in chat.documents
            if item.id
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


def ensure_summary_belongs_to_context(
    summary,
    chat_id: int,
    document_id: int,
):
    if (
        summary is None
        or summary.chat_id
        != chat_id
        or summary.document_id
        != document_id
    ):
        raise HTTPException(
            status_code=404,
            detail="Summary not found",
        )

    return summary


def summary_was_cancelled(
    db: Session,
    summary_id: int,
) -> bool:
    summary = (
        get_summary_by_id(
            db=db,
            summary_id=summary_id,
        )
    )

    if summary is None:
        return True

    db.refresh(
        summary
    )

    return (
        summary.status
        == "cancelled"
    )


def build_cancelled_content(
    title: str | None,
    sections: list,
    mode: SummaryMode,
) -> dict:
    safe_title = (
        title
        or (
            "Transcription"
            if mode
            == "transcription"
            else "Summary"
        )
    )

    stopped_message = {
        "type":
            "text",

        "title":
            "Generation stopped",

        "content":
            (
                "تم إيقاف التوليد هنا. "
                "لعرض النسخة كاملة، "
                "اضغط Regenerate."
            ),

        "asset_id":
            None,

        "caption":
            None,

        "location":
            None,
    }

    safe_sections = list(
        sections
    )

    already_has_message = (
        bool(
            safe_sections
        )
        and safe_sections[-1]
        .get(
            "title"
        )
        == "Generation stopped"
    )

    if not already_has_message:
        safe_sections.append(
            stopped_message
        )

    return {
        "title":
            safe_title,

        "sections":
            safe_sections,
    }


@router.get(
    "/{document_id}/summaries",
    response_model=list[
        DocumentSummaryResponse
    ],
)
def list_document_summaries(
    document_id: int,
    chat_id: int,
    mode: SummaryMode = "summary",
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

    return get_document_summaries(
        db=db,
        chat_id=chat_id,
        document_id=document_id,
        mode=mode,
    )


@router.get(
    "/{document_id}/summaries/selected",
    response_model=DocumentSummaryResponse,
)
def read_selected_summary(
    document_id: int,
    chat_id: int,
    mode: SummaryMode = "summary",
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

    summary = get_selected_summary(
        db=db,
        chat_id=chat_id,
        document_id=document_id,
        mode=mode,
    )

    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No selected summary found"
            ),
        )

    return summary


@router.post(
    "/{document_id}/summaries/generate",
    response_model=DocumentSummaryResponse,
    status_code=status.HTTP_200_OK,
)
def create_document_summary(
    document_id: int,
    data: SummaryGenerateRequest,
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
                "Document is not ready "
                "for summary generation"
            ),
        )

    summary = create_summary_record(
        db=db,
        chat_id=data.chat_id,
        document_id=document_id,
        mode=data.mode,
    )

    return generate_summary_for_record(
        db=db,
        document=document,
        summary=summary,
        mode=data.mode,
    )


@router.post(
    "/{document_id}/summaries/generate/stream"
)
def stream_document_summary(
    document_id: int,
    data: SummaryGenerateRequest,
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
                "Document is not ready "
                "for summary generation"
            ),
        )

    user_id = current_user.id

    chat_id = data.chat_id

    mode = data.mode

    def generate():
        stream_db = SessionLocal()

        summary = None

        title = None

        sections = []

        generator = None


        def persist_cancelled_partial():
            if summary is None:
                return

            try:
                current_summary = (
                    get_summary_by_id(
                        db=stream_db,
                        summary_id=summary.id,
                    )
                )

                if (
                    current_summary
                    is None
                    or current_summary
                    .status
                    == "completed"
                ):
                    return

                partial_content = (
                    build_cancelled_content(
                        title=title,
                        sections=sections,
                        mode=mode,
                    )
                )

                mark_summary_cancelled(
                    db=stream_db,
                    summary=current_summary,
                    content=partial_content,
                )

            except Exception:
                stream_db.rollback()


        try:
            stream_chat = (
                stream_db.query(Chat)
                .filter(
                    Chat.id
                    == chat_id,
                    Chat.user_id
                    == user_id,
                )
                .first()
            )

            if stream_chat is None:
                raise ValueError(
                    "Chat not found"
                )

            stream_document = (
                stream_db.query(Document)
                .filter(
                    Document.id
                    == document_id,
                    Document.user_id
                    == user_id,
                )
                .first()
            )

            if stream_document is None:
                raise ValueError(
                    "Document not found"
                )

            document_in_chat = any(
                item.id
                == document_id
                for item
                in stream_chat.documents
            )

            if not document_in_chat:
                raise ValueError(
                    "Document is not attached "
                    "to this chat"
                )

            if (
                stream_document
                .processing_status
                != "ready"
            ):
                raise ValueError(
                    "Document is not ready "
                    "for summary generation"
                )

            summary = (
                create_summary_record(
                    db=stream_db,
                    chat_id=chat_id,
                    document_id=document_id,
                    mode=mode,
                )
            )

            mark_summary_generating(
                db=stream_db,
                summary=summary,
            )

            yield (
                json.dumps(
                    {
                        "type":
                            "start",

                        "summary_id":
                            summary.id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

            generator = (
                stream_summary_content(
                    db=stream_db,
                    document=(
                        stream_document
                    ),
                    chat_id=chat_id,
                    mode=mode,
                )
            )

            while True:
                try:
                    if (
                        summary_was_cancelled(
                            db=stream_db,
                            summary_id=summary.id,
                        )
                    ):
                        persist_cancelled_partial()

                        try:
                            generator.close()
                        except Exception:
                            pass

                        return

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

                    if (
                        summary_was_cancelled(
                            db=stream_db,
                            summary_id=summary.id,
                        )
                    ):
                        persist_cancelled_partial()

                        try:
                            generator.close()
                        except Exception:
                            pass

                        return

                    yield (
                        json.dumps(
                            event,
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                except StopIteration as stop:
                    final_content = (
                        stop.value
                        or {
                            "title":
                                title,

                            "sections":
                                sections,
                        }
                    )

                    break

            if (
                summary_was_cancelled(
                    db=stream_db,
                    summary_id=summary.id,
                )
            ):
                persist_cancelled_partial()

                return

            summary = (
                mark_summary_completed(
                    db=stream_db,
                    summary=summary,
                    content=final_content,
                )
            )

            if (
                summary.status
                == "cancelled"
            ):
                return

            response_summary = (
                DocumentSummaryResponse
                .model_validate(
                    summary
                )
                .model_dump(
                    mode="json"
                )
            )

            yield (
                json.dumps(
                    {
                        "type":
                            "done",

                        "summary":
                            response_summary,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        except GeneratorExit:
            # The browser closed the stream, for example after Stop.
            # Persist exactly what had already streamed instead of
            # losing it or falling back to the previous result.
            persist_cancelled_partial()

            if generator is not None:
                try:
                    generator.close()
                except Exception:
                    pass

            raise

        except Exception as error:
            stream_db.rollback()

            if (
                summary is not None
            ):
                try:
                    summary = (
                        stream_db
                        .query(
                            type(summary)
                        )
                        .filter(
                            type(summary).id
                            == summary.id
                        )
                        .first()
                    )

                    if summary is not None:
                        mark_summary_failed(
                            db=stream_db,
                            summary=summary,
                            error=(
                                "Summary generation failed. "
                                "Please try again."
                            ),
                        )

                except Exception:
                    stream_db.rollback()

            yield (
                json.dumps(
                    {
                        "type":
                            "error",

                        "message": (
                            "Summary generation failed. "
                            "Please try again."
                        ),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        finally:
            stream_db.close()

    return StreamingResponse(
        generate(),
        media_type=(
            "application/x-ndjson"
        ),
        headers={
            "Cache-Control":
                "no-cache",

            "X-Accel-Buffering":
                "no",
        },
    )


@router.post(
    "/{document_id}/summaries/{summary_id}/cancel",
)
def cancel_document_summary(
    document_id: int,
    summary_id: int,
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

    summary = get_summary_by_id(
        db=db,
        summary_id=summary_id,
    )

    summary = (
        ensure_summary_belongs_to_context(
            summary=summary,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    if (
        summary.status
        == "completed"
    ):
        return {
            "message":
                "Summary already completed",
            "summary_id":
                summary.id,
            "status":
                summary.status,
        }

    if (
        summary.status
        != "cancelled"
    ):
        summary = (
            mark_summary_cancelled(
                db=db,
                summary=summary,
            )
        )

    return {
        "message":
            "Summary generation cancelled",
        "summary_id":
            summary.id,
        "status":
            summary.status,
    }


@router.get(
    "/{document_id}/summaries/{summary_id}",
    response_model=DocumentSummaryResponse,
)
def read_document_summary(
    document_id: int,
    summary_id: int,
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

    summary = get_summary_by_id(
        db=db,
        summary_id=summary_id,
    )

    return (
        ensure_summary_belongs_to_context(
            summary=summary,
            chat_id=chat_id,
            document_id=document_id,
        )
    )


@router.post(
    "/{document_id}/summaries/{summary_id}/select",
    response_model=DocumentSummaryResponse,
)
def choose_document_summary(
    document_id: int,
    summary_id: int,
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

    summary = select_summary(
        db=db,
        chat_id=chat_id,
        document_id=document_id,
        summary_id=summary_id,
    )

    if summary is None:
        raise HTTPException(
            status_code=404,
            detail="Summary not found",
        )

    return summary


@router.delete(
    "/{document_id}/summaries/{summary_id}",
)
def delete_document_summary(
    document_id: int,
    summary_id: int,
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

    summary = get_summary_by_id(
        db=db,
        summary_id=summary_id,
    )

    summary = (
        ensure_summary_belongs_to_context(
            summary=summary,
            chat_id=chat_id,
            document_id=document_id,
        )
    )

    delete_summary(
        db=db,
        summary=summary,
    )

    return {
        "message":
            "Summary deleted successfully",

        "summary_id":
            summary_id,

        "deleted_version":
            1,

        "was_selected":
            True,

        "selected_summary_id":
            None,

        "selected_version":
            None,
    }