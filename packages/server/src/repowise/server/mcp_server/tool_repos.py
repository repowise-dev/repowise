"""MCP tool: list_repos - discover repos served by this MCP server."""

from __future__ import annotations

from typing import Any

from repowise.core.registry import mcp_tool_registry as mcp
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._references import repository_identity


def _workspace_repos(registry: Any) -> list[dict[str, Any]]:
    ws_config = getattr(registry, "ws_config", None)
    # The registry's ws_config is a snapshot taken when this (long-lived) MCP
    # server started. A `repowise update` that runs later rewrites the
    # workspace config's per-repo ``indexed_at`` / ``last_commit_at_index``,
    # but the in-process copy would keep reporting the startup values, making
    # the index look stale long after it was synced. Re-read from disk so the
    # freshness fields track updates without a server restart. Best-effort:
    # fall back to the cached snapshot if the reload fails.
    workspace_root = getattr(registry, "workspace_root", None)
    if workspace_root is not None:
        try:
            from repowise.core.workspace import WorkspaceConfig

            fresh = WorkspaceConfig.load(workspace_root)
            if getattr(fresh, "repos", None):
                ws_config = fresh
        except Exception:
            pass  # never fail discovery on a config reload hiccup (missing/locked file)
    default_alias = registry.get_default_alias()

    if ws_config is None:
        return [
            {
                "alias": alias,
                "path": None,
                "absolute_path": None,
                "is_default": alias == default_alias,
            }
            for alias in registry.get_all_aliases()
        ]

    repos: list[dict[str, Any]] = []
    for entry in ws_config.repos:
        absolute_path = (registry.workspace_root / entry.path).resolve()
        repos.append(
            {
                "alias": repository_identity(entry.alias),
                "path": entry.path,
                "absolute_path": absolute_path.as_posix(),
                "is_default": entry.alias == default_alias,
                "indexed_at": entry.indexed_at,
                "last_commit_at_index": entry.last_commit_at_index,
            }
        )
    return repos


@mcp.tool(
    tier="utility",
    requires_workspace=True,
    surface_order=110,
    artifact_type="repository_list",
    presentation="repository_list",
)
async def list_repos() -> dict[str, Any]:
    """List repos available through this MCP server.

    In workspace mode this returns every configured repository's canonical
    alias, config-relative path, and absolute path. Any emitted identity can
    be passed unchanged as ``repo`` to workspace-aware tools.
    """
    registry = _state._registry
    if registry is not None:
        return {
            "workspace": True,
            "workspace_root": registry.workspace_root.as_posix(),
            "default_repo": repository_identity(registry.get_default_alias()),
            "repos": _workspace_repos(registry),
            "hint": (
                "Pass any emitted alias, path, or absolute_path unchanged as repo "
                "on workspace-aware tools."
            ),
            "_meta": _build_meta(),
        }

    return {
        "workspace": False,
        "workspace_root": None,
        "default_repo": "default",
        "repos": [
            {
                "alias": "default",
                "path": str(_state._repo_path) if _state._repo_path else None,
                "absolute_path": str(_state._repo_path) if _state._repo_path else None,
                "is_default": True,
            }
        ],
        "hint": "This MCP server is serving a single repo; omit repo unless a tool asks for it.",
        "_meta": _build_meta(),
    }


__all__ = ["list_repos"]
