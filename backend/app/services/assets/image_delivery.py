"""Controlled retrieval of stored document images, called only after ownership checks."""

import mimetypes
import os
import re
import time
from pathlib import Path, PureWindowsPath
from tempfile import SpooledTemporaryFile
from urllib.parse import urlsplit

import cloudinary
import cloudinary.utils
import requests
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask


PRIVATE_IMAGE_HEADERS = {
    "Cache-Control": "private, no-cache",
    "Vary": "Cookie, Authorization, Origin",
    "X-Content-Type-Options": "nosniff",
}
ASSET_ROOT = Path("uploads/assets")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
SPOOL_MEMORY_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
REMOTE_TIMEOUT = (5, 15)
MAX_FETCH_SECONDS = 60
IMAGE_MEDIA_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/avif", "image/bmp", "image/tiff",
}


def image_error(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail, headers=PRIVATE_IMAGE_HEADERS)


def document_asset_folders(document) -> set[str]:
    folders = {f"document_{document.id}"}
    # Before a DB ID was passed into extraction, the upload's stem was used.
    if document.file_path:
        stem = PureWindowsPath(str(document.file_path)).stem
        if stem and stem not in {".", ".."} and not any(c in stem for c in "/\\:"):
            folders.add(stem)
    return folders


def cloudinary_fetch_url(stored_url: str, document) -> str:
    """Accept only our configured account's original document images, never fetch URLs."""
    config = cloudinary.config()
    cloud_name = config.cloud_name
    try:
        parsed = urlsplit(stored_url)
        valid = (
            cloud_name and re.fullmatch(r"[A-Za-z0-9_-]+", cloud_name)
            and parsed.scheme == "https"
            and parsed.netloc == "res.cloudinary.com"
            and not parsed.query and not parsed.fragment
            and not any(ord(c) < 32 for c in stored_url)
        )
        if not valid:
            raise ValueError
        # No percent escapes, transformations, dot segments, userinfo, ports,
        # alternate accounts or remote-fetch delivery types are accepted.
        match = re.fullmatch(
            rf"/{re.escape(cloud_name)}/image/(upload|private|authenticated)/"
            r"(?:s--[A-Za-z0-9_-]+--/)?(?:v([0-9]+)/)?"
            r"(ai-document-assistant/([A-Za-z0-9_-]+)/[A-Za-z0-9_-]+)\.([A-Za-z0-9]+)",
            parsed.path,
        )
        if match is None or match[4] not in document_asset_folders(document):
            raise ValueError
    except (TypeError, ValueError):
        raise image_error(400, "Invalid image storage location") from None

    delivery_type, version, public_id, _, image_format = match.groups()
    if delivery_type == "upload":
        return stored_url

    if not config.api_key or not config.api_secret:
        raise image_error(503, "Private image storage is not configured")
    # Generate a signed CDN URL only on the server. Explicit host/account options
    # prevent SDK defaults from expanding the permitted retrieval origins.
    signed_url, _ = cloudinary.utils.cloudinary_url(
        public_id, format=image_format, version=version,
        type=delivery_type, resource_type="image", secure=True, sign_url=True,
        cloud_name=cloud_name, api_secret=config.api_secret,
        secure_distribution="res.cloudinary.com", private_cdn=False,
        cname=None, cdn_subdomain=False, secure_cdn_subdomain=False,
        url_suffix=None, use_root_path=False, shorten=False,
    )
    if urlsplit(signed_url).netloc != "res.cloudinary.com":
        raise image_error(503, "Private image storage is not configured")
    return signed_url


def remote_image_response(stored_url: str, document):
    fetch_url = cloudinary_fetch_url(stored_url, document)
    spool = SpooledTemporaryFile(max_size=SPOOL_MEMORY_BYTES, mode="w+b")
    started = time.monotonic()
    try:
        # No client credentials/cookies or implicit .netrc/environment proxies.
        with requests.Session() as session:
            session.trust_env = False
            with session.get(
                fetch_url, stream=True, allow_redirects=False,
                timeout=REMOTE_TIMEOUT, headers={"Accept-Encoding": "identity"},
            ) as remote:
                if remote.status_code != 200:
                    raise image_error(502, "Could not load stored image")
                media_type = remote.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if media_type not in IMAGE_MEDIA_TYPES:
                    raise image_error(502, "Invalid stored image media type")
                if remote.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise image_error(502, "Unsupported stored image encoding")
                length = remote.headers.get("Content-Length")
                if length is not None:
                    if not re.fullmatch(r"[0-9]+", length) or len(length) > 12:
                        raise image_error(502, "Invalid stored image length")
                    if int(length) > MAX_IMAGE_BYTES:
                        raise image_error(502, "Stored image exceeds delivery size limit")
                size = 0
                for chunk in remote.iter_content(chunk_size=READ_CHUNK_BYTES):
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise image_error(502, "Stored image exceeds delivery size limit")
                    if time.monotonic() - started > MAX_FETCH_SECONDS:
                        raise image_error(502, "Stored image retrieval timed out")
                    spool.write(chunk)
                if not size or (length is not None and size != int(length)):
                    raise image_error(502, "Incomplete stored image")
        spool.seek(0)
    except requests.RequestException:
        spool.close()
        raise image_error(502, "Could not load stored image") from None
    except BaseException:
        spool.close()
        raise

    def body():
        try:
            while chunk := spool.read(READ_CHUNK_BYTES):
                yield chunk
        finally:
            spool.close()

    return StreamingResponse(
        body(), media_type=media_type,
        headers={**PRIVATE_IMAGE_HEADERS, "Content-Length": str(size)},
        background=BackgroundTask(spool.close),
    )


def local_image_response(stored_path: str, document):
    try:
        # Reject UNC/device paths before resolving, so even path validation
        # cannot initiate a Windows network share connection.
        if (
            stored_path.startswith(("\\", "//"))
            or "\x00" in stored_path
            or (os.name != "nt" and ("\\" in stored_path or PureWindowsPath(stored_path).drive))
            or ":" in stored_path[2:]
        ):
            raise ValueError
        root = ASSET_ROOT.resolve()
        path = Path(stored_path).resolve()
        contained = False
        for folder in document_asset_folders(document):
            # Keep the expected directory lexical under the canonical root.
            # Resolving it too would allow a document-directory symlink to point
            # at another document's assets and silently redefine containment.
            directory = root / folder
            if path.is_relative_to(directory):
                contained = True
        if not contained:
            raise ValueError
    except (OSError, RuntimeError, ValueError):
        raise image_error(400, "Invalid image storage location") from None
    if not path.is_file():
        raise image_error(404, "Image file not found")
    media_type = mimetypes.guess_type(path.name)[0]
    if media_type not in IMAGE_MEDIA_TYPES:
        raise image_error(400, "Unsupported image file type")
    return FileResponse(path, media_type=media_type, headers=PRIVATE_IMAGE_HEADERS)


def image_file_response(stored_path, document):
    if not isinstance(stored_path, str) or not stored_path.strip():
        raise image_error(404, "Image file path not found")
    stored_path = stored_path.strip()
    if "://" in stored_path:
        return remote_image_response(stored_path, document)
    return local_image_response(stored_path, document)
