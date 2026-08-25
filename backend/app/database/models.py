from datetime import datetime

from pgvector.sqlalchemy import Vector

from sqlalchemy import (
    Table,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from sqlalchemy.dialects.postgresql import JSONB

from sqlalchemy.orm import (
    Mapped,
    mapped_column,
    relationship,
)

from sqlalchemy.sql import func

from app.database.database import Base


chat_documents = Table(
    "chat_documents",
    Base.metadata,

    Column(
        "chat_id",
        ForeignKey(
            "chats.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),

    Column(
        "document_id",
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),
)


message_documents = Table(
    "message_documents",
    Base.metadata,

    Column(
        "message_id",
        ForeignKey(
            "messages.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),

    Column(
        "document_id",
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    ),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    google_sub: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    picture: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )

    documents: Mapped[
        list["Document"]
    ] = relationship(
        back_populates="owner",
    )

    chats: Mapped[
        list["Chat"]
    ] = relationship(
        back_populates="owner",
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    file_type: Mapped[
        str | None
    ] = mapped_column(
        String(20),
        nullable=True,
    )

    file_path: Mapped[
        str | None
    ] = mapped_column(
        String(500),
        nullable=True,
    )

    pages_count: Mapped[
        int | None
    ] = mapped_column(
        Integer,
        nullable=True,
    )

    processing_status: Mapped[
        str
    ] = mapped_column(
        String(20),
        nullable=False,
        server_default="ready",
    )

    processing_stage: Mapped[
        str | None
    ] = mapped_column(
        String(50),
        nullable=True,
    )

    processing_progress: Mapped[
        int
    ] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )

    processing_error: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime,
        server_default=func.now(),
    )

    owner: Mapped[
        "User | None"
    ] = relationship(
        back_populates="documents",
    )

    chats: Mapped[
        list["Chat"]
    ] = relationship(
        secondary=chat_documents,
        back_populates="documents",
    )

    messages: Mapped[
        list["Message"]
    ] = relationship(
        secondary=message_documents,
        back_populates="documents",
    )

    chunks: Mapped[
        list["DocumentChunk"]
    ] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )

    summaries: Mapped[
        list["DocumentSummary"]
    ] = relationship(
        "DocumentSummary",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentSummary.version",
    )

    assets: Mapped[
        list["DocumentAsset"]
    ] = relationship(
        "DocumentAsset",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    __table_args__ = (
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={
                "embedding": "vector_cosine_ops",
            },
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    document_id: Mapped[
        int
    ] = mapped_column(
        ForeignKey(
            "documents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    content: Mapped[
        str
    ] = mapped_column(
        Text,
        nullable=False,
    )

    content_type: Mapped[
        str | None
    ] = mapped_column(
        String(50),
        nullable=True,
    )

    location: Mapped[
        str | None
    ] = mapped_column(
        String(255),
        nullable=True,
    )

    chunk_metadata: Mapped[
        dict | None
    ] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    embedding: Mapped[
        list[float] | None
    ] = mapped_column(
        Vector(384),
        nullable=True,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime,
        server_default=func.now(),
    )

    document: Mapped[
        "Document"
    ] = relationship(
        back_populates="chunks",
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    user_id: Mapped[
        int | None
    ] = mapped_column(
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    title: Mapped[
        str | None
    ] = mapped_column(
        String(255),
        nullable=True,
    )

    is_pinned: Mapped[
        bool
    ] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    is_archived: Mapped[
        bool
    ] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime,
        server_default=func.now(),
    )

    owner: Mapped[
        "User | None"
    ] = relationship(
        back_populates="chats",
    )

    documents: Mapped[
        list["Document"]
    ] = relationship(
        secondary=chat_documents,
        back_populates="chats",
    )

    messages: Mapped[
        list["Message"]
    ] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    chat_id: Mapped[
        int
    ] = mapped_column(
        ForeignKey(
            "chats.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    role: Mapped[
        str
    ] = mapped_column(
        String(20),
        nullable=False,
    )

    content: Mapped[
        str
    ] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[
        str
    ] = mapped_column(
        String(20),
        nullable=False,
        server_default="completed",
    )

    error: Mapped[
        str | None
    ] = mapped_column(
        Text,
        nullable=True,
    )

    sources: Mapped[
        list | None
    ] = mapped_column(
        JSONB,
        nullable=True,
    )

    created_at: Mapped[
        datetime
    ] = mapped_column(
        DateTime,
        server_default=func.now(),
    )

    chat: Mapped[
        "Chat"
    ] = relationship(
        back_populates="messages",
    )

    documents: Mapped[
        list["Document"]
    ] = relationship(
        secondary=message_documents,
        back_populates="messages",
    )


from app.database.summary_models import (
    DocumentSummary,
)

from app.database.document_asset_models import (
    DocumentAsset,
)