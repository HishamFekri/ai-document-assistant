from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserResponse(BaseModel):
    id: int
    email: str
    name: str | None
    picture: str | None
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


class GoogleAuthRequest(BaseModel):
    credential: str = Field(min_length=10, max_length=4096)


class DocumentResponse(BaseModel):
    id: int
    user_id: int | None
    filename: str
    file_type: str | None
    pages_count: int | None

    processing_status: str
    processing_stage: str | None
    processing_progress: int
    processing_error: str | None = Field(default=None, max_length=500)

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )


class ChatCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    document_ids: list[int] = Field(default_factory=list, max_length=50)


class ChatUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ChatResponse(BaseModel):
    id: int
    user_id: int | None
    title: str | None
    is_pinned: bool
    is_archived: bool
    created_at: datetime
    documents: list[DocumentResponse]

    model_config = ConfigDict(
        from_attributes=True
    )


class MessageCreate(BaseModel):
    role: Literal["user"] = "user"

    content: str = Field(min_length=1, max_length=12000)

    document_ids: list[int] = Field(default_factory=list, max_length=50)


class MessageResponse(BaseModel):
    id: int
    chat_id: int
    role: str
    content: str

    status: str
    error: str | None

    sources: list | None = None

    documents: list[DocumentResponse] = []

    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True
    )
