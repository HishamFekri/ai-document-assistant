from app.services.observability import log_event, log_exception, submit_observed
import base64
from concurrent.futures import ThreadPoolExecutor
import binascii
import hashlib
import io
import json
import logging
import os
import random
import threading
import time
from pathlib import Path

import cloudinary
import cloudinary.exceptions
import cloudinary.uploader
import requests
import urllib3.exceptions
from dotenv import load_dotenv
from app.services.document_processing_errors import RetryableDocumentProcessingError
from app.services.document_resource_errors import DocumentResourceError
from app.services.resource_limits import upload_limits


load_dotenv()


CLOUDINARY_URL = os.getenv(
    "CLOUDINARY_URL"
)

if not CLOUDINARY_URL:
    raise RuntimeError(
        "CLOUDINARY_URL is not set"
    )

# The Cloudinary Python SDK reads CLOUDINARY_URL
# automatically from the environment.
cloudinary.config(
    secure=True
)


logger = logging.getLogger(__name__)


DATALAB_API_KEY = os.getenv(
    "DATALAB_API_KEY"
)

DATALAB_CONVERT_URL = os.getenv(
    "DATALAB_CONVERT_URL",
    "https://www.datalab.to/api/v1/convert",
)

UPLOAD_TIMEOUT_SECONDS = int(
    os.getenv(
        "DATALAB_UPLOAD_TIMEOUT_SECONDS",
        "120",
    )
)

POLL_TIMEOUT_SECONDS = int(
    os.getenv(
        "DATALAB_POLL_TIMEOUT_SECONDS",
        "30",
    )
)

MAX_UPLOAD_RETRIES = int(
    os.getenv(
        "DATALAB_MAX_UPLOAD_RETRIES",
        "3",
    )
)

MAX_POLL_RETRIES = int(
    os.getenv(
        "DATALAB_MAX_POLL_RETRIES",
        "3",
    )
)

POLL_INTERVAL_SECONDS = float(
    os.getenv(
        "DATALAB_POLL_INTERVAL_SECONDS",
        "2",
    )
)

MAX_POLLS = int(
    os.getenv(
        "DATALAB_MAX_POLLS",
        "300",
    )
)

# Simultaneous Cloudinary uploads of extracted images per batch (bounded).
IMAGE_UPLOAD_CONCURRENCY = int(
    os.getenv(
        "DATALAB_IMAGE_UPLOAD_CONCURRENCY",
        "4",
    )
)

if not 1 <= IMAGE_UPLOAD_CONCURRENCY <= 16:
    raise RuntimeError(
        "DATALAB_IMAGE_UPLOAD_CONCURRENCY must be between 1 and 16"
    )

# Finite per-attempt socket timeout for each Cloudinary image upload.
IMAGE_UPLOAD_TIMEOUT_SECONDS = float(
    os.getenv(
        "DATALAB_IMAGE_UPLOAD_TIMEOUT_SECONDS",
        "30",
    )
)

if not 1 <= IMAGE_UPLOAD_TIMEOUT_SECONDS <= 300:
    raise RuntimeError(
        "DATALAB_IMAGE_UPLOAD_TIMEOUT_SECONDS must be between 1 and 300"
    )

# Total attempts per distinct image; only transient failures are retried.
IMAGE_UPLOAD_MAX_ATTEMPTS = int(
    os.getenv(
        "DATALAB_IMAGE_UPLOAD_MAX_ATTEMPTS",
        "3",
    )
)

if not 1 <= IMAGE_UPLOAD_MAX_ATTEMPTS <= 6:
    raise RuntimeError(
        "DATALAB_IMAGE_UPLOAD_MAX_ATTEMPTS must be between 1 and 6"
    )

IMAGE_UPLOAD_BACKOFF_SECONDS = 1.0
IMAGE_UPLOAD_MAX_BACKOFF_SECONDS = 8.0


if not DATALAB_API_KEY:
    raise RuntimeError(
        "DATALAB_API_KEY is not set"
    )


def read_bounded_json(response):
    limit = upload_limits().datalab_response_bytes
    data = bytearray()
    try:
        for part in response.iter_content(chunk_size=64 * 1024):
            if len(data) + len(part) > limit:
                raise DocumentResourceError("content_limit")
            data.extend(part)
        return json.loads(data)
    finally:
        response.close()


def should_retry(
    status_code: int,
) -> bool:
    return (
        status_code == 429
        or status_code >= 500
    )


def post_with_retry(
    url: str,
    **kwargs,
):
    streams = []
    for value in (kwargs.get('files') or {}).values():
        stream = value[1] if isinstance(value, tuple) else value
        if hasattr(stream, 'read'):
            if not stream.seekable():
                raise ValueError('Datalab retry requires a seekable upload')
            streams.append(stream)
    last_error: Exception | None = None

    for attempt in range(
        1,
        MAX_UPLOAD_RETRIES + 1,
    ):
        # requests rebuilds multipart encoding from this rewound file per call.
        for stream in streams:
            stream.seek(0)
        try:
            response = requests.post(
                url,
                timeout=UPLOAD_TIMEOUT_SECONDS,
                **kwargs,
            )

            if (
                response.ok
                or not should_retry(
                    response.status_code
                )
            ):
                return response

            response.close()
            last_error = RetryableDocumentProcessingError(
                "Temporary Datalab upload error "
                f"({response.status_code})"
            )

        except requests.RequestException as error:
            last_error = error

        if attempt < MAX_UPLOAD_RETRIES:
            wait_time = attempt * 2

            logger.warning(
                "Datalab upload retry %s/%s in %ss",
                attempt,
                MAX_UPLOAD_RETRIES,
                wait_time,
            )
            log_event(logger, logging.INFO, "datalab_retry", operation="datalab_upload",
                      retry=attempt, delay_seconds=wait_time)

            time.sleep(
                wait_time
            )

    logger.error(
        "Datalab upload failed after %s attempts",
        MAX_UPLOAD_RETRIES,
    )

    raise RuntimeError(
        "Datalab upload failed after retries"
    ) from last_error


def get_with_retry(
    url: str,
    headers: dict,
):
    last_error: Exception | None = None

    for attempt in range(
        1,
        MAX_POLL_RETRIES + 1,
    ):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=POLL_TIMEOUT_SECONDS,
                stream=True,
            )

            if (
                response.ok
                or not should_retry(
                    response.status_code
                )
            ):
                return response

            response.close()
            last_error = RetryableDocumentProcessingError(
                "Temporary Datalab polling error "
                f"({response.status_code})"
            )

        except requests.RequestException as error:
            last_error = error

        if attempt < MAX_POLL_RETRIES:
            wait_time = attempt * 2

            logger.warning(
                "Datalab poll retry %s/%s in %ss",
                attempt,
                MAX_POLL_RETRIES,
                wait_time,
            )
            log_event(logger, logging.INFO, "datalab_retry", operation="datalab_poll",
                      retry=attempt, delay_seconds=wait_time)

            time.sleep(
                wait_time
            )

    logger.error(
        "Datalab polling failed after %s attempts",
        MAX_POLL_RETRIES,
    )

    raise RuntimeError(
        "Datalab polling failed after retries"
    ) from last_error


def convert_document_with_datalab(
    file_path,
    page_range: str | None = None,
    mode: str = "balanced",
    output_format: str = "json",
    *, admission=None,
):
    path = Path(
        file_path
    )
    if admission is None:
        raise DocumentResourceError("advanced_pages")
    admission.consume(path, page_range)

    if not path.exists():
        raise FileNotFoundError(
            f"File not found: {path.name}"
        )

    if not path.is_file():
        raise ValueError(
            "Datalab input path must be a file"
        )

    headers = {
        "X-API-Key": DATALAB_API_KEY
    }

    data = {
        "output_format": output_format,
        "mode": mode,
        "disable_image_extraction": "false",
        "disable_image_captions": "false",
        "extras": (
            "chart_understanding,"
            "new_block_types"
        ),
    }

    if page_range:
        data["page_range"] = page_range

    log_event(logger, logging.INFO, "datalab_upload_started")

    if page_range:
        log_event(logger, logging.INFO, "datalab_page_range_selected")

    start_time = time.perf_counter()

    try:
        with open(
            path,
            "rb",
        ) as file:
            response = post_with_retry(
                DATALAB_CONVERT_URL,
                headers=headers,
                files={
                    "file": (
                        path.name,
                        file,
                        "application/pdf",
                    )
                },
                data=data,
                stream=True,
            )

    except OSError as error:
        log_exception(logger, 'read_document_for_datalab')

        raise RuntimeError(
            "Could not read document for processing"
        ) from error

    if not response.ok:
        response.close()
        logger.error(
            "Datalab upload rejected with status %s",
            response.status_code,
        )

        raise RuntimeError(
            "Datalab upload was rejected"
        )

    try:
        submit_result = read_bounded_json(response)

    except ValueError as error:
        logger.error(
            "Datalab upload returned invalid JSON"
        )

        raise RuntimeError(
            "Datalab returned invalid JSON"
        ) from error

    if not submit_result.get(
        "success",
        False,
    ):
        logger.error(
            "Datalab rejected document"
        )

        raise RuntimeError(
            "Datalab rejected the document"
        )

    check_url = submit_result.get(
        "request_check_url"
    )

    if not check_url:
        logger.error(
            "Datalab response did not include request_check_url"
        )

        raise RuntimeError(
            "Datalab returned an incomplete response"
        )

    request_id = submit_result.get(
        "request_id"
    )

    if request_id:
        log_event(logger, logging.DEBUG, "datalab_request_accepted")

    for poll_number in range(
        1,
        MAX_POLLS + 1,
    ):
        time.sleep(
            POLL_INTERVAL_SECONDS
        )

        result_response = get_with_retry(
            check_url,
            headers,
        )

        if not result_response.ok:
            result_response.close()
            logger.error(
                "Datalab polling rejected with status %s",
                result_response.status_code,
            )

            raise RuntimeError(
                "Datalab polling failed"
            )

        try:
            result = (
                read_bounded_json(result_response)
            )

        except ValueError as error:
            logger.error(
                "Datalab polling returned invalid JSON"
            )

            raise RuntimeError(
                "Datalab polling returned invalid JSON"
            ) from error

        status = result.get(
            "status"
        )

        log_event(logger, logging.DEBUG, "datalab_poll", count=poll_number)

        if status == "complete":
            total_time = (
                time.perf_counter()
                - start_time
            )

            images = (
                result.get("images")
                or {}
            )

            log_event(logger, logging.INFO, "datalab_conversion_completed", images=len(images), duration_ms=total_time * 1000)

            log_event(logger, logging.DEBUG, "datalab_conversion_usage_received")

            return result

        if status == "failed":
            logger.error(
                "Datalab conversion reported failure"
            )

            raise RuntimeError(
                "Datalab document conversion failed"
            )

    logger.error(
        "Datalab processing timed out after %s polls",
        MAX_POLLS,
    )

    raise TimeoutError(
        "Datalab processing exceeded "
        "the maximum wait time"
    )


def extract_content_with_datalab(
    file_path,
    page_range: str | None = None,
    *, admission=None,
):
    result = convert_document_with_datalab(
        file_path=file_path,
        page_range=page_range,
        mode="balanced",
        output_format="json",
        admission=admission,
    )

    document_json = result.get(
        "json"
    )

    if not document_json:
        raise ValueError(
            "Datalab returned no JSON content"
        )

    images = (
        result.get("images")
        or {}
    )

    return {
        "document_json": document_json,
        "images": images,
    }


class CloudinaryUploadRejected(Exception):
    """A JSON error response from the Upload API; carries only its HTTP status."""

    def __init__(self, status_code):
        super().__init__("Cloudinary rejected the image upload")
        self.status_code = status_code


def upload_failure_category(error):
    """(category, transient) for one failed attempt, from structured types only.

    The SDK raises RateLimited for 420/429, GeneralError for 500, and wraps
    urllib3/socket errors and unparseable (e.g. gateway HTML) responses in
    Error, chaining the original. Anything unrecognised is permanent.
    """
    if isinstance(error, CloudinaryUploadRejected):
        if error.status_code in (420, 429):
            return "rate_limited", True
        if 500 <= error.status_code <= 599:
            return "server_error", True
        return "rejected", False
    if isinstance(error, cloudinary.exceptions.RateLimited):
        return "rate_limited", True
    if isinstance(error, cloudinary.exceptions.GeneralError):
        return "server_error", True
    if type(error) is cloudinary.exceptions.Error:
        cause = error.__cause__ or error.__context__
        if isinstance(cause, urllib3.exceptions.MaxRetryError):
            cause = cause.reason or cause
        # urllib3 2.x derives NewConnectionError (e.g. refused) from ConnectTimeoutError.
        if isinstance(cause, urllib3.exceptions.NewConnectionError):
            return "network", True
        if isinstance(cause, (urllib3.exceptions.TimeoutError, TimeoutError)):
            return "timeout", True
        if isinstance(cause, (urllib3.exceptions.HTTPError, OSError)):
            return "network", True
        if isinstance(cause, ValueError):
            return "invalid_response", True
    return "rejected", False


def image_upload_backoff(attempt):
    """Exponential backoff with jitter before retry number `attempt` (1-based)."""
    delay = min(
        IMAGE_UPLOAD_MAX_BACKOFF_SECONDS,
        IMAGE_UPLOAD_BACKOFF_SECONDS * 2 ** (attempt - 1),
    )
    return random.uniform(delay / 2, delay)


def _upload_once(
    image_bytes: bytes,
    cloudinary_folder: str,
    public_id: str,
):
    upload_result = (
        cloudinary.uploader.upload(
            io.BytesIO(
                image_bytes
            ),
            folder=(
                cloudinary_folder
            ),
            public_id=(
                public_id
            ),
            resource_type="image",
            type="authenticated",
            overwrite=False,
            unique_filename=False,
            use_filename=False,
            timeout=IMAGE_UPLOAD_TIMEOUT_SECONDS,
            # JSON error responses come back with their HTTP status instead of
            # a generic exception, so 5xx can be told apart from 4xx.
            return_error=True,
        )
    )

    error = upload_result.get("error")

    if error:
        status_code = error.get("http_code") if isinstance(error, dict) else None
        raise CloudinaryUploadRejected(
            status_code if isinstance(status_code, int) else 0
        )

    return upload_result


def _upload_image(
    image_bytes: bytes,
    cloudinary_folder: str,
    public_id: str,
    retries_stopped: threading.Event | None = None,
):
    """One authenticated Cloudinary upload, retrying transient failures.

    Returns (secure_url or None, failure category or None, retries used).
    Once any upload in the same batch exhausts its retries on a transient
    failure, retries_stopped is set and later uploads get a single attempt,
    bounding the time spent during a Cloudinary outage.
    """
    retries = 0

    while True:
        try:
            upload_result = _upload_once(
                image_bytes,
                cloudinary_folder,
                public_id,
            )

        except Exception as error:
            category, transient = upload_failure_category(error)
            stopped = retries_stopped is not None and retries_stopped.is_set()

            if transient and not stopped and retries + 1 < IMAGE_UPLOAD_MAX_ATTEMPTS:
                retries += 1
                log_event(logger, logging.INFO, "datalab_image_upload_retry",
                          failure_category=category, retry=retries)
                time.sleep(image_upload_backoff(retries))

                continue

            if transient and retries_stopped is not None:
                retries_stopped.set()

            log_exception(logger, "datalab_image_upload", error,
                          failure_category=category, retry=retries)

            return None, category, retries

        break

    secure_url = (
        upload_result.get(
            "secure_url"
        )
    )

    if not secure_url:
        log_event(logger, logging.WARNING, "datalab_image_url_missing")

        return None, "url_missing", retries

    return str(
        secure_url
    ), None, retries


def save_datalab_images(
    images: dict,
    output_directory,
    upload_cache: dict | None = None,
    report: dict | None = None,
):
    """
    Upload newly extracted images with authenticated Cloudinary delivery.

    Duplicate image bytes generate the same content hash and
    therefore the same Cloudinary public_id. This prevents
    repeated page headers / decorative images from becoming
    separate assets only because their filenames differ. Existing objects are
    never overwritten; legacy public objects are left in their original type.

    Each distinct image is uploaded once, with at most
    IMAGE_UPLOAD_CONCURRENCY uploads in flight (retry backoff included).
    Transient failures are retried up to IMAGE_UPLOAD_MAX_ATTEMPTS; a distinct
    image that still fails is left out for every occurrence in this batch.
    upload_cache (scoped to one document's processing job) reuses URLs from
    earlier batches; failures are never cached, so a later batch tries again.

    report (optional) accumulates report["missing_images"]: image occurrences
    with data and a usable name that end without a stored asset (undecodable,
    upload failed, or no URL returned). Every occurrence of a failed duplicate
    counts, across all batches that share the dict.

    Return shape and order remain:
        {original_filename: cloudinary_secure_url}
    """
    output_path = (
        Path(output_directory)
    )

    document_folder = (
        output_path.parent.name
        or "document"
    )

    cloudinary_folder = (
        "ai-document-assistant/"
        f"{document_folder}"
    )

    # Decode in input order, skipping exactly what was skipped before.
    entries = []
    unique_images = {}
    undecodable = 0

    for (
        filename,
        encoded_image,
    ) in images.items():
        if not encoded_image:
            continue

        safe_filename = (
            Path(filename).name
        )

        if not safe_filename:
            continue

        try:
            image_bytes = (
                base64.b64decode(
                    encoded_image,
                    validate=True,
                )
            )

        except (
            binascii.Error,
            ValueError,
        ):
            log_exception(logger, "datalab_image_decode")
            undecodable += 1

            continue

        image_hash = (
            hashlib.sha256(
                image_bytes
            ).hexdigest()
        )

        entries.append((safe_filename, image_hash))
        unique_images.setdefault(image_hash, image_bytes)

    cache = {} if upload_cache is None else upload_cache
    urls = {
        image_hash: cache[(cloudinary_folder, image_hash)]
        for image_hash in unique_images
        if (cloudinary_folder, image_hash) in cache
    }
    pending = [
        image_hash
        for image_hash in unique_images
        if image_hash not in urls
    ]
    failed = 0
    retries = 0

    if pending:
        workers = min(IMAGE_UPLOAD_CONCURRENCY, len(pending))
        retries_stopped = threading.Event()
        # The executor joins every upload before results are read; no thread outlives this call.
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                image_hash: submit_observed(
                    executor,
                    _upload_image,
                    unique_images[image_hash],
                    cloudinary_folder,
                    f"img_{image_hash[:40]}",
                    retries_stopped,
                )
                for image_hash in pending
            }

        for image_hash, future in futures.items():
            secure_url, failure, used = future.result()
            retries += used

            if secure_url:
                urls[image_hash] = secure_url
            elif failure:
                failed += 1

    # Assemble in input order, independent of upload completion order.
    saved_images = {}

    for safe_filename, image_hash in entries:
        if image_hash in urls:
            saved_images[
                safe_filename
            ] = urls[image_hash]

    for image_hash, secure_url in urls.items():
        cache[(cloudinary_folder, image_hash)] = secure_url

    missing = undecodable + sum(
        1
        for _, image_hash in entries
        if image_hash not in urls
    )

    if report is not None:
        report["missing_images"] = report.get("missing_images", 0) + missing

    log_event(logger, logging.INFO, "datalab_images_uploaded",
              images=len(saved_images), count=len(pending),
              failed=failed, missing=missing, retry=retries)

    if missing:
        # Visible, ID-free signal that this batch's output lacks image assets.
        log_event(logger, logging.WARNING, "datalab_images_upload_incomplete",
                  failed=failed, missing=missing, images=len(saved_images))

    logger.info(
        "Uploaded %s Datalab images "
        "to Cloudinary",
        len(saved_images),
    )

    return saved_images
