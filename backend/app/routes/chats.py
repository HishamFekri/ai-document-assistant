import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from fastapi.responses import (
    FileResponse,
    StreamingResponse,
)

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import (
    SessionLocal,
    get_db,
)

from app.database.models import (
    Chat,
    Document,
    DocumentChunk,
    Message,
    User,
)

from app.schemas.schemas import (
    ChatCreate,
    ChatResponse,
    ChatUpdate,
    MessageCreate,
    MessageResponse,
)

from app.routes.auth import (
    get_current_user,
)

from app.services.search_service import (
    search_similar_chunks,
)

from app.services.rag_service import (
    answer_question,
    filter_sources_by_citations,
    prepare_answer_context,
)

from app.services.llm_service import (
    generate_answer_stream,
)

from app.services.chat_intent_service import (
    detect_chat_intent,
)

from app.services.chat_summary_service import (
    generate_summary_from_chat,
    save_summary_instruction,
)

from app.services.chat_title_service import (
    maybe_generate_chat_title,
)


router = APIRouter(
    tags=["Chats"],
)


logger = logging.getLogger(__name__)


MAX_QUESTION_LENGTH = int(
    os.getenv(
        "MAX_QUESTION_LENGTH",
        "4000",
    )
)

MAX_SEARCH_QUERY_LENGTH = int(
    os.getenv(
        "MAX_SEARCH_QUERY_LENGTH",
        "1000",
    )
)


class SearchRequest(BaseModel):
    query: str


class AskRequest(BaseModel):
    question: str
    allow_general_knowledge: bool = False
    document_ids: list[int] = Field(default_factory=list)


def validate_question(
    question: str,
) -> str:
    question = question.strip()

    if not question:
        raise HTTPException(
            status_code=400,
            detail="Question cannot be empty",
        )

    if len(question) > MAX_QUESTION_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=(
                "Question is too long. "
                "Maximum allowed length is "
                f"{MAX_QUESTION_LENGTH} characters."
            ),
        )

    return question


def get_owned_chat(
    db: Session,
    chat_id: int,
    current_user: User,
) -> Chat:
    chat = (
        db.query(Chat)
        .filter(
            Chat.id == chat_id,
            Chat.user_id == current_user.id,
        )
        .first()
    )

    if not chat:
        raise HTTPException(
            status_code=404,
            detail="Chat not found",
        )

    return chat


def get_owned_document(
    db: Session,
    document_id: int,
    current_user: User,
) -> Document:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
        .first()
    )

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return document


def chat_has_failed_documents(
    chat: Chat,
) -> bool:
    return any(
        document.processing_status
        == "failed"
        for document in chat.documents
    )


def chat_documents_are_ready(
    chat: Chat,
) -> bool:
    if not chat.documents:
        return False

    return all(
        document.processing_status
        == "ready"
        for document in chat.documents
    )


@router.post(
    "/chats",
    response_model=ChatResponse,
)
def create_chat(
    data: ChatCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    requested_document_ids = list(
        set(
            data.document_ids
        )
    )

    documents = []

    if requested_document_ids:
        documents = (
            db.query(Document)
            .filter(
                Document.id.in_(
                    requested_document_ids
                ),
                Document.user_id
                == current_user.id,
            )
            .all()
        )

        if len(documents) != len(
            requested_document_ids
        ):
            raise HTTPException(
                status_code=404,
                detail=(
                    "One or more documents "
                    "were not found"
                ),
            )

    chat = Chat(
        user_id=current_user.id,
        title=data.title,
        documents=documents,
    )

    db.add(
        chat
    )

    db.commit()

    db.refresh(
        chat
    )

    return chat


@router.get(
    "/chats",
    response_model=list[
        ChatResponse
    ],
)
def get_chats(
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    query = (
        db.query(Chat)
        .filter(
            Chat.user_id
            == current_user.id,
            db.query(Message.id)
            .filter(
                Message.chat_id
                == Chat.id
            )
            .exists(),
        )
        .order_by(
            Chat.is_archived.asc(),
            Chat.is_pinned.desc(),
            Chat.created_at.desc(),
            Chat.id.desc(),
        )
    )

    if limit is not None:
        query = query.offset(offset).limit(limit)

    return query.all()


@router.get(
    "/chats/{chat_id}",
    response_model=ChatResponse,
)
def get_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    return get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )


@router.patch(
    "/chats/{chat_id}",
    response_model=ChatResponse,
)
def update_chat(
    chat_id: int,
    data: ChatUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    title = (
        data.title.strip()
    )

    if not title:
        raise HTTPException(
            status_code=400,
            detail=(
                "Chat title cannot be empty"
            ),
        )

    chat.title = title

    db.commit()

    db.refresh(
        chat
    )

    return chat


@router.patch(
    "/chats/{chat_id}/pin",
    response_model=ChatResponse,
)
def toggle_chat_pin(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    chat.is_pinned = (
        not chat.is_pinned
    )

    if chat.is_pinned:
        chat.is_archived = False

    db.commit()

    db.refresh(
        chat
    )

    return chat


@router.patch(
    "/chats/{chat_id}/archive",
    response_model=ChatResponse,
)
def toggle_chat_archive(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    chat.is_archived = (
        not chat.is_archived
    )

    if chat.is_archived:
        chat.is_pinned = False

    db.commit()

    db.refresh(
        chat
    )

    return chat


@router.delete(
    "/chats/{chat_id}"
)
def delete_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    db.delete(
        chat
    )

    db.commit()

    return {
        "message":
            "Chat deleted successfully"
    }


@router.post(
    "/chats/{chat_id}/documents/{document_id}",
    response_model=ChatResponse,
)
def add_document_to_chat(
    chat_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    document = (
        get_owned_document(
            db=db,
            document_id=document_id,
            current_user=current_user,
        )
    )

    document_already_attached = any(
        existing_document.id
        == document.id
        for existing_document
        in chat.documents
    )

    if document_already_attached:
        return chat

    chat.documents.append(
        document
    )

    db.commit()

    db.refresh(
        chat
    )

    return chat


@router.delete(
    "/chats/{chat_id}/documents/{document_id}",
    response_model=ChatResponse,
)
def remove_document_from_chat(
    chat_id: int,
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    document = (
        get_owned_document(
            db=db,
            document_id=document_id,
            current_user=current_user,
        )
    )

    document_in_chat = next(
        (
            existing_document
            for existing_document
            in chat.documents
            if existing_document.id
            == document.id
        ),
        None,
    )

    if not document_in_chat:
        raise HTTPException(
            status_code=404,
            detail=(
                "Document is not attached "
                "to this chat"
            ),
        )

    chat.documents.remove(
        document_in_chat
    )

    db.commit()

    db.refresh(
        chat
    )

    return chat


@router.post(
    "/chats/{chat_id}/messages",
    response_model=MessageResponse,
)
def create_message(
    chat_id: int,
    data: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    message = Message(
        chat_id=chat_id,
        role=data.role,
        content=data.content,
        status="completed",
        error=None,
        sources=None,
    )

    db.add(
        message
    )

    db.commit()

    db.refresh(
        message
    )

    return message


@router.get(
    "/chats/{chat_id}/messages",
    response_model=list[
        MessageResponse
    ],
)
def get_chat_messages(
    chat_id: int,
    limit: int | None = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    query = (
        db.query(Message)
        .filter(
            Message.chat_id
            == chat_id
        )
        .order_by(
            Message.created_at,
            Message.id,
        )
    )

    if limit is not None:
        query = query.offset(offset).limit(limit)

    return query.all()


@router.post(
    "/chats/{chat_id}/search"
)
def search_chat_documents(
    chat_id: int,
    data: SearchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    query = (
        data.query.strip()
    )

    if not query:
        raise HTTPException(
            status_code=400,
            detail=(
                "Search query cannot be empty"
            ),
        )

    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=(
                "Search query is too long. "
                "Maximum allowed length is "
                f"{MAX_SEARCH_QUERY_LENGTH} characters."
            ),
        )

    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    if not chat.documents:
        raise HTTPException(
            status_code=400,
            detail=(
                "This chat has no documents"
            ),
        )

    if chat_has_failed_documents(
        chat
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "One or more documents "
                "failed to process"
            ),
        )

    if not chat_documents_are_ready(
        chat
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Documents are still processing"
            ),
        )

    document_ids = [
        document.id
        for document
        in chat.documents
        if document.user_id
        == current_user.id
    ]

    results = (
        search_similar_chunks(
            db=db,
            query=query,
            document_ids=document_ids,
            limit=8,
        )
    )

    return [
        {
            "chunk_id":
                result[
                    "chunk"
                ].id,

            "document_id":
                result[
                    "chunk"
                ].document_id,

            "content_type":
                result[
                    "chunk"
                ].content_type,

            "location":
                result[
                    "chunk"
                ].location,

            "metadata":
                result[
                    "chunk"
                ].chunk_metadata,

            "content":
                result[
                    "chunk"
                ].content,

            "similarity":
                round(
                    result[
                        "similarity"
                    ],
                    4,
                ),
        }
        for result
        in results
    ]


@router.post(
    "/chats/{chat_id}/ask"
)
def ask_chat(
    chat_id: int,
    data: AskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    question = validate_question(
        data.question
    )

    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    if (
        not chat.documents
        and not data.allow_general_knowledge
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Attach at least one document "
                "or enable general knowledge"
            ),
        )

    if chat.documents:
        if chat_has_failed_documents(chat):
            raise HTTPException(
                status_code=400,
                detail=(
                    "One or more documents "
                    "failed to process"
                ),
            )

        if not chat_documents_are_ready(chat):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Documents are still processing"
                ),
            )

    intent = {
        "action": "chat",
        "document_ids": [],
        "needs_document_selection": False,
    }

    if chat.documents:
        try:
            intent = detect_chat_intent(
                question=question,
                documents=list(chat.documents),
            )
        except Exception:
            logger.exception(
                "Chat intent detection failed"
            )

    user_message = Message(
        chat_id=chat_id,
        role="user",
        content=question,
        status="processing",
        error=None,
        sources=None,
    )

    db.add(user_message)
    db.commit()
    db.refresh(user_message)

    title_document_ids = (
        data.document_ids
        or [
            document.id
            for document
            in chat.documents
        ]
    )

    generated_chat_title = None

    try:
        generated_chat_title = (
            maybe_generate_chat_title(
                db=db,
                chat_id=chat_id,
                question=question,
                document_ids=(
                    title_document_ids
                ),
            )
        )

    except Exception:
        logger.exception(
            "Chat title generation failed"
        )

    try:
        action = intent.get(
            "action",
            "chat",
        )
        document_ids = list(
            intent.get(
                "document_ids",
                [],
            )
        )
        needs_selection = bool(
            intent.get(
                "needs_document_selection",
                False,
            )
        )

        if (
            action in {
                "generate_summary",
                "summary_preferences",
            }
            and needs_selection
        ):
            result = {
                "answer": (
                    "Which document would you "
                    "like me to use for the summary?"
                ),
                "sources": [],
                "mode": "summary_selection",
            }

        elif action == "summary_preferences":
            if (
                not document_ids
                and len(chat.documents) == 1
            ):
                document_ids = [
                    chat.documents[0].id
                ]

            if not document_ids:
                result = {
                    "answer": (
                        "Tell me which document these "
                        "summary preferences should "
                        "apply to."
                    ),
                    "sources": [],
                    "mode": "summary_selection",
                }
            else:
                for document_id in document_ids:
                    save_summary_instruction(
                        db=db,
                        chat_id=chat_id,
                        document_id=document_id,
                        content=question,
                    )

                result = {
                    "answer": (
                        "Got it. I'll use those "
                        "preferences for the next "
                        "summary."
                    ),
                    "sources": [],
                    "mode": "summary_preferences",
                }

        elif action == "generate_summary":
            if (
                not document_ids
                and len(chat.documents) == 1
            ):
                document_ids = [
                    chat.documents[0].id
                ]

            if not document_ids:
                raise ValueError(
                    "Could not determine which "
                    "document to summarize"
                )

            generated = []

            for document_id in document_ids:
                document = get_owned_document(
                    db=db,
                    document_id=document_id,
                    current_user=current_user,
                )

                if not any(
                    item.id == document.id
                    for item in chat.documents
                ):
                    continue

                summary = generate_summary_from_chat(
                    db=db,
                    chat_id=chat_id,
                    document=document,
                    instruction=question,
                )
                generated.append(summary)

            if not generated:
                raise ValueError(
                    "Could not generate summary"
                )

            result = {
                "answer": (
                    "I've generated the summary."
                    if len(generated) == 1
                    else (
                        "I've generated summaries "
                        "for the selected documents."
                    )
                ),
                "sources": [],
                "mode": "summary",
                "summaries": [
                    {
                        "document_id": item.document_id,
                        "summary_id": item.id,
                        "version": item.version,
                        "status": item.status,
                    }
                    for item in generated
                ],
            }

        else:
            result = answer_question(
                db=db,
                chat_id=chat_id,
                question=question,
                allow_general_knowledge=(
                    data.allow_general_knowledge
                ),
                document_ids=(
                    data.document_ids
                    or None
                ),
            )

        assistant_message = Message(
            chat_id=chat_id,
            role="assistant",
            content=result["answer"],
            status="completed",
            error=None,
            sources=result.get(
                "sources",
                [],
            ),
        )

        db.add(assistant_message)

        user_message.status = "completed"
        user_message.error = None

        db.commit()

        if generated_chat_title:
            result[
                "chat_title"
            ] = generated_chat_title

        return result

    except ValueError as error:
        db.rollback()

        stored_user_message = db.get(
            Message,
            user_message.id,
        )

        if stored_user_message:
            stored_user_message.status = "failed"
            stored_user_message.error = (
                "Answer generation failed"
            )
            db.commit()

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception:
        db.rollback()

        logger.exception(
            "Chat answer generation failed"
        )

        stored_user_message = db.get(
            Message,
            user_message.id,
        )

        if stored_user_message:
            stored_user_message.status = "failed"
            stored_user_message.error = (
                "Answer generation failed"
            )
            db.commit()

        raise HTTPException(
            status_code=500,
            detail="Could not generate answer",
        )


@router.post(
    "/chats/{chat_id}/ask/stream"
)
def ask_chat_stream(
    chat_id: int,
    data: AskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    question = validate_question(
        data.question
    )

    chat = get_owned_chat(
        db=db,
        chat_id=chat_id,
        current_user=current_user,
    )

    requested_document_ids = list(
        dict.fromkeys(
            data.document_ids
        )
    )

    if requested_document_ids:
        request_documents = (
            db.query(Document)
            .filter(
                Document.id.in_(
                    requested_document_ids
                ),
                Document.user_id
                == current_user.id,
            )
            .all()
        )

        if (
            len(request_documents)
            != len(
                requested_document_ids
            )
        ):
            raise HTTPException(
                status_code=404,
                detail=(
                    "One or more attached "
                    "documents were not found"
                ),
            )

        chat_document_ids = {
            document.id
            for document
            in chat.documents
        }

        if any(
            document.id
            not in chat_document_ids
            for document
            in request_documents
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "One or more documents "
                    "are not attached to this chat"
                ),
            )

    else:
        request_documents = list(
            chat.documents
        )

    if (
        not request_documents
        and not data.allow_general_knowledge
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Attach at least one document "
                "or enable general knowledge"
            ),
        )

    failed_documents = [
        document
        for document
        in request_documents
        if document.processing_status
        == "failed"
    ]

    if failed_documents:
        raise HTTPException(
            status_code=400,
            detail=(
                "One or more attached documents "
                "failed to process"
            ),
        )

    intent = {
        "action": "chat",
        "document_ids": [],
        "needs_document_selection": False,
    }

    if request_documents:
        try:
            intent = detect_chat_intent(
                question=question,
                documents=(
                    request_documents
                ),
            )

        except Exception:
            logger.exception(
                "Chat intent detection failed"
            )

    current_user_id = (
        current_user.id
    )

    message_document_ids = [
        document.id
        for document
        in request_documents
    ]

    user_message = Message(
        chat_id=chat_id,
        role="user",
        content=question,
        status="processing",
        error=None,
        sources=None,
        documents=(
            request_documents
        ),
    )

    db.add(
        user_message
    )

    db.commit()

    db.refresh(
        user_message
    )

    user_message_id = (
        user_message.id
    )

    def generate():
        stream_db = (
            SessionLocal()
        )

        full_answer = ""

        title_executor = None
        title_future = None
        title_event_sent = False

        def generate_chat_title():
            title_db = SessionLocal()

            try:
                return (
                    maybe_generate_chat_title(
                        db=title_db,
                        chat_id=chat_id,
                        question=question,
                        document_ids=(
                            message_document_ids
                        ),
                    )
                )

            finally:
                title_db.close()

        def get_ready_chat_title():
            nonlocal title_event_sent

            if (
                title_event_sent
                or title_future is None
                or not title_future.done()
            ):
                return None

            title_event_sent = True

            try:
                return title_future.result()

            except Exception:
                logger.exception(
                    "Background chat title generation failed"
                )

                return None

        def wait_for_chat_title(
            timeout_seconds: float = 5.0,
        ):
            if (
                title_event_sent
                or title_future is None
            ):
                return None

            deadline = (
                time.monotonic()
                + timeout_seconds
            )

            while (
                not title_future.done()
                and time.monotonic()
                < deadline
            ):
                time.sleep(
                    0.05
                )

            return (
                get_ready_chat_title()
            )

        title_executor = (
            ThreadPoolExecutor(
                max_workers=1
            )
        )

        title_future = (
            title_executor.submit(
                generate_chat_title
            )
        )

        try:
            if message_document_ids:
                wait_started = (
                    time.monotonic()
                )

                last_progress_payload = None

                while True:
                    stream_db.expire_all()
                    
                    current_documents = (
                        stream_db
                        .query(Document)
                        .filter(
                            Document.id.in_(
                                message_document_ids
                            ),
                            Document.user_id
                            == current_user_id,
                        )
                        .all()
                    )

                    failed = [
                        document
                        for document
                        in current_documents
                        if (
                            document
                            .processing_status
                            == "failed"
                        )
                    ]

                    if failed:
                        raise ValueError(
                            (
                                "An attached document "
                                "failed to process"
                            )
                        )

                    ready = (
                        len(
                            current_documents
                        )
                        == len(
                            message_document_ids
                        )
                        and all(
                            document
                            .processing_status
                            == "ready"
                            for document
                            in current_documents
                        )
                    )

                    progress_payload = [
                        {
                            "id":
                                document.id,

                            "filename":
                                document.filename,

                            "processing_status":
                                (
                                    document
                                    .processing_status
                                ),

                            "processing_stage":
                                (
                                    document
                                    .processing_stage
                                ),

                            "processing_progress":
                                (
                                    document
                                    .processing_progress
                                ),
                        }
                        for document
                        in current_documents
                    ]

                    if (
                        progress_payload
                        != last_progress_payload
                    ):
                        yield (
                            json.dumps(
                                {
                                    "type":
                                        "attachment_status",

                                    "documents":
                                        progress_payload,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )

                        last_progress_payload = (
                            progress_payload
                        )

                    ready_chat_title = (
                        get_ready_chat_title()
                    )

                    if ready_chat_title:
                        yield (
                            json.dumps(
                                {
                                    "type":
                                        "chat_title",

                                    "title":
                                        ready_chat_title,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )

                    if ready:
                        break

                    if (
                        time.monotonic()
                        - wait_started
                        > 300
                    ):
                        raise TimeoutError(
                            (
                                "Document processing "
                                "took too long"
                            )
                        )

                    time.sleep(
                        1
                    )

            ready_chat_title = (
                get_ready_chat_title()
            )

            if ready_chat_title:
                yield (
                    json.dumps(
                        {
                            "type":
                                "chat_title",

                            "title":
                                ready_chat_title,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            action = intent.get(
                "action",
                "chat",
            )

            document_ids = list(
                intent.get(
                    "document_ids",
                    [],
                )
            )

            needs_selection = bool(
                intent.get(
                    "needs_document_selection",
                    False,
                )
            )

            if (
                action
                in {
                    "generate_summary",
                    "summary_preferences",
                }
                and needs_selection
            ):
                full_answer = (
                    "Which document would you "
                    "like me to use for the summary?"
                )

                mode = (
                    "summary_selection"
                )

                sources = []

            elif (
                action
                == "summary_preferences"
            ):
                if (
                    not document_ids
                    and len(
                        message_document_ids
                    )
                    == 1
                ):
                    document_ids = [
                        message_document_ids[
                            0
                        ]
                    ]

                if not document_ids:
                    full_answer = (
                        "Tell me which document these "
                        "summary preferences should "
                        "apply to."
                    )

                    mode = (
                        "summary_selection"
                    )

                else:
                    for document_id in (
                        document_ids
                    ):
                        if (
                            document_id
                            not in
                            message_document_ids
                        ):
                            continue

                        save_summary_instruction(
                            db=stream_db,
                            chat_id=chat_id,
                            document_id=(
                                document_id
                            ),
                            content=question,
                        )

                    full_answer = (
                        "Got it. I'll use those "
                        "preferences for the next "
                        "summary."
                    )

                    mode = (
                        "summary_preferences"
                    )

                sources = []

            elif (
                action
                == "generate_summary"
            ):
                if (
                    not document_ids
                    and len(
                        message_document_ids
                    )
                    == 1
                ):
                    document_ids = [
                        message_document_ids[
                            0
                        ]
                    ]

                if not document_ids:
                    raise ValueError(
                        (
                            "Could not determine "
                            "which document to summarize"
                        )
                    )

                generated_count = 0

                for document_id in (
                    document_ids
                ):
                    if (
                        document_id
                        not in
                        message_document_ids
                    ):
                        continue

                    document = (
                        stream_db
                        .query(Document)
                        .filter(
                            Document.id
                            == document_id,

                            Document.user_id
                            == current_user_id,
                        )
                        .first()
                    )

                    if not document:
                        continue

                    summary = (
                        generate_summary_from_chat(
                            db=stream_db,
                            chat_id=chat_id,
                            document=document,
                            instruction=question,
                        )
                    )

                    generated_count += 1

                    yield (
                        json.dumps(
                            {
                                "type":
                                    "summary_generated",

                                "document_id":
                                    document.id,

                                "summary_id":
                                    summary.id,

                                "version":
                                    summary.version,

                                "status":
                                    summary.status,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                if generated_count == 0:
                    raise ValueError(
                        "Could not generate summary"
                    )

                full_answer = (
                    "I've generated the summary."
                    if generated_count == 1
                    else (
                        "I've generated summaries "
                        "for the selected documents."
                    )
                )

                mode = "summary"
                sources = []

            else:
                prepared = (
                    prepare_answer_context(
                        db=stream_db,
                        chat_id=chat_id,
                        question=question,
                        allow_general_knowledge=(
                            data
                            .allow_general_knowledge
                        ),
                        document_ids=(
                            message_document_ids
                            or None
                        ),
                    )
                )

                immediate_answer = (
                    prepared[
                        "immediate_answer"
                    ]
                )

                if immediate_answer:
                    full_answer = (
                        immediate_answer
                    )

                    yield (
                        json.dumps(
                            {
                                "type":
                                    "token",

                                "content":
                                    immediate_answer,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )

                else:
                    answer_stream = (
                        generate_answer_stream(
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
                                data
                                .allow_general_knowledge
                            ),
                        )
                    )

                    for token in (
                        answer_stream
                    ):
                        full_answer += (
                            token
                        )

                        ready_chat_title = (
                            get_ready_chat_title()
                        )

                        if ready_chat_title:
                            yield (
                                json.dumps(
                                    {
                                        "type":
                                            "chat_title",

                                        "title":
                                            ready_chat_title,
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )

                        yield (
                            json.dumps(
                                {
                                    "type":
                                        "token",

                                    "content":
                                        token,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
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
                        answer=(
                            full_answer
                        ),
                    )
                )

                mode = prepared[
                    "mode"
                ]

            if (
                action
                in {
                    "generate_summary",
                    "summary_preferences",
                }
            ):
                yield (
                    json.dumps(
                        {
                            "type":
                                "token",

                            "content":
                                full_answer,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            ready_chat_title = (
                wait_for_chat_title()
            )

            if ready_chat_title:
                yield (
                    json.dumps(
                        {
                            "type":
                                "chat_title",

                            "title":
                                ready_chat_title,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            assistant_message = (
                Message(
                    chat_id=chat_id,
                    role="assistant",
                    content=full_answer,
                    status="completed",
                    error=None,
                    sources=sources,
                )
            )

            stream_db.add(
                assistant_message
            )

            stored_user_message = (
                stream_db.get(
                    Message,
                    user_message_id,
                )
            )

            if stored_user_message:
                stored_user_message.status = (
                    "completed"
                )

                stored_user_message.error = (
                    None
                )

            stream_db.commit()

            yield (
                json.dumps(
                    {
                        "type":
                            "done",

                        "sources":
                            sources,

                        "mode":
                            mode,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        except Exception:
            stream_db.rollback()

            logger.exception(
                "Chat stream failed"
            )

            stored_user_message = (
                stream_db.get(
                    Message,
                    user_message_id,
                )
            )

            if stored_user_message:
                stored_user_message.status = (
                    "failed"
                )

                stored_user_message.error = (
                    "Answer generation failed"
                )

                stream_db.commit()

            yield (
                json.dumps(
                    {
                        "type": "error",
                        "message": (
                            "Could not generate answer"
                        ),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        finally:
            if title_executor is not None:
                title_executor.shutdown(
                    wait=False
                )

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

@router.get(
    "/documents/{document_id}/assets/{asset_filename}"
)
def get_document_asset(
    document_id: int,
    asset_filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        get_current_user
    ),
):
    document = (
        get_owned_document(
            db=db,
            document_id=document_id,
            current_user=current_user,
        )
    )

    safe_filename = (
        Path(
            asset_filename
        ).name
    )

    if (
        safe_filename
        != asset_filename
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid asset filename"
            ),
        )

    image_chunks = (
        db.query(
            DocumentChunk
        )
        .filter(
            DocumentChunk.document_id
            == document.id,
            DocumentChunk.content_type
            == "image",
        )
        .all()
    )

    asset_path = None

    for chunk in image_chunks:
        metadata = (
            chunk.chunk_metadata
            or {}
        )

        chunk_asset_filename = (
            metadata.get(
                "asset_filename"
            )
        )

        if (
            chunk_asset_filename
            == safe_filename
        ):
            asset_path = (
                metadata.get(
                    "asset_path"
                )
            )

            break

    if not asset_path:
        raise HTTPException(
            status_code=404,
            detail=(
                "Image asset not found"
            ),
        )

    path = Path(
        asset_path
    ).resolve()

    if (
        not path.exists()
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "Image file not found"
            ),
        )

    return FileResponse(
        path=path,
    )