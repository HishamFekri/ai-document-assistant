from app.services.admission_dependencies import admit_summary
from app.services.resource_admission import (
    Permit, ResourceRejected, AdmittedStreamingResponse, stream_resource_error,
)
import json
from types import SimpleNamespace

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from fastapi.responses import (
    StreamingResponse,
)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.summary_models import DocumentSummary
from app.services.error_service import log_generation_failure
from app.services.summaries.summary_claim import SummaryGenerationBusy

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
    snapshot_document,
    start_summary_generation,
    SummaryGenerationStopped,
)

from app.services.summaries.summary_service import (
    SummaryMode,
    delete_summary,
    get_document_summaries,
    get_selected_summary,
    get_summary_by_id,
    mark_summary_cancelled,
    mark_summary_completed,
    mark_summary_failed,
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


def summary_was_cancelled(db: Session, summary_id: int) -> bool:
    current_status = db.scalar(select(DocumentSummary.status).where(
        DocumentSummary.id == summary_id,
    ))
    db.rollback()
    return current_status not in ("pending", "generating")


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
    admission: Permit = Depends(admit_summary, scope="request"),
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

    return generate_summary_for_record(
        db=db, document=document, chat_id=data.chat_id, mode=data.mode, admission=admission,
    )



@router.post(
    "/{document_id}/summaries/generate/stream"
)
def stream_document_summary(
    document_id: int,
    data: SummaryGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    admission: Permit = Depends(admit_summary, scope="request"),
):
    _, document = get_chat_document(data.chat_id, document_id, current_user, db)
    if document.processing_status != "ready":
        raise HTTPException(409, "Document is not ready for summary generation")
    user_id, chat_id, mode = current_user.id, data.chat_id, data.mode
    db.rollback()

    def encode(event):
        return json.dumps(event, ensure_ascii=False) + "\n"

    def generate():
        summary = None
        try:
            # Streaming may begin after the request dependency has closed.
            with SessionLocal() as validation_db:
                _, document = get_chat_document(
                    chat_id, document_id, SimpleNamespace(id=user_id), validation_db,
                )
                if document.processing_status != "ready":
                    raise ValueError("Document is not ready for summary generation")
                document = snapshot_document(document)
            with start_summary_generation(chat_id, document_id, mode, admission=admission) as (
                stream_db, summary, owns_lifecycle,
            ):
                if not owns_lifecycle:
                    return
                title, sections, generator = None, [], None

                def persist_cancelled_partial():
                    mark_summary_cancelled(
                        stream_db, summary,
                        build_cancelled_content(title, sections, mode),
                    )

                try:
                    yield encode({"type": "start", "summary_id": summary.id})
                    generator = stream_summary_content(
                        db=stream_db, document=document, chat_id=chat_id, mode=mode,
                    )
                    while True:
                        if summary_was_cancelled(stream_db, summary.id):
                            persist_cancelled_partial()
                            return
                        try:
                            event = next(generator)
                        except StopIteration as stop:
                            final_content = stop.value or {"title": title, "sections": sections}
                            break
                        # A blocked provider may have returned after cancellation.
                        if summary_was_cancelled(stream_db, summary.id):
                            persist_cancelled_partial()
                            return
                        if event.get("type") == "title":
                            title = event.get("title")
                        elif event.get("type") == "section":
                            sections.append(event["section"])
                        yield encode(event)
                    completed = mark_summary_completed(stream_db, summary, final_content)
                    if completed is None or completed.status != "completed":
                        persist_cancelled_partial()
                        return
                    yield encode({
                        "type": "done",
                        "summary": DocumentSummaryResponse.model_validate(completed).model_dump(mode="json"),
                    })
                except SummaryGenerationStopped:
                    persist_cancelled_partial()
                    return
                except GeneratorExit:
                    # Save only what this owner actually sent, before releasing
                    # the claim. A completed/deleted record remains untouched.
                    persist_cancelled_partial()
                    raise
                except Exception as error:
                    public_error = log_generation_failure(
                        error, "summary", document_id=document_id,
                        chat_id=chat_id, summary_id=summary.id,
                    )
                    stream_db.rollback()
                    mark_summary_failed(stream_db, summary, public_error)
                    yield encode({"type": "error", "message": public_error})
                finally:
                    if generator is not None:
                        generator.close()
        except ResourceRejected as error:
            yield encode(stream_resource_error(error))
        except SummaryGenerationBusy as error:
            # Existing frontend understands this error event; never send a start
            # event that would let this duplicate cancel the original request.
            yield encode({"type": "error", "message": error.detail})
        except Exception as error:
            public_error = log_generation_failure(
                error, "summary", document_id=document_id,
                chat_id=chat_id, summary_id=summary.id if summary else None,
            )
            yield encode({"type": "error", "message": public_error})

    return AdmittedStreamingResponse(
        generate(), admission, media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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

    summary = mark_summary_cancelled(db=db, summary=summary)
    if summary is None:
        raise HTTPException(404, "Summary not found")
    return {
        "message": (
            "Summary already completed" if summary.status == "completed"
            else "Summary generation cancelled" if summary.status == "cancelled"
            else "Summary generation already stopped"
        ),
        "summary_id": summary.id,
        "status": summary.status,
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
