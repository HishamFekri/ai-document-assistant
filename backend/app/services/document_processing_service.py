from pathlib import Path
from time import perf_counter

from pypdf import PdfReader

from app.database.database import (
    SessionLocal,
)

from app.database.models import (
    Document,
    DocumentChunk,
)

from app.services.file_service import (
    extract_content,
)

from app.services.chunk_service import (
    create_chunks_from_content,
)

from app.services.embedding_service import (
    create_passage_embeddings,
)

from app.services.queued_message_service import (
    process_waiting_messages_for_document,
)

from app.services.error_service import (
    log_and_get_public_error,
)

from app.services.assets.asset_extraction_service import (
    replace_document_assets,
)


def update_progress(
    db,
    document: Document,
    stage: str,
    progress: int,
):
    document.processing_stage = stage

    document.processing_progress = max(
        0,
        min(
            progress,
            100,
        ),
    )

    db.commit()

    print(
        f"[PROGRESS] Document "
        f"{document.id}: "
        f"{stage} "
        f"({document.processing_progress}%)"
    )


def process_document(
    document_id: int,
    file_path: str,
):
    db = SessionLocal()
    processing_error = None

    total_start = perf_counter()

    try:
        document = db.get(
            Document,
            document_id,
        )

        if not document:
            return

        print(
            f"\n[PROCESSING] Document "
            f"{document_id} started"
        )

        document.processing_status = (
            "processing"
        )

        document.processing_error = None

        update_progress(
            db=db,
            document=document,
            stage="starting",
            progress=10,
        )

        path = Path(
            file_path
        )

        if not path.exists():
            raise FileNotFoundError(
                f"File not found: {path}"
            )

        # ===============================
        # Document extraction
        # ===============================

        update_progress(
            db=db,
            document=document,
            stage="analyzing_document",
            progress=20,
        )

        extraction_start = (
            perf_counter()
        )

        content = extract_content(
            file_path=path,
            document_id=document_id,
        )

        extraction_time = (
            perf_counter()
            - extraction_start
        )

        print(
            f"[TIMING] Extraction: "
            f"{extraction_time:.2f}s"
        )

        if not content:
            raise ValueError(
                "No readable content found in file"
            )

        update_progress(
            db=db,
            document=document,
            stage="document_analyzed",
            progress=45,
        )

        # ===============================
        # Document assets
        # ===============================

        update_progress(
            db=db,
            document=document,
            stage="extracting_assets",
            progress=50,
        )

        asset_start = (
            perf_counter()
        )

        assets = (
            replace_document_assets(
                db=db,
                document_id=document.id,
                content=content,
            )
        )

        asset_time = (
            perf_counter()
            - asset_start
        )

        print(
            f"[TIMING] Asset processing: "
            f"{asset_time:.2f}s"
        )

        print(
            f"[INFO] Assets created: "
            f"{len(assets)}"
        )

        update_progress(
            db=db,
            document=document,
            stage="assets_ready",
            progress=58,
        )

        # ===============================
        # Chunking
        # ===============================

        update_progress(
            db=db,
            document=document,
            stage="creating_chunks",
            progress=65,
        )

        chunks = (
            create_chunks_from_content(
                content
            )
        )

        if not chunks:
            raise ValueError(
                "Could not create chunks from file"
            )

        print(
            f"[INFO] Chunks created: "
            f"{len(chunks)}"
        )

        update_progress(
            db=db,
            document=document,
            stage="chunks_ready",
            progress=72,
        )

        # ===============================
        # Embeddings
        # ===============================

        update_progress(
            db=db,
            document=document,
            stage="creating_embeddings",
            progress=78,
        )

        chunk_texts = [
            chunk[
                "content"
            ]
            for chunk in chunks
        ]

        embeddings = (
            create_passage_embeddings(
                chunk_texts
            )
        )

        if len(
            embeddings
        ) != len(
            chunks
        ):
            raise ValueError(
                "Embedding count does not "
                "match chunk count"
            )

        update_progress(
            db=db,
            document=document,
            stage="saving_document",
            progress=92,
        )

        # ===============================
        # Replace chunks
        # ===============================

        existing_chunks = (
            db.query(
                DocumentChunk
            )
            .filter(
                DocumentChunk.document_id
                == document.id
            )
            .all()
        )

        for existing_chunk in (
            existing_chunks
        ):
            db.delete(
                existing_chunk
            )

        for chunk, embedding in zip(
            chunks,
            embeddings,
        ):
            db.add(
                DocumentChunk(
                    document_id=document.id,
                    content=chunk[
                        "content"
                    ],
                    content_type=chunk[
                        "content_type"
                    ],
                    location=chunk[
                        "location"
                    ],
                    chunk_metadata=chunk[
                        "metadata"
                    ],
                    embedding=embedding,
                )
            )

        # ===============================
        # Document metadata
        # ===============================

        if (
            document.file_type
            == "pdf"
        ):
            reader = PdfReader(
                path
            )

            document.pages_count = len(
                reader.pages
            )

        # ===============================
        # Ready
        # ===============================

        document.processing_status = (
            "ready"
        )

        document.processing_stage = (
            "ready"
        )

        document.processing_progress = (
            100
        )

        document.processing_error = None

        db.commit()

        total_time = (
            perf_counter()
            - total_start
        )

        print(
            f"[SUCCESS] Document "
            f"{document_id} ready "
            f"in {total_time:.2f}s\n"
        )

    except Exception as error:
        processing_error = error
        db.rollback()

        document = db.get(
            Document,
            document_id,
        )

        if document:
            document.processing_status = (
                "failed"
            )

            document.processing_stage = (
                "failed"
            )

            document.processing_error = (
                log_and_get_public_error(
                    error,
                    "Document processing failed. Please try again.",
                )
            )

            db.commit()

        print(
            f"[ERROR] Document "
            f"{document_id}: "
            f"{error}"
        )

    finally:
        db.close()

    # =====================================
    # Wake queued questions
    # =====================================

    try:
        process_waiting_messages_for_document(
            document_id
        )

    except Exception as error:
        print(
            "[QUEUE ERROR] Could not process "
            f"waiting messages: {error}"
        )

    if processing_error is not None:
        raise processing_error