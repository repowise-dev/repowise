"""Page-aware prefetch: the one tool call a chat page context already justifies.

Depends only on the tool registry, the chat schemas, and the artifact
envelope so a host with its own executor can import it unchanged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from repowise.core.registry import ToolEntry
from repowise.server.chat_artifacts import create_artifact_envelope
from repowise.server.schemas.chat import ChatPageContext

logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

# Page kind -> (tool, argument name, takes a list). Kinds absent here never
# prefetch; the model still reaches every tool on its own.
_PREFETCH_BY_KIND: dict[str, tuple[str, str, bool]] = {
    "file": ("get_context", "targets", True),
    "module": ("get_context", "targets", True),
    "symbol": ("get_symbol", "symbol_id", False),
    "risk": ("get_risk", "targets", True),
    "health": ("get_health", "targets", True),
    "decision": ("get_why", "id", False),
    "commit": ("get_change_risk", "revspec", False),
}

# Hosts join multi-select targets with ", " (see repository-chat-context.ts).
_TARGET_SEPARATOR = ", "


@dataclass(frozen=True)
class GroundingPlan:
    entry: ToolEntry
    arguments: dict[str, Any]
    target: str

    @property
    def tool_name(self) -> str:
        return self.entry.name


@dataclass(frozen=True)
class Grounding:
    """One completed prefetch, shaped like a tool turn the model made itself."""

    tool_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    summary: str
    artifact: dict[str, Any]

    def llm_messages(self) -> list[dict[str, Any]]:
        return [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": self.tool_id,
                        "type": "function",
                        "function": {
                            "name": self.tool_name,
                            "arguments": json.dumps(self.arguments),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": self.tool_id,
                "name": self.tool_name,
                "content": json.dumps(self.result),
            },
        ]

    def sse_payload(self) -> dict[str, Any]:
        return {
            "type": "grounding",
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "input": self.arguments,
            "summary": self.summary,
            "artifact": self.artifact,
        }


def plan_grounding(
    context: ChatPageContext | None,
    enabled: Iterable[ToolEntry],
) -> GroundingPlan | None:
    """Return the prefetch a page context justifies, or None.

    ``enabled`` is the surface this request may call; a mapped tool the
    repository has switched off is treated as no mapping at all.
    """
    if context is None or not context.target:
        return None
    mapping = _PREFETCH_BY_KIND.get(context.kind)
    if mapping is None:
        return None
    tool_name, argument, takes_list = mapping
    entry = next((candidate for candidate in enabled if candidate.name == tool_name), None)
    if entry is None or entry.safety == "mutating":
        return None

    targets = [part.strip() for part in context.target.split(_TARGET_SEPARATOR)]
    targets = [part for part in targets if part]
    if not targets:
        return None
    value: Any = targets if takes_list else targets[0]
    return GroundingPlan(
        entry=entry,
        arguments={argument: value},
        target=_TARGET_SEPARATOR.join(targets) if takes_list else targets[0],
    )


async def run_grounding(plan: GroundingPlan | None, execute: ToolExecutor) -> Grounding | None:
    """Run the planned prefetch; a failed read is dropped, never shown."""
    if plan is None:
        return None
    try:
        result = await execute(plan.tool_name, plan.arguments)
    except Exception:
        logger.exception("chat_grounding_failed", extra={"tool": plan.tool_name})
        return None
    if not isinstance(result, dict) or "error" in result:
        logger.info("chat_grounding_skipped", extra={"tool": plan.tool_name})
        return None

    artifact = create_artifact_envelope(
        tool_name=plan.tool_name,
        artifact_type=plan.entry.artifact_type,
        presentation=plan.entry.presentation,
        data=result,
        title=plan.target,
        evidence_basis=plan.entry.evidence_basis,
    )
    return Grounding(
        tool_id=f"grounding-{uuid4().hex[:12]}",
        tool_name=plan.tool_name,
        arguments=plan.arguments,
        result=result,
        summary=plan.target,
        artifact=artifact,
    )
