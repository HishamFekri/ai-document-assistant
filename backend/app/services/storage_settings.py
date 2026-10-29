"""Storage backend selection and the persistent local uploads root.

STORAGE_BACKEND=cloudinary (default) keeps the existing deployment behavior:
production/staging originals and all extracted images live in Cloudinary.
STORAGE_BACKEND=local keeps both on the filesystem under UPLOADS_ROOT, which a
self-hosted deployment mounts as a persistent volume.
"""

import os
from pathlib import Path
import stat
import tempfile

from app.services.environment import is_production_environment


STORAGE_BACKENDS = {"cloudinary", "local"}
DEFAULT_UPLOADS_ROOT = "uploads"
# Created by the operator on the mounted volume. Its absence means the volume is
# not mounted, and the root is an empty placeholder directory instead.
STORAGE_SENTINEL_NAME = ".storage-sentinel"


class LocalStorageUnavailable(RuntimeError):
    """The persistent uploads root is missing, unmounted or not writable."""


def storage_backend() -> str:
    value = os.getenv("STORAGE_BACKEND", "cloudinary").strip().lower() or "cloudinary"
    if value not in STORAGE_BACKENDS:
        raise ValueError("STORAGE_BACKEND must be cloudinary or local")
    return value


def uses_local_storage() -> bool:
    return storage_backend() == "local"


def uploads_root() -> Path:
    return Path(os.getenv("UPLOADS_ROOT", DEFAULT_UPLOADS_ROOT).strip() or DEFAULT_UPLOADS_ROOT)


def assets_root() -> Path:
    return uploads_root() / "assets"


def document_asset_directory(document_id: int) -> Path:
    """The only directory one document's extracted images may be written to."""
    if not isinstance(document_id, int) or isinstance(document_id, bool) or document_id <= 0:
        raise ValueError("Invalid document ID")
    return assets_root() / f"document_{document_id}"


def requires_storage_sentinel() -> bool:
    return uses_local_storage() and is_production_environment()


def validate_local_storage_root(root: Path | None = None) -> Path:
    """Fail closed unless the root is an existing, mounted and writable directory."""
    root = uploads_root() if root is None else root
    try:
        if not root.is_dir():
            raise LocalStorageUnavailable("Uploads root is missing or not a directory")
        sentinel = os.lstat(root / STORAGE_SENTINEL_NAME)
    except FileNotFoundError:
        raise LocalStorageUnavailable("Uploads root storage sentinel is missing") from None
    except OSError:
        raise LocalStorageUnavailable("Uploads root is not accessible") from None
    if not stat.S_ISREG(sentinel.st_mode):
        raise LocalStorageUnavailable("Uploads root storage sentinel must be a regular file")
    try:
        descriptor, probe = tempfile.mkstemp(prefix=".write-probe-", dir=root)
        os.close(descriptor)
        os.unlink(probe)
    except OSError:
        raise LocalStorageUnavailable("Uploads root is not writable") from None
    return root


def ensure_local_storage_ready() -> Path:
    """Checked before each local write so an unmounted volume is never written to."""
    root = uploads_root()
    if requires_storage_sentinel():
        try:
            sentinel = os.lstat(root / STORAGE_SENTINEL_NAME)
        except OSError:
            raise LocalStorageUnavailable("Uploads root storage sentinel is missing") from None
        if not stat.S_ISREG(sentinel.st_mode):
            raise LocalStorageUnavailable("Uploads root storage sentinel must be a regular file")
        return root
    root.mkdir(parents=True, exist_ok=True)
    return root
