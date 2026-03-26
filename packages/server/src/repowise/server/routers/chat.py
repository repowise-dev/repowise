"""Chat router — SSE streaming agentic loop and conversation management."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import StreamingResponse

from repowise.core.persistence.database import get_session
from repowise.core.persistence import crud
from repowise.core.providers.base import ChatProvider, ChatStreamEvent, ProviderError
from repowise.server.chat_tools import (
    execute_tool,
    get_artifact_type,
    get_tool_schemas_for_llm,
    init_tool_state,
)
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.provider_config import get_chat_provider_instance, set_active_provider
from repowise.server.schemas import (
    ChatMessageResponse,
    ChatRequest,
    ConversationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["chat"],
    dependencies=[Depends(verify_api_key)],
)

_MAX_AGENTIC_LOOPS = 10

_SYSTEM_PROMPT_TEMPLATE = """You are a codebase intelligence assistant for the repository "{repo_name}" located at {repo_path}.

You have access to 8 specialized tools for querying the codebase wiki, dependency graph, git history, and architectural decisions. Use them proactively — do NOT answer from memory when a tool gives more accurate answers.

Guidelines:
- Call get_overview first if the user asks about the codebase generally and no prior context exists
- Pass all relevant targets to get_context and get_risk in a single call — never call the same tool twice for different targets when they can be batched
- Call get_why for any "why was this built this way" question
- Call search_codebase for broad questions about where something is implemented
- Cite specific file paths, function names, and line numbers from tool results — be concrete, not general
- Format responses in markdown. File paths in backticks. Code in fenced blocks.
- When tool results contain documentation, synthesize and explain rather than dumping raw content
- If a tool returns an error, explain what happened and suggest alternatives"""


def _build_system_prompt(repo_name: str, repo_path: str) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(repo_name=repo_name, repo_path=repo_path)


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


@router.post("/api/repos/{repo_id}/chat/messages")
async def chat_messages(repo_id: str, body: ChatRequest, request: Request):
    """Stream an agentic chat response via SSE."""
    factory = request.app.state.session_factory

    # Resolve repo
    repo_name, repo_path = await _get_repo_info(factory, repo_id)

    # Resolve provider (optional per-request override)
    if body.provider:
        try:
            set_active_provider(body.provider, body.model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    try:
        provider = get_chat_provider_instance()
    except Exception as exc:
        raise HTTPException(422, f"No chat provider available: {exc}") from exc

    if not isinstance(provider, ChatProvider):
        raise HTTPException(
            422,
            f"Provider '{provider.provider_name}' does not support streaming chat. "
            "Configure a provider that supports tool use (Anthropic, OpenAI, Gemini).",
        )

    async def event_stream():
        conv_id = body.conversation_id
        msg_id = ""

        try:
            # Emit retry interval
            yield "retry: 3000\n\n"

            # Create or load conversation
            async with get_session(factory) as session:
                if conv_id:
                    conv = await crud.get_conversation(session, conv_id)
                    if not conv or conv.repository_id != repo_id:
                        yield _sse_event("error", {"message": "Conversation not found"})
                        return
                else:
                    title = " ".join(body.message.split()[:6])
                    conv = await crud.create_conversation(
                        session, repository_id=repo_id, title=title
                    )
                    conv_id = conv.id

                # Save user message
                await crud.create_chat_message(
                    session,
                    conversation_id=conv_id,
                    role="user",
                    content={"text": body.message},
                )

            # Build message history from DB
            async with get_session(factory) as session:
                db_messages = await crud.list_chat_messages(session, conv_id)
                llm_messages = _db_messages_to_llm_format(db_messages)

            system_prompt = _build_system_prompt(repo_name, repo_path)
            tool_schemas = get_tool_schemas_for_llm()

            # Tool executor callback — used by providers that run the
            # agentic loop internally (e.g. Gemini for thought_signature).
            async def _tool_executor(name: str, args: dict) -> dict:
                return await execute_tool(name, args)

            # Agentic loop
            assistant_text_parts: list[str] = []
            tool_calls_made: list[dict[str, Any]] = []

            for loop_idx in range(_MAX_AGENTIC_LOOPS):
                stop_reason = None
                pending_tool_calls: list[dict[str, Any]] = []

                try:
                    async for event in provider.stream_chat(
                        messages=llm_messages,
                        tools=tool_schemas,
                        system_prompt=system_prompt,
                        max_tokens=8192,
                        temperature=0.7,
                        tool_executor=_tool_executor,
                    ):
                        if await request.is_disconnected():
                            return

                        if event.type == "text_delta" and event.text:
                            assistant_text_parts.append(event.text)
                            yield _sse_event("data", {
                                "type": "text_delta",
                                "text": event.text,
                            })

                        elif event.type == "tool_start" and event.tool_call:
                            tc = event.tool_call
                            pending_tool_calls.append({
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                            })
                            yield _sse_event("data", {
                                "type": "tool_start",
                                "tool_id": tc.id,
                                "tool_name": tc.name,
                                "input": tc.arguments,
                            })

                        elif event.type == "tool_result" and event.tool_call:
                            # Provider executed the tool internally (e.g. Gemini).
                            # Emit the result to the frontend.
                            tc = event.tool_call
                            result = event.tool_result_data or {}
                            artifact_type = get_artifact_type(tc.name)
                            summary = _build_tool_summary(tc.name, result)

                            yield _sse_event("data", {
                                "type": "tool_result",
                                "tool_id": tc.id,
                                "tool_name": tc.name,
                                "summary": summary,
                                "artifact": {
                                    "type": artifact_type,
                                    "data": result,
                                },
                            })

                            tool_calls_made.append({
                                "id": tc.id,
                                "name": tc.name,
                                "arguments": tc.arguments,
                                "result": result,
                            })

                            # Remove from pending since provider already executed it
                            pending_tool_calls = [
                                p for p in pending_tool_calls if p["id"] != tc.id
                            ]

                        elif event.type == "stop":
                            stop_reason = event.stop_reason

                except ProviderError as exc:
                    yield _sse_event("data", {
                        "type": "error",
                        "message": str(exc),
                    })
                    return

                # Execute tool calls that weren't handled internally by the provider
                if pending_tool_calls:
                    # Add assistant message with tool calls to history
                    assistant_text = "".join(assistant_text_parts)
                    assistant_msg: dict[str, Any] = {"role": "assistant"}
                    if assistant_text:
                        assistant_msg["content"] = assistant_text
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                        for tc in pending_tool_calls
                    ]
                    llm_messages.append(assistant_msg)
                    assistant_text_parts.clear()

                    # Execute each tool and add results
                    for tc in pending_tool_calls:
                        result = await execute_tool(tc["name"], tc["arguments"])
                        artifact_type = get_artifact_type(tc["name"])

                        # Build summary from result
                        summary = _build_tool_summary(tc["name"], result)

                        yield _sse_event("data", {
                            "type": "tool_result",
                            "tool_id": tc["id"],
                            "tool_name": tc["name"],
                            "summary": summary,
                            "artifact": {
                                "type": artifact_type,
                                "data": result,
                            },
                        })

                        tool_calls_made.append({
                            "id": tc["id"],
                            "name": tc["name"],
                            "arguments": tc["arguments"],
                            "result": result,
                        })

                        # Add tool result to LLM history
                        llm_messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": tc["name"],
                            "content": json.dumps(result),
                        })

                    # Always loop back so the LLM can generate a text
                    # response based on the tool results.
                    continue

                # No pending tool calls — end of generation
                break

            # Save assistant message to DB
            final_text = "".join(assistant_text_parts)
            async with get_session(factory) as session:
                msg = await crud.create_chat_message(
                    session,
                    conversation_id=conv_id,
                    role="assistant",
                    content={
                        "text": final_text,
                        "tool_calls": tool_calls_made,
                    },
                )
                msg_id = msg.id
                await crud.touch_conversation(session, conv_id)

            yield _sse_event("data", {
                "type": "done",
                "conversation_id": conv_id,
                "message_id": msg_id,
            })

        except Exception as exc:
            logger.exception("Chat stream error")
            yield _sse_event("data", {
                "type": "error",
                "message": f"Internal error: {type(exc).__name__}: {exc}",
            })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Conversation history endpoints
# ---------------------------------------------------------------------------


@router.get("/api/repos/{repo_id}/chat/conversations")
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


@router.get("/api/repos/{repo_id}/chat/conversations/{conversation_id}")
async def get_conversation(
    repo_id: str,
    conversation_id: str,
    session=Depends(get_db_session),
):
    conv = await crud.get_conversation(session, conversation_id)
    if not conv or conv.repository_id != repo_id:
        raise HTTPException(404, "Conversation not found")

    messages = await crud.list_chat_messages(session, conversation_id)
    return {
        "conversation": ConversationResponse.from_orm(conv, message_count=len(messages)),
        "messages": [ChatMessageResponse.from_orm(m) for m in messages],
    }


@router.delete("/api/repos/{repo_id}/chat/conversations/{conversation_id}")
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_event(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _db_messages_to_llm_format(db_messages: list) -> list[dict[str, Any]]:
    """Convert DB chat messages to OpenAI-format message list."""
    llm_messages: list[dict[str, Any]] = []

    for msg in db_messages:
        content = json.loads(msg.content_json) if isinstance(msg.content_json, str) else msg.content_json

        if msg.role == "user":
            llm_messages.append({
                "role": "user",
                "content": content.get("text", ""),
            })
        elif msg.role == "assistant":
            text = content.get("text", "")
            tool_calls = content.get("tool_calls", [])

            if tool_calls:
                # Reconstruct the assistant + tool result messages
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if text:
                    assistant_msg["content"] = text
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    }
                    for tc in tool_calls
                ]
                llm_messages.append(assistant_msg)

                # Add tool results
                for tc in tool_calls:
                    llm_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": json.dumps(tc.get("result", {})),
                    })
            else:
                llm_messages.append({
                    "role": "assistant",
                    "content": text,
                })

    return llm_messages


def _build_tool_summary(tool_name: str, result: dict[str, Any]) -> str:
    """Build a short summary string from a tool result."""
    if "error" in result:
        return f"Error: {result['error']}"

    if tool_name == "get_overview":
        title = result.get("title", "")
        modules = len(result.get("key_modules", []))
        return f"Overview: {title} ({modules} key modules)"

    if tool_name == "get_context":
        targets = result.get("targets", {})
        return f"Context for {len(targets)} target(s)"

    if tool_name == "get_risk":
        targets = result.get("targets", {})
        increasing = sum(1 for t in targets.values() if t.get("trend") == "increasing")
        bug_prone = sum(1 for t in targets.values() if t.get("risk_type") == "bug-prone")
        parts = [f"Risk assessment for {len(targets)} file(s)"]
        if increasing:
            parts.append(f"{increasing} increasing")
        if bug_prone:
            parts.append(f"{bug_prone} bug-prone")
        return ", ".join(parts)

    if tool_name == "get_why":
        mode = result.get("mode", "")
        if mode == "health":
            counts = result.get("counts", {})
            return f"Decision health: {counts.get('active', 0)} active, {counts.get('stale', 0)} stale"
        if mode == "path":
            decisions = result.get("decisions", [])
            alignment = result.get("alignment", {})
            score = alignment.get("score", "unknown")
            origin = result.get("origin_story", {})
            author = origin.get("primary_author", "unknown") if origin.get("available") else "unknown"
            return f"{len(decisions)} decision(s), alignment: {score}, author: {author}"
        decisions = result.get("decisions", [])
        return f"Found {len(decisions)} decision(s)"

    if tool_name == "search_codebase":
        results = result.get("results", [])
        return f"Found {len(results)} result(s)"

    if tool_name == "get_dependency_path":
        dist = result.get("distance", -1)
        if dist >= 0:
            return f"Path found (distance: {dist})"
        ctx = result.get("visual_context", {})
        ancestors = ctx.get("nearest_common_ancestors", [])
        if ancestors:
            return f"No direct path — nearest bridge: {ancestors[0]['node']}"
        if ctx.get("disconnected"):
            return "No path — nodes are in separate dependency clusters"
        return "No direct path found"

    if tool_name == "get_dead_code":
        summary = result.get("summary", {})
        tiers = result.get("tiers", {})
        high_count = tiers.get("high", {}).get("count", 0)
        total = summary.get("total_findings", 0)
        lines = summary.get("deletable_lines", 0)
        return f"{total} findings ({high_count} high-confidence), {lines} deletable lines"

    if tool_name == "get_architecture_diagram":
        return f"Generated {result.get('diagram_type', 'diagram')}"

    return "Completed"
