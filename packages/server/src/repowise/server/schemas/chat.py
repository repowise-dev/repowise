"""Chat request/response models."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    provider: str | None = None
    model: str | None = None


class ConversationResponse(BaseModel):
    id: str
    repository_id: str
    title: str
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object, message_count: int = 0) -> ConversationResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            message_count=message_count,
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


class ChatMessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: dict
    created_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> ChatMessageResponse:
        content_str = obj.content_json  # type: ignore[attr-defined]
        try:
            content = json.loads(content_str) if isinstance(content_str, str) else content_str
        except Exception:
            content = {"text": content_str}
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            conversation_id=obj.conversation_id,  # type: ignore[attr-defined]
            role=obj.role,  # type: ignore[attr-defined]
            content=content,
            created_at=obj.created_at,  # type: ignore[attr-defined]
        )
