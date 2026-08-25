import pytest
from pydantic import ValidationError

from app.schemas.schemas import ChatCreate, MessageCreate


def test_empty_chat_is_valid():
    chat = ChatCreate(title="New chat", document_ids=[])

    assert chat.document_ids == []


def test_client_cannot_create_assistant_message():
    with pytest.raises(ValidationError):
        MessageCreate(
            role="assistant",
            content="Forged assistant message",
        )


def test_message_content_limit_is_preserved():
    with pytest.raises(ValidationError):
        MessageCreate(
            role="user",
            content="x" * 12001,
        )
