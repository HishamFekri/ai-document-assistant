"""Private shared storage for original documents and bounded worker materialization."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4

import cloudinary
import cloudinary.uploader
import cloudinary.utils
from dotenv import load_dotenv
import requests

from app.services.environment import is_production_environment
from app.services.document_resource_errors import DocumentResourceError


load_dotenv()
cloudinary.config(secure=True)


READ_CHUNK_BYTES = 1024 * 1024
CLOUDINARY_UPLOAD_CHUNK_BYTES = 20_000_000
REMOTE_TIMEOUT = (5, 30)
MAX_DOWNLOAD_SECONDS = 120
SIGNED_URL_SECONDS = 300
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".txt"}
STORAGE_KEY_PATTERN = re.compile(
    r"ai-document-assistant/originals/[0-9a-f]{32}\.(?:pdf|docx|xlsx|txt)"
)


@dataclass(frozen=True)
class StoredOriginal:
    file_path: str | None
    storage_key: str | None
    file_size_bytes: int
    file_sha256: str


class _NonClosingStream:
    """Let Cloudinary chunk a request-owned UploadFile without closing it."""

    def __init__(self, source, filename: str):
        self.source = source
        self.name = filename

    def read(self, size=-1):
        return self.source.read(size)

    def seek(self, offset, whence=0):
        return self.source.seek(offset, whence)

    def tell(self):
        return self.source.tell()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def uses_shared_original_storage() -> bool:
    """Production and staging have separate web/worker filesystems."""
    return is_production_environment()


def _validated_extension(extension: str) -> str:
    normalized = extension.lower()
    if normalized not in SUPPORTED_EXTENSIONS:
        raise ValueError("Unsupported original document extension")
    return normalized


def _hash_source(source, expected_size: int, max_size: int) -> str:
    digest = hashlib.sha256()
    size = 0
    source.seek(0)
    try:
        while chunk := source.read(READ_CHUNK_BYTES):
            size += len(chunk)
            if size > max_size:
                raise DocumentResourceError("file_size")
            digest.update(chunk)
    finally:
        source.seek(0)
    if size != expected_size:
        raise DocumentResourceError("file_size")
    return digest.hexdigest()


def _cloudinary_config():
    config = cloudinary.config()
    if not config.cloud_name or not config.api_key or not config.api_secret:
        raise RuntimeError("Private original storage is not configured")
    return config


def _valid_storage_key(storage_key: str) -> bool:
    return isinstance(storage_key, str) and STORAGE_KEY_PATTERN.fullmatch(storage_key) is not None


def _delete_cloudinary_original(storage_key: str) -> None:
    if not _valid_storage_key(storage_key):
        raise ValueError("Invalid original storage key")
    _cloudinary_config()
    result = cloudinary.uploader.destroy(
        storage_key,
        resource_type="raw",
        type="authenticated",
        invalidate=False,
        timeout=30,
    )
    if result.get("result") not in {"ok", "not found"}:
        raise RuntimeError("Private original storage deletion failed")


def store_original(source, extension: str, expected_size: int, max_size: int, upload_dir: Path) -> StoredOriginal:
    extension = _validated_extension(extension)
    checksum = _hash_source(source, expected_size, max_size)

    if uses_shared_original_storage():
        _cloudinary_config()
        storage_key = f"ai-document-assistant/originals/{uuid4().hex}{extension}"
        try:
            result = cloudinary.uploader.upload_large(
                _NonClosingStream(source, storage_key.rsplit("/", 1)[-1]),
                public_id=storage_key,
                resource_type="raw",
                type="authenticated",
                overwrite=False,
                unique_filename=False,
                use_filename=False,
                discard_original_filename=True,
                chunk_size=CLOUDINARY_UPLOAD_CHUNK_BYTES,
                timeout=60,
            )
            try:
                stored_size = int(result.get("bytes"))
            except (TypeError, ValueError):
                stored_size = -1
            if (
                result.get("public_id") != storage_key
                or result.get("resource_type") != "raw"
                or result.get("type") != "authenticated"
                or stored_size != expected_size
            ):
                raise RuntimeError("Private original storage verification failed")
        except BaseException:
            # The upload response can be invalid or ambiguous. An idempotent delete
            # is the safest compensation and never creates a processable DB row.
            try:
                _delete_cloudinary_original(storage_key)
            except Exception:
                pass
            raise
        finally:
            source.seek(0)
        return StoredOriginal(None, storage_key, expected_size, checksum)

    upload_dir.mkdir(exist_ok=True)
    file_path = upload_dir / f"{uuid4().hex}{extension}"
    bytes_written = 0
    source.seek(0)
    try:
        with open(file_path, "xb") as destination:
            while chunk := source.read(READ_CHUNK_BYTES):
                bytes_written += len(chunk)
                if bytes_written > max_size:
                    raise DocumentResourceError("file_size")
                destination.write(chunk)
        if bytes_written != expected_size:
            raise DocumentResourceError("file_size")
    except BaseException:
        file_path.unlink(missing_ok=True)
        raise
    finally:
        source.seek(0)
    return StoredOriginal(str(file_path), None, bytes_written, checksum)


def delete_stored_original(storage_key: str) -> None:
    _delete_cloudinary_original(storage_key)


def _signed_download_url(storage_key: str, file_type: str) -> str:
    if not _valid_storage_key(storage_key):
        raise ValueError("Invalid original storage key")
    extension = _validated_extension("." + file_type).lstrip(".")
    config = _cloudinary_config()
    url = cloudinary.utils.private_download_url(
        storage_key,
        extension,
        resource_type="raw",
        type="authenticated",
        expires_at=int(time.time()) + SIGNED_URL_SECONDS,
    )
    parsed = urlsplit(url)
    expected_path = f"/v1_1/{config.cloud_name}/raw/download"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "api.cloudinary.com"
        or parsed.path != expected_path
        or not parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Private original storage generated an invalid download URL")
    return url


def _download_original(path: Path, storage_key: str, file_type: str, expected_size: int, checksum: str, max_size: int) -> None:
    if (
        not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size <= 0
        or expected_size > max_size
    ):
        raise ValueError("Invalid stored original size")
    if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
        raise ValueError("Invalid stored original checksum")

    url = _signed_download_url(storage_key, file_type)
    digest = hashlib.sha256()
    size = 0
    started = time.monotonic()
    with requests.Session() as session:
        session.trust_env = False
        with session.get(
            url,
            stream=True,
            allow_redirects=False,
            timeout=REMOTE_TIMEOUT,
            headers={"Accept-Encoding": "identity"},
        ) as remote:
            if remote.status_code != 200:
                raise requests.HTTPError(
                    "Stored original could not be downloaded",
                    response=remote,
                )
            if remote.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise ValueError("Unsupported stored original encoding")
            length = remote.headers.get("Content-Length")
            if length is not None:
                if re.fullmatch(r"[0-9]+", length) is None or len(length) > 12:
                    raise ValueError("Invalid stored original length")
                if int(length) != expected_size:
                    raise ValueError("Stored original size does not match")
            with open(path, "wb") as destination:
                for chunk in remote.iter_content(chunk_size=READ_CHUNK_BYTES):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > expected_size or size > max_size:
                        raise ValueError("Stored original exceeds its expected size")
                    if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                        raise requests.Timeout("Stored original download timed out")
                    digest.update(chunk)
                    destination.write(chunk)

    if size != expected_size:
        raise ValueError("Stored original download is incomplete")
    if not hmac.compare_digest(digest.hexdigest(), checksum):
        raise ValueError("Stored original checksum does not match")


@contextmanager
def materialize_original(
    *, file_path: str | None, storage_key: str | None, file_type: str | None,
    expected_size: int | None, checksum: str | None, max_size: int,
):
    if not storage_key:
        if not file_path:
            raise FileNotFoundError("Document source is unavailable")
        yield Path(file_path)
        return

    file_type = file_type or ""
    extension = _validated_extension("." + file_type)
    descriptor, temporary_name = tempfile.mkstemp(prefix="document-original-", suffix=extension)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        _download_original(
            temporary_path,
            storage_key,
            file_type,
            expected_size,
            checksum,
            max_size,
        )
        yield temporary_path
    finally:
        temporary_path.unlink(missing_ok=True)
