from pathlib import Path

from sqlalchemy.orm import Session

from app.database.document_asset_models import (
    DocumentAsset,
)


ASSET_TYPE_MAP = {
    "image": "image",
    "table": "table",
    "formula": "equation",
    "equation": "equation",
}


def make_json_safe(
    value,
):
    if value is None:
        return None

    if isinstance(
        value,
        Path,
    ):
        return str(
            value
        )

    if isinstance(
        value,
        dict,
    ):
        return {
            str(key): make_json_safe(
                item
            )
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return [
            make_json_safe(
                item
            )
            for item in value
        ]

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    return str(
        value
    )


def get_block_metadata(
    block: dict,
) -> dict:
    metadata = block.get(
        "metadata"
    )

    if not isinstance(
        metadata,
        dict,
    ):
        return {}

    return dict(
        metadata
    )


def get_asset_file_path(
    asset_type: str,
    metadata: dict,
) -> str | None:
    if asset_type != "image":
        return None

    asset_path = (
        metadata.get(
            "asset_path"
        )
    )

    if not asset_path:
        return None

    return str(
        asset_path
    )


def get_asset_title(
    asset_type: str,
    metadata: dict,
) -> str | None:
    possible_titles = [
        metadata.get(
            "title"
        ),
        metadata.get(
            "name"
        ),
        metadata.get(
            "label"
        ),
    ]

    for value in possible_titles:
        if value:
            return str(
                value
            ).strip()

    if asset_type == "image":
        filename = (
            metadata.get(
                "asset_filename"
            )
        )

        if filename:
            return Path(
                str(
                    filename
                )
            ).stem

    return None


def get_asset_caption(
    asset_type: str,
    block: dict,
    metadata: dict,
) -> str | None:
    possible_captions = [
        block.get(
            "caption"
        ),
        metadata.get(
            "caption"
        ),
        metadata.get(
            "description"
        ),
    ]

    for value in possible_captions:
        if value:
            return str(
                value
            ).strip()

    if asset_type == "image":
        content = (
            block.get(
                "content"
            )
        )

        if content:
            return str(
                content
            ).strip()

    return None


def get_asset_content(
    asset_type: str,
    block: dict,
) -> str | None:
    if asset_type == "image":
        return None

    content = (
        block.get(
            "content"
        )
    )

    if content is None:
        return None

    if isinstance(
        content,
        str,
    ):
        content = (
            content.strip()
        )

        return (
            content
            or None
        )

    return str(
        content
    )


def build_document_assets(
    document_id: int,
    content: list[dict],
) -> list[DocumentAsset]:
    assets = []

    for block in content:
        if not isinstance(
            block,
            dict,
        ):
            continue

        block_type = str(
            block.get(
                "type",
                ""
            )
        ).lower()

        asset_type = (
            ASSET_TYPE_MAP.get(
                block_type
            )
        )

        if asset_type is None:
            continue

        metadata = (
            get_block_metadata(
                block
            )
        )

        location = (
            block.get(
                "location"
            )
        )

        if location is not None:
            location = str(
                location
            )

        file_path = (
            get_asset_file_path(
                asset_type=asset_type,
                metadata=metadata,
            )
        )

        title = (
            get_asset_title(
                asset_type=asset_type,
                metadata=metadata,
            )
        )

        caption = (
            get_asset_caption(
                asset_type=asset_type,
                block=block,
                metadata=metadata,
            )
        )

        asset_content = (
            get_asset_content(
                asset_type=asset_type,
                block=block,
            )
        )

        safe_metadata = (
            make_json_safe(
                metadata
            )
        )

        asset = DocumentAsset(
            document_id=document_id,
            asset_type=asset_type,
            location=location,
            title=title,
            caption=caption,
            content=asset_content,
            file_path=file_path,
            asset_metadata=safe_metadata,
        )

        assets.append(
            asset
        )

    return assets


def replace_document_assets(
    db: Session,
    document_id: int,
    content: list[dict],
) -> list[DocumentAsset]:
    existing_assets = (
        db.query(
            DocumentAsset
        )
        .filter(
            DocumentAsset.document_id
            == document_id
        )
        .all()
    )

    for asset in existing_assets:
        db.delete(
            asset
        )

    assets = (
        build_document_assets(
            document_id=document_id,
            content=content,
        )
    )

    db.add_all(
        assets
    )

    db.flush()

    print(
        f"[ASSETS] Document "
        f"{document_id}: "
        f"{len(assets)} assets found"
    )

    image_count = sum(
        1
        for asset in assets
        if asset.asset_type
        == "image"
    )

    table_count = sum(
        1
        for asset in assets
        if asset.asset_type
        == "table"
    )

    equation_count = sum(
        1
        for asset in assets
        if asset.asset_type
        == "equation"
    )

    print(
        f"[ASSETS] Images: "
        f"{image_count}"
    )

    print(
        f"[ASSETS] Tables: "
        f"{table_count}"
    )

    print(
        f"[ASSETS] Equations: "
        f"{equation_count}"
    )

    return assets