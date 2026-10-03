"""Chat router: the SSE agentic loop and conversation management."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from starlette.responses import StreamingResponse

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.providers.llm.base import ChatProvider, ProviderError
from repowise.server.chat_artifacts import (
    create_artifact_envelope,
    find_artifact,
    set_artifact_pinned,
)
from repowise.server.chat_grounding import plan_grounding, run_grounding
from repowise.server.chat_prompt import (
    _build_system_prompt,
    _db_messages_to_llm_format,
    _history_has_call,
    _with_navigation_context,
    assistant_tool_call_message,
    tool_result_message,
)
from repowise.server.chat_suggestions import (
    conversation_title,
    follow_up_suggestions,
    page_suggestions,
    tool_names,
)
from repowise.server.chat_summaries import _build_tool_summary
from repowise.server.chat_tools import (
    execute_tool,
    get_artifact_evidence_basis,
    get_artifact_presentation,
    get_artifact_type,
    get_tool_catalog,
    get_tool_schemas_for_llm,
)
from repowise.server.deps import (
    get_db_session,
    resolve_request_session_factory,
    verify_api_key,
)
from repowise.server.provider_config import get_chat_provider_instance
from repowise.server.schemas import (
    ArtifactUpdateRequest,
    ChatArtifactEnvelope,
    ChatMessageResponse,
    ChatRequest,
    ChatSuggestionsResponse,
    ConversationDetailResponse,
    ConversationForkRequest,
    ConversationResponse,
    ConversationUpdateRequest,
    OkResponse,
)
from repowise.server.schemas.chat import ChatPageContext

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["chat"],
    dependencies=[Depends(verify_api_key)],
)

_MAX_AGENTIC_LOOPS = 10


async def _get_repo_info(factory: Any, repo_id: str) -> tuple[str, str]:
    """Get repo name and path from DB."""
    async with get_session(factory) as session:
        repo = await crud.get_repository(session, repo_id)
        if not repo:
            raise HTTPException(404, f"Repository {repo_id} not found")
        return repo.name, repo.local_path


# ---------------------------------------------------------------------------
# SSE Chat Endpoint
# ---------------------------------------------------------------------------


def _workspace_alias(request: Request, repo_path: str, repo_name: str) -> str | None:
    """Return the workspace alias owning *repo_path*, or None outside a workspace.

    ``local_path`` is stored verbatim at index time, so it can be relative
    (``repowise init .`` records ``"."``) and resolve against the server's
    cwd rather than the repo. The name fallback covers that; a miss only
    costs us the repo scoping, so it is logged rather than raised.
    """
    ws_config = getattr(request.app.state, "workspace_config", None)
    ws_root = getattr(request.app.state, "workspace_root", None)
    if ws_config is None or ws_root is None:
        return None

    resolved = Path(repo_path).resolve()
    for entry in ws_config.repos:
        if (Path(ws_root) / entry.path).resolve() == resolved:
            return entry.alias
    for entry in ws_config.repos:
        if entry.alias == repo_name:
            return entry.alias

    logger.warning(
        "chat_workspace_alias_unresolved",
        extra={"repo_path": repo_path, "repo_name": repo_name},
    )
    return None


def _is_live(conv: Any, repo_id: str) -> bool:
    return bool(conv) and conv.repository_id == repo_id and conv.deleted_at is None


async def _open_conversation(
    factory: Any, repo_id: str, body: ChatRequest
) -> tuple[str, str, bool] | None:
    """Load or create the turn's conversation and save the user message.

    Returns ``(conversation_id, user_message_id, created)``, or None when the
    requested conversation is not a live one of this repo.
    """
    async with get_session(factory) as session:
        created = not body.conversation_id
        if created:
            # Placeholder; refined once the turn's tools are known.
            conv = await crud.create_conversation(
                session,
                repository_id=repo_id,
                title=" ".join(body.message.split()[:6]),
            )
        else:
            conv = await crud.get_conversation(session, body.conversation_id)
            if not _is_live(conv, repo_id):
                return None
        user_msg = await crud.create_chat_message(
            session,
            conversation_id=conv.id,
            role="user",
            content={"text": body.message},
        )
        return conv.id, user_msg.id, created


async def _llm_history(factory: Any, conv_id: str, page_context: Any) -> list[dict[str, Any]]:
    async with get_session(factory) as session:
        db_messages = await crud.list_chat_messages(session, conv_id)
        llm_messages = _db_messages_to_llm_format(db_messages)
        return _with_navigation_context(llm_messages, page_context)


async def _save_reply(
    factory: Any, conv_id: str, content: dict[str, Any], opening_message: str | None
) -> str:
    """Store the assistant message; *opening_message* titles a new conversation."""
    async with get_session(factory) as session:
        msg = await crud.create_chat_message(
            session,
            conversation_id=conv_id,
            role="assistant",
            content=content,
        )
        await crud.touch_conversation(session, conv_id)
        # Opening turn only, so a later rename is never overwritten.
        if opening_message is not None:
            await crud.update_conversation_title(
                session,
                conv_id,
                conversation_title(opening_message, tool_names(content["tool_calls"])),
            )
        return msg.id


class _AgentTurn:
    """One assistant turn: the model and tool loop, and what it records.

    ``aborted`` is set when the client disconnects or the provider fails, in
    which case nothing is saved; ``truncated`` when the loop cap is reached.
    """

    def __init__(
        self,
        repo_path: str,
        repo_alias: str | None,
        provider: ChatProvider,
        llm_messages: list[dict[str, Any]],
    ) -> None:
        self.repo_path = repo_path
        self.repo_alias = repo_alias
        self.provider = provider
        self.llm_messages = llm_messages
        self.text_parts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.aborted = False
        self.truncated = False

    async def execute(self, name: str, args: dict) -> dict:
        """Run one tool scoped to this repo; also handed to providers that loop internally."""
        return await execute_tool(name, args, repo_path=self.repo_path, repo=self.repo_alias)

    async def ground(self, page_context: Any, tool_catalog: list) -> str | None:
        """Make the one read the page already justifies, before the first model turn.

        A repeat of a call the history already holds is skipped.
        """
        plan = plan_grounding(page_context, (tool.entry for tool in tool_catalog))
        if plan is not None and _history_has_call(self.llm_messages, plan.tool_name, plan.arguments):
            plan = None
        grounding = await run_grounding(plan, self.execute)
        if grounding is None:
            return None
        self.llm_messages.extend(grounding.llm_messages())
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
        return _sse_event("data", grounding.sse_payload())

    async def run(
        self, request: Request, system_prompt: str, tool_schemas: list
    ) -> AsyncIterator[str]:
        """Alternate model turns and tool runs until the model answers."""
        for _ in range(_MAX_AGENTIC_LOOPS):
            pending: list[dict[str, Any]] = []
            async for sse in self._model_turn(request, system_prompt, tool_schemas, pending):
                yield sse
            if self.aborted or not pending:
                return
            async for sse in self._run_pending(pending):
                yield sse
        # Every turn ended in a tool call, so no final answer exists.
        self.truncated = True
        yield _sse_event("data", {"type": "truncated", "loops": _MAX_AGENTIC_LOOPS})

    def reply_content(self) -> dict[str, Any]:
        content: dict[str, Any] = {
            "text": "".join(self.text_parts),
            "tool_calls": self.tool_calls,
            "provider": self.provider.provider_name,
            "model": self.provider.model_name,
        }
        if self.truncated:
            content["truncated"] = True
        # A turn that read nothing, or read only failures, has no next
        # step to propose.
        follow_ups = follow_up_suggestions(self.tool_calls)
        if follow_ups:
            content["follow_ups"] = follow_ups
        return content

    async def _model_turn(
        self,
        request: Request,
        system_prompt: str,
        tool_schemas: list,
        pending: list[dict[str, Any]],
    ) -> AsyncIterator[str]:
        """Stream one provider call, collecting the tool calls left to us in *pending*."""
        try:
            async for event in self.provider.stream_chat(
                messages=self.llm_messages,
                tools=tool_schemas,
                system_prompt=system_prompt,
                max_tokens=8192,
                temperature=0.7,
                tool_executor=self.execute,
            ):
                if await request.is_disconnected():
                    self.aborted = True
                    return
                sse = self._on_provider_event(event, pending)
                if sse is not None:
                    yield sse
        except ProviderError as exc:
            self.aborted = True
            yield _sse_event("data", {"type": "error", "message": str(exc)})

    def _on_provider_event(self, event: Any, pending: list[dict[str, Any]]) -> str | None:
        if event.type == "text_delta" and event.text:
            self.text_parts.append(event.text)
            return _sse_event("data", {"type": "text_delta", "text": event.text})
        tc = event.tool_call
        if not tc:
            return None
        if event.type == "tool_start":
            pending.append({"id": tc.id, "name": tc.name, "arguments": tc.arguments})
            return _sse_event(
                "data",
                {
                    "type": "tool_start",
                    "tool_id": tc.id,
                    "tool_name": tc.name,
                    "input": tc.arguments,
                },
            )
        if event.type == "tool_result":
            # The provider already ran this tool (e.g. Gemini), so it leaves the pending list.
            pending[:] = [p for p in pending if p["id"] != tc.id]
            return self._tool_result(tc.id, tc.name, tc.arguments, event.tool_result_data or {})
        return None

    async def _run_pending(self, pending: list[dict[str, Any]]) -> AsyncIterator[str]:
        """Run the tool calls the provider left to us and feed the results back."""
        self.llm_messages.append(assistant_tool_call_message("".join(self.text_parts), pending))
        self.text_parts.clear()
        for tc in pending:
            result = await self.execute(tc["name"], tc["arguments"])
            yield self._tool_result(tc["id"], tc["name"], tc["arguments"], result)
            self.llm_messages.append(tool_result_message(tc["id"], tc["name"], result))

    def _tool_result(
        self, tool_id: str, name: str, arguments: dict[str, Any], result: dict[str, Any]
    ) -> str:
        """Record one tool result as a stored call and return its SSE event."""
        artifact_type = get_artifact_type(name, self.repo_path)
        summary = _build_tool_summary(name, result)
        artifact = create_artifact_envelope(
            tool_name=name,
            artifact_type=artifact_type,
            presentation=get_artifact_presentation(name, self.repo_path),
            data=result,
            title=summary,
            evidence_basis=get_artifact_evidence_basis(name, self.repo_path),
        )
        self.tool_calls.append(_stored_tool_call(tool_id, name, arguments, summary, artifact))
        return _sse_event(
            "data",
            {
                "type": "tool_result",
                "tool_id": tool_id,
                "tool_name": name,
                "summary": summary,
                "artifact": artifact,
            },
        )


@router.post("/api/repos/{repo_id}/chat/messages")
async def chat_messages(repo_id: str, body: ChatRequest, request: Request):
    """Stream an agentic chat response via SSE."""
    # In workspace mode each repo has its own ``wiki.db``, so every session
    # this request opens must come from the factory resolved by ``repo_id``.
    factory = resolve_request_session_factory(request)

    repo_name, repo_path = await _get_repo_info(factory, repo_id)
    # In workspace mode the MCP tools address repos by alias, not by the id
    # in this URL, so resolve it once and scope every tool call to it.
    repo_alias = _workspace_alias(request, repo_path, repo_name)

    # An override (the UI model picker) applies to this request only; without
    # one the repo's own config and ``.env`` pick the provider, as for update.
    try:
        provider = get_chat_provider_instance(
            repo_path=repo_path,
            repo_id=repo_id,
            provider_override=body.provider,
            model_override=body.model,
        )
    except ValueError as exc:
        # Unknown provider override, or no provider resolvable at all.
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(422, f"No chat provider available: {exc}") from exc

    if not isinstance(provider, ChatProvider):
        raise HTTPException(
            422,
            f"Provider '{provider.provider_name}' does not support streaming chat. "
            "Configure a provider that supports tool use (Anthropic, OpenAI, Gemini).",
        )

    async def event_stream():
        try:
            yield "retry: 3000\n\n"

            opened = await _open_conversation(factory, repo_id, body)
            if opened is None:
                yield _sse_event("data", {"type": "error", "message": "Conversation not found"})
                return
            conv_id, user_msg_id, created = opened

            llm_messages = await _llm_history(factory, conv_id, body.context)
            tool_catalog = get_tool_catalog(repo_path)
            system_prompt = _build_system_prompt(repo_name, repo_path, tool_catalog, body.context)
            tool_schemas = get_tool_schemas_for_llm(repo_path)

            turn = _AgentTurn(repo_path, repo_alias, provider, llm_messages)
            grounded = await turn.ground(body.context, tool_catalog)
            if grounded is not None:
                yield grounded
            async for sse in turn.run(request, system_prompt, tool_schemas):
                yield sse
            if turn.aborted:
                return

            content = turn.reply_content()
            msg_id = await _save_reply(
                factory, conv_id, content, body.message if created else None
            )
            if "follow_ups" in content:
                yield _sse_event(
                    "data", {"type": "suggestions", "suggestions": content["follow_ups"]}
                )

            yield _sse_event(
                "data",
                {
                    "type": "done",
                    "conversation_id": conv_id,
                    "message_id": msg_id,
                    "user_message_id": user_msg_id,
                    "provider": provider.provider_name,
                    "model": provider.model_name,
                },
            )

        except Exception as exc:
            logger.exception("Chat stream error")
            yield _sse_event(
                "data",
                {
                    "type": "error",
                    "message": f"Internal error: {type(exc).__name__}: {exc}",
                },
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # no-transform keeps the Next.js rewrite proxy's compression
            # middleware from gzip-buffering the stream (see jobs stream).
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get(
    "/api/repos/{repo_id}/chat/suggestions",
    response_model=ChatSuggestionsResponse,
    # `toolHint` is absent, never null: the TypeScript contract declares it
    # optional, and a serialized null would be a value the clients do not type.
    response_model_exclude_none=True,
)
async def chat_suggestions(
    repo_id: str,
    request: Request,
    kind: str,
    target: str | None = None,
):
    """Questions this page has earned, from the read a first question makes.

    Goes through the same two grounding functions ``chat_messages`` uses, so the
    two can never map a page kind differently, and makes no model call. Returns
    the measured tier only: an empty list leaves the client's static tier
    standing rather than restating copy the UI already ships.
    """
    factory = resolve_request_session_factory(request)
    repo_name, repo_path = await _get_repo_info(factory, repo_id)
    repo_alias = _workspace_alias(request, repo_path, repo_name)

    try:
        context = ChatPageContext(kind=kind, label=kind, target=target)
    except ValidationError:
        # A page kind this server does not serve yet, not an error worth a
        # banner over a composer.
        return {"suggestions": []}

    tool_catalog = get_tool_catalog(repo_path)
    plan = plan_grounding(context, (tool.entry for tool in tool_catalog))
    if plan is None:
        return {"suggestions": []}

    async def _execute(name: str, args: dict) -> dict:
        return await execute_tool(name, args, repo_path=repo_path, repo=repo_alias)

    grounding = await run_grounding(plan, _execute)
    if grounding is None:
        return {"suggestions": []}
    return {
        "suggestions": page_suggestions(
            grounding.tool_name, grounding.summary, grounding.result
        )
    }


# ---------------------------------------------------------------------------
# Conversation history endpoints
# ---------------------------------------------------------------------------


async def _live_conversation(session: Any, repo_id: str, conversation_id: str) -> Any:
    conv = await crud.get_conversation(session, conversation_id)
    if not _is_live(conv, repo_id):
        raise HTTPException(404, "Conversation not found")
    return conv


def _dict_contents(messages: list) -> Iterator[tuple[Any, dict[str, Any]]]:
    """Each message with its stored content, skipping rows that are not a JSON object."""
    for message in messages:
        raw = message.content_json
        try:
            content = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(content, dict):
            yield message, content


@router.get("/api/repos/{repo_id}/chat/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    repo_id: str,
    session=Depends(get_db_session),
):
    convs = await crud.list_conversations(session, repo_id)
    result = []
    for c in convs:
        count = await crud.count_chat_messages(session, c.id)
        result.append(ConversationResponse.from_orm(c, message_count=count))
    return result


@router.get("/api/repos/{repo_id}/chat/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    repo_id: str,
    conversation_id: str,
    session=Depends(get_db_session),
):
    conv = await _live_conversation(session, repo_id, conversation_id)
    messages = await crud.list_chat_messages(session, conversation_id)
    return {
        "conversation": ConversationResponse.from_orm(conv, message_count=len(messages)),
        "messages": [ChatMessageResponse.from_orm(m) for m in messages],
    }


@router.get(
    "/api/repos/{repo_id}/chat/conversations/{conversation_id}/artifacts/{artifact_id}",
    response_model=ChatArtifactEnvelope,
)
async def get_conversation_artifact(
    repo_id: str,
    conversation_id: str,
    artifact_id: str,
    session=Depends(get_db_session),
):
    await _live_conversation(session, repo_id, conversation_id)
    messages = await crud.list_chat_messages(session, conversation_id)
    for message, content in _dict_contents(messages):
        artifact = find_artifact(
            content,
            message_id=message.id,
            artifact_id=artifact_id,
        )
        if artifact is not None:
            return artifact
    raise HTTPException(404, "Artifact not found")


@router.patch(
    "/api/repos/{repo_id}/chat/conversations/{conversation_id}/artifacts/{artifact_id}",
    response_model=ChatArtifactEnvelope,
)
async def update_conversation_artifact(
    repo_id: str,
    conversation_id: str,
    artifact_id: str,
    body: ArtifactUpdateRequest,
    session=Depends(get_db_session),
):
    await _live_conversation(session, repo_id, conversation_id)
    messages = await crud.list_chat_messages(session, conversation_id)
    for message, content in _dict_contents(messages):
        updated, found = set_artifact_pinned(
            content,
            message_id=message.id,
            artifact_id=artifact_id,
            pinned=body.pinned,
        )
        if found:
            await crud.update_chat_message_content(session, message.id, updated)
            await crud.touch_conversation(session, conversation_id)
            return find_artifact(updated, message_id=message.id, artifact_id=artifact_id)
    raise HTTPException(404, "Artifact not found")


@router.delete("/api/repos/{repo_id}/chat/conversations/{conversation_id}", response_model=OkResponse)
async def delete_conversation(
    repo_id: str,
    conversation_id: str,
    session=Depends(get_db_session),
):
    conv = await crud.get_conversation(session, conversation_id)
    if not conv or conv.repository_id != repo_id:
        raise HTTPException(404, "Conversation not found")
    await crud.delete_conversation(session, conversation_id)
    return {"ok": True}


@router.post("/api/repos/{repo_id}/chat/conversations/{conversation_id}/restore", response_model=ConversationResponse)
async def restore_conversation(repo_id: str, conversation_id: str, session=Depends(get_db_session)):
    conv = await crud.get_conversation(session, conversation_id)
    if not conv or conv.repository_id != repo_id:
        raise HTTPException(404, "Conversation not found")
    restored = await crud.restore_conversation(session, conversation_id)
    return ConversationResponse.from_orm(restored)


@router.patch("/api/repos/{repo_id}/chat/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    repo_id: str,
    conversation_id: str,
    body: ConversationUpdateRequest,
    session=Depends(get_db_session),
):
    conv = await _live_conversation(session, repo_id, conversation_id)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(422, "Conversation title cannot be blank")
        conv = await crud.update_conversation_title(session, conversation_id, title)
    if body.pinned is not None:
        conv = await crud.set_conversation_pinned(session, conversation_id, body.pinned)
    return ConversationResponse.from_orm(conv)


@router.post("/api/repos/{repo_id}/chat/conversations/{conversation_id}/fork", response_model=ConversationResponse)
async def fork_conversation(
    repo_id: str,
    conversation_id: str,
    body: ConversationForkRequest,
    session=Depends(get_db_session),
):
    await _live_conversation(session, repo_id, conversation_id)
    if body.through_message_id is not None and body.before_message_id is not None:
        raise HTTPException(422, "Choose either a through or before fork point")
    fork_point = body.through_message_id or body.before_message_id
    if fork_point is not None:
        messages = await crud.list_chat_messages(session, conversation_id)
        if all(message.id != fork_point for message in messages):
            raise HTTPException(404, "Fork point not found")
    fork = await crud.fork_conversation(
        session,
        conversation_id,
        through_message_id=body.through_message_id,
        before_message_id=body.before_message_id,
    )
    count = await crud.count_chat_messages(session, fork.id)
    return ConversationResponse.from_orm(fork, message_count=count)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


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
