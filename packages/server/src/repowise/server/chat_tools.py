"""Thin chat adapter over the canonical MCP registry and selected surface."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from repowise.core.registry import ToolEntry
from repowise.server.auth import Principal, current_principal, get_authorizer
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


def get_tool_catalog(
    repo_path: str | None, principal: Principal | None = None
) -> list[ChatToolContract]:
    """Return the repository's configured MCP surface with generated schemas,
    narrowed to ``principal``'s tool scope when one is given."""
    catalog: list[ChatToolContract] = []
    authorizer = get_authorizer()
    for entry in selected_tool_entries(repo_path):
        if principal is not None and not authorizer.can_use_tool(principal, entry.name):
            continue
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


def tool_schemas(catalog: list[ChatToolContract]) -> list[dict[str, Any]]:
    """OpenAI-format definitions for *catalog*, the only tools a model is shown."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.entry.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in catalog
    ]


def get_tool_schemas_for_llm(
    repo_path: str | None, principal: Principal | None = None
) -> list[dict[str, Any]]:
    """Return OpenAI-format definitions from FastMCP's canonical schemas."""
    return tool_schemas(get_tool_catalog(repo_path, principal))


def make_json_serializable(obj: Any) -> Any:
    """*obj* as plain JSON values; a value JSON has no type for becomes its string."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(key): make_json_serializable(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [make_json_serializable(item) for item in obj]
    if hasattr(obj, "__dict__"):
        return make_json_serializable(vars(obj))
    return str(obj)


def _scope_repo_arg(
    entry: ToolEntry, arguments: dict[str, Any], repo: str | None, pinned: str | None = None
) -> None:
    """Backstop a model-supplied repo value with the active workspace alias.

    ``pinned`` (set for repo-restricted principals) always wins, so neither
    ``"all"`` nor another alias can widen the call.
    """
    if "repo" not in inspect.signature(entry.fn).parameters:
        return
    if pinned is not None:
        arguments["repo"] = pinned
        return
    if not repo:
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
    principal: Principal | None = None,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Execute one selected registry entry under its safety contract.

    With a ``principal``, the tool must be in its scope, and a repo-restricted
    principal is pinned to ``repo`` (or ``repo_id`` outside a workspace).
    """
    if principal is not None and not get_authorizer().can_use_tool(principal, entry.name):
        return {
            "error": f"Tool is outside this credential's scope: {entry.name}",
            "error_code": "tool_not_in_scope",
        }
    restricted = principal is not None and principal.repo_scope is not None
    pinned = (repo or repo_id) if restricted else None
    if entry.safety == "mutating" and not confirmed:
        return {
            "error": f"{entry.name} requires explicit confirmation before it can run",
            "error_code": "confirmation_required",
            "requires_confirmation": True,
            "tool_name": entry.name,
        }

    reset = current_principal.set(principal)
    try:
        scoped_arguments = dict(arguments)
        _scope_repo_arg(entry, scoped_arguments, repo, pinned)
        return make_json_serializable(await entry.fn(**scoped_arguments))
    except Exception as exc:
        logger.exception("Tool execution failed: %s", entry.name)
        return {"error": f"{type(exc).__name__}: {exc}", "error_code": "tool_failed"}
    finally:
        current_principal.reset(reset)


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    repo_path: str | None = None,
    repo: str | None = None,
    confirmed: bool = False,
    principal: Principal | None = None,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Execute a tool only when it belongs to the configured MCP surface."""
    entry = _catalog_entry(name, repo_path)
    if entry is None:
        return {
            "error": f"Tool is not enabled for this repository: {name}",
            "error_code": "tool_not_enabled",
        }
    return await execute_entry(
        entry, arguments, repo=repo, confirmed=confirmed, principal=principal, repo_id=repo_id
    )


def _catalog_entry(tool_name: str, repo_path: str | None) -> ToolEntry | None:
    """The named entry when it belongs to the repository's configured surface."""
    return next(
        (
            candidate.entry
            for candidate in get_tool_catalog(repo_path)
            if candidate.entry.name == tool_name
        ),
        None,
    )


class LocalToolBackend:
    """The default chat ``ToolBackend``: this server's configured MCP surface.

    One instance is bound to one session's repository and principal, which is
    the per-session part of tool state. The tools' data handles (database,
    search, workspace registry) stay process-wide in ``mcp_server``, published
    by :func:`init_tool_state` and :func:`set_tool_workspace`, so one process
    serves one index or workspace; repo scoping keeps sessions apart within it.
    """

    def __init__(
        self,
        repo_path: str | None,
        *,
        repo_alias: str | None = None,
        repo_id: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.repo_alias = repo_alias
        self.repo_id = repo_id
        self.principal = principal

    def catalog_for(self, policy: Any, principal: Principal | None) -> list[ChatToolContract]:
        """The repository's surface within *principal*'s tool scope. The session
        narrows it further by *policy*, for every backend alike."""
        return get_tool_catalog(self.repo_path, principal)

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await execute_tool(
            name,
            arguments,
            repo_path=self.repo_path,
            repo=self.repo_alias,
            principal=self.principal,
            repo_id=self.repo_id,
        )


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
