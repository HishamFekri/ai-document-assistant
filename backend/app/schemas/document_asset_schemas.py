from datetime import datetime

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    model_serializer,
)

from app.services.assets.image_references import asset_file_url, normalize_image_metadata


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

    @model_serializer(mode="wrap")
    def serialize_asset(self, handler):
        result = handler(self)
        if self.asset_type != "image":
            return result
        return normalize_image_metadata(result, asset_file_url(self.document_id, self.id))
