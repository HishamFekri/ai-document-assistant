import logging
from pathlib import Path
from time import perf_counter

from pypdf import PdfReader

from app.database.database import SessionLocal
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


logger = logging.getLogger(__name__)


def process_document(
    document_id: int,
    file_path: str,
) -> None:
    db = SessionLocal()
    total_start = perf_counter()

    try:
        document = db.get(
            Document,
            document_id,
        )

        if not document:
            logger.warning(
                "Document %s not found; processing skipped",
                document_id,
            )
            return

        existing_chunks = (
            db.query(DocumentChunk.id)
            .filter(
                DocumentChunk.document_id
                == document_id
            )
            .first()
        )

        if (
            document.processing_status == "ready"
            and existing_chunks
        ):
            logger.info(
                "Document %s is already processed; skipping duplicate task",
                document_id,
            )
            return

        logger.info(
            "Document %s processing started",
            document_id,
        )

        document.processing_status = "processing"
        document.processing_stage = "starting"
        document.processing_progress = 0
        document.processing_error = None

        db.commit()

        path = Path(
            file_path
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Document file does not exist: {path.name}"
            )

        if not path.is_file():
            raise ValueError(
                "Document path is not a file"
            )

        document.processing_stage = "extracting"
        document.processing_progress = 10

        db.commit()

        extraction_start = perf_counter()

        content = extract_content(
            path
        )

        extraction_time = (
            perf_counter()
            - extraction_start
        )

        logger.info(
            "Document %s extraction completed in %.2fs",
            document_id,
            extraction_time,
        )

        if not content:
            raise ValueError(
                "No readable content found in file"
            )

        document.processing_stage = "chunking"
        document.processing_progress = 35

        db.commit()

        chunking_start = perf_counter()

        chunks = create_chunks_from_content(
            content
        )

        chunking_time = (
            perf_counter()
            - chunking_start
        )

        logger.info(
            "Document %s chunking completed in %.2fs; chunks=%s",
            document_id,
            chunking_time,
            len(chunks),
        )

        if not chunks:
            raise ValueError(
                "Could not create chunks from file"
            )

        document.processing_stage = "embedding"
        document.processing_progress = 55

        db.commit()

        chunk_texts = [
            chunk["content"]
            for chunk in chunks
        ]

        embeddings_start = perf_counter()

        embeddings = create_passage_embeddings(
            chunk_texts
        )

        embeddings_time = (
            perf_counter()
            - embeddings_start
        )

        logger.info(
            "Document %s embeddings completed in %.2fs",
            document_id,
            embeddings_time,
        )

        if len(embeddings) != len(chunks):
            raise ValueError(
                "Embedding count does not match chunk count"
            )

        document.processing_stage = "saving"
        document.processing_progress = 80

        db.commit()

        database_start = perf_counter()

        (
            db.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id
                == document.id
            )
            .delete(
                synchronize_session=False
            )
        )

        for chunk, embedding in zip(
            chunks,
            embeddings,
        ):
            document_chunk = DocumentChunk(
                document_id=document.id,
                content=chunk["content"],
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

            db.add(
                document_chunk
            )

        if (
            document.file_type
            and document.file_type.lower()
            == "pdf"
        ):
            reader = PdfReader(
                path
            )

            document.pages_count = len(
                reader.pages
            )

        document.processing_status = "ready"
        document.processing_stage = "completed"
        document.processing_progress = 100
        document.processing_error = None

        db.commit()

        database_time = (
            perf_counter()
            - database_start
        )

        total_time = (
            perf_counter()
            - total_start
        )

        logger.info(
            (
                "Document %s processing completed "
                "successfully; database=%.2fs total=%.2fs"
            ),
            document_id,
            database_time,
            total_time,
        )

    except Exception:
        db.rollback()

        total_time = (
            perf_counter()
            - total_start
        )

        logger.exception(
            "Document %s processing failed after %.2fs",
            document_id,
            total_time,
        )

        try:
            document = db.get(
                Document,
                document_id,
            )

            if document:
                document.processing_status = "failed"
                document.processing_stage = "failed"
                document.processing_progress = 0
                document.processing_error = (
                    "Document processing failed"
                )

                db.commit()

        except Exception:
            db.rollback()

            logger.exception(
                "Could not update failed status for document %s",
                document_id,
            )

        raise

    finally:
        db.close()