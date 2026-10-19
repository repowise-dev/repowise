"""Transport-neutral chat sessions: the one agentic loop every surface drives.

A :class:`ChatSession` is one conversation opened under a :class:`SessionScope`,
a :class:`SessionPolicy` and an optional principal. ``stream`` yields the turn
as event dicts (the names the web chat reads: ``grounding``, ``text_delta``,
``tool_start``, ``tool_result``, ``view``, ``truncated``, ``suggestions``,
``done``, ``error``); ``send`` runs the same turn and returns a
:class:`ChatTurnResult`. Nothing here imports FastAPI: the HTTP routes format
events as SSE, and an in-process caller uses the class directly.

The tools a model is shown, and the only ones it can run, are the backend's
catalog for the principal narrowed by the policy's allowlist and safety
ceiling, plus the chat-only ``show_view`` tool, which builds tables and
charts from the conversation's tool results (:mod:`repowise.server.chat_views`),
the chat-only ``chart``, which draws a repository dataset in one call
(:mod:`repowise.server.chat_chart`), and the chat-only ``find_suspects``
(:mod:`repowise.server.chat_suspects`).
Everything outside the loop goes through the seams in
:mod:`repowise.server.chat_seams`.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from repowise.core.providers.llm.base import ChatProvider
from repowise.server.auth import Principal
from repowise.server.chat_artifacts import create_artifact_envelope
from repowise.server.chat_chart import CHART_TOOL, chart_schema, run_chart
from repowise.server.chat_context import (
    OVERFLOW_MESSAGE,
    bound_history,
    compose_request,
    is_context_overflow,
)
from repowise.server.chat_errors import NO_PROVIDER_MESSAGE, internal_error, provider_failure
from repowise.server.chat_figures import check_figures
from repowise.server.chat_grounding import plan_grounding, run_grounding
from repowise.server.chat_prompt import (
    _build_system_prompt,
    _history_has_call,
    _with_navigation_context,
    assistant_tool_call_message,
    stored_messages_to_llm_format,
    tool_result_message,
)
from repowise.server.chat_references import VIEW_TOOLS, ArtifactIndex
from repowise.server.chat_seams import (
    ConversationStore,
    NoTurnHooks,
    SessionPolicy,
    SessionScope,
    StoredConversation,
    StoredMessage,
    ToolBackend,
    TurnHooks,
    TurnUsage,
    conversation_stores,
    tool_backends,
    turn_hooks,
)
from repowise.server.chat_store import SqlConversationStore
from repowise.server.chat_suggestions import conversation_title, follow_up_suggestions, tool_names
from repowise.server.chat_summaries import _build_tool_summary
from repowise.server.chat_suspects import (
    SCOPE_TOOL,
    SUSPECTS_TOOL,
    find_suspects_schema,
    run_find_suspects,
)
from repowise.server.chat_tools import (
    ChatToolContract,
    LocalToolBackend,
    make_json_serializable,
    tool_schemas,
)
from repowise.server.chat_views import SHOW_VIEW_TOOL, TurnViews, show_view_schema
from repowise.server.schemas.evidence import CardRef

logger = logging.getLogger(__name__)

#: A headless session: read-only tools, 20 model calls a turn.
DEFAULT_POLICY = SessionPolicy()
#: The web chat keeps the surface it always had: every configured tool, a
#: mutating one still refused at execution without a confirmation grant.
WEB_POLICY = SessionPolicy(safety_ceiling="mutating")

StopReason = Literal[
    "answered", "loop_cap", "token_budget", "turn_budget", "refused", "cancelled", "error"
]
IsCancelled = Callable[[], Awaitable[bool]]

conversation_stores.register("local", lambda scope: SqlConversationStore(scope.storage))
tool_backends.register(
    "local",
    lambda scope, principal: LocalToolBackend(
        scope.repo_path,
        repo_alias=scope.repo_alias if scope.kind == "repo" else None,
        repo_id=scope.repo_id,
        principal=principal,
    ),
)
turn_hooks.register("local", NoTurnHooks())


class SessionNotFoundError(LookupError):
    """The conversation does not exist, was deleted, or belongs to another repository."""


class IdempotencyConflictError(ValueError):
    """An idempotency key already sent with a different message."""


@dataclass(frozen=True)
class ToolTraceEntry:
    """What one tool call did, without its payload."""

    name: str
    summary: str
    ok: bool
    basis: str
    origin: str | None = None


@dataclass(frozen=True)
class ChatTurnResult:
    conversation_id: str
    #: None when nothing was stored (refused, cancelled, failed).
    message_id: str | None
    user_message_id: str | None
    answer: str
    stop_reason: StopReason
    provider: str
    model: str
    citations: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    views: list[dict[str, Any]] = field(default_factory=list)
    #: Each number the answer states, looked up in the turn's tool results;
    #: a verified one carries the ``artifact_id`` it came from.
    figures: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, int] = field(default_factory=dict)
    tool_trace: list[ToolTraceEntry] = field(default_factory=list)
    follow_ups: list[dict[str, Any]] = field(default_factory=list)
    usage: TurnUsage = field(default_factory=TurnUsage)
    error: str | None = None
    #: True when an idempotency key matched and the stored reply was returned.
    replayed: bool = False


# ---------------------------------------------------------------------------
# Per-conversation turn lock
# ---------------------------------------------------------------------------

# In-process only: two server processes on one database can still interleave
# turns of one conversation. A multi-process deployment needs a database lock
# (an advisory lock or SELECT ... FOR UPDATE) behind the store.
_turn_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _turn_lock(conversation_id: str) -> asyncio.Lock:
    lock = _turn_locks.get(conversation_id)
    if lock is None:
        lock = asyncio.Lock()
        _turn_locks[conversation_id] = lock
    return lock


async def _never_cancelled() -> bool:
    return False


# ---------------------------------------------------------------------------
# Stored content <-> results
# ---------------------------------------------------------------------------


_CITATION_FIELDS = (
    ("file_path", str),
    ("symbol_name", str),
    ("start_line", int),
    ("end_line", int),
)


def _citations(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Files the turn's tool results cite, first mention first, each with the
    ``artifact_id`` of the result that cited it and its entity ``ref``."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for call in tool_calls:
        artifact = call.get("artifact") or {}
        data = artifact.get("data") or {}
        raw = data.get("citations") if isinstance(data, dict) else None
        for item in raw if isinstance(raw, list) else []:
            if isinstance(item, str):
                citation = {"file_path": item}
            elif isinstance(item, dict) and isinstance(item.get("file_path"), str):
                # Only the types the citation contract takes: a tool's "10-20" is not a line.
                citation = {
                    key: item[key]
                    for key, kind in _CITATION_FIELDS
                    if isinstance(item.get(key), kind) and not isinstance(item[key], bool)
                }
            else:
                continue
            key = tuple(sorted(citation.items()))
            if key in seen:
                continue
            seen.add(key)
            if isinstance(artifact.get("id"), str):
                citation["artifact_id"] = artifact["id"]
            ref = _citation_ref(citation)
            if ref is not None:
                citation["ref"] = ref
            out.append(citation)
    return out


def _citation_ref(citation: dict[str, Any]) -> dict[str, Any] | None:
    """The file or symbol a citation names, as a ``CardRef``; None when it does not validate."""
    symbol = citation.get("symbol_name")
    ref: dict[str, Any] = (
        {"type": "symbol", "id": f"{citation['file_path']}::{symbol}"}
        if isinstance(symbol, str) and symbol
        else {"type": "file", "id": citation["file_path"]}
    )
    for key in ("start_line", "end_line"):
        if key in citation:
            ref[key] = citation[key]
    try:
        return CardRef.model_validate(ref).model_dump(exclude_none=True)
    except ValidationError:
        return None


def _tool_trace(tool_calls: list[dict[str, Any]]) -> list[ToolTraceEntry]:
    trace = []
    for call in tool_calls:
        artifact = call.get("artifact") or {}
        data = artifact.get("data")
        trace.append(
            ToolTraceEntry(
                name=str(call.get("name", "")),
                summary=str(call.get("summary", "")),
                ok=not (isinstance(data, dict) and "error" in data),
                basis=str((artifact.get("evidence") or {}).get("basis", "unknown")),
                origin=call.get("origin"),
            )
        )
    return trace


def _result_from_content(
    content: dict[str, Any],
    *,
    conversation_id: str,
    message_id: str | None,
    user_message_id: str | None,
    stop_reason: StopReason,
    replayed: bool = False,
) -> ChatTurnResult:
    tool_calls = [c for c in content.get("tool_calls") or [] if isinstance(c, dict)]
    usage = content.get("usage")
    return ChatTurnResult(
        conversation_id=conversation_id,
        message_id=message_id,
        user_message_id=user_message_id,
        answer=str(content.get("text", "")),
        stop_reason=stop_reason,
        provider=str(content.get("provider", "")),
        model=str(content.get("model", "")),
        citations=_citations(tool_calls),
        cards=list(content.get("cards") or []),
        views=list(content.get("views") or []),
        figures=list(content.get("figures") or []),
        diagnostics=dict(content.get("diagnostics") or {}),
        tool_trace=_tool_trace(tool_calls),
        follow_ups=list(content.get("follow_ups") or []),
        usage=TurnUsage(**usage) if isinstance(usage, dict) else TurnUsage(),
        replayed=replayed,
    )


def _closing_events(result: ChatTurnResult, text_shift: int = 0) -> list[dict[str, Any]]:
    """``text_shift`` moves figure offsets from the answer onto the streamed
    text, which also holds what the model wrote before its tool calls."""
    events: list[dict[str, Any]] = []
    if result.diagnostics:
        events.append(
            {
                "type": "diagnostics",
                **result.diagnostics,
                "unverified_figures": [
                    {
                        "text": f["text"],
                        "start": f["start"] + text_shift,
                        "end": f["end"] + text_shift,
                    }
                    for f in result.figures
                    if not f.get("verified")
                ],
            }
        )
    if result.follow_ups:
        events.append({"type": "suggestions", "suggestions": result.follow_ups})
    done: dict[str, Any] = {
        "type": "done",
        "conversation_id": result.conversation_id,
        "message_id": result.message_id,
        "user_message_id": result.user_message_id,
        "provider": result.provider,
        "model": result.model,
    }
    if result.replayed:
        done["replayed"] = True
    events.append(done)
    return events


# ---------------------------------------------------------------------------
# One turn
# ---------------------------------------------------------------------------


class _Turn:
    """The model and tool loop of one turn, and what it records.

    ``base`` is the bounded history ending with the question; each tool round
    (an assistant tool call and its results) is kept whole in ``rounds`` so
    the request can shed old rounds without breaking the transcript.
    """

    def __init__(
        self,
        *,
        provider: ChatProvider,
        tools: ToolBackend,
        catalog: list[ChatToolContract],
        policy: SessionPolicy,
        base: list[dict[str, Any]],
        tokens_before: int,
        is_cancelled: IsCancelled,
        shows_views: bool = False,
        finds_suspects: bool = False,
        draws_charts: bool = False,
        question: str = "",
        prior_calls: list[dict[str, Any]] | None = None,
        artifacts: ArtifactIndex | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.catalog = {tool.entry.name: tool for tool in catalog}
        self.shows_views = shows_views
        self.finds_suspects = finds_suspects
        self.draws_charts = draws_charts
        #: Earlier turns' results stay citable by show_view.
        self.views = TurnViews(artifacts)
        self.policy = policy
        self.base = base
        self.rounds: list[list[dict[str, Any]]] = []
        self.tokens_before = tokens_before
        self.is_cancelled = is_cancelled
        self.text_parts: list[str] = []
        #: Text streamed this turn in UTF-16 units, the offsets a browser uses.
        self.streamed_units = 0
        self.question = question
        #: Earlier turns' tool calls: a figure restated from them is still a tool's.
        self.prior_calls = prior_calls or []
        self.provider_content: list[dict[str, Any]] | None = None
        self.tool_calls: list[dict[str, Any]] = []
        self.stop_reason: StopReason = "answered"
        self.error: str | None = None
        self.model_calls = 0
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None

    @property
    def usage(self) -> TurnUsage:
        return TurnUsage(self.input_tokens, self.output_tokens, self.model_calls)

    @property
    def stopped(self) -> bool:
        return self.stop_reason in ("cancelled", "error")

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one tool the session admits; also handed to providers that loop internally.

        Each result is stamped with the artifact id ``show_view`` takes. A
        result is JSON-safe from here on, so a backend's datetime never fails
        the request, the stream or the store: the local backend and
        ``find_suspects`` already serialize theirs, any other backend's is
        serialized here, once.
        """
        if name == SHOW_VIEW_TOOL and self.shows_views:
            return self.views.show(arguments)
        if name == CHART_TOOL and self.draws_charts:
            return await run_chart(arguments, getattr(self.tools, "repo_alias", None), self.views)
        if name == SUSPECTS_TOOL and self.finds_suspects:
            result = await run_find_suspects(arguments, getattr(self.tools, "repo_alias", None))
            return self.views.record(name, arguments, result, evidence_basis="inferred")
        tool = self.catalog.get(name)
        if tool is None:
            return {
                "error": f"Tool is not available in this session: {name}",
                "error_code": "tool_not_enabled",
            }
        result = await self.tools.execute(name, arguments)
        if not isinstance(self.tools, LocalToolBackend):
            result = make_json_serializable(result)
        return self.views.record(
            name,
            arguments,
            result,
            artifact_type=tool.entry.artifact_type,
            evidence_basis=tool.entry.evidence_basis,
        )

    async def ground(self, page_context: Any) -> dict[str, Any] | None:
        """Make the one read the page already justifies, before the first model call.

        A repeat of a call the history already holds is skipped.
        """
        plan = plan_grounding(page_context, (tool.entry for tool in self.catalog.values()))
        if plan is not None and _history_has_call(self.base, plan.tool_name, plan.arguments):
            plan = None
        grounding = await run_grounding(plan, self.execute, unstamp=self.views.unstamp)
        if grounding is None:
            return None
        self.rounds.append(grounding.llm_messages())
        self.tool_calls.append(
            _stored_tool_call(
                grounding.tool_id,
                grounding.tool_name,
                grounding.arguments,
                grounding.summary,
                grounding.artifact,
                origin="grounding",
            )
        )
        return grounding.sse_payload()

    def _over_budget(self) -> bool:
        used = self.usage.total
        policy = self.policy
        if policy.max_turn_tokens is not None and used >= policy.max_turn_tokens:
            return True
        return (
            policy.max_session_tokens is not None
            and self.tokens_before + used >= policy.max_session_tokens
        )

    async def run(self, system_prompt: str, schemas: list[dict[str, Any]]) -> AsyncIterator[dict]:
        """Alternate model calls and tool runs until the model answers or a limit stops it."""
        for _ in range(self.policy.max_tool_loops):
            if self._over_budget():
                self.stop_reason = "token_budget"
                break
            pending: list[dict[str, Any]] = []
            async for event in self._model_call(system_prompt, schemas, pending):
                yield event
            if self.stopped or not pending:
                return
            async for event in self._run_pending(pending):
                yield event
            if self.stopped:
                return
        else:
            # Every call ended in a tool call, so no final answer exists.
            self.stop_reason = "loop_cap"
        yield {
            "type": "truncated",
            "loops": self.policy.max_tool_loops
            if self.stop_reason == "loop_cap"
            else self.model_calls,
            "reason": self.stop_reason,
        }

    async def _model_call(
        self, system_prompt: str, schemas: list[dict[str, Any]], pending: list[dict[str, Any]]
    ) -> AsyncIterator[dict]:
        """Stream one provider call, collecting the tool calls left to us in *pending*.

        A provider that reports a context overflow before anything streamed is
        asked once more with only the question and the newest tool round.
        """
        self.model_calls += 1
        for attempt in range(2):
            emitted = False
            try:
                async for event in self.provider.stream_chat(
                    messages=compose_request(self.base, self.rounds, emergency=attempt > 0),
                    tools=schemas,
                    system_prompt=system_prompt,
                    max_tokens=self.policy.max_output_tokens,
                    temperature=self.policy.temperature,
                    tool_executor=self.execute,
                ):
                    if await self.is_cancelled():
                        self.stop_reason = "cancelled"
                        return
                    for out in self._on_provider_event(event, pending):
                        emitted = True
                        yield out
                return
            except Exception as exc:
                # A provider failure is the person's to fix; anything else is a bug.
                message = provider_failure(exc, self.provider.provider_name)
                if message is None:
                    raise
                overflow = is_context_overflow(exc)
                if overflow and attempt == 0 and not emitted:
                    logger.warning("chat_context_overflow_retry")
                    pending.clear()
                    continue
                self.stop_reason = "error"
                self.error = OVERFLOW_MESSAGE if overflow else message
                yield {"type": "error", "message": self.error}
                return

    def _on_provider_event(self, event: Any, pending: list[dict[str, Any]]) -> list[dict]:
        if event.type == "text_delta" and event.text:
            self.text_parts.append(event.text)
            self.streamed_units += len(event.text.encode("utf-16-le")) // 2
            return [{"type": "text_delta", "text": event.text}]
        if event.type == "usage":
            self.input_tokens = (self.input_tokens or 0) + event.input_tokens
            self.output_tokens = (self.output_tokens or 0) + event.output_tokens
            return []
        if event.type == "assistant_content":
            self.provider_content = event.content_blocks
            return []
        tc = event.tool_call
        if not tc:
            return []
        if event.type == "tool_start":
            pending.append({"id": tc.id, "name": tc.name, "arguments": tc.arguments})
            return [
                {
                    "type": "tool_start",
                    "tool_id": tc.id,
                    "tool_name": tc.name,
                    "input": tc.arguments,
                }
            ]
        if event.type == "tool_result":
            # The provider already ran this tool (e.g. Gemini), so it leaves the pending list.
            pending[:] = [p for p in pending if p["id"] != tc.id]
            return self._tool_result(tc.id, tc.name, tc.arguments, event.tool_result_data or {})
        return []

    async def _run_pending(self, pending: list[dict[str, Any]]) -> AsyncIterator[dict]:
        """Run the tool calls the provider left to us and feed the results back."""
        round_messages = [
            assistant_tool_call_message(
                "".join(self.text_parts), pending, provider_content=self.provider_content
            )
        ]
        self.rounds.append(round_messages)
        self.text_parts.clear()
        self.provider_content = None
        for tc in pending:
            if await self.is_cancelled():
                self.stop_reason = "cancelled"
                return
            result = await self.execute(tc["name"], tc["arguments"])
            for event in self._tool_result(tc["id"], tc["name"], tc["arguments"], result):
                yield event
            round_messages.append(tool_result_message(tc["id"], tc["name"], result))

    def _tool_result(
        self, tool_id: str, name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Record one tool result as a stored call and return its events: the
        result, then the view a successful ``show_view`` built."""
        tool = self.catalog.get(name)
        # The stored artifact keeps the tool's own data; only the model's copy is stamped.
        artifact_id, data = self.views.unstamp(result)
        summary = _build_tool_summary(name, data)
        artifact = create_artifact_envelope(
            tool_name=name,
            artifact_type=tool.entry.artifact_type if tool else "generic",
            presentation=tool.entry.presentation if tool else "generic",
            data=data,
            title=summary,
            artifact_id=artifact_id,
            evidence_basis=tool.entry.evidence_basis if tool else "unknown",
        )
        self.tool_calls.append(_stored_tool_call(tool_id, name, arguments, summary, artifact))
        event = {
            "type": "tool_result",
            "tool_id": tool_id,
            "tool_name": name,
            "summary": summary,
            "artifact": artifact,
        }
        view = self.views.event(tool_id, result) if name in VIEW_TOOLS else None
        return [event] if view is None else [event, view]

    def reply_content(self) -> dict[str, Any]:
        content: dict[str, Any] = {
            "text": "".join(self.text_parts),
            "tool_calls": self.tool_calls,
            "provider": self.provider.provider_name,
            "model": self.provider.model_name,
            "usage": asdict(self.usage),
        }
        if self.stop_reason in ("loop_cap", "token_budget"):
            content["truncated"] = True
            content["stop_reason"] = self.stop_reason
        # A turn that read nothing, or read only failures, has no next step to propose.
        follow_ups = follow_up_suggestions(self.tool_calls)
        if follow_ups:
            content["follow_ups"] = follow_ups
        if self.views.parts:
            content["views"] = self.views.parts
        return content

    async def check_figures(self, content: dict[str, Any]) -> None:
        """Add the numeric check to *content*; CPU-bound, so off the event loop."""
        check = await asyncio.to_thread(
            check_figures,
            content["text"],
            [
                *_artifact_sources(self.tool_calls),
                *((part["source"]["artifact_id"], part) for part in self.views.parts),
            ],
            self.question,
            _artifact_sources(self.prior_calls),
        )
        if check.figures:
            content["figures"] = [figure.to_json() for figure in check.figures]
        if diagnostics := check.diagnostics():
            content["diagnostics"] = diagnostics

    @property
    def answer_shift(self) -> int:
        """UTF-16 units streamed before the final answer's text."""
        answer = "".join(self.text_parts)
        return self.streamed_units - len(answer.encode("utf-16-le")) // 2


def _artifact_sources(tool_calls: list[dict[str, Any]]) -> list[tuple[str | None, Any]]:
    """``(artifact_id, data)`` of each stored tool call that has a result."""
    sources: list[tuple[str | None, Any]] = []
    for call in tool_calls:
        artifact = call.get("artifact") or {}
        if isinstance(artifact, dict) and "data" in artifact:
            sources.append((artifact.get("id"), artifact["data"]))
    return sources


def _stored_tool_call(
    tool_id: str,
    name: str,
    arguments: dict[str, Any],
    summary: str,
    artifact: dict[str, Any],
    origin: str | None = None,
) -> dict[str, Any]:
    """Persist one durable artifact payload with the assistant message.

    ``origin`` marks a call the server made for the page rather than the
    model; absent for model-made calls so stored rows keep their shape.
    """
    stored = {
        "id": tool_id,
        "name": name,
        "arguments": arguments,
        "summary": summary,
        "artifact": artifact,
    }
    if origin:
        stored["origin"] = origin
    return stored


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------


@dataclass
class _Outcome:
    result: ChatTurnResult | None = None


class ChatSession:
    """One conversation and the limits it runs under. Open with :meth:`open`."""

    def __init__(
        self,
        *,
        conversation: StoredConversation,
        created: bool,
        scope: SessionScope,
        policy: SessionPolicy,
        provider: ChatProvider | None,
        principal: Principal | None,
        store: ConversationStore,
        tools: ToolBackend,
        hooks: TurnHooks,
    ) -> None:
        self.conversation = conversation
        self.created = created
        self.scope = scope
        self.policy = policy
        self.provider = provider
        self.principal = principal
        self.store = store
        self.tools = tools
        self.hooks = hooks

    @property
    def id(self) -> str:
        return self.conversation.id

    @classmethod
    async def open(
        cls,
        *,
        scope: SessionScope,
        provider: ChatProvider | None = None,
        principal: Principal | None = None,
        policy: SessionPolicy | None = None,
        fallback_policy: SessionPolicy = DEFAULT_POLICY,
        conversation_id: str | None = None,
        namespace: str | None = None,
        external_ref: str | None = None,
        title: str = "New conversation",
        store: ConversationStore | None = None,
        tools: ToolBackend | None = None,
        hooks: TurnHooks | None = None,
    ) -> ChatSession:
        """Resume *conversation_id*, get or create the one ``(namespace,
        external_ref)`` names, or start a new one. A session opened without a
        *provider* can be inspected but not sent to.

        *policy* is frozen on a conversation this call creates; a resumed one
        keeps the policy it was opened with, or *fallback_policy* if it has
        none. Raises :class:`SessionNotFoundError` for a missing or deleted
        conversation, or one stored with another repository.
        """
        store = store if store is not None else conversation_stores.get()(scope)
        if conversation_id is not None:
            conversation = await store.get(conversation_id)
            created = False
        else:
            conversation, created = await store.get_or_create(
                repository_id=scope.repo_id,
                title=title,
                scope=scope.stored(),
                namespace=namespace,
                external_ref=external_ref,
                principal_id=principal.id if principal is not None else None,
                policy_json=policy.to_json() if policy is not None else None,
            )
        if (
            conversation is None
            or conversation.deleted
            or conversation.repository_id != scope.repo_id
        ):
            raise SessionNotFoundError("Conversation not found")
        return cls(
            conversation=conversation,
            created=created,
            scope=scope,
            policy=SessionPolicy.from_json(
                conversation.policy_json, default=policy or fallback_policy
            ),
            provider=provider,
            principal=principal,
            store=store,
            tools=tools if tools is not None else tool_backends.get()(scope, principal),
            hooks=hooks if hooks is not None else turn_hooks.get(),
        )

    def catalog(self) -> list[ChatToolContract]:
        """The tools this session may show and run: backend ∩ principal ∩ policy."""
        return [
            tool
            for tool in self.tools.catalog_for(self.policy, self.principal)
            if self.policy.admits(tool)
        ]

    def stream(
        self,
        message: str,
        *,
        context: Any = None,
        idempotency_key: str | None = None,
        is_cancelled: IsCancelled | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one turn, yielding its events."""
        return self._events(message, context, idempotency_key, is_cancelled, _Outcome())

    async def send(
        self,
        message: str,
        *,
        context: Any = None,
        idempotency_key: str | None = None,
        is_cancelled: IsCancelled | None = None,
    ) -> ChatTurnResult:
        """Run one turn and return its result."""
        outcome = _Outcome()
        async for _ in self._events(message, context, idempotency_key, is_cancelled, outcome):
            pass
        assert outcome.result is not None
        return outcome.result

    async def _events(
        self,
        message: str,
        context: Any,
        idempotency_key: str | None,
        is_cancelled: IsCancelled | None,
        outcome: _Outcome,
    ) -> AsyncIterator[dict[str, Any]]:
        if self.provider is None:
            outcome.result = ChatTurnResult(
                conversation_id=self.id,
                message_id=None,
                user_message_id=None,
                answer="",
                stop_reason="error",
                provider="",
                model="",
                error=NO_PROVIDER_MESSAGE,
            )
            yield {"type": "error", "message": NO_PROVIDER_MESSAGE}
            return
        async with _turn_lock(self.id):
            try:
                async for event in self._turn(
                    message, context, idempotency_key, is_cancelled or _never_cancelled, outcome
                ):
                    yield event
                    for extra in await self.hooks.on_event(event):
                        yield extra
            except IdempotencyConflictError:
                raise
            except Exception as exc:
                logger.exception("Chat turn error")
                text = internal_error(exc)
                outcome.result = self._unstored("error", error=text)
                yield {"type": "error", "message": text}

    async def check_send(self, message: str, idempotency_key: str | None) -> None:
        """Raise :class:`IdempotencyConflictError` when *idempotency_key* was
        already sent with another message. ``send`` and ``stream`` check too;
        this lets a transport refuse before it starts a response."""
        if idempotency_key is None:
            return
        sent = await self.store.find_sent(self.id, idempotency_key)
        if sent is not None:
            _check_same_message(sent[0], message, idempotency_key)

    def _unstored(
        self, stop_reason: StopReason, *, error: str | None = None, **values: Any
    ) -> ChatTurnResult:
        return ChatTurnResult(
            conversation_id=self.id,
            message_id=None,
            user_message_id=values.pop("user_message_id", None),
            answer=values.pop("answer", ""),
            stop_reason=stop_reason,
            provider=self.provider.provider_name,
            model=self.provider.model_name,
            error=error,
            **values,
        )

    async def _refusal(self, conversation: StoredConversation) -> tuple[StopReason, str] | None:
        policy = self.policy
        if policy.max_turns is not None and conversation.turns >= policy.max_turns:
            return "turn_budget", "This conversation has used its turn budget."
        if (
            policy.max_session_tokens is not None
            and conversation.tokens_used >= policy.max_session_tokens
        ):
            return "token_budget", "This conversation has used its token budget."
        reason = await self.hooks.preflight(self.principal, policy, conversation)
        return ("refused", reason) if reason else None

    async def _turn(
        self,
        message: str,
        context: Any,
        idempotency_key: str | None,
        is_cancelled: IsCancelled,
        outcome: _Outcome,
    ) -> AsyncIterator[dict[str, Any]]:
        conversation = await self.store.get(self.id)
        if conversation is None or conversation.deleted:
            outcome.result = self._unstored("error", error="Conversation not found")
            yield {"type": "error", "message": "Conversation not found"}
            return
        self.conversation = conversation

        user_message_id: str | None = None
        if idempotency_key is not None:
            sent = await self.store.find_sent(self.id, idempotency_key)
            if sent is not None:
                user_message, reply = sent
                _check_same_message(user_message, message, idempotency_key)
                user_message_id = user_message.id
                if reply is not None:
                    outcome.result = _replayed(reply, self.id, user_message_id)
                    if outcome.result.answer:
                        yield {"type": "text_delta", "text": outcome.result.answer}
                    for event in _closing_events(outcome.result):
                        yield event
                    return
                # That send stored its question and then failed; run it again on it.

        refusal = await self._refusal(conversation)
        if refusal is not None:
            stop_reason, reason = refusal
            outcome.result = self._unstored(stop_reason, error=reason)
            yield {"type": "error", "message": reason}
            return

        if user_message_id is None:
            user_message_id = await self.store.add_user_message(
                self.id, {"text": message}, idempotency_key=idempotency_key
            )
        history = await self.store.messages(self.id)
        # Indexed once for the turn, so each show_view is a dict lookup.
        artifacts = ArtifactIndex.from_history(history)
        opening = all(m.id == user_message_id for m in history)

        catalog = self.catalog()
        # show_view is chat-only and read-only: never an MCP tool, offered
        # whenever there is a tool whose result it could be built from.
        shows_views = bool(catalog) and self.policy.admits_name(SHOW_VIEW_TOOL, "read_only")
        # find_suspects is chat-only too: it reads the index in process, so it
        # needs the local backend and follows get_context's admission.
        finds_suspects = (
            isinstance(self.tools, LocalToolBackend)
            and any(tool.entry.name == SCOPE_TOOL for tool in catalog)
            and self.policy.admits_name(SUSPECTS_TOOL, "read_only")
        )
        # chart reads the same index in process, so it is local like find_suspects.
        draws_charts = (
            shows_views
            and isinstance(self.tools, LocalToolBackend)
            and self.policy.admits_name(CHART_TOOL, "read_only")
        )
        schemas = tool_schemas(catalog)
        if draws_charts:
            schemas.append(chart_schema())
        if shows_views:
            schemas.append(show_view_schema([tool.entry.artifact_type for tool in catalog]))
        if finds_suspects:
            schemas.append(find_suspects_schema())
        turn = _Turn(
            provider=self.provider,
            tools=self.tools,
            catalog=catalog,
            policy=self.policy,
            base=bound_history(
                _with_navigation_context(stored_messages_to_llm_format(history), context)
            ),
            tokens_before=conversation.tokens_used,
            is_cancelled=is_cancelled,
            shows_views=shows_views,
            finds_suspects=finds_suspects,
            draws_charts=draws_charts,
            question=message,
            prior_calls=[
                call
                for stored in history
                if stored.id != user_message_id
                for call in stored.content.get("tool_calls") or []
                if isinstance(call, dict)
            ],
            artifacts=artifacts,
        )
        grounded = await turn.ground(context)
        if grounded is not None:
            yield grounded
        system_prompt = _build_system_prompt(
            self.scope.repo_name,
            self.scope.repo_path,
            catalog,
            context,
            shows_views=shows_views,
            draws_charts=draws_charts,
        )
        async for event in turn.run(system_prompt, schemas):
            yield event
        if turn.stopped:
            # Nothing is stored for a cancelled or failed turn; the question stays.
            outcome.result = self._unstored(
                turn.stop_reason,
                error=turn.error,
                user_message_id=user_message_id,
                answer="".join(turn.text_parts),
                usage=turn.usage,
            )
            return

        content = turn.reply_content()
        await turn.check_figures(content)
        title = conversation_title(message, tool_names(turn.tool_calls)) if opening else None
        message_id = await self.store.add_reply(
            self.id, content, title=title, usage=turn.usage
        )
        await self.hooks.on_usage(self.principal, conversation, turn.usage)
        outcome.result = _result_from_content(
            content,
            conversation_id=self.id,
            message_id=message_id,
            user_message_id=user_message_id,
            stop_reason=turn.stop_reason,
        )
        for event in _closing_events(outcome.result, turn.answer_shift):
            yield event


def _check_same_message(sent: StoredMessage, message: str, idempotency_key: str) -> None:
    if sent.content.get("text") != message:
        raise IdempotencyConflictError(
            f"idempotency key {idempotency_key!r} was already sent with a different message"
        )


def _replayed(reply: StoredMessage, conversation_id: str, user_message_id: str) -> ChatTurnResult:
    content = reply.content
    return _result_from_content(
        content,
        conversation_id=conversation_id,
        message_id=reply.id,
        user_message_id=user_message_id,
        stop_reason=content.get("stop_reason", "answered"),
        replayed=True,
    )
