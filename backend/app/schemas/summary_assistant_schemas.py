from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)


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

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


class SummaryAssistantReplyResponse(BaseModel):
    user_message: SummaryAssistantMessageResponse

    assistant_message: SummaryAssistantMessageResponse

    action: SummaryAssistantAction

    generated_summary: GeneratedSummaryResponse | None = None