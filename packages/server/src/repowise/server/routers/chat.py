"""Chat router: SSE and JSON transports over chat sessions, and conversation management.

The turn itself runs in :mod:`repowise.server.chat_session`; these routes
resolve the repository, principal and provider, then stream its events as SSE
or return its result as JSON.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from pydantic import ValidationError
from starlette.responses import StreamingResponse

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.providers.llm.base import ChatProvider
from repowise.core.providers.llm.resolve import UntrustedBaseUrlError
from repowise.core.redaction import redact_secrets
from repowise.server.auth import PER_CALL_TOOL_SCOPE, Principal, get_authorizer
from repowise.server.chat_artifacts import find_artifact, set_artifact_pinned
from repowise.server.chat_datasets import cached_rows
from repowise.server.chat_errors import (
    INVALID_PROVIDER_MESSAGE,
    NO_PROVIDER_MESSAGE,
    PROVIDER_START_MESSAGE,
    internal_error,
)
from repowise.server.chat_grounding import plan_grounding, run_grounding
from repowise.server.chat_seams import (
    SessionPolicy,
    SessionScope,
    StoredConversation,
    conversation_stores,
    tool_backends,
)
from repowise.server.chat_session import (
    DEFAULT_POLICY,
    WEB_POLICY,
    ChatSession,
    IdempotencyConflictError,
    SessionNotFoundError,
)
from repowise.server.chat_suggestions import (
    collection_datasets,
    collection_suggestions,
    page_suggestions,
)
from repowise.server.deps import (
    get_db_session,
    request_principal,
    resolve_request_session_factory,
    resolve_session_factory,
    verify_api_key,
)
from repowise.server.provider_config import (
    NoProviderConfiguredError,
    UnknownProviderError,
    get_chat_provider_instance,
)
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
from repowise.server.schemas.chat import (
    ChatPageContext,
    ChatSessionMessageRequest,
    ChatSessionOpenRequest,
    ChatSessionPolicyRequest,
    ChatSessionResponse,
    ChatTurnResultResponse,
)

logger = logging.getLogger(__name__)

# Chat checks tool scope on every tool it runs, so tool-scoped tokens may use it.
router = APIRouter(
    tags=["chat"],
    dependencies=[Security(verify_api_key, scopes=[PER_CALL_TOOL_SCOPE])],
)


async def _get_repo_info(factory: Any, repo_id: str) -> tuple[str, str]:
    """Get repo name and path from DB."""
    async with get_session(factory) as session:
        repo = await crud.get_repository(session, repo_id)
        if not repo:
            raise HTTPException(404, f"Repository {repo_id} not found")
        return repo.name, repo.local_path


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


def _chat_provider(
    repo_path: str, repo_id: str, provider: str | None, model: str | None
) -> ChatProvider:
    """The provider for one request. An override (the UI model picker) applies to
    this request only; without one the repo's config and ``.env`` pick it."""
    try:
        instance = get_chat_provider_instance(
            repo_path=repo_path,
            repo_id=repo_id,
            provider_override=provider,
            model_override=model,
        )
    except ValueError as exc:
        # Every detail here is shown to the person, so none echoes the config.
        # Another ValueError (a bad base URL, say) is an invalid configuration.
        reason = str(exc)
        logger.warning("chat_provider_unavailable: %s", redact_secrets(reason))
        if isinstance(exc, NoProviderConfiguredError):
            raise HTTPException(400, NO_PROVIDER_MESSAGE) from exc
        if isinstance(exc, UnknownProviderError):
            raise HTTPException(
                400, f"Unknown provider {provider!r}. Pick a provider in Settings."
            ) from exc
        if isinstance(exc, UntrustedBaseUrlError):
            # Names only the URL, already stripped of any credentials.
            raise HTTPException(400, str(exc)) from exc
        raise HTTPException(400, INVALID_PROVIDER_MESSAGE) from exc
    except Exception as exc:
        logger.warning(
            "chat_provider_start_failed: %s: %s", type(exc).__name__, redact_secrets(str(exc))
        )
        raise HTTPException(422, PROVIDER_START_MESSAGE) from exc
    if not isinstance(instance, ChatProvider):
        raise HTTPException(
            422,
            f"Provider '{instance.provider_name}' does not support streaming chat. "
            "Configure a provider that supports tool use (Anthropic, OpenAI, Gemini).",
        )
    return instance


def _sse_response(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            # no-transform keeps the Next.js rewrite proxy's compression
            # middleware from gzip-buffering the stream (see jobs stream).
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _session_sse(open_session: Any, run: Any) -> AsyncIterator[str]:
    """Open the session inside the stream, so a missing conversation or a
    failure to open still ends the stream with a typed ``error`` event."""
    yield "retry: 3000\n\n"
    try:
        session = await open_session()
    except SessionNotFoundError as exc:
        yield _sse_event("data", {"type": "error", "message": str(exc)})
        return
    except HTTPException as exc:
        # A setup failure the person can fix; ``_chat_provider`` writes these
        # details for the person, without the config or exception text.
        yield _sse_event("data", {"type": "error", "message": str(exc.detail)})
        return
    except Exception as exc:
        logger.exception("Chat session open error")
        yield _sse_event("data", {"type": "error", "message": internal_error(exc)})
        return
    try:
        async for event in run(session):
            yield _sse_event("data", event)
    except IdempotencyConflictError as exc:
        # Checked before the response; only a concurrent send reaches this.
        yield _sse_event("data", {"type": "error", "message": str(exc)})


# ---------------------------------------------------------------------------
# SSE chat for the web client
# ---------------------------------------------------------------------------


@router.post("/api/repos/{repo_id}/chat/messages")
async def chat_messages(repo_id: str, body: ChatRequest, request: Request):
    """Stream an agentic chat response via SSE."""
    # In workspace mode each repo has its own ``wiki.db``, so every session
    # this request opens must come from the factory resolved by ``repo_id``.
    factory = resolve_request_session_factory(request)
    repo_name, repo_path = await _get_repo_info(factory, repo_id)

    # Inside the stream, so a missing provider or any other setup failure
    # reaches the chat as an error event instead of a bare HTTP error.
    async def open_session() -> ChatSession:
        scope = SessionScope(
            kind="repo",
            repo_id=repo_id,
            repo_name=repo_name,
            repo_path=repo_path,
            # In workspace mode the MCP tools address repos by alias, not by the
            # id in this URL, so every tool call is scoped to it.
            repo_alias=_workspace_alias(request, repo_path, repo_name),
            storage=factory,
        )
        return await ChatSession.open(
            scope=scope,
            provider=_chat_provider(repo_path, repo_id, body.provider, body.model),
            principal=request_principal(request),
            fallback_policy=WEB_POLICY,
            conversation_id=body.conversation_id,
            # Placeholder; the opening turn retitles it from what it read.
            title=" ".join(body.message.split()[:6]),
        )

    def run(session: ChatSession) -> AsyncIterator[dict[str, Any]]:
        return session.stream(
            body.message, context=body.context, is_cancelled=request.is_disconnected
        )

    return _sse_response(_session_sse(open_session, run))


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

    Goes through the same grounding functions and tool backend a chat turn
    uses, so the two can never map a page kind differently, and makes no
    model call. Returns the measured tier only: an empty list leaves the
    client's static tier standing rather than restating copy the UI already
    ships. A page with no target gets chart questions quoting the datasets a
    chart has already cached, and nothing is read cold for them.
    """
    factory = resolve_request_session_factory(request)
    repo_name, repo_path = await _get_repo_info(factory, repo_id)

    try:
        context = ChatPageContext(kind=kind, label=kind, target=target)
    except ValidationError:
        # A page kind this server does not serve yet, not an error worth a
        # banner over a composer.
        return {"suggestions": []}

    alias = _workspace_alias(request, repo_path, repo_name)
    if not context.target:
        return {"suggestions": await _collection_suggestions(kind, alias)}

    principal = request_principal(request)
    scope = SessionScope(
        kind="repo",
        repo_id=repo_id,
        repo_name=repo_name,
        repo_path=repo_path,
        repo_alias=alias,
        storage=factory,
    )
    tools = tool_backends.get()(scope, principal)
    catalog = [t for t in tools.catalog_for(WEB_POLICY, principal) if WEB_POLICY.admits(t)]
    plan = plan_grounding(context, (tool.entry for tool in catalog))
    if plan is None:
        return {"suggestions": []}

    grounding = await run_grounding(plan, tools.execute)
    if grounding is None:
        return {"suggestions": []}
    return {
        "suggestions": page_suggestions(
            grounding.tool_name, grounding.summary, grounding.result
        )
    }


async def _collection_suggestions(kind: str, alias: str | None) -> list[dict[str, Any]]:
    """Chart questions from datasets a chart already cached; a failed read is no chips."""
    try:
        rows = await cached_rows(collection_datasets(kind), alias)
    except Exception:
        logger.exception("chat_collection_suggestions_failed", extra={"kind": kind})
        return []
    return collection_suggestions(kind, rows)


# ---------------------------------------------------------------------------
# Headless sessions
# ---------------------------------------------------------------------------


def _requested_policy(requested: ChatSessionPolicyRequest | None) -> SessionPolicy:
    """The default session policy narrowed by *requested*; widening is refused."""
    base = DEFAULT_POLICY
    if requested is None:
        return base
    caps = {
        "max_tool_loops": base.max_tool_loops,
        "max_output_tokens": base.max_output_tokens,
    }
    for name, cap in caps.items():
        value = getattr(requested, name)
        if value is not None and value > cap:
            raise HTTPException(422, f"policy.{name} may not exceed {cap}")
    values = {
        name: value
        for name, value in requested.model_dump(exclude_none=True).items()
        if name != "tool_allowlist"
    }
    if requested.tool_allowlist is not None:
        values["tool_allowlist"] = frozenset(requested.tool_allowlist)
    return SessionPolicy(**{**asdict(base), **values})


def _require_repo_access(principal: Principal | None, repo_id: str, repo_name: str) -> None:
    if principal is not None and not get_authorizer().can_access_repo(
        principal, (repo_id, repo_name)
    ):
        raise HTTPException(403, "Repository is outside this credential's scope")


def _index_factories(request: Request) -> list[Any]:
    """Every index database this server serves: the primary, then each workspace repo's."""
    state = request.app.state
    factories = [state.session_factory]
    for factory in (getattr(state, "workspace_sessions", None) or {}).values():
        if all(factory is not seen for seen in factories):
            factories.append(factory)
    return factories


def _lookup_store(factory: Any) -> Any:
    """A store over *factory* for finding a conversation before its repo is known."""
    return conversation_stores.get()(
        SessionScope(kind="repo", repo_id="", repo_name="", repo_path="", storage=factory)
    )


def _session_response(
    conv: StoredConversation, *, created: bool = False
) -> ChatSessionResponse:
    policy = SessionPolicy.from_json(conv.policy_json, default=DEFAULT_POLICY)
    return ChatSessionResponse(
        id=conv.id,
        repository_id=conv.repository_id,
        title=conv.title,
        scope=conv.scope.get("kind", "repo"),
        namespace=conv.namespace,
        external_ref=conv.external_ref,
        created=created,
        policy=json.loads(policy.to_json()),
        turns=conv.turns,
        input_tokens=conv.input_tokens,
        output_tokens=conv.output_tokens,
    )


async def _session_scope(
    request: Request, repo_id: str, kind: str, alias: str | None = None
) -> SessionScope:
    factory = resolve_session_factory(request.app.state, repo_id)
    repo_name, repo_path = await _get_repo_info(factory, repo_id)
    _require_repo_access(request_principal(request), repo_id, repo_name)
    if kind == "workspace" and getattr(request.app.state, "workspace_config", None) is None:
        raise HTTPException(422, "A workspace session needs a server started on a workspace")
    if kind == "repo" and alias is None:
        alias = _workspace_alias(request, repo_path, repo_name)
    return SessionScope(
        kind=kind,  # type: ignore[arg-type]
        repo_id=repo_id,
        repo_name=repo_name,
        repo_path=repo_path,
        repo_alias=alias if kind == "repo" else None,
        storage=factory,
    )


@router.post("/api/chat/sessions", response_model=ChatSessionResponse)
async def open_chat_session(body: ChatSessionOpenRequest, request: Request):
    """Open a session, or resume the one ``(namespace, external_ref)`` names.

    A resumed session keeps the policy it was opened with.
    """
    scope = await _session_scope(request, body.repo_id, body.scope)
    try:
        session = await ChatSession.open(
            scope=scope,
            principal=request_principal(request),
            policy=_requested_policy(body.policy),
            namespace=body.namespace if body.external_ref else None,
            external_ref=body.external_ref,
            title=body.title or "New conversation",
        )
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _session_response(session.conversation, created=session.created)


@router.get("/api/chat/sessions", response_model=list[ChatSessionResponse])
async def find_chat_sessions(request: Request, external_ref: str, namespace: str = "default"):
    """The live session a caller's thread key names, as a list of zero or one."""
    principal = request_principal(request)
    for factory in _index_factories(request):
        conv = await _lookup_store(factory).find(namespace, external_ref)
        if conv is None or conv.deleted:
            continue
        async with get_session(factory) as session:
            repo = await crud.get_repository(session, conv.repository_id)
        if repo is None:
            continue
        if principal is not None and not get_authorizer().can_access_repo(
            principal, (repo.id, repo.name)
        ):
            return []
        return [_session_response(conv)]
    return []


async def _find_session(request: Request, session_id: str) -> StoredConversation:
    for factory in _index_factories(request):
        conv = await _lookup_store(factory).get(session_id)
        if conv is not None and not conv.deleted:
            return conv
    raise HTTPException(404, "Session not found")


@router.post(
    "/api/chat/sessions/{session_id}/messages",
    response_model=ChatTurnResultResponse,
    responses={
        200: {"content": {"text/event-stream": {}}},
        409: {"description": "The idempotency key was already sent with another message"},
    },
)
async def send_chat_session_message(
    session_id: str, body: ChatSessionMessageRequest, request: Request
):
    """Run one turn: SSE events when ``stream`` is true, else the turn's result."""
    conv = await _find_session(request, session_id)
    scope = await _session_scope(
        request, conv.repository_id, conv.scope.get("kind", "repo"), conv.scope.get("alias")
    )
    provider = _chat_provider(scope.repo_path, scope.repo_id, body.provider, body.model)

    try:
        session = await ChatSession.open(
            scope=scope,
            provider=provider,
            principal=request_principal(request),
            conversation_id=session_id,
        )
        await session.check_send(body.message, body.idempotency_key)
    except SessionNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except IdempotencyConflictError as exc:
        raise HTTPException(409, str(exc)) from exc

    async def opened() -> ChatSession:
        return session

    if body.stream:
        return _sse_response(
            _session_sse(
                opened,
                lambda session: session.stream(
                    body.message,
                    context=body.context,
                    idempotency_key=body.idempotency_key,
                    is_cancelled=request.is_disconnected,
                ),
            )
        )
    try:
        result = await session.send(
            body.message,
            context=body.context,
            idempotency_key=body.idempotency_key,
            is_cancelled=request.is_disconnected,
        )
    except IdempotencyConflictError as exc:  # a concurrent send won the key
        raise HTTPException(409, str(exc)) from exc
    return ChatTurnResultResponse.model_validate(asdict(result))


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
    # default=str: one value JSON has no type for must not end the stream.
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
