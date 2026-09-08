"""Incremental text/block/chunk admission shared by all extraction paths."""

from threading import Lock

from app.services.document_resource_errors import DocumentResourceError
from app.services.resource_limits import upload_limits


class ContentBudget:
    def __init__(self):
        self.limits = upload_limits()
        self.characters = self.bytes = self.blocks = self.chunks = 0
        self.chunk_characters = self.chunk_bytes = 0
        self._lock = Lock()

    def text(self, value):
        with self._lock:
            # Check character count before allocating an encoded copy.
            self.characters += len(value)
            if self.characters > self.limits.text_chars:
                raise DocumentResourceError("content_limit")
            self.bytes += len(value.encode("utf-8"))
            if self.bytes > self.limits.text_bytes:
                raise DocumentResourceError("content_limit")

    def block(self, block):
        self.text(block.get("content") or "")
        self.blocks += 1
        if self.blocks > self.limits.blocks:
            raise DocumentResourceError("content_limit")
        return block

    def chunk(self, value):
        self.chunks += 1
        if self.chunks > self.limits.chunks:
            raise DocumentResourceError("content_limit")
        self.chunk_characters += len(value)
        if self.chunk_characters > self.limits.text_chars:
            raise DocumentResourceError("content_limit")
        self.chunk_bytes += len(value.encode("utf-8"))
        if self.chunk_bytes > self.limits.text_bytes:
            raise DocumentResourceError("content_limit")


def check_content(blocks):
    budget = ContentBudget()
    for block in blocks:
        budget.block(block)
