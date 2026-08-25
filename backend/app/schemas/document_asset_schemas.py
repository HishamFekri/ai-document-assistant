from datetime import datetime

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
)


DocumentAssetType = Literal[
    "image",
    "table",
    "equation",
]


class DocumentAssetResponse(BaseModel):
    id: int

    document_id: int

    asset_type: DocumentAssetType

    location: str | None

    title: str | None

    caption: str | None

    content: str | None

    file_path: str | None

    asset_metadata: dict | None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )