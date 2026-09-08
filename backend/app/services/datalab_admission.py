"""One finite page selection shared by every batch of one extraction attempt."""

from pathlib import Path
from threading import Lock

from app.services.document_resource_errors import DocumentResourceError
from app.services.resource_limits import upload_limits


class AdvancedPageBudget:
    def __init__(self, path, pages):
        self.limits = upload_limits()
        self.path = Path(path).resolve()
        self.allowed = frozenset(pages)
        if (not self.allowed or len(pages) != len(self.allowed)
                or len(pages) > self.limits.datalab_document_pages
                or any(type(page) is not int or not 1 <= page <= self.limits.pdf_pages for page in pages)):
            raise DocumentResourceError("advanced_pages")
        self.used = set()
        self.stopped = False
        self._lock = Lock()

    def stop(self):
        with self._lock:
            self.stopped = True

    def consume(self, path, page_range):
        with self._lock:
            if self.stopped or Path(path).resolve() != self.path or not page_range:
                raise DocumentResourceError("advanced_pages")
            requested = set()
            try:
                for part in page_range.split(","):
                    ends = [int(value) for value in part.split("-")]
                    if not 1 <= len(ends) <= 2:
                        raise ValueError()
                    first, last = ends[0], ends[-1]
                    if first < 0 or last < first or last - first + 1 > self.limits.datalab_batch_size:
                        raise ValueError()
                    pages = set(range(first + 1, last + 2))
                    if requested & pages:
                        raise ValueError()
                    requested.update(pages)
                    if len(requested) > self.limits.datalab_batch_size:
                        raise ValueError()
            except (ValueError, AttributeError):
                raise DocumentResourceError("advanced_pages") from None
            if not requested <= self.allowed or requested & self.used:
                raise DocumentResourceError("advanced_pages")
            self.used.update(requested)
