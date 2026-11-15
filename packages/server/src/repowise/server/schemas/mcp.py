"""Pydantic models for the configurable MCP tool surface."""

from __future__ import annotations

from pydantic import BaseModel, Field


class McpToolRecipeInfo(BaseModel):
    name: str
    call: str
    requires: list[str]


class McpToolInfo(BaseModel):
    """One tool in the configurable surface, with the flags a UI needs."""

    name: str
    description: str
    tier: str
    default: bool
    default_single_repo: bool
    default_workspace: bool
    eligible: bool
    eligible_single_repo: bool
    eligible_workspace: bool
    requires_workspace: bool
    enabled: bool
    recipes: list[McpToolRecipeInfo] = Field(default_factory=list)
    artifact_type: str
    presentation: str
    safety: str
    evidence_basis: str
    #: About how many tokens this tool's schema costs a client that loads
    #: every schema up front, on every turn (characters / 4).
    schema_tokens: int = 0


class McpToolSurfaceResponse(BaseModel):
    """The full tool surface for a repo plus its current override."""

    repo_id: str | None = None
    is_workspace: bool
    override: list[str] | str | None = None
    #: The profile the override names, or ``None`` when each client gets its
    #: own default (Claude Code ``standard``, every other client ``core``).
    profile: str | None = None
    #: Each profile's tools in this mode, for building a selection off one.
    profiles: dict[str, list[str]] = Field(default_factory=dict)
    tools: list[McpToolInfo]


class UpdateMcpToolsRequest(BaseModel):
    """Persist a new ``mcp.tools`` override for a repo.

    ``tools`` accepts the same shapes as the config block: a list of explicit
    names, a profile name optionally followed by ``+``/``-`` deltas, deltas
    alone, or ``null``/empty to clear the override and fall back to each
    client's default.
    """

    repo_id: str
    tools: list[str] | str | None = None
