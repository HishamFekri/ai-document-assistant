"""Compatibility entry point; all execution uses the same processing claim."""

from app.services.document_processing_service import process_document


__all__ = ["process_document"]
