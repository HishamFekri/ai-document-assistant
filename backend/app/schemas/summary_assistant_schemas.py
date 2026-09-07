from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
)

from app.services.error_service import public_generation_error


SummaryAssistantAction = Literal[
    "update_preferences",
    "generate_summary",
]


class SummaryAssistantMessageCreate(BaseModel):
    chat_id: int = Field(
        gt=0,
    )

    content: str = Field(
        min_length=1,
        max_length=4000,
    )


class SummaryAssistantMessageResponse(BaseModel):
    id: int

    chat_id: int | None

    document_id: int

    role: str

    content: str

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


class SummaryAssistantChatResponse(BaseModel):
    messages: list[
        SummaryAssistantMessageResponse
    ]


class GeneratedSummaryBlock(BaseModel):
    type: str

    title: str | None = None

    content: str | None = None

    asset_id: int | None = None

    caption: str | None = None

    location: str | None = None


class GeneratedSummaryContent(BaseModel):
    title: str

    sections: list[
        GeneratedSummaryBlock
    ]


class GeneratedSummaryResponse(BaseModel):
    id: int

    chat_id: int | None

    document_id: int

    version: int

    status: str

    content: GeneratedSummaryContent | None

    is_selected: bool

    error: str | None

    @field_serializer("error")
    def serialize_error(self, error):
        return public_generation_error(error, "summary")

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


class SummaryAssistantReplyResponse(BaseModel):
    user_message: SummaryAssistantMessageResponse

    assistant_message: SummaryAssistantMessageResponse

    action: SummaryAssistantAction

    generated_summary: GeneratedSummaryResponse | None = None
