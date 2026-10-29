"""Extracted document images on the persistent local uploads volume.

Files are named by content hash with an extension taken from the validated
image signature, never from provider filenames. Every directory touched is a
real directory (not a symlink) at its exact expected location under the root.
"""

import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from app.services.observability import log_event, log_exception
from app.services.storage_settings import (
    LocalStorageUnavailable,
    assets_root,
    document_asset_directory,
    ensure_local_storage_ready,
)


# Shared with image delivery: nothing larger is ever stored or served.
MAX_STORED_IMAGE_BYTES = 20 * 1024 * 1024
DOCUMENT_DIRECTORY_PATTERN = re.compile(r"document_[1-9][0-9]*")
WINDOWS_LINK_REPARSE_TAGS = frozenset(
    tag for tag in (
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", None),
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None),
    ) if tag is not None
)
logger = logging.getLogger(__name__)


class LocalImageRejected(ValueError):
    """Image content that is never written (unsupported format or too large)."""

    def __init__(self, category):
        super().__init__(category)
        self.category = category


def detect_image_extension(data: bytes) -> str | None:
    """Raster formats only, by signature; SVG/HTML/unknown content is rejected."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _is_link(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    # Windows junctions are links without S_IFLNK (development only). The tag
    # constants and st_reparse_tag only exist on Windows.
    tag = getattr(info, "st_reparse_tag", 0)
    return bool(tag) and tag in WINDOWS_LINK_REPARSE_TAGS


def _real_directory(path: Path) -> None:
    try:
        os.mkdir(path, 0o750)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise LocalStorageUnavailable("Image storage directory is not a real directory")


def _document_directory(document_id: int) -> Path:
    root = ensure_local_storage_ready()
    directory = document_asset_directory(document_id)
    _real_directory(directory.parent)
    _real_directory(directory)
    # Also rejects a symlinked component anywhere between the root and the file.
    if directory.resolve() != root.resolve() / "assets" / directory.name:
        raise LocalStorageUnavailable("Image storage directory escapes the uploads root")
    return directory


def _matches(path: Path, digest: str) -> bool:
    info = os.lstat(path)
    if _is_link(info) or not stat.S_ISREG(info.st_mode):
        raise LocalStorageUnavailable("Existing image storage entry is not a regular file")
    if info.st_size > MAX_STORED_IMAGE_BYTES:
        return False
    with open(path, "rb") as existing:
        return hashlib.sha256(existing.read()).hexdigest() == digest


def save_local_image(image_bytes: bytes, document_id: int) -> str:
    """Store once per document and content; returns the stored POSIX path.

    Raises LocalImageRejected for content that must not be stored, and
    LocalStorageUnavailable/OSError when the volume itself cannot be used.
    """
    if not image_bytes:
        raise LocalImageRejected("empty_image")
    if len(image_bytes) > MAX_STORED_IMAGE_BYTES:
        raise LocalImageRejected("image_too_large")
    extension = detect_image_extension(image_bytes)
    if extension is None:
        raise LocalImageRejected("unsupported_image")

    digest = hashlib.sha256(image_bytes).hexdigest()
    directory = _document_directory(document_id)
    target = directory / f"img_{digest[:40]}{extension}"
    try:
        if _matches(target, digest):
            return target.as_posix()
    except FileNotFoundError:
        pass

    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(image_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
        # Atomic within the directory; replaces (never follows) an existing entry.
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return target.as_posix()


def delete_local_document_images(document_id: int) -> bool:
    """Remove exactly one document's image directory; never the shared root.

    Returns whether a directory was removed. A symlink or unexpected entry in
    its place is refused (raised), and nothing it points at is touched.
    """
    directory = document_asset_directory(document_id)
    if DOCUMENT_DIRECTORY_PATTERN.fullmatch(directory.name) is None:
        raise ValueError("Invalid document image directory")
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        return False
    if _is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise LocalStorageUnavailable("Document image directory is not a real directory")
    root = assets_root()
    if _is_link(os.lstat(root)) or directory.resolve() != root.resolve() / directory.name:
        raise LocalStorageUnavailable("Document image directory escapes the uploads root")
    # On Linux rmtree uses descriptor-based traversal and unlinks (never follows)
    # nested symlinks; see shutil.rmtree.avoids_symlink_attacks.
    shutil.rmtree(directory)
    return True


def discard_local_document_images(document_id: int) -> None:
    """Best-effort cleanup after the document row is gone; failures are logged."""
    try:
        if delete_local_document_images(document_id):
            log_event(logger, logging.INFO, "document_images_deleted", document_id=document_id)
    except Exception as error:
        log_exception(logger, "delete_document_images", error, document_id=document_id)
