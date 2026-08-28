"""Thin chat adapter over the canonical MCP registry and selected surface."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from repowise.core.registry import ToolEntry
from repowise.server.mcp_server._tool_selection import (
    get_registered_tool,
    selected_tool_entries,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatToolContract:
    """One request-scoped projection of a canonical MCP entry."""

    entry: ToolEntry
    description: str
    parameters: dict[str, Any]


def get_tool_catalog(repo_path: str | None) -> list[ChatToolContract]:
    """Return the repository's configured MCP surface with generated schemas."""
    catalog: list[ChatToolContract] = []
    for entry in selected_tool_entries(repo_path):
        registered = get_registered_tool(entry.name)
        if registered is None:
            logger.error("Registered MCP entry has no FastMCP contract: %s", entry.name)
            continue
        catalog.append(
            ChatToolContract(
                entry=entry,
                description=str(registered.description or ""),
                parameters=dict(registered.parameters),
            )
        )
    return catalog


def get_tool_schemas_for_llm(repo_path: str | None) -> list[dict[str, Any]]:
    """Return OpenAI-format definitions from FastMCP's canonical schemas."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.entry.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in get_tool_catalog(repo_path)
    ]


def _make_json_serializable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(key): _make_json_serializable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_serializable(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return _make_json_serializable(vars(obj))
    return str(obj)


def _scope_repo_arg(entry: ToolEntry, arguments: dict[str, Any], repo: str | None) -> None:
    """Backstop a model-supplied repo value with the active workspace alias."""
    if not repo or "repo" not in inspect.signature(entry.fn).parameters:
        return

    import repowise.server.mcp_server as mcp_mod

    workspace = mcp_mod._registry
    if workspace is None:
        return

    requested = arguments.get("repo")
    if requested == "all" or requested in workspace.get_all_aliases():
        return
    arguments["repo"] = repo


async def execute_entry(
    entry: ToolEntry,
    arguments: dict[str, Any],
    *,
    repo: str | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Execute one selected registry entry under its safety contract."""
    if entry.safety == "mutating" and not confirmed:
        return {
            "error": f"{entry.name} requires explicit confirmation before it can run",
            "error_code": "confirmation_required",
            "requires_confirmation": True,
            "tool_name": entry.name,
        }

    try:
        scoped_arguments = dict(arguments)
        _scope_repo_arg(entry, scoped_arguments, repo)
        return _make_json_serializable(await entry.fn(**scoped_arguments))
    except Exception as exc:
        logger.exception("Tool execution failed: %s", entry.name)
        return {"error": f"{type(exc).__name__}: {exc}", "error_code": "tool_failed"}


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    repo_path: str | None = None,
    repo: str | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Execute a tool only when it belongs to the configured MCP surface."""
    tool = next(
        (candidate for candidate in get_tool_catalog(repo_path) if candidate.entry.name == name),
        None,
    )
    if tool is None:
        return {
            "error": f"Tool is not enabled for this repository: {name}",
            "error_code": "tool_not_enabled",
        }
    return await execute_entry(tool.entry, arguments, repo=repo, confirmed=confirmed)


def get_artifact_type(tool_name: str, repo_path: str | None) -> str:
    tool = next(
        (
            candidate
            for candidate in get_tool_catalog(repo_path)
            if candidate.entry.name == tool_name
        ),
        None,
    )
    return tool.entry.artifact_type if tool is not None else "generic"


def get_artifact_presentation(tool_name: str, repo_path: str | None) -> str:
    tool = next(
        (
            candidate
            for candidate in get_tool_catalog(repo_path)
            if candidate.entry.name == tool_name
        ),
        None,
    )
    return tool.entry.presentation if tool is not None else "generic"


def get_artifact_evidence_basis(tool_name: str, repo_path: str | None) -> str:
    tool = next(
        (
            candidate
            for candidate in get_tool_catalog(repo_path)
            if candidate.entry.name == tool_name
        ),
        None,
    )
    return tool.entry.evidence_basis if tool is not None else "unknown"


def init_tool_state(
    session_factory: Any,
    fts: Any,
    vector_store: Any,
    decision_store: Any | None = None,
    repo_path: str | None = None,
) -> None:
    """Bridge FastAPI app state to the MCP server module globals."""
    import repowise.server.mcp_server as mcp_mod

    mcp_mod._session_factory = session_factory
    mcp_mod._fts = fts
    mcp_mod._vector_store = vector_store
    if decision_store is not None:
        mcp_mod._decision_store = decision_store
    if repo_path is not None:
        mcp_mod._repo_path = repo_path
    logger.info("Chat tool state initialized")


_UNSET = object()


def set_tool_workspace(
    registry: Any = _UNSET,
    workspace_root: Any = _UNSET,
    cross_repo_enricher: Any = _UNSET,
) -> None:
    """Publish workspace state to the MCP tool globals used by both surfaces."""
    import repowise.server.mcp_server as mcp_mod

    if registry is not _UNSET:
        mcp_mod._registry = registry
    if workspace_root is not _UNSET:
        mcp_mod._workspace_root = workspace_root
    if cross_repo_enricher is not _UNSET:
        mcp_mod._cross_repo_enricher = cross_repo_enricher
