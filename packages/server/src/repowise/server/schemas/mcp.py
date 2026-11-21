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


class McpClientSurface(BaseModel):
    """The tools one agent is listed for this repo, resolved as its server resolves them."""

    #: ``None`` when ``mcp.tools`` is an exact list of names.
    profile: str | None = None
    tools: list[str] = Field(default_factory=list)


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
    #: The ``repowise config`` command that sets this surface, for an agent.
    command: str = ""
    #: Per agent asked for with ``?clients=``, keyed by the id as asked
    #: (``repowise agents`` target ids, as Setup lists them).
    clients: dict[str, McpClientSurface] = Field(default_factory=dict)


class McpClientRef(BaseModel):
    """An agent repowise can print an MCP config for."""

    id: str
    display_name: str


class McpClientConfigResponse(BaseModel):
    """One agent's MCP config for a repo, as ``repowise agents print-config`` prints it."""

    repo_id: str
    #: Every agent with a config to print, in the CLI's order.
    clients: list[McpClientRef]
    target: str
    display_name: str
    #: The snippet to paste, in the agent's own format (JSON, TOML or YAML).
    config: str
    docs_url: str | None = None
    #: The profile this agent lists (``core``, ``lean``, ``standard``, ``all``),
    #: or ``None`` when ``mcp.tools`` is an exact list of names.
    profile: str | None = None
    tool_count: int
    #: Whether ``mcp.always_load`` is on, for an agent that defers tool
    #: schemas; ``None`` for one that loads every schema up front.
    always_load: bool | None = None


class UpdateMcpToolsRequest(BaseModel):
    """Persist a new ``mcp.tools`` override for a repo.

    ``tools`` accepts the same shapes as the config block: a list of explicit
    names, a profile name optionally followed by ``+``/``-`` deltas, deltas
    alone, or ``null``/empty to clear the override and fall back to each
    client's default.
    """

    repo_id: str
    tools: list[str] | str | None = None
