"""Conformance suite for ChatSession and its seams.

Every test runs the real session engine against a scripted provider and a fake
``ToolBackend``; the store-dependent ones run against both an in-memory
``ConversationStore`` and the local SQL store, so another store can join the
``store`` fixture and must pass the same checks.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import asdict, replace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import Repository
from repowise.core.providers.llm.base import ChatStreamEvent, ChatToolCall, ProviderError
from repowise.core.registry import ToolEntry
from repowise.server.chat_context import (
    OVERFLOW_MESSAGE,
    bound_history,
    bound_tool_messages,
    compose_request,
)
from repowise.server.chat_seams import (
    ConversationStore,
    NoTurnHooks,
    SessionPolicy,
    SessionScope,
    StoredConversation,
    StoredMessage,
    ToolBackend,
    TurnUsage,
)
from repowise.server.chat_session import ChatSession, SessionNotFoundError
from repowise.server.chat_store import SqlConversationStore
from repowise.server.chat_tools import ChatToolContract
from repowise.server.chat_views import SHOW_VIEW_TOOL

_REPO_ID = "repo-1"
_SCOPE = SessionScope(kind="repo", repo_id=_REPO_ID, repo_name="demo", repo_path="/tmp/demo")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _text(text: str) -> ChatStreamEvent:
    return ChatStreamEvent(type="text_delta", text=text)


def _tool(tool_id: str, name: str, arguments: dict | None = None) -> ChatStreamEvent:
    return ChatStreamEvent(
        type="tool_start",
        tool_call=ChatToolCall(id=tool_id, name=name, arguments=arguments or {}),
    )


def _usage(input_tokens: int, output_tokens: int) -> ChatStreamEvent:
    return ChatStreamEvent(type="usage", input_tokens=input_tokens, output_tokens=output_tokens)


class ScriptedProvider:
    """Plays one scripted turn per call; a turn may be an exception to raise."""

    provider_name = "test"
    model_name = "test-model"

    def __init__(self, turns: Iterable[Any] | Callable[[int], Any]) -> None:
        self._turns = turns if callable(turns) else list(turns)
        self.calls: list[dict[str, Any]] = []

    async def stream_chat(self, **kwargs) -> AsyncIterator[ChatStreamEvent]:
        index = len(self.calls)
        self.calls.append(
            {
                "messages": [dict(m) for m in kwargs["messages"]],
                "tools": [t["function"]["name"] for t in kwargs["tools"]],
                "max_tokens": kwargs["max_tokens"],
            }
        )
        if callable(self._turns):
            turn = self._turns(index)
        else:
            turn = self._turns[index] if index < len(self._turns) else []
        if isinstance(turn, BaseException):
            raise turn
        for event in turn:
            yield event


class FakeTools:
    """One read-only, one generative and one mutating tool; records every run."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.catalog_requests: list[Any] = []

    def catalog_for(self, policy, principal) -> list[ChatToolContract]:
        self.catalog_requests.append(principal)
        risk = ToolEntry(
            fn=_noop,
            name="get_risk",
            artifact_type="risk",
            presentation="table",
            evidence_basis="measured",
        )
        return [
            ChatToolContract(entry=entry, description=entry.name, parameters={})
            for entry in (
                risk,
                ToolEntry(fn=_noop, name="get_context"),
                ToolEntry(fn=_noop, name="gen_code", safety="generative"),
                ToolEntry(fn=_noop, name="set_status", safety="mutating"),
            )
        ]

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.executed.append(name)
        return {"ok": True, "citations": ["src/a.py"], "files": [{"path": "src/a.py", "fixes": 3}]}


async def _noop() -> dict:
    return {}


class MemoryStore:
    """A ConversationStore with no database, the reference for the conformance suite."""

    def __init__(self) -> None:
        self.conversations: dict[str, StoredConversation] = {}
        self.messages_by_conv: dict[str, list[tuple[StoredMessage, str | None]]] = {}

    async def get_or_create(self, *, repository_id, title, scope, namespace, external_ref, principal_id, policy_json):
        if namespace is not None and external_ref is not None:
            found = await self.find(namespace, external_ref)
            if found is not None:
                return found, False
        conv = StoredConversation(
            id=f"c{len(self.conversations) + 1}",
            repository_id=repository_id,
            title=title,
            scope=scope,
            namespace=namespace,
            external_ref=external_ref,
            principal_id=principal_id,
            policy_json=policy_json,
            input_tokens=0,
            output_tokens=0,
            turns=0,
            deleted=False,
        )
        self.conversations[conv.id] = conv
        self.messages_by_conv[conv.id] = []
        return conv, True

    async def get(self, conversation_id):
        return self.conversations.get(conversation_id)

    async def find(self, namespace, external_ref):
        return next(
            (
                c
                for c in self.conversations.values()
                if c.namespace == namespace and c.external_ref == external_ref
            ),
            None,
        )

    async def messages(self, conversation_id):
        return [m for m, _ in self.messages_by_conv[conversation_id]]

    async def find_sent(self, conversation_id, idempotency_key):
        rows = self.messages_by_conv[conversation_id]
        for index, (message, key) in enumerate(rows):
            if key == idempotency_key:
                after = rows[index + 1 : index + 2]
                reply = after[0][0] if after and after[0][0].role == "assistant" else None
                return message, reply
        return None

    async def add_user_message(self, conversation_id, content, *, idempotency_key):
        return self._add(conversation_id, "user", content, idempotency_key)

    async def add_reply(self, conversation_id, content, *, title, usage):
        message_id = self._add(conversation_id, "assistant", content, None)
        conv = self.conversations[conversation_id]
        self.conversations[conversation_id] = replace(
            conv,
            title=title if title is not None else conv.title,
            turns=conv.turns + 1,
            input_tokens=conv.input_tokens + (usage.input_tokens or 0),
            output_tokens=conv.output_tokens + (usage.output_tokens or 0),
        )
        return message_id

    def _add(self, conversation_id, role, content, key) -> str:
        rows = self.messages_by_conv[conversation_id]
        message = StoredMessage(id=f"{conversation_id}-m{len(rows) + 1}", role=role, content=content)
        rows.append((message, key))
        return message.id


async def _sql_store() -> SqlConversationStore:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        session.add(
            Repository(
                id=_REPO_ID,
                name="demo",
                url="https://example.com/demo",
                local_path="/tmp/demo",
                default_branch="main",
                settings_json="{}",
            )
        )
        await session.commit()
    return SqlConversationStore(factory)


@pytest.fixture(params=["memory", "sql"])
async def store(request) -> ConversationStore:
    return MemoryStore() if request.param == "memory" else await _sql_store()


async def _open(store, provider, *, policy=None, tools=None, hooks=None, **kwargs) -> ChatSession:
    return await ChatSession.open(
        scope=_SCOPE,
        provider=provider,
        policy=policy,
        store=store,
        tools=tools or FakeTools(),
        hooks=hooks or NoTurnHooks(),
        **kwargs,
    )


async def _collect(stream: AsyncIterator[dict]) -> list[dict]:
    return [event async for event in stream]


def test_the_fakes_satisfy_the_seam_protocols():
    assert isinstance(FakeTools(), ToolBackend)
    assert isinstance(MemoryStore(), ConversationStore)


# ---------------------------------------------------------------------------
# Tool allowlist: what the model sees and what can run
# ---------------------------------------------------------------------------


async def test_default_policy_shows_only_read_only_tools(store):
    provider = ScriptedProvider([[_text("hi")]])
    session = await _open(store, provider)

    await session.send("hello")

    assert provider.calls[0]["tools"] == ["get_risk", "get_context", SHOW_VIEW_TOOL]


async def test_allowlist_narrows_the_schemas_sent_to_the_model(store):
    provider = ScriptedProvider([[_text("hi")]])
    session = await _open(store, provider, policy=SessionPolicy(tool_allowlist=frozenset({"get_risk"})))

    await session.send("hello")

    assert provider.calls[0]["tools"] == ["get_risk"]


async def test_a_hidden_tool_named_by_the_model_never_runs(store):
    tools = FakeTools()
    provider = ScriptedProvider([[_tool("t1", "gen_code"), _tool("t2", "get_context")], [_text("done")]])
    session = await _open(
        store, provider, tools=tools, policy=SessionPolicy(tool_allowlist=frozenset({"get_risk", "gen_code"}))
    )

    result = await session.send("generate")

    # gen_code is allowlisted but above the read-only ceiling; get_context is
    # under the ceiling but not allowlisted. Neither reaches the backend.
    assert tools.executed == []
    errors = {t.name: t.ok for t in result.tool_trace}
    assert errors == {"gen_code": False, "get_context": False}
    tool_messages = [m for m in provider.calls[1]["messages"] if m["role"] == "tool"]
    assert all(json.loads(m["content"])["error_code"] == "tool_not_enabled" for m in tool_messages)


async def test_artifact_metadata_comes_from_the_catalog_entry(store):
    provider = ScriptedProvider([[_tool("t1", "get_risk"), _tool("t2", "missing")], [_text("ok")]])
    session = await _open(store, provider)

    events = await _collect(session.stream("q"))

    artifacts = {e["tool_name"]: e["artifact"] for e in events if e["type"] == "tool_result"}
    assert (artifacts["get_risk"]["type"], artifacts["get_risk"]["presentation"]) == ("risk", "table")
    assert artifacts["get_risk"]["evidence"]["basis"] == "measured"
    assert (artifacts["missing"]["type"], artifacts["missing"]["presentation"]) == ("generic", "generic")
    assert artifacts["missing"]["evidence"]["basis"] == "unknown"


async def test_the_catalog_is_asked_for_the_sessions_principal(store):
    from repowise.server.auth import Principal

    tools = FakeTools()
    principal = Principal("token:t", "token")
    session = await _open(store, ScriptedProvider([[_text("x")]]), tools=tools, principal=principal)

    await session.send("hi")

    assert tools.catalog_requests == [principal]


# ---------------------------------------------------------------------------
# Limits and stop reasons
# ---------------------------------------------------------------------------


async def test_the_loop_cap_stops_the_turn_and_says_so(store):
    provider = ScriptedProvider(lambda i: [_tool(f"t{i}", "get_risk")])
    session = await _open(store, provider, policy=SessionPolicy(max_tool_loops=3))

    events = await _collect(session.stream("loop"))
    result = await session.send("loop again")

    assert len(provider.calls) == 6
    truncated = next(e for e in events if e["type"] == "truncated")
    assert truncated == {"type": "truncated", "loops": 3, "reason": "loop_cap"}
    assert events[-1]["type"] == "done"
    assert result.stop_reason == "loop_cap"
    assert result.message_id is not None


async def test_a_turn_token_budget_stops_before_the_next_model_call(store):
    provider = ScriptedProvider(lambda i: [_tool(f"t{i}", "get_risk"), _usage(400, 200)])
    session = await _open(store, provider, policy=SessionPolicy(max_turn_tokens=1000))

    result = await session.send("spend")

    assert len(provider.calls) == 2
    assert result.stop_reason == "token_budget"
    assert result.usage == TurnUsage(800, 400, 2)


async def test_a_session_token_budget_refuses_the_next_turn(store):
    provider = ScriptedProvider(lambda i: [_text("ok"), _usage(700, 300)])
    session = await _open(store, provider, policy=SessionPolicy(max_session_tokens=1000))

    first = await session.send("one")
    second = await session.send("two")

    assert first.stop_reason == "answered"
    assert second.stop_reason == "token_budget"
    assert second.message_id is None
    assert len(provider.calls) == 1


async def test_a_turn_budget_refuses_the_turn_after_the_last(store):
    provider = ScriptedProvider(lambda i: [_text("ok")])
    session = await _open(store, provider, policy=SessionPolicy(max_turns=1))

    await session.send("one")
    second = await session.send("two")

    assert second.stop_reason == "turn_budget"
    assert len(provider.calls) == 1


async def test_usage_is_unknown_when_the_provider_reports_none(store):
    session = await _open(store, ScriptedProvider([[_text("ok")]]))

    result = await session.send("hi")

    assert result.usage == TurnUsage(None, None, 1)


async def test_a_cancelled_turn_stores_no_reply(store):
    provider = ScriptedProvider([[_text("partial"), _text(" more")]])
    session = await _open(store, provider)
    calls = 0

    async def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    result = await session.send("hi", is_cancelled=cancelled)

    assert result.stop_reason == "cancelled"
    assert result.message_id is None
    assert [m.role for m in await store.messages(session.id)] == ["user"]


async def test_preflight_refusal_runs_nothing(store):
    class Refusing(NoTurnHooks):
        async def preflight(self, principal, policy, conversation):
            return "allowance used up"

    provider = ScriptedProvider([[_text("never")]])
    session = await _open(store, provider, hooks=Refusing())

    events = await _collect(session.stream("hi"))

    assert events == [{"type": "error", "message": "allowance used up"}]
    assert provider.calls == []
    assert await store.messages(session.id) == []


async def test_hooks_see_usage_and_can_add_events(store):
    seen: list[TurnUsage] = []

    class Metering(NoTurnHooks):
        async def on_usage(self, principal, conversation, usage):
            seen.append(usage)

        async def on_event(self, event):
            return [{"type": "allowance", "left": 1}] if event["type"] == "done" else []

    session = await _open(store, ScriptedProvider([[_text("ok"), _usage(5, 6)]]), hooks=Metering())

    events = await _collect(session.stream("hi"))

    assert seen == [TurnUsage(5, 6, 1)]
    assert [e["type"] for e in events][-2:] == ["done", "allowance"]


# ---------------------------------------------------------------------------
# Conversations: get-or-create, idempotency, turn lock
# ---------------------------------------------------------------------------


async def test_get_or_create_resumes_by_namespace_and_external_ref(store):
    provider = ScriptedProvider(lambda i: [_text("ok")])
    first = await _open(
        store, provider, namespace="bot", external_ref="T1/C2/123.4", policy=SessionPolicy(max_tool_loops=4)
    )
    again = await _open(
        store, provider, namespace="bot", external_ref="T1/C2/123.4", policy=SessionPolicy(max_tool_loops=9)
    )
    other = await _open(store, provider, namespace="other", external_ref="T1/C2/123.4")

    assert first.created and not again.created and other.created
    assert again.id == first.id != other.id
    # The policy is frozen when the conversation is created.
    assert again.policy.max_tool_loops == 4


async def test_a_conversation_of_another_repository_is_not_found(store):
    session = await _open(store, ScriptedProvider([]))
    elsewhere = replace(_SCOPE, repo_id="repo-2")

    with pytest.raises(SessionNotFoundError):
        await ChatSession.open(
            scope=elsewhere, provider=ScriptedProvider([]), conversation_id=session.id, store=store, tools=FakeTools()
        )


async def test_a_retried_send_returns_the_stored_reply(store):
    provider = ScriptedProvider(lambda i: [_tool(f"t{i}", "get_risk")] if i == 0 else [_text("answer")])
    session = await _open(store, provider)

    first = await session.send("q", idempotency_key="k1")
    events = await _collect(session.stream("q", idempotency_key="k1"))
    again = await session.send("q", idempotency_key="k1")

    assert len(provider.calls) == 2
    assert again.replayed and not first.replayed
    assert again.message_id == first.message_id
    assert again.answer == first.answer == "answer"
    assert again.citations == first.citations
    (citation,) = first.citations
    stored = (await store.messages(session.id))[-1].content
    assert citation == {
        "file_path": "src/a.py",
        "artifact_id": stored["tool_calls"][0]["artifact"]["id"],
        "ref": {"type": "file", "id": "src/a.py"},
    }
    assert events[-1]["type"] == "done" and events[-1]["replayed"] is True
    assert [m.role for m in await store.messages(session.id)] == ["user", "assistant"]


async def test_a_retry_of_a_failed_send_runs_on_the_stored_question(store):
    provider = ScriptedProvider([ProviderError("test", "boom"), [_text("second try")]])
    session = await _open(store, provider)

    failed = await session.send("q", idempotency_key="k1")
    retried = await session.send("q", idempotency_key="k1")

    assert failed.stop_reason == "error" and failed.message_id is None
    assert retried.stop_reason == "answered" and retried.answer == "second try"
    assert retried.user_message_id == failed.user_message_id
    assert [m.role for m in await store.messages(session.id)] == ["user", "assistant"]


async def test_turns_of_one_conversation_never_overlap(store):
    active = 0
    peak = 0

    class SlowProvider(ScriptedProvider):
        async def stream_chat(self, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            yield _text("ok")

    provider = SlowProvider([])
    session = await _open(store, provider)
    twin = await _open(store, provider, conversation_id=session.id)

    await asyncio.gather(session.send("a"), twin.send("b"), session.send("c"))

    assert peak == 1
    assert (await store.get(session.id)).turns == 3


async def test_the_opening_turn_titles_the_conversation_and_later_ones_do_not(store):
    provider = ScriptedProvider(lambda i: [_text("ok")])
    session = await _open(store, provider, title="placeholder")

    await session.send("What are the riskiest files to modify?")
    titled = (await store.get(session.id)).title
    await session.send("And the next ones?")

    assert titled != "placeholder"
    assert (await store.get(session.id)).title == titled


# ---------------------------------------------------------------------------
# Context: overflow retry and trimming
# ---------------------------------------------------------------------------


async def test_an_overflow_is_retried_once_with_the_emergency_request(store):
    provider = ScriptedProvider(
        [
            [_tool("t1", "get_risk")],
            ProviderError("test", "prompt is too long: 300000 tokens > 200000 maximum"),
            [_text("fits now")],
        ]
    )
    session = await _open(store, provider)

    result = await session.send("big question")

    assert result.stop_reason == "answered" and result.answer == "fits now"
    emergency = provider.calls[2]["messages"]
    assert [m["role"] for m in emergency] == ["user", "assistant", "tool"]
    assert emergency[0]["content"] == "big question"


async def test_a_second_overflow_fails_with_a_plain_message(store):
    overflow = ProviderError("test", "context_length_exceeded")
    session = await _open(store, ScriptedProvider([overflow, overflow]))

    result = await session.send("q")

    assert result.stop_reason == "error"
    assert result.error == OVERFLOW_MESSAGE


async def test_an_error_after_streaming_is_not_retried(store):
    class Midway(ScriptedProvider):
        async def stream_chat(self, **kwargs):
            self.calls.append(kwargs)
            yield _text("partial")
            raise ProviderError("test", "context window exceeded")

    provider = Midway([])
    session = await _open(store, provider)

    result = await session.send("q")

    assert len(provider.calls) == 1
    assert result.stop_reason == "error"


def test_history_keeps_the_newest_whole_turns_within_budget():
    turns = []
    for i in range(5):
        turns += [
            {"role": "user", "content": f"q{i}"},
            {"role": "assistant", "content": "x" * 400},
        ]
    turns.append({"role": "user", "content": "now"})

    kept = bound_history(turns, budget=1000)

    assert kept[0]["role"] == "user"
    assert kept[-1] == {"role": "user", "content": "now"}
    assert 1 < len(kept) < len(turns)
    assert [m["content"] for m in kept if m["role"] == "user"] == ["q3", "q4", "now"]


def test_a_huge_question_is_truncated_not_dropped():
    kept = bound_history([{"role": "user", "content": "y" * 50_000}], budget=5_000)

    assert len(kept) == 1
    assert len(kept[0]["content"]) <= 5_000
    assert "omitted" in kept[0]["content"]


def test_an_oversized_tool_result_becomes_a_marked_excerpt():
    message = {"role": "tool", "tool_call_id": "t", "name": "x", "content": "z" * 100_000}

    bounded = bound_tool_messages([message], limit=10_000)[0]

    payload = json.loads(bounded["content"])
    assert payload["truncated"] is True and payload["original_chars"] == 100_000
    assert len(bounded["content"]) < 11_000


def test_a_request_over_budget_sheds_its_oldest_whole_rounds(monkeypatch):
    monkeypatch.setattr("repowise.server.chat_context.REQUEST_CHAR_BUDGET", 2_500)
    base = [{"role": "user", "content": "q"}]
    rounds = [
        [
            {"role": "assistant", "tool_calls": [{"id": f"t{i}"}]},
            {"role": "tool", "tool_call_id": f"t{i}", "content": "r" * 900},
        ]
        for i in range(4)
    ]

    messages = compose_request(base, rounds)

    assert messages[0] == base[0]
    ids = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
    assert ids == ["t2", "t3"]


# ---------------------------------------------------------------------------
# show_view: the chat-only tool that builds views from this turn's results
# ---------------------------------------------------------------------------


def _show_view_turns(provider_ref: list) -> Callable[[int], list]:
    def turns(index: int) -> list:
        if index == 0:
            return [_tool("t1", "get_risk")]
        if index == 1:
            stamped = json.loads(provider_ref[0].calls[1]["messages"][-1]["content"])
            arguments = {"artifact_id": stamped["artifact_id"], "view": "table", "options": {"path": "files"}}
            return [_tool("t2", SHOW_VIEW_TOOL, arguments)]
        return [_text("See the table.")]

    return turns


async def test_show_view_streams_a_view_and_the_result_carries_it(store):
    ref: list = []
    provider = ScriptedProvider(_show_view_turns(ref))
    ref.append(provider)
    tools = FakeTools()
    session = await _open(store, provider, tools=tools)

    events = await _collect(session.stream("table please"))

    types = [e["type"] for e in events]
    assert types[:5] == ["tool_start", "tool_result", "tool_start", "tool_result", "view"]
    assert tools.executed == ["get_risk"]
    view = events[4]["view"]
    assert view["kind"] == "table"
    assert view["source"]["artifact_id"] == events[1]["artifact"]["id"]
    stored = (await store.messages(session.id))[-1].content
    assert stored["views"] == [view]
    replay = await session.send("again")
    assert replay.views == []


async def test_show_view_is_offered_under_the_read_only_ceiling_but_follows_the_allowlist(store):
    open_provider = ScriptedProvider([[_text("x")]])
    await (await _open(store, open_provider)).send("q")
    narrow = ScriptedProvider([[_tool("t1", SHOW_VIEW_TOOL, {"artifact_id": "a", "view": "table"})], [_text("x")]])
    result = await (
        await _open(store, narrow, policy=SessionPolicy(tool_allowlist=frozenset({"get_risk"})))
    ).send("q")

    assert SHOW_VIEW_TOOL in open_provider.calls[0]["tools"]
    assert SHOW_VIEW_TOOL not in narrow.calls[0]["tools"]
    assert [t.ok for t in result.tool_trace] == [False]


async def test_a_key_reused_with_another_message_conflicts(store):
    from repowise.server.chat_session import IdempotencyConflictError

    provider = ScriptedProvider(lambda i: [_text("ok")])
    session = await _open(store, provider)
    await session.send("first question", idempotency_key="k1")

    with pytest.raises(IdempotencyConflictError):
        await session.check_send("another question", "k1")
    with pytest.raises(IdempotencyConflictError):
        await session.send("another question", idempotency_key="k1")
    await session.check_send("first question", "k1")
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# Numeric check
# ---------------------------------------------------------------------------


def _figure_turns(answer: str) -> Callable[[int], list]:
    return lambda i: [_text("Let me look. "), _tool("t1", "get_risk")] if i == 0 else [_text(answer)]


async def test_figures_are_checked_against_the_turns_tool_results(store):
    session = await _open(store, ScriptedProvider(_figure_turns("src/a.py has 3 fixes and 17 callers.")))

    events = await _collect(session.stream("how risky is a.py?"))

    streamed = "".join(e["text"] for e in events if e["type"] == "text_delta")
    (diagnostics,) = [e for e in events if e["type"] == "diagnostics"]
    assert diagnostics["figures_checked"] == 2 and diagnostics["figures_unverified"] == 1
    # Offsets index the streamed text, which also holds the text before the tool call.
    (span,) = diagnostics["unverified_figures"]
    assert streamed[span["start"] : span["end"]] == span["text"] == "17"
    assert [e["type"] for e in events][-3:] == ["diagnostics", "suggestions", "done"]

    content = (await store.messages(session.id))[-1].content
    artifact_id = content["tool_calls"][0]["artifact"]["id"]
    assert content["diagnostics"] == {"figures_checked": 2, "figures_unverified": 1}
    assert content["figures"] == [
        {"text": "3", "start": 13, "end": 14, "verified": True, "artifact_id": artifact_id},
        {"text": "17", "start": 25, "end": 27, "verified": False},
    ]


async def test_the_result_and_a_replay_carry_the_figures(store):
    session = await _open(store, ScriptedProvider(_figure_turns("3 fixes, 40 callers.")))

    first = await session.send("q", idempotency_key="k1")
    replay_events = await _collect(session.stream("q", idempotency_key="k1"))

    assert first.diagnostics == {"figures_checked": 2, "figures_unverified": 1}
    assert [f["verified"] for f in first.figures] == [True, False]
    (diagnostics,) = [e for e in replay_events if e["type"] == "diagnostics"]
    # A replay streams the answer alone, so the offsets are the stored ones.
    assert diagnostics["unverified_figures"] == [{"text": "40", "start": 9, "end": 11}]


async def test_a_figure_from_an_earlier_turns_tool_result_is_verified(store):
    provider = ScriptedProvider(
        lambda i: [_tool("t1", "get_risk")] if i == 0 else [_text("Noted." if i == 1 else "Still 3 fixes.")]
    )
    session = await _open(store, provider)

    await session.send("first")
    later = await session.send("and now?")

    assert later.diagnostics == {"figures_checked": 1, "figures_unverified": 0}


async def test_an_answer_without_figures_stores_no_check(store):
    session = await _open(store, ScriptedProvider([[_text("No numbers here.")]]))

    events = await _collect(session.stream("hello"))

    assert "diagnostics" not in [e["type"] for e in events]
    content = (await store.messages(session.id))[-1].content
    assert "figures" not in content and "diagnostics" not in content


# ---------------------------------------------------------------------------
# find_suspects: chat-only, local backend, follows get_context's admission
# ---------------------------------------------------------------------------


async def test_find_suspects_is_offered_on_the_local_backend_and_runs(store, monkeypatch):
    import repowise.server.chat_session as chat_session_mod
    from repowise.server.chat_suspects import SUSPECTS_TOOL
    from repowise.server.chat_tools import LocalToolBackend

    class FakeLocal(LocalToolBackend):
        def __init__(self) -> None:
            super().__init__("/tmp/demo", repo_alias="demo")
            self.inner = FakeTools()

        def catalog_for(self, policy, principal):
            return self.inner.catalog_for(policy, principal)

        async def execute(self, name, arguments):
            return await self.inner.execute(name, arguments)

    seen: list = []

    async def fake_run(arguments, repo):
        seen.append((arguments, repo))
        return {"suspects": [{"id": "pr-2", "rank": 1, "signals": {"text": {"rank": 1, "evidence": []}}}]}

    monkeypatch.setattr(chat_session_mod, "run_find_suspects", fake_run)
    provider = ScriptedProvider([[_tool("t1", SUSPECTS_TOOL, {"symptom": "login fails"})], [_text("pr-2")]])
    session = await _open(store, provider, tools=FakeLocal())
    result = await session.send("what broke login?")

    assert SUSPECTS_TOOL in provider.calls[0]["tools"]
    assert seen == [({"symptom": "login fails"}, "demo")]
    assert [t.ok for t in result.tool_trace] == [True]

    narrow = ScriptedProvider([[_text("x")]])
    await (
        await _open(store, narrow, tools=FakeLocal(), policy=SessionPolicy(tool_allowlist=frozenset({"get_risk"})))
    ).send("q")
    assert SUSPECTS_TOOL not in narrow.calls[0]["tools"]


# ---------------------------------------------------------------------------
# chart: chat-only, local backend, one call to a stored view
# ---------------------------------------------------------------------------


class _FakeLocal:
    """A local backend over FakeTools; built lazily so the import stays in the test."""

    @staticmethod
    def make():
        from repowise.server.chat_tools import LocalToolBackend

        class FakeLocal(LocalToolBackend):
            def __init__(self) -> None:
                super().__init__("/tmp/demo", repo_alias="demo")
                self.inner = FakeTools()

            def catalog_for(self, policy, principal):
                return self.inner.catalog_for(policy, principal)

            async def execute(self, name, arguments):
                return await self.inner.execute(name, arguments)

        return FakeLocal()


async def test_chart_draws_a_dataset_and_the_next_turn_redraws_it(store, monkeypatch):
    import repowise.server.chat_chart as chat_chart_mod
    from repowise.server.chat_datasets import DatasetRows
    from repowise.server.chat_references import CHART_TOOL

    rows = [{"health_band": band} for band in ("good", "good", "fair")]

    async def fake_rows(name, repo):
        assert (name, repo) == ("files", "demo")
        return DatasetRows(rows=rows, as_of="4c1e31fe3a9b", stale=False)

    monkeypatch.setattr(chat_chart_mod, "load_rows", fake_rows)
    spec = {"dataset": "files", "x": "health_band", "kind": "pie"}
    provider = ScriptedProvider([[_tool("t1", CHART_TOOL, spec)], [_text("two thirds are good")]])
    session = await _open(store, provider, tools=_FakeLocal.make())
    events = await _collect(session.stream("health bands as a donut"))

    assert CHART_TOOL in provider.calls[0]["tools"]
    view = next(e for e in events if e["type"] == "view")["view"]
    assert view["chart_type"] == "donut" and view["source"]["tool_name"] == CHART_TOOL
    receipt = json.loads(provider.calls[1]["messages"][-1]["content"])
    assert receipt["figures"]["total"] == 3 and "kind 'pie' read as 'donut'" in receipt["notes"]
    stored = (await store.messages(session.id))[-1].content
    assert [v["chart_type"] for v in stored["views"]] == ["donut"]

    redraw = {"artifact_id": "v1", "view": "chart", "options": {"kind": "table"}}
    again = ScriptedProvider([[_tool("t2", SHOW_VIEW_TOOL, redraw)], [_text("as a table")]])
    result = await (
        await _open(store, again, tools=_FakeLocal.make(), conversation_id=session.id)
    ).send("show that as a table")
    assert [v["kind"] for v in result.views] == ["table"]
    assert result.views[0]["rows"] == view["data"]["rows"]


async def test_chart_is_offered_only_on_the_local_backend(store):
    from repowise.server.chat_references import CHART_TOOL

    remote = ScriptedProvider([[_text("x")]])
    await (await _open(store, remote)).send("q")
    assert CHART_TOOL not in remote.calls[0]["tools"]

    narrow = ScriptedProvider([[_text("x")]])
    policy = SessionPolicy(tool_allowlist=frozenset({"get_risk", SHOW_VIEW_TOOL}))
    await (await _open(store, narrow, tools=_FakeLocal.make(), policy=policy)).send("q")
    assert CHART_TOOL not in narrow.calls[0]["tools"]


# ---------------------------------------------------------------------------
# show_view references across the conversation
# ---------------------------------------------------------------------------


def _table(reference: str) -> dict:
    return {"artifact_id": reference, "view": "table", "options": {"path": "files"}}


async def test_a_view_cites_a_result_from_an_earlier_turn_by_its_replayed_id(store):
    first = ScriptedProvider([[_tool("t1", "get_risk")], [_text("read it")]])
    session = await _open(store, first)
    await session.send("risk?")

    def turns(index: int) -> list:
        if index == 0:
            replayed = next(m for m in second.calls[0]["messages"] if m["role"] == "tool")
            # The id is the first key, so an excerpt of a long result still shows it.
            assert replayed["content"].startswith('{"artifact_id": "')
            artifact_id = json.loads(replayed["content"])["artifact_id"]
            return [_tool("t2", SHOW_VIEW_TOOL, _table(artifact_id))]
        return [_text("charted")]

    second = ScriptedProvider(turns)
    tools = FakeTools()
    result = await (await _open(store, second, tools=tools, conversation_id=session.id)).send(
        "now as a table"
    )

    assert tools.executed == []
    assert [view["kind"] for view in result.views] == ["table"]
    stored = (await store.messages(session.id))[1].content["tool_calls"][0]["artifact"]
    assert result.views[0]["source"]["artifact_id"] == stored["id"]


async def test_latest_reference_resolves_a_call_made_earlier_in_the_same_round(store):
    provider = ScriptedProvider(
        [
            [_tool("t1", "get_risk"), _tool("t2", SHOW_VIEW_TOOL, _table("latest:get_risk"))],
            [_text("done")],
        ]
    )
    result = await (await _open(store, provider)).send("table of risk")
    assert [view["kind"] for view in result.views] == ["table"]


async def test_an_unknown_reference_lists_the_valid_ones_for_one_retry(store):
    first = ScriptedProvider([[_tool("t1", "get_risk")], [_text("ok")]])
    session = await _open(store, first)
    await session.send("risk?")

    second = ScriptedProvider(
        [[_tool("t2", SHOW_VIEW_TOOL, _table("placeholder"))], [_text("sorry")]]
    )
    await (await _open(store, second, conversation_id=session.id)).send("table")

    error = json.loads(second.calls[1]["messages"][-1]["content"])
    assert error["error_code"] == "unknown_artifact"
    assert "latest:<tool_name>" in error["error"]
    assert [(ref["tool"], ref["age"]) for ref in error["available"]] == [("get_risk", "1 turn ago")]


# ---------------------------------------------------------------------------
# Failures a person can fix end the turn with a plain message, not a crash
# ---------------------------------------------------------------------------


async def test_a_tool_result_holding_a_datetime_is_sent_streamed_and_stored(store):
    from datetime import UTC, datetime

    class DatedTools(FakeTools):
        async def execute(self, name, arguments):
            return {"files": [{"path": "src/a.py", "changed": datetime(2026, 10, 1, tzinfo=UTC)}]}

    provider = ScriptedProvider([[_tool("t1", "get_risk")], [_text("ok")]])
    session = await _open(store, provider, tools=DatedTools())
    events = await _collect(session.stream("when?"))

    assert events[-1]["type"] == "done"
    json.dumps(events)
    stored = (await store.messages(session.id))[-1].content["tool_calls"][0]["artifact"]["data"]
    assert stored["files"][0]["changed"].startswith("2026-10-01")


_KEY = "sk-ant-" + "a1b2c3d4e5f6g7h8"  # pragma: allowlist secret
_GOOGLE_URL = "https://generativelanguage.googleapis.com/v1/models?key=AIza" + "SyA1b2c3d4e5f6"


def _sdk_status_error(status: int, message: str):
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    error = anthropic.AuthenticationError if status == 401 else anthropic.APIStatusError
    return error(message, response=response, body=None)


async def test_an_sdk_auth_error_says_to_check_the_api_key(store):
    provider = ScriptedProvider([_sdk_status_error(401, f"invalid x-api-key {_KEY}")])
    result = await (await _open(store, provider)).send("hi")

    assert result.stop_reason == "error"
    assert "rejected its API key" in result.error
    assert "Settings" in result.error
    assert _KEY not in result.error


async def test_an_sdk_error_body_is_not_echoed_to_the_person(store):
    provider = ScriptedProvider([_sdk_status_error(400, f"bad request, echoed key {_KEY}")])
    result = await (await _open(store, provider)).send("hi")

    assert result.error == (
        "The test provider failed (APIStatusError, HTTP 400). See the server log for details."
    )


async def test_an_httpx_error_is_not_labelled_a_provider_failure_and_its_key_is_redacted(store):
    import httpx

    request = httpx.Request("GET", _GOOGLE_URL)
    leaked = httpx.HTTPStatusError(
        f"Client error for url {_GOOGLE_URL}", request=request, response=httpx.Response(400)
    )
    provider = ScriptedProvider([leaked])
    result = await (await _open(store, provider)).send("hi")

    assert result.error.startswith("Internal error: HTTPStatusError")
    assert "provider failed" not in result.error
    assert "AIza" not in result.error and "key=[redacted]" in result.error


async def test_a_provider_that_runs_tools_itself_gets_stamped_json_safe_results(store):
    from datetime import UTC, datetime

    class DatedTools(FakeTools):
        async def execute(self, name, arguments):
            return {"files": [{"path": "src/a.py", "changed": datetime(2026, 10, 1, tzinfo=UTC)}]}

    seen: list = []

    class LoopingProvider(ScriptedProvider):
        """Runs its tool calls through ``tool_executor``, the way Gemini does."""

        async def stream_chat(self, **kwargs):
            self.calls.append({"tools": [t["function"]["name"] for t in kwargs["tools"]]})
            call = ChatToolCall(id="g1", name="get_risk", arguments={})
            yield ChatStreamEvent(type="tool_start", tool_call=call)
            result = await kwargs["tool_executor"]("get_risk", {})
            seen.append(result)
            yield ChatStreamEvent(type="tool_result", tool_call=call, tool_result_data=result)
            yield _text("done")

    session = await _open(store, LoopingProvider([]), tools=DatedTools())
    result = await session.send("when?")

    assert next(iter(seen[0])) == "artifact_id"
    json.dumps(seen[0])
    assert [t.ok for t in result.tool_trace] == [True]


async def test_a_bug_outside_the_provider_stays_an_internal_error(store):
    provider = ScriptedProvider([KeyError("boom")])
    result = await (await _open(store, provider)).send("hi")
    assert result.error.startswith("Internal error: KeyError")


async def test_a_session_without_a_provider_ends_with_a_setup_message(store):
    from repowise.server.chat_errors import NO_PROVIDER_MESSAGE

    session = await _open(store, None)
    events = await _collect(session.stream("hi"))
    assert events == [{"type": "error", "message": NO_PROVIDER_MESSAGE}]
    assert (await session.send("hi")).error == NO_PROVIDER_MESSAGE


async def test_a_citation_line_that_is_not_a_number_is_dropped_not_fatal(store):
    from repowise.server.schemas.chat import ChatTurnResultResponse

    class RangeTools(FakeTools):
        async def execute(self, name, arguments):
            return {"citations": [{"file_path": "src/a.py", "start_line": "10-20", "end_line": 20}]}

    provider = ScriptedProvider([[_tool("t1", "get_risk")], [_text("ok")]])
    result = await (await _open(store, provider, tools=RangeTools())).send("where?")

    assert [(c["file_path"], c.get("start_line"), c["end_line"]) for c in result.citations] == [
        ("src/a.py", None, 20)
    ]
    ChatTurnResultResponse.model_validate(asdict(result))
