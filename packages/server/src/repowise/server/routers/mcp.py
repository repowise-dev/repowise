"""MCP tool-surface endpoints — read and edit which tools a repo exposes.

The MCP server advertises a curated, configurable set of tools (see
``mcp_server/_tool_selection.py``). These endpoints let the dashboard show that
surface for a repo and persist a ``mcp.tools`` override into the repo's
``.repowise/config.yaml``. Changes take effect the next time ``repowise mcp`` is
started for that repo.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.server import mcp_server as _mcp_server
from repowise.server.deps import resolve_request_session_factory, verify_api_key
from repowise.server.mcp_server._tool_selection import (
    describe_tool_surface,
    set_tool_override,
)
from repowise.server.schemas import McpToolSurfaceResponse, UpdateMcpToolsRequest

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
        factory = resolve_request_session_factory(request)
        async with get_session(factory) as session:
            repo = await crud.get_repository(session, repo_id)
            return repo.local_path if repo else None
    except Exception:
        return None


def _surface(repo_id: str | None, repo_path: str | None) -> McpToolSurfaceResponse:
    data = describe_tool_surface(repo_path)
    return McpToolSurfaceResponse(repo_id=repo_id, **data)


@router.get("/tools", response_model=McpToolSurfaceResponse)
async def get_tool_surface(
    request: Request, repo_id: str | None = None
) -> McpToolSurfaceResponse:
    """Return the configurable tool surface for a repo.

    Pass ``?repo_id=`` so the response reflects that repo's workspace mode and
    its ``mcp.tools`` override; without it, the default single-repo surface is
    described.
    """
    repo_path = await _repo_path_for(request, repo_id)
    return _surface(repo_id, repo_path)


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
