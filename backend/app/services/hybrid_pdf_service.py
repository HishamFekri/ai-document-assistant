import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pypdf import PdfReader

from app.services.retrieval_conventions import integer_page
from app.services.page_classifier_service import (
    is_complex_page,
)

from app.services.datalab_service import (
    extract_content_with_datalab,
    save_datalab_images,
)




logger = logging.getLogger(__name__)


from app.services.resource_limits import upload_limits
from app.services.upload_validation import validate_document_source, check_pdf_pages, PDFTextBudget
from app.services.document_resource_errors import DocumentResourceError
from app.services.content_budget import ContentBudget, check_content
from app.services.datalab_admission import AdvancedPageBudget


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


def classify_pdf_pages(reader: PdfReader):
    simple_blocks, advanced_pages = [], []
    budget = PDFTextBudget()
    limit = upload_limits().datalab_document_pages
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            text = budget.extract(page)
        except DocumentResourceError:
            raise
        except Exception:
            logger.warning("Could not extract text from PDF page %s", page_number)
            text = ""
        if is_complex_page(page=page, text=text):
            if len(advanced_pages) < limit:
                advanced_pages.append(page_number)
                continue
            if not text:
                # Reject before any batches start; never silently drop scanned pages.
                raise DocumentResourceError("advanced_pages")
        if text:
            simple_blocks.append(create_pypdf_block(text, page_number))
    return simple_blocks, advanced_pages


def validate_processing_cost(total_pages: int, complex_pages: list[int]):
    limits = upload_limits()
    if total_pages > limits.pdf_pages:
        raise DocumentResourceError("pdf_pages")
    if len(complex_pages) > limits.datalab_document_pages:
        raise DocumentResourceError("advanced_pages")


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
        return "equation"

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

    values = [value for value in possible_values if value is not None]
    if not values:
        return None
    first = integer_page(values[0])
    if any(integer_page(value) != first for value in values[1:]):
        return "conflicting page metadata"
    return values[0]


def resolve_original_page(page_number, complex_pages: list[int], convention: str = "unknown"):
    """Map only an explicit contract; unverified provider values stay unknown."""
    if page_number is None:
        return complex_pages[0] if len(complex_pages) == 1 else None
    value = integer_page(page_number)
    if value is None:
        return None
    if convention == "original_one_based":
        return value if value in complex_pages else None
    if convention == "original_zero_based":
        return value + 1 if value + 1 in complex_pages else None
    if convention == "batch_zero_based" and 0 <= value < len(complex_pages):
        return complex_pages[value]
    if convention == "batch_one_based" and 1 <= value <= len(complex_pages):
        return complex_pages[value - 1]
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

    convention = os.getenv("DATALAB_PAGE_NUMBERING", "unknown")
    original_page = resolve_original_page(page_number, complex_pages, convention)
    metadata["provider_page"] = page_number
    metadata["page_numbering"] = convention
    metadata["page_mapping_status"] = "resolved" if original_page is not None else "unknown"
    for key in ("page", "page_number", "page_id", "page_num", "location"):
        metadata.pop(key, None)
    metadata['parser'] = 'datalab'
    location = 'Unknown location'
    if original_page is not None:
        metadata['page'] = original_page
        location = f'Page {original_page}'

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
    inherited_page=None,
):
    blocks = []

    if not isinstance(
        children,
        list,
    ):
        return blocks

    for child in children:
        image_state["nodes"] += 1
        if image_state["nodes"] > upload_limits().xml_nodes:
            raise DocumentResourceError("content_limit")
        if not isinstance(
            child,
            dict,
        ):
            continue

        child = dict(child)
        metadata = child.get('metadata')
        reported_page = get_datalab_page_number(child, metadata if isinstance(metadata, dict) else {})
        if reported_page is None:
            reported_page = inherited_page
            if reported_page is not None:
                child['page'] = reported_page

        block = (
            convert_datalab_child(
                child=child,
                complex_pages=complex_pages,
                image_assets=image_assets,
                image_state=image_state,
            )
        )

        if block:
            blocks.append(image_state["budget"].block(block))

        nested_children = (
            child.get(
                "children"
            )
        )

        if isinstance(
            nested_children,
            list,
        ):
            image_state["depth"] += 1
            if image_state["depth"] > upload_limits().xml_depth:
                raise DocumentResourceError("content_limit")
            nested_blocks = (
                convert_datalab_children(
                    children=nested_children,
                    complex_pages=complex_pages,
                    image_assets=image_assets,
                    image_state=image_state,
                    inherited_page=reported_page,
                )
            )

            image_state["depth"] -= 1
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
        "used_indices": set(), "nodes": 0, "depth": 0, "budget": ContentBudget(),
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
            image_state["budget"].block({
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
            })
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



def process_datalab_batch(*, path: Path, batch_number: int, batch_pages: list[int],
                          total_batches: int, asset_directory: Path, admission):
    try:
        result = extract_content_with_datalab(file_path=path, page_range=build_page_range(batch_pages), admission=admission)
        document_json = result.get("document_json")
        images = result.get("images") or {}
        if not document_json:
            raise ValueError("Datalab returned no document JSON")
        # Validate provider text before any image upload. The image count is also
        # included as a block reservation, since fallback image blocks may be added.
        preliminary = extract_datalab_blocks(document_json, batch_pages, {})
        check_content(preliminary)
        if len(preliminary) + len(images) > upload_limits().blocks:
            raise DocumentResourceError("content_limit")
        return {"batch_number": batch_number, "batch_pages": batch_pages,
                "document_json": document_json, "images": images}
    except DocumentResourceError:
        admission.stop()
        raise


def extract_content_from_hybrid_pdf(file_path, document_id: int | None = None):
    path = Path(file_path)
    validate_document_source(path, ".pdf")
    reader = PdfReader(path)
    try:
        total_pages = check_pdf_pages(reader)
        simple_blocks, complex_pages = classify_pdf_pages(reader)
    finally:
        reader.close()
    validate_processing_cost(total_pages, complex_pages)
    from app.services.chunk_service import create_chunks_from_content
    create_chunks_from_content(simple_blocks)
    if not complex_pages:
        return simple_blocks
    limits = upload_limits()
    admission = AdvancedPageBudget(path, complex_pages)
    page_batches = split_page_batches(complex_pages, limits.datalab_batch_size)
    asset_directory = get_asset_directory(path, document_id)
    results = []
    # Submit only bounded batches; shared admission checks every paid entry point.
    with ThreadPoolExecutor(max_workers=min(limits.datalab_parallel_batches, len(page_batches))) as executor:
        futures = [executor.submit(process_datalab_batch, path=path, batch_number=index,
                                   batch_pages=pages, total_batches=len(page_batches),
                                   asset_directory=asset_directory, admission=admission)
                   for index, pages in enumerate(page_batches, 1)]
        try:
            for future in as_completed(futures):
                results.append(future.result())
        except BaseException:
            admission.stop()
            for future in futures:
                future.cancel()
            raise
    results.sort(key=lambda result: result["batch_number"])
    all_blocks = list(simple_blocks)
    for result in results:
        # Preview successful image references, including fallback descriptions,
        # before creating remote assets. Failed image uploads can only remove
        # these references, so they cannot expand the approved content budget.
        preview = {name: "pending" for name, data in result["images"].items()
                   if data and Path(name).name}
        all_blocks.extend(extract_datalab_blocks(result["document_json"], result["batch_pages"], preview))
        check_content(all_blocks)
    # Also check chunk admission before creating remote image assets.
    create_chunks_from_content(all_blocks)
    all_blocks = list(simple_blocks)
    for result in results:
        saved = save_datalab_images(result["images"], asset_directory / f"batch_{result['batch_number']}") if result["images"] else {}
        all_blocks.extend(extract_datalab_blocks(result["document_json"], result["batch_pages"], saved))
    check_content(all_blocks)
    all_blocks.sort(key=lambda block: block.get("metadata", {}).get("page", 999999))
    return all_blocks
