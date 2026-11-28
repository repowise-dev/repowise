"""The chat-only ``find_suspects`` tool: changes before a symptom, in reading order.

Chat gets its own tool for an incident described in words, the input the
suspects measurement scored, rather than routing it through get_context's
``include=["suspects"]`` and its ``symptom`` argument. It reads the
same index through the in-process tool state, so it is offered only on the
local backend, and only when the session may run ``get_context``, which reads
the same data under the same scope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

SUSPECTS_TOOL = "find_suspects"
#: The tool whose admission this one follows.
SCOPE_TOOL = "get_context"


def find_suspects_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": SUSPECTS_TOOL,
            "description": (
                "Rank the changes merged in a window (default the 14 days before `until`) "
                "against a symptom: an issue's text, an error, files, a stack trace. Each row "
                "lists the signals that fired and their evidence. It is an order to read "
                "changes in, never a cause or a probability; say so, and suggest "
                "`git bisect run` to confirm one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symptom": {"type": "string", "description": "What was seen, in words."},
                    "files": {"type": "array", "items": {"type": "string"}},
                    "trace": {"type": "string", "description": "A pasted stack trace."},
                    "since": {"type": "string", "description": "ISO 8601 window start."},
                    "until": {"type": "string", "description": "ISO 8601: when it was seen."},
                },
                "required": ["symptom"],
            },
        },
    }


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def run_find_suspects(arguments: dict[str, Any], repo: str | None) -> dict[str, Any]:
    from repowise.core.persistence.database import get_session
    from repowise.core.suspects import Symptom
    from repowise.core.suspects.index import find_suspects
    from repowise.server.chat_tools import make_json_serializable
    from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context

    try:
        since, until = _when(arguments.get("since")), _when(arguments.get("until"))
    except ValueError:
        return {"error": "since and until must be ISO 8601 times", "error_code": "bad_arguments"}
    files = arguments.get("files") or []
    if not isinstance(files, list):
        files = []
    # Cut to the core's bounds (``bounded``): a pasted log keeps its head.
    symptom = Symptom(
        text=str(arguments.get("symptom") or ""),
        files=tuple(str(f) for f in files if isinstance(f, str)),
        trace=str(arguments.get("trace") or ""),
        at=until,
    )
    if not (symptom.text.strip() or symptom.files or symptom.trace.strip()):
        return {"error": "Give a symptom, files or a trace", "error_code": "bad_arguments"}
    try:
        ctx = await _resolve_repo_context(repo)
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            return make_json_serializable(
                await find_suspects(
                    session, repository.id, repository.local_path, symptom, since=since, until=until
                )
            )
    except Exception as exc:  # a tool failure is the model's to read, not the turn's end
        return {"error": f"{type(exc).__name__}: {exc}", "error_code": "tool_failed"}


__all__ = ["SCOPE_TOOL", "SUSPECTS_TOOL", "find_suspects_schema", "run_find_suspects"]
