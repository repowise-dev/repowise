"""Pydantic models for the configurable MCP tool surface."""

from __future__ import annotations

from pydantic import BaseModel


class McpToolInfo(BaseModel):
    """One tool in the configurable surface, with the flags a UI needs."""

    name: str
    description: str
    default: bool
    requires_workspace: bool
    enabled: bool


class McpToolSurfaceResponse(BaseModel):
    """The full tool surface for a repo plus its current override."""

    repo_id: str | None = None
    is_workspace: bool
    override: list[str] | str | None = None
    tools: list[McpToolInfo]


class UpdateMcpToolsRequest(BaseModel):
    """Persist a new ``mcp.tools`` override for a repo.

    ``tools`` accepts the same shapes as the config block: a list of explicit
    names or ``+``/``-`` deltas, the string ``"all"``, or ``null``/empty to
    clear the override and fall back to the default surface.
    """

    repo_id: str
    tools: list[str] | str | None = None
