"""Chat request/response models."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from repowise.server.chat_artifacts import normalize_message_artifacts


class ChatPageContext(BaseModel):
    """Navigation metadata supplied by a product chat surface."""

    kind: Literal[
        "repository",
        "overview",
        "documentation",
        "architecture",
        "graph",
        "health",
        "refactoring",
        "file",
        "symbol",
        "module",
        "dependency",
        "commit",
        "contributor",
        "decision",
        "risk",
        "security",
        "usage",
        "settings",
        "chat",
    ]
    label: str = Field(max_length=120)
    target: str | None = Field(default=None, max_length=2000)
    target_kind: (
        Literal[
            "path",
            "symbol",
            "module",
            "commit",
            "person",
            "decision",
            "documentation",
        ]
        | None
    ) = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    provider: str | None = None
    model: str | None = None
    context: ChatPageContext | None = None


class ConversationResponse(BaseModel):
    id: str
    repository_id: str
    title: str
    message_count: int = 0
    pinned: bool = False
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object, message_count: int = 0) -> ConversationResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            message_count=message_count,
            pinned=bool(getattr(obj, "pinned", False)),
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    pinned: bool | None = None


class ConversationForkRequest(BaseModel):
    through_message_id: str | None = None
    before_message_id: str | None = None


class ArtifactUpdateRequest(BaseModel):
    pinned: bool


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
        if isinstance(content, dict):
            content = normalize_message_artifacts(
                content,
                message_id=str(obj.id),  # type: ignore[attr-defined]
            )
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            conversation_id=obj.conversation_id,  # type: ignore[attr-defined]
            role=obj.role,  # type: ignore[attr-defined]
            content=content,
            created_at=obj.created_at,  # type: ignore[attr-defined]
        )


class ChatArtifactEnvelope(BaseModel):
    """One completed tool call, as stored inside a chat message.

    ``data`` and ``evidence`` stay open: ``data`` is the raw result of
    whichever MCP tool ran (a different shape per tool) and ``evidence`` is
    derived from it, so closing either would turn tool variance into a 500.
    """

    id: str
    version: int = 1
    type: str
    tool_name: str
    title: str
    presentation: str
    data: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    pinned: bool = False
    #: Absent on rows written before the envelope carried one; the legacy
    #: normalizer backfills every other key but not this.
    created_at: str | None = None


class ConversationDetailResponse(BaseModel):
    conversation: ConversationResponse
    messages: list[ChatMessageResponse] = []
