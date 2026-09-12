from fastapi import Response
from app.services.pagination import DEFAULT_PAGE_SIZE, paginated
from sqlalchemy.orm import Session

from app.database.document_asset_models import (
    DocumentAsset,
)


VALID_ASSET_TYPES = {
    "image",
    "table",
    "equation",
}


def create_document_asset(
    db: Session,
    document_id: int,
    asset_type: str,
    location: str | None = None,
    title: str | None = None,
    caption: str | None = None,
    content: str | None = None,
    file_path: str | None = None,
    asset_metadata: dict | None = None,
) -> DocumentAsset:
    if asset_type not in VALID_ASSET_TYPES:
        raise ValueError(
            f"Unsupported asset type: {asset_type}"
        )

    asset = DocumentAsset(
        document_id=document_id,
        asset_type=asset_type,
        location=location,
        title=title,
        caption=caption,
        content=content,
        file_path=file_path,
        asset_metadata=asset_metadata,
    )

    db.add(asset)
    db.commit()
    db.refresh(asset)

    return asset


def get_document_asset(
    db: Session,
    asset_id: int,
) -> DocumentAsset | None:
    return (
        db.query(DocumentAsset)
        .filter(
            DocumentAsset.id
            == asset_id
        )
        .first()
    )


def get_document_assets(db: Session, document_id: int, asset_type: str | None=None, *, response=None, limit=DEFAULT_PAGE_SIZE, cursor=None, owner=None) -> list[DocumentAsset]:
    query = db.query(DocumentAsset).filter(DocumentAsset.document_id == document_id)
    if asset_type is not None:
        if asset_type not in VALID_ASSET_TYPES:
            raise ValueError(f'Unsupported asset type: {asset_type}')
        query = query.filter(DocumentAsset.asset_type == asset_type)
    return paginated(query, [(DocumentAsset.id, False)], owner=owner, scope=f'assets:{document_id}:{asset_type}',
                     response=response if response is not None else Response(), limit=limit, cursor=cursor)


def delete_document_asset(
    db: Session,
    asset: DocumentAsset,
) -> None:
    db.delete(asset)
    db.commit()


def delete_document_assets(
    db: Session,
    document_id: int,
    asset_type: str | None = None,
) -> int:
    query = (
        db.query(DocumentAsset)
        .filter(
            DocumentAsset.document_id
            == document_id
        )
    )

    if asset_type is not None:
        if asset_type not in VALID_ASSET_TYPES:
            raise ValueError(
                f"Unsupported asset type: {asset_type}"
            )

        query = query.filter(
            DocumentAsset.asset_type
            == asset_type
        )

    deleted_count = query.delete(
        synchronize_session=False
    )

    db.commit()

    return deleted_count
