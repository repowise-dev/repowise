"""The seams a chat session runs on, and the values that cross them.

:mod:`repowise.server.chat_session` owns the loop; everything it reads or
writes outside itself goes through one of three seams, each a named
``SeamRegistry`` with ``local`` registered by the session module:

* ``chat-tools`` (:class:`ToolBackend`): the tools a session may call. Local is
  this server's configured MCP surface.
* ``chat-store`` (:class:`ConversationStore`): where conversations live. Local
  is the SQLAlchemy ``conversations`` table of the repository's index.
* ``chat-hooks`` (:class:`TurnHooks`): admission, usage and event callbacks
  around each turn. Local does nothing.

The tool and store seams hold factories, called once per session with its
:class:`SessionScope` (and principal, for tools), so an implementation can bind
to the session's repository. A plugin registers its own from the
``repowise.plugins`` entry point and selects it with ``default=True`` or
``REPOWISE_SEAM_CHAT_TOOLS`` / ``_CHAT_STORE`` / ``_CHAT_HOOKS``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Literal, Protocol, runtime_checkable

from repowise.core.registry.plugins import SeamRegistry
from repowise.server.auth import Principal
from repowise.server.chat_tools import ChatToolContract

#: Tool safety kinds in widening order; a policy admits its ceiling and below.
SAFETY_ORDER: tuple[str, ...] = ("read_only", "generative", "mutating")

Audience = Literal["engineer", "business"]
SafetyCeiling = Literal["read_only", "generative", "mutating"]


@dataclass(frozen=True)
class SessionScope:
    """What a session is about.

    ``repo`` sessions pin tool calls to ``repo_alias`` inside a workspace;
    ``workspace`` sessions leave the repo argument to the model, so ``"all"``
    and any alias are reachable. ``repo_id`` is the repository the
    conversation is stored with (a workspace's primary for workspace scope).
    ``storage`` is the handle the local store writes through (a session
    factory); other stores may ignore it.
    """

    kind: Literal["repo", "workspace"]
    repo_id: str
    repo_name: str
    repo_path: str
    repo_alias: str | None = None
    storage: Any = field(default=None, compare=False, repr=False)

    def stored(self) -> dict[str, Any]:
        return {"kind": self.kind, "alias": self.repo_alias}


@dataclass(frozen=True)
class SessionPolicy:
    """Limits one session runs under, frozen when the session is opened.

    The defaults suit a headless caller: read-only tools, 20 model calls a
    turn. ``audience`` is stored and passed along; nothing reads it yet.
    """

    tool_allowlist: frozenset[str] | None = None
    safety_ceiling: SafetyCeiling = "read_only"
    max_tool_loops: int = 20
    max_turns: int | None = None
    max_output_tokens: int = 8192
    max_turn_tokens: int | None = None
    max_session_tokens: int | None = None
    temperature: float = 0.7
    audience: Audience = "engineer"

    def admits(self, tool: ChatToolContract) -> bool:
        return self.admits_name(tool.entry.name, tool.entry.safety)

    def admits_name(self, name: str, safety: str) -> bool:
        if self.tool_allowlist is not None and name not in self.tool_allowlist:
            return False
        safety = safety if safety in SAFETY_ORDER else "mutating"
        return SAFETY_ORDER.index(safety) <= SAFETY_ORDER.index(self.safety_ceiling)

    def to_json(self) -> str:
        data = asdict(self)
        if self.tool_allowlist is not None:
            data["tool_allowlist"] = sorted(self.tool_allowlist)
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | None, default: SessionPolicy) -> SessionPolicy:
        """The stored policy, or *default* for a conversation that has none.
        Unknown keys (from a newer server) are ignored."""
        if not raw:
            return default
        data = json.loads(raw)
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        if values.get("tool_allowlist") is not None:
            values["tool_allowlist"] = frozenset(values["tool_allowlist"])
        return cls(**values)


@dataclass(frozen=True)
class TurnUsage:
    """Tokens one turn used. ``None`` means the provider reported nothing."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    model_calls: int = 0

    @property
    def total(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass(frozen=True)
class StoredConversation:
    id: str
    repository_id: str
    title: str
    scope: dict[str, Any]
    namespace: str | None
    external_ref: str | None
    principal_id: str | None
    policy_json: str | None
    input_tokens: int
    output_tokens: int
    turns: int
    deleted: bool

    @property
    def tokens_used(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class StoredMessage:
    id: str
    role: str
    content: dict[str, Any]


@runtime_checkable
class ToolBackend(Protocol):
    """The tools one session may see and call."""

    def catalog_for(
        self, policy: SessionPolicy, principal: Principal | None
    ) -> list[ChatToolContract]:
        """Tools available to *principal*. The session intersects this with
        *policy* itself and refuses any other name at execution."""
        ...

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool. Failures come back as ``{"error": ..., "error_code": ...}``."""
        ...


@runtime_checkable
class ConversationStore(Protocol):
    """Where a session's conversation and messages live."""

    async def get_or_create(
        self,
        *,
        repository_id: str,
        title: str,
        scope: dict[str, Any],
        namespace: str | None,
        external_ref: str | None,
        principal_id: str | None,
        policy_json: str | None,
    ) -> tuple[StoredConversation, bool]:
        """The conversation keyed by ``(namespace, external_ref)``, created
        when absent (always created without a key); the flag says which."""
        ...

    async def get(self, conversation_id: str) -> StoredConversation | None: ...

    async def find(self, namespace: str, external_ref: str) -> StoredConversation | None: ...

    async def messages(self, conversation_id: str) -> list[StoredMessage]:
        """Every message, oldest first."""
        ...

    async def find_sent(
        self, conversation_id: str, idempotency_key: str
    ) -> tuple[StoredMessage, StoredMessage | None] | None:
        """``(user message, reply or None)`` for a key already sent, else None."""
        ...

    async def add_user_message(
        self, conversation_id: str, content: dict[str, Any], *, idempotency_key: str | None
    ) -> str: ...

    async def add_reply(
        self,
        conversation_id: str,
        content: dict[str, Any],
        *,
        title: str | None,
        usage: TurnUsage,
    ) -> str:
        """Store the assistant message, count the turn and its tokens, and
        retitle the conversation when *title* is given."""
        ...


@runtime_checkable
class TurnHooks(Protocol):
    """Callbacks around each turn, for admission control and metering."""

    async def preflight(
        self, principal: Principal | None, policy: SessionPolicy, conversation: StoredConversation
    ) -> str | None:
        """None to run the turn, or the reason it is refused."""
        ...

    async def on_usage(
        self, principal: Principal | None, conversation: StoredConversation, usage: TurnUsage
    ) -> None: ...

    async def on_event(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """Called with every event the turn emits; returns extra events to emit after it."""
        ...


class NoTurnHooks:
    """The local default: admit every turn, record nothing, add nothing."""

    async def preflight(self, principal, policy, conversation) -> str | None:
        return None

    async def on_usage(self, principal, conversation, usage) -> None:
        return None

    async def on_event(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        return []


ToolBackendFactory = Callable[[SessionScope, Principal | None], ToolBackend]
ConversationStoreFactory = Callable[[SessionScope], ConversationStore]

tool_backends: SeamRegistry[ToolBackendFactory] = SeamRegistry("chat-tools")
conversation_stores: SeamRegistry[ConversationStoreFactory] = SeamRegistry("chat-store")
turn_hooks: SeamRegistry[TurnHooks] = SeamRegistry("chat-hooks")
