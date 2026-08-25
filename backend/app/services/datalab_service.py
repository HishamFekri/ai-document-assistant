import base64
import binascii
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()


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


if not DATALAB_API_KEY:
    raise RuntimeError(
        "DATALAB_API_KEY is not set"
    )


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
    last_error: Exception | None = None

    for attempt in range(
        1,
        MAX_UPLOAD_RETRIES + 1,
    ):
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

            last_error = RuntimeError(
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
            )

            if (
                response.ok
                or not should_retry(
                    response.status_code
                )
            ):
                return response

            last_error = RuntimeError(
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
):
    path = Path(
        file_path
    )

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

    logger.info(
        "Uploading document to Datalab: %s",
        path.name,
    )

    if page_range:
        logger.info(
            "Datalab page range: %s",
            page_range,
        )

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
            )

    except OSError as error:
        logger.exception(
            "Could not read document for Datalab"
        )

        raise RuntimeError(
            "Could not read document for processing"
        ) from error

    if not response.ok:
        logger.error(
            "Datalab upload rejected with status %s",
            response.status_code,
        )

        raise RuntimeError(
            "Datalab upload was rejected"
        )

    try:
        submit_result = response.json()

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
        logger.debug(
            "Datalab request ID: %s",
            request_id,
        )

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
            logger.error(
                "Datalab polling rejected with status %s",
                result_response.status_code,
            )

            raise RuntimeError(
                "Datalab polling failed"
            )

        try:
            result = (
                result_response.json()
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

        logger.debug(
            "Datalab poll %s/%s status=%s",
            poll_number,
            MAX_POLLS,
            status,
        )

        if status == "complete":
            total_time = (
                time.perf_counter()
                - start_time
            )

            images = (
                result.get("images")
                or {}
            )

            logger.info(
                (
                    "Datalab conversion complete "
                    "pages=%s quality=%s images=%s "
                    "duration=%.2fs"
                ),
                result.get("page_count"),
                result.get(
                    "parse_quality_score"
                ),
                len(images),
                total_time,
            )

            logger.debug(
                "Datalab cost breakdown: %s",
                result.get(
                    "cost_breakdown"
                ),
            )

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
):
    result = convert_document_with_datalab(
        file_path=file_path,
        page_range=page_range,
        mode="balanced",
        output_format="json",
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


def save_datalab_images(
    images: dict,
    output_directory,
):
    output_path = Path(
        output_directory
    )

    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    saved_images = {}

    for filename, encoded_image in images.items():
        if not encoded_image:
            continue

        safe_filename = (
            Path(filename).name
        )

        if not safe_filename:
            continue

        image_path = (
            output_path
            / safe_filename
        )

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
            logger.warning(
                "Could not decode Datalab image: %s",
                safe_filename,
            )

            continue

        try:
            with open(
                image_path,
                "wb",
            ) as image_file:
                image_file.write(
                    image_bytes
                )

        except OSError:
            logger.exception(
                "Could not save Datalab image: %s",
                safe_filename,
            )

            continue

        saved_images[
            safe_filename
        ] = str(
            image_path
        )

    logger.info(
        "Saved %s Datalab images",
        len(saved_images),
    )

    return saved_images