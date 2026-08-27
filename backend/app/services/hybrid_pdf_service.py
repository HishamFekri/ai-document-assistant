import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from pypdf import PdfReader

from app.services.page_classifier_service import (
    is_complex_page,
)

from app.services.datalab_service import (
    extract_content_with_datalab,
    save_datalab_images,
)


load_dotenv()


logger = logging.getLogger(__name__)


MAX_PDF_PAGES = int(
    os.getenv(
        "MAX_PDF_PAGES",
        "500",
    )
)

MAX_DATALAB_PAGES = int(
    os.getenv(
        "MAX_DATALAB_PAGES",
        "20",
    )
)

MAX_DATALAB_RATIO = float(
    os.getenv(
        "MAX_DATALAB_RATIO",
        "0.35",
    )
)


DATALAB_PARALLEL_BATCHES = int(
    os.getenv(
        "DATALAB_PARALLEL_BATCHES",
        "2",
    )
)


def create_pypdf_block(
    text: str,
    page_number: int,
):
    return {
        "type": "text",
        "content": text.strip(),
        "location": f"Page {page_number}",
        "metadata": {
            "page": page_number,
            "parser": "pypdf",
        },
    }


def build_page_range(
    pages: list[int],
) -> str:
    if not pages:
        return ""

    zero_based_pages = sorted(
        page_number - 1
        for page_number in pages
    )

    ranges = []

    start = zero_based_pages[0]
    end = start

    for page in zero_based_pages[1:]:
        if page == end + 1:
            end = page
            continue

        if start == end:
            ranges.append(
                str(start)
            )
        else:
            ranges.append(
                f"{start}-{end}"
            )

        start = page
        end = page

    if start == end:
        ranges.append(
            str(start)
        )
    else:
        ranges.append(
            f"{start}-{end}"
        )

    return ",".join(
        ranges
    )


def classify_pdf_pages(
    reader: PdfReader,
):
    simple_blocks = []
    complex_pages = []

    for page_number, page in enumerate(
        reader.pages,
        start=1,
    ):
        try:
            text = (
                page.extract_text()
                or ""
            ).strip()

        except Exception:
            logger.warning(
                "Could not extract text from PDF page %s",
                page_number,
            )

            text = ""

        if is_complex_page(
            page=page,
            text=text,
        ):
            complex_pages.append(
                page_number
            )

        elif text:
            simple_blocks.append(
                create_pypdf_block(
                    text=text,
                    page_number=page_number,
                )
            )

    return (
        simple_blocks,
        complex_pages,
    )


def validate_processing_cost(
    total_pages: int,
    complex_pages: list[int],
):
    if total_pages > MAX_PDF_PAGES:
        raise ValueError(
            (
                f"PDF contains {total_pages} pages. "
                f"Maximum allowed is "
                f"{MAX_PDF_PAGES} pages."
            )
        )

    complex_count = len(
        complex_pages
    )

    if complex_count == 0:
        logger.info(
            "No advanced PDF processing required"
        )
        return

    complex_ratio = (
        complex_count
        / total_pages
    )

    batch_count = (
        complex_count
        + MAX_DATALAB_PAGES
        - 1
    ) // MAX_DATALAB_PAGES

    logger.info(
        (
            "Advanced PDF processing "
            "pages=%s/%s ratio=%.2f%% "
            "batch_size=%s batches=%s"
        ),
        complex_count,
        total_pages,
        complex_ratio * 100,
        MAX_DATALAB_PAGES,
        batch_count,
    )

    if complex_ratio > MAX_DATALAB_RATIO:
        logger.warning(
            (
                "Complex page ratio %.2f%% exceeds "
                "preferred threshold %.2f%%"
            ),
            complex_ratio * 100,
            MAX_DATALAB_RATIO * 100,
        )


def split_page_batches(
    pages: list[int],
    batch_size: int,
) -> list[list[int]]:
    if batch_size <= 0:
        raise ValueError(
            "Datalab batch size must be greater than zero"
        )

    return [
        pages[
            index:index + batch_size
        ]
        for index in range(
            0,
            len(pages),
            batch_size,
        )
    ]


def get_block_content(
    block: dict,
) -> str:
    possible_values = [
        block.get("content"),
        block.get("text"),
        block.get("markdown"),
        block.get("html"),
        block.get("caption"),
        block.get("description"),
    ]

    for value in possible_values:
        if value is None:
            continue

        if isinstance(
            value,
            list,
        ):
            value = "\n".join(
                str(item)
                for item in value
            )

        if not isinstance(
            value,
            str,
        ):
            value = str(
                value
            )

        value = value.strip()

        if value:
            return value

    return ""


def normalize_block_type(
    block: dict,
) -> str:
    raw_type = (
        block.get("block_type")
        or block.get("type")
        or block.get("label")
        or "text"
    )

    raw_type = str(
        raw_type
    ).lower()

    if "table" in raw_type:
        return "table"

    if (
        "formula" in raw_type
        or "equation" in raw_type
        or "math" in raw_type
    ):
        return "formula"

    if (
        "picture" in raw_type
        or "image" in raw_type
        or "figure" in raw_type
        or "chart" in raw_type
        or "diagram" in raw_type
    ):
        return "image"

    if "code" in raw_type:
        return "code"

    return "text"


def get_datalab_page_number(
    child: dict,
    metadata: dict,
):
    possible_values = [
        child.get("page"),
        child.get("page_number"),
        child.get("page_id"),
        metadata.get("page"),
        metadata.get("page_number"),
        metadata.get("page_id"),
    ]

    for value in possible_values:
        if value is not None:
            return value

    return None


def resolve_original_page(
    page_number,
    complex_pages: list[int],
):
    if page_number is None:
        return None

    if isinstance(
        page_number,
        str,
    ):
        try:
            page_number = int(
                page_number
            )

        except ValueError:
            return None

    if not isinstance(
        page_number,
        int,
    ):
        return None

    if (
        0 <= page_number
        < len(complex_pages)
    ):
        return complex_pages[
            page_number
        ]

    if (
        1 <= page_number
        <= len(complex_pages)
    ):
        return complex_pages[
            page_number - 1
        ]

    if page_number in complex_pages:
        return page_number

    return None


def build_image_fallback_content(
    original_page,
    asset_filename,
):
    content = (
        "Image extracted from the document"
    )

    if original_page is not None:
        content += (
            f" on page {original_page}"
        )

    if asset_filename:
        content += (
            f". Image asset: {asset_filename}"
        )

    return content


def build_saved_image_list(
    saved_images: dict,
):
    image_assets = []

    for filename, path in (
        saved_images.items()
    ):
        image_assets.append(
            {
                "filename": (
                    Path(filename).name
                ),
                "path": path,
            }
        )

    return image_assets


def get_image_reference_candidates(
    child: dict,
    metadata: dict,
) -> list[str]:
    values = [
        child.get("filename"),
        child.get("image"),
        child.get("image_name"),
        child.get("image_path"),
        child.get("asset_filename"),
        child.get("src"),
        child.get("uri"),
        metadata.get("filename"),
        metadata.get("image"),
        metadata.get("image_name"),
        metadata.get("image_path"),
        metadata.get("asset_filename"),
        metadata.get("src"),
        metadata.get("uri"),
    ]

    candidates = []

    for value in values:
        if not isinstance(
            value,
            str,
        ):
            continue

        value = value.strip()

        if not value:
            continue

        candidates.append(
            Path(value).name
        )

    return candidates


def find_matching_image_index(
    child: dict,
    metadata: dict,
    image_assets: list[dict],
    used_indices: set[int],
) -> int | None:
    candidates = (
        get_image_reference_candidates(
            child=child,
            metadata=metadata,
        )
    )

    normalized_candidates = {
        candidate.lower()
        for candidate in candidates
    }

    if normalized_candidates:
        for index, asset in enumerate(
            image_assets
        ):
            if index in used_indices:
                continue

            filename = str(
                asset.get(
                    "filename",
                    "",
                )
            ).lower()

            if filename in normalized_candidates:
                return index

    for index in range(
        len(image_assets)
    ):
        if index not in used_indices:
            return index

    return None


def convert_datalab_child(
    child: dict,
    complex_pages: list[int],
    image_assets: list[dict],
    image_state: dict,
):
    if not isinstance(
        child,
        dict,
    ):
        return None

    block_type = (
        normalize_block_type(
            child
        )
    )

    metadata = child.get(
        "metadata"
    )

    if not isinstance(
        metadata,
        dict,
    ):
        metadata = {}

    metadata = dict(
        metadata
    )

    page_number = (
        get_datalab_page_number(
            child=child,
            metadata=metadata,
        )
    )

    original_page = (
        resolve_original_page(
            page_number=page_number,
            complex_pages=complex_pages,
        )
    )

    if (
        original_page is None
        and len(complex_pages) == 1
    ):
        original_page = (
            complex_pages[0]
        )

    metadata[
        "parser"
    ] = "datalab"

    if original_page is not None:
        metadata[
            "page"
        ] = original_page

        location = (
            f"Page {original_page}"
        )

    else:
        raw_location = (
            child.get(
                "location"
            )
            or metadata.get(
                "location"
            )
        )

        if raw_location:
            location = str(
                raw_location
            )

        else:
            location = (
                "Unknown location"
            )

    content = (
        get_block_content(
            child
        )
    )

    if block_type == "image":
        used_indices = image_state[
            "used_indices"
        ]

        matched_index = (
            find_matching_image_index(
                child=child,
                metadata=metadata,
                image_assets=image_assets,
                used_indices=used_indices,
            )
        )

        asset_filename = None
        asset_path = None

        if matched_index is not None:
            asset = image_assets[
                matched_index
            ]

            asset_filename = asset[
                "filename"
            ]

            asset_path = asset[
                "path"
            ]

            used_indices.add(
                matched_index
            )

        if asset_filename:
            metadata[
                "asset_filename"
            ] = asset_filename

        if asset_path:
            metadata[
                "asset_path"
            ] = asset_path

        metadata[
            "has_asset"
        ] = bool(
            asset_path
        )

        metadata[
            "image_index"
        ] = matched_index

        if not content:
            content = (
                build_image_fallback_content(
                    original_page=original_page,
                    asset_filename=asset_filename,
                )
            )

    if not content:
        return None

    return {
        "type": block_type,
        "content": content,
        "location": location,
        "metadata": metadata,
    }


def convert_datalab_children(
    children,
    complex_pages: list[int],
    image_assets: list[dict],
    image_state: dict,
):
    blocks = []

    if not isinstance(
        children,
        list,
    ):
        return blocks

    for child in children:
        if not isinstance(
            child,
            dict,
        ):
            continue

        block = (
            convert_datalab_child(
                child=child,
                complex_pages=complex_pages,
                image_assets=image_assets,
                image_state=image_state,
            )
        )

        if block:
            blocks.append(
                block
            )

        nested_children = (
            child.get(
                "children"
            )
        )

        if isinstance(
            nested_children,
            list,
        ):
            nested_blocks = (
                convert_datalab_children(
                    children=nested_children,
                    complex_pages=complex_pages,
                    image_assets=image_assets,
                    image_state=image_state,
                )
            )

            blocks.extend(
                nested_blocks
            )

    return blocks


def extract_datalab_blocks(
    document_json,
    complex_pages: list[int],
    saved_images: dict,
):
    if not isinstance(
        document_json,
        dict,
    ):
        raise ValueError(
            "Unexpected Datalab JSON format"
        )

    children = document_json.get(
        "children"
    )

    if children is None:
        raise ValueError(
            (
                "Datalab JSON does not "
                "contain 'children'"
            )
        )

    image_assets = (
        build_saved_image_list(
            saved_images
        )
    )

    image_state = {
        "used_indices": set(),
    }

    blocks = (
        convert_datalab_children(
            children=children,
            complex_pages=complex_pages,
            image_assets=image_assets,
            image_state=image_state,
        )
    )

    used_indices = image_state[
        "used_indices"
    ]

    unassigned_count = 0

    for index, asset in enumerate(
        image_assets
    ):
        if index in used_indices:
            continue

        fallback_page = (
            complex_pages[0]
            if len(complex_pages) == 1
            else None
        )

        metadata = {
            "parser": "datalab",
            "asset_filename": asset[
                "filename"
            ],
            "asset_path": asset[
                "path"
            ],
            "has_asset": True,
            "image_index": index,
            "unassigned_image": True,
        }

        if fallback_page is not None:
            metadata[
                "page"
            ] = fallback_page

        blocks.append(
            {
                "type": "image",
                "content": (
                    build_image_fallback_content(
                        original_page=fallback_page,
                        asset_filename=asset[
                            "filename"
                        ],
                    )
                ),
                "location": (
                    f"Page {fallback_page}"
                    if fallback_page is not None
                    else "Unknown location"
                ),
                "metadata": metadata,
            }
        )

        unassigned_count += 1

    logger.info(
        (
            "Hybrid PDF image assignment "
            "assigned=%s total=%s"
        ),
        len(used_indices),
        len(image_assets),
    )

    if unassigned_count:
        logger.info(
            "Preserved %s unassigned images as fallback blocks",
            unassigned_count,
        )

    return blocks


def get_asset_directory(
    pdf_path: Path,
    document_id: int | None,
) -> Path:
    base_directory = (
        pdf_path.parent
        / "assets"
    )

    if document_id is not None:
        return (
            base_directory
            / f"document_{document_id}"
        )

    return (
        base_directory
        / pdf_path.stem
    )



def process_datalab_batch(
    *,
    path: Path,
    batch_number: int,
    batch_pages: list[int],
    total_batches: int,
    asset_directory: Path,
):
    page_range = (
        build_page_range(
            batch_pages
        )
    )

    logger.info(
        (
            "Processing Datalab batch "
            "%s/%s range=%s"
        ),
        batch_number,
        total_batches,
        page_range,
    )

    datalab_result = (
        extract_content_with_datalab(
            file_path=path,
            page_range=page_range,
        )
    )

    document_json = (
        datalab_result.get(
            "document_json"
        )
    )

    images = (
        datalab_result.get(
            "images"
        )
        or {}
    )

    if not document_json:
        raise ValueError(
            (
                "Datalab returned no "
                "document JSON"
            )
        )

    logger.info(
        "Datalab batch %s returned %s images",
        batch_number,
        len(images),
    )

    batch_asset_directory = (
        asset_directory
        / f"batch_{batch_number}"
    )

    saved_images = {}

    if images:
        saved_images = (
            save_datalab_images(
                images=images,
                output_directory=(
                    batch_asset_directory
                ),
            )
        )

    logger.info(
        "Datalab batch %s saved %s images",
        batch_number,
        len(saved_images),
    )

    return {
        "batch_number":
            batch_number,
        "batch_pages":
            batch_pages,
        "document_json":
            document_json,
        "saved_images":
            saved_images,
    }


def extract_content_from_hybrid_pdf(
    file_path,
    document_id: int | None = None,
):
    path = Path(
        file_path
    )

    if not path.exists():
        raise FileNotFoundError(
            f"PDF not found: {path.name}"
        )

    if not path.is_file():
        raise ValueError(
            "PDF path is not a file"
        )

    reader = PdfReader(
        path
    )

    total_pages = len(
        reader.pages
    )

    logger.info(
        "Hybrid PDF processing started pages=%s",
        total_pages,
    )

    if total_pages > MAX_PDF_PAGES:
        raise ValueError(
            (
                f"PDF contains {total_pages} pages. "
                f"Maximum allowed is "
                f"{MAX_PDF_PAGES}."
            )
        )

    (
        simple_blocks,
        complex_pages,
    ) = classify_pdf_pages(
        reader
    )

    simple_count = (
        total_pages
        - len(complex_pages)
    )

    logger.info(
        (
            "PDF classification complete "
            "simple_pages=%s complex_pages=%s"
        ),
        simple_count,
        len(complex_pages),
    )

    logger.debug(
        "Complex PDF page numbers: %s",
        complex_pages,
    )

    validate_processing_cost(
        total_pages=total_pages,
        complex_pages=complex_pages,
    )

    datalab_blocks = []

    if complex_pages:
        page_batches = (
            split_page_batches(
                pages=complex_pages,
                batch_size=MAX_DATALAB_PAGES,
            )
        )

        asset_directory = (
            get_asset_directory(
                pdf_path=path,
                document_id=document_id,
            )
        )

        total_batches = len(
            page_batches
        )

        max_workers = max(
            1,
            min(
                DATALAB_PARALLEL_BATCHES,
                total_batches,
            ),
        )

        batch_results = []

        if max_workers == 1:
            for (
                batch_number,
                batch_pages,
            ) in enumerate(
                page_batches,
                start=1,
            ):
                batch_results.append(
                    process_datalab_batch(
                        path=path,
                        batch_number=(
                            batch_number
                        ),
                        batch_pages=(
                            batch_pages
                        ),
                        total_batches=(
                            total_batches
                        ),
                        asset_directory=(
                            asset_directory
                        ),
                    )
                )

        else:
            logger.info(
                (
                    "Running Datalab batches "
                    "in parallel workers=%s "
                    "total_batches=%s"
                ),
                max_workers,
                total_batches,
            )

            with ThreadPoolExecutor(
                max_workers=max_workers
            ) as executor:
                future_to_batch = {
                    executor.submit(
                        process_datalab_batch,
                        path=path,
                        batch_number=(
                            batch_number
                        ),
                        batch_pages=(
                            batch_pages
                        ),
                        total_batches=(
                            total_batches
                        ),
                        asset_directory=(
                            asset_directory
                        ),
                    ):
                        batch_number
                    for (
                        batch_number,
                        batch_pages,
                    ) in enumerate(
                        page_batches,
                        start=1,
                    )
                }

                for future in as_completed(
                    future_to_batch
                ):
                    batch_number = (
                        future_to_batch[
                            future
                        ]
                    )

                    try:
                        batch_results.append(
                            future.result()
                        )

                    except Exception:
                        logger.exception(
                            (
                                "Datalab batch %s "
                                "failed during parallel "
                                "processing"
                            ),
                            batch_number,
                        )

                        raise

        batch_results.sort(
            key=lambda item:
                item["batch_number"]
        )

        for batch_result in batch_results:
            batch_blocks = (
                extract_datalab_blocks(
                    document_json=(
                        batch_result[
                            "document_json"
                        ]
                    ),
                    complex_pages=(
                        batch_result[
                            "batch_pages"
                        ]
                    ),
                    saved_images=(
                        batch_result[
                            "saved_images"
                        ]
                    ),
                )
            )

            datalab_blocks.extend(
                batch_blocks
            )

        image_blocks = [
            block
            for block in datalab_blocks
            if block.get(
                "type"
            ) == "image"
        ]

        image_blocks_with_assets = [
            block
            for block in image_blocks
            if block.get(
                "metadata",
                {},
            ).get(
                "asset_path"
            )
        ]

        logger.info(
            (
                "Datalab processing complete "
                "blocks=%s image_blocks=%s "
                "images_with_assets=%s"
            ),
            len(datalab_blocks),
            len(image_blocks),
            len(image_blocks_with_assets),
        )

    else:
        logger.info(
            "No Datalab processing required"
        )

    all_blocks = (
        simple_blocks
        + datalab_blocks
    )

    all_blocks.sort(
        key=lambda block: (
            block.get(
                "metadata",
                {},
            ).get(
                "page",
                999999,
            )
        )
    )

    logger.info(
        (
            "Hybrid PDF processing complete "
            "pypdf_blocks=%s datalab_blocks=%s "
            "total_blocks=%s"
        ),
        len(simple_blocks),
        len(datalab_blocks),
        len(all_blocks),
    )

    return all_blocks