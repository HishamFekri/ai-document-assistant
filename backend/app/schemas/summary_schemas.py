from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


SummaryBlockType = Literal[
    "text",
    "image",
    "table",
    "equation",
]


SummaryMode = Literal[
    "summary",
    "transcription",
]


class SummaryGenerateRequest(BaseModel):
    chat_id: int = Field(
        gt=0,
    )

    mode: SummaryMode = "summary"


class SummaryBlock(BaseModel):
    type: SummaryBlockType

    title: str | None = None

    content: str | None = None

    asset_id: int | None = None

    caption: str | None = None

    location: str | None = None


class SummaryContent(BaseModel):
    title: str

    sections: list[SummaryBlock]


class DocumentSummaryResponse(BaseModel):
    id: int

    chat_id: int | None

    document_id: int

    mode: SummaryMode

    version: int

    status: str

    content: SummaryContent | None

    is_selected: bool

    error: str | None

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )