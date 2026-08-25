from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from sqlalchemy.sql import func

from app.database.database import Base


if TYPE_CHECKING:
    from app.database.models import (
        Chat,
        Document,
    )


class SummaryAssistantMessage(Base):
    __tablename__ = "summary_assistant_messages"

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_summary_assistant_role",
        ),

        Index(
            "ix_summary_assistant_document_id",
            "document_id",
        ),

        Index(
            "ix_summary_assistant_document_created",
            "document_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    chat_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "chats.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    document_id: Mapped[int] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    chat: Mapped["Chat | None"] = relationship(
        "Chat",
    )

    document: Mapped["Document"] = relationship(
        "Document",
    )