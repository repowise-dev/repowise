"""MCP endpoints: which tools a repo exposes, and each agent's config snippet.

The MCP server advertises a curated, configurable set of tools (see
``mcp_server/_tool_selection.py``). These endpoints let the dashboard show that
surface for a repo and persist a ``mcp.tools`` override into the repo's
``.repowise/config.yaml``. Changes take effect the next time ``repowise mcp`` is
started for that repo.

``/client-config`` answers with ``repowise agents print-config``, run in a child
process: the snippets are written by the CLI's agent targets, which the server
never imports (see ``services/setup_cli.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server import mcp_server as _mcp_server
from repowise.server.deps import resolve_session_factory, verify_api_key
from repowise.server.mcp_server._tool_selection import (
    client_tool_surface,
    describe_tool_surface,
    set_tool_override,
)
from repowise.server.schemas import McpToolSurfaceResponse, UpdateMcpToolsRequest
from repowise.server.schemas.mcp import (
    McpClientConfigResponse,
    McpClientRef,
    McpClientSurface,
)
from repowise.server.services import setup_cli

# Both endpoints below are ``async def`` and call ``describe_tool_surface``
# synchronously, and that call imports all 17 tool modules the first time it
# runs. Pay it here, at app import, so it lands before uvicorn starts serving
# rather than inside the event loop on whichever request happens to be first —
# which is where it used to land, back when importing ``_tool_selection`` pulled
# the whole tool surface in as a side effect.
_mcp_server.ensure_full_surface()

router = APIRouter(
    prefix="/api/mcp",
    tags=["mcp"],
    dependencies=[Depends(verify_api_key)],
)


async def _repo_path_for(request: Request, repo_id: str | None) -> str | None:
    """Resolve a repo's on-disk path from its id (None when not found)."""
    if not repo_id:
        return None
    try:
        factory = resolve_session_factory(request.app.state, repo_id)
        async with get_session(factory) as session:
            repo = await crud.get_repository(session, repo_id)
            return repo.local_path if repo else None
    except Exception:
        return None


def _surface(repo_id: str | None, repo_path: str | None) -> McpToolSurfaceResponse:
    data = describe_tool_surface(repo_path)
    return McpToolSurfaceResponse(repo_id=repo_id, **data)


#: Agents one ``?clients=`` request resolves; ``repowise agents`` lists fewer.
_MAX_CLIENTS = 32


def _client_surfaces(repo_path: str | None, clients: str | None) -> dict[str, McpClientSurface]:
    """Each asked-for agent's resolved tools, keyed by the id as asked.

    An id the identity registry does not know is skipped: there is no client
    to resolve a default for.
    """
    from repowise.core.agents.identity import identity_for_target_id

    out: dict[str, McpClientSurface] = {}
    for target in dict.fromkeys(c.strip() for c in (clients or "").split(",") if c.strip()):
        if len(out) >= _MAX_CLIENTS:
            break
        identity = identity_for_target_id(target)
        if identity is None:
            continue
        surface = client_tool_surface(repo_path, identity.slug)
        out[target] = McpClientSurface(profile=surface["profile"], tools=surface["tools"])
    return out


@router.get("/tools", response_model=McpToolSurfaceResponse)
async def get_tool_surface(
    request: Request, repo_id: str | None = None, clients: str | None = None
) -> McpToolSurfaceResponse:
    """Return the configurable tool surface for a repo.

    Pass ``?repo_id=`` so the response reflects that repo's workspace mode and
    its ``mcp.tools`` override; without it, the default single-repo surface is
    described. ``?clients=claude-code,cursor`` adds the tools each of those
    agents is listed, resolved from the override and that agent's default.
    """
    repo_path = await _repo_path_for(request, repo_id)
    response = _surface(repo_id, repo_path)
    if clients:
        response.clients = _client_surfaces(repo_path, clients)
    return response


@router.patch("/tools", response_model=McpToolSurfaceResponse)
async def update_tool_surface(
    body: UpdateMcpToolsRequest, request: Request
) -> McpToolSurfaceResponse:
    """Persist a new ``mcp.tools`` override for a repo and return the surface."""
    repo_path = await _repo_path_for(request, body.repo_id)
    if repo_path is None:
        raise HTTPException(404, f"Repo not found: {body.repo_id}")
    set_tool_override(repo_path, body.tools)
    return _surface(body.repo_id, repo_path)


#: The agents ``repowise agents`` lists. A fixed table in the installed CLI, so
#: it is read once per process.
_clients: list[McpClientRef] | None = None


def _list_clients(repo_path: str) -> list[McpClientRef]:
    global _clients
    if _clients is None:
        payload = setup_cli.agents_json(Path(repo_path)) or {}
        found = [
            McpClientRef(id=a["id"], display_name=a.get("display_name") or a["id"])
            for a in payload.get("agents") or []
            if isinstance(a, dict) and isinstance(a.get("id"), str)
        ]
        if found:
            _clients = found
        return found
    return _clients


def _print_config(repo_path: str, target: str) -> dict | None:
    run = setup_cli.run_cli(
        ["agents", "print-config", target, repo_path, "--format", "json"],
        cwd=Path(repo_path),
        # It prints a snippet and touches nothing; a hung run should not hold a worker.
        timeout=30.0,
    )
    payload = run.json() if run.ok else None
    return payload if isinstance(payload, dict) and isinstance(payload.get("config"), str) else None


@router.get("/client-config", response_model=McpClientConfigResponse)
async def get_client_config(
    request: Request, repo_id: str, target: str | None = None
) -> McpClientConfigResponse:
    """One agent's MCP config snippet for a repo, and what that agent lists.

    Without ``target``, the first agent in the CLI's order. ``clients`` names
    every agent there is a snippet for.
    """
    from repowise.core.agents.identity import UNKNOWN_AGENT, identity_for_target_id

    repo_path = await _repo_path_for(request, repo_id)
    if repo_path is None:
        raise HTTPException(404, f"Repo not found: {repo_id}")
    clients = await asyncio.to_thread(_list_clients, repo_path)
    if not clients:
        raise HTTPException(502, "Could not list agents: run 'repowise agents' in a terminal.")
    chosen = next((c for c in clients if c.id == (target or clients[0].id)), None)
    if chosen is None:
        known = ", ".join(c.id for c in clients)
        raise HTTPException(404, f"Unknown agent: {target}. Known: {known}.")
    payload = await asyncio.to_thread(_print_config, repo_path, chosen.id)
    if payload is None:
        raise HTTPException(
            502,
            f"Could not print the config: run 'repowise agents print-config {chosen.id}' "
            "in a terminal.",
        )
    identity = identity_for_target_id(chosen.id)
    surface = client_tool_surface(repo_path, identity.slug if identity else UNKNOWN_AGENT)
    return McpClientConfigResponse(
        repo_id=repo_id,
        clients=clients,
        target=chosen.id,
        display_name=chosen.display_name,
        config=payload["config"],
        docs_url=payload.get("docs_url"),
        profile=surface["profile"],
        tool_count=len(surface["tools"]),
        always_load=surface["always_load"],
    )
