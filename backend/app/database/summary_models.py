from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from sqlalchemy.dialects.postgresql import JSONB

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


class DocumentSummary(Base):
    __tablename__ = "document_summaries"

    __table_args__ = (
        UniqueConstraint(
            "chat_id",
            "document_id",
            "mode",
            "version",
            name="uq_chat_document_summary_mode_version",
        ),

        Index(
            "ix_document_summaries_selected",
            "document_id",
            "is_selected",
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
        index=True,
    )

    mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="summary",
        server_default="summary",
        index=True,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="pending",
    )

    content: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )

    is_selected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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
        back_populates="summaries",
    )