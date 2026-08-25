from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
)

from fastapi.responses import FileResponse

from sqlalchemy.orm import Session

from app.database.database import get_db

from app.database.models import (
    Document,
    User,
)

from app.routes.auth import (
    get_current_user,
)

from app.schemas.document_asset_schemas import (
    DocumentAssetResponse,
)

from app.services.assets.document_asset_service import (
    get_document_asset,
    get_document_assets,
)


router = APIRouter(
    prefix="/documents",
    tags=["Document Assets"],
)


VALID_ASSET_TYPES = {
    "image",
    "table",
    "equation",
}


def get_owned_document(
    document_id: int,
    current_user: User,
    db: Session,
) -> Document:
    document = (
        db.query(Document)
        .filter(
            Document.id == document_id,
            Document.user_id == current_user.id,
        )
        .first()
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return document


@router.get(
    "/{document_id}/assets",
    response_model=list[
        DocumentAssetResponse
    ],
)
def list_document_assets(
    document_id: int,
    asset_type: str | None = Query(
        default=None
    ),
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    document = get_owned_document(
        document_id=document_id,
        current_user=current_user,
        db=db,
    )

    if (
        asset_type is not None
        and asset_type
        not in VALID_ASSET_TYPES
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid asset type",
        )

    assets = get_document_assets(
        db=db,
        document_id=document_id,
        asset_type=asset_type,
    )

    return assets


@router.get(
    "/{document_id}/assets/{asset_id}",
    response_model=DocumentAssetResponse,
)
def read_document_asset(
    document_id: int,
    asset_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    get_owned_document(
        document_id=document_id,
        current_user=current_user,
        db=db,
    )

    asset = get_document_asset(
        db=db,
        asset_id=asset_id,
    )

    if (
        asset is None
        or asset.document_id
        != document_id
    ):
        raise HTTPException(
            status_code=404,
            detail="Asset not found",
        )

    return asset


@router.get(
    "/{document_id}/assets/{asset_id}/file",
)
def read_document_asset_file(
    document_id: int,
    asset_id: int,
    current_user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    document = get_owned_document(
        document_id=document_id,
        current_user=current_user,
        db=db,
    )

    asset = get_document_asset(
        db=db,
        asset_id=asset_id,
    )

    if (
        asset is None
        or asset.document_id
        != document_id
    ):
        raise HTTPException(
            status_code=404,
            detail="Asset not found",
        )

    if asset.asset_type != "image":
        raise HTTPException(
            status_code=400,
            detail=(
                "This asset does not "
                "contain an image file"
            ),
        )

    if not asset.file_path:
        raise HTTPException(
            status_code=404,
            detail="Image file path not found",
        )

    path = Path(
        asset.file_path
    ).resolve()

    if not document.file_path:
        raise HTTPException(
            status_code=404,
            detail="Document file not found",
        )

    assets_root = Path(
        document.file_path
    ).resolve().parent

    if path != assets_root and assets_root not in path.parents:
        raise HTTPException(
            status_code=404,
            detail="Image file not found",
        )

    if (
        not path.exists()
        or not path.is_file()
    ):
        raise HTTPException(
            status_code=404,
            detail="Image file not found",
        )

    return FileResponse(
        path=path,
    )