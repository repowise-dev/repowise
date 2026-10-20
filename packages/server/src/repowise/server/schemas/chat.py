"""Chat request/response models."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from repowise.server.chat_artifacts import normalize_message_artifacts
from repowise.server.schemas.evidence import CardRef


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
        "dead-code",
        "blast-radius",
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
            "dependency",
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


class ChatSuggestion(BaseModel):
    """One composer chip. ``source`` lets a client rank a measured question
    above the static tier it already ships."""

    text: str
    source: Literal["static", "page", "followup"]
    toolHint: str | None = None  # noqa: N815 - wire shape is camelCase


class ChatSuggestionsResponse(BaseModel):
    """Only the measured tier. An empty list means the page had nothing to
    measure, and the client's own static tier stands."""

    suggestions: list[ChatSuggestion] = []


class ConversationDetailResponse(BaseModel):
    conversation: ConversationResponse
    messages: list[ChatMessageResponse] = []


# ---------------------------------------------------------------------------
# Headless chat sessions
# ---------------------------------------------------------------------------


class ChatSessionPolicyRequest(BaseModel):
    """Limits a caller asks for. Each may only narrow the server's default
    session policy; a wider value is refused."""

    tool_allowlist: list[str] | None = None
    max_tool_loops: int | None = Field(default=None, ge=1)
    max_turns: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    max_turn_tokens: int | None = Field(default=None, ge=1)
    max_session_tokens: int | None = Field(default=None, ge=1)
    audience: Literal["engineer", "business"] | None = None


class ChatSessionOpenRequest(BaseModel):
    """Open a session, or resume the one ``(namespace, external_ref)`` names."""

    repo_id: str
    scope: Literal["repo", "workspace"] = "repo"
    namespace: str = Field(default="default", min_length=1, max_length=128)
    external_ref: str | None = Field(default=None, min_length=1, max_length=512)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    policy: ChatSessionPolicyRequest | None = None


class ChatSessionResponse(BaseModel):
    id: str
    repository_id: str
    title: str
    scope: Literal["repo", "workspace"]
    namespace: str | None = None
    external_ref: str | None = None
    #: True when this call created the conversation.
    created: bool = False
    policy: dict[str, Any] = {}
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ChatSessionMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=32_000)
    #: A retried send with the same key returns the stored reply.
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    context: ChatPageContext | None = None
    #: True streams the turn as SSE events; false returns one ``ChatTurnResultResponse``.
    stream: bool = False
    provider: str | None = None
    model: str | None = None


class ChatToolTraceEntry(BaseModel):
    name: str
    summary: str
    ok: bool
    basis: str
    origin: str | None = None


class ChatTurnUsage(BaseModel):
    """Null token counts mean the provider reported none."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    model_calls: int = 0


class ChatCitation(BaseModel):
    """A file a tool result cited, and that result's ``artifact_id``."""

    file_path: str
    symbol_name: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    artifact_id: str | None = None
    ref: CardRef | None = None


class ChatFigure(BaseModel):
    """A number the answer states. Offsets are UTF-16 units into ``answer``;
    a verified figure carries the ``artifact_id`` of the result holding it."""

    text: str
    start: int
    end: int
    verified: bool
    artifact_id: str | None = None
    #: Worked out from the answer's matched figures (a ratio or a sum), not read in a result.
    derived: bool = False


class ChatTurnResultResponse(BaseModel):
    conversation_id: str
    message_id: str | None = None
    user_message_id: str | None = None
    answer: str
    stop_reason: Literal[
        "answered", "loop_cap", "token_budget", "turn_budget", "refused", "cancelled", "error"
    ]
    provider: str
    model: str
    citations: list[ChatCitation] = []
    cards: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    figures: list[ChatFigure] = []
    #: ``figures_checked``, ``figures_unverified`` and, past the evidence read
    #: cap, ``figures_not_checked``; empty when the answer states no figure.
    diagnostics: dict[str, int] = {}
    tool_trace: list[ChatToolTraceEntry] = []
    follow_ups: list[ChatSuggestion] = []
    usage: ChatTurnUsage = ChatTurnUsage()
    error: str | None = None
    replayed: bool = False
