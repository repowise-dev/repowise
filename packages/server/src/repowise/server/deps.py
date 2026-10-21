"""FastAPI dependency injection for repowise server.

Provides Depends() callables for:
- Database sessions (async, auto-commit/rollback)
- Vector store access
- Full-text search access
- API key authentication, returning the caller's ``Principal``
"""

from __future__ import annotations

import ipaddress
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader, SecurityScopes
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.database import get_session
from repowise.server.auth import (
    PER_CALL_TOOL_SCOPE,
    Principal,
    get_authenticator,
    get_authorizer,
)

logger = logging.getLogger(__name__)

_API_KEY = os.environ.get("REPOWISE_API_KEY")
_REPOWISE_HOST = os.environ.get("REPOWISE_HOST", "127.0.0.1")
_header_scheme = APIKeyHeader(name="Authorization", auto_error=False)

# Warn at import time if the server is network-exposed without a key. Only an
# advisory: what a request is actually allowed to do is decided per request
# from the peer address, since REPOWISE_HOST is not always set.
if _API_KEY is None and _REPOWISE_HOST in ("0.0.0.0", "::"):
    logger.warning(
        "SECURITY WARNING: Server is binding to %s without REPOWISE_API_KEY set. "
        "Requests from other hosts will be refused. "
        "Set REPOWISE_API_KEY to serve them, or bind to 127.0.0.1.",
        _REPOWISE_HOST,
    )


def resolve_session_factory(app_state, repo_id: str | None):
    """Pick the session factory whose database contains ``repo_id``.

    In workspace mode each repo has its own ``wiki.db`` registered under
    ``app_state.workspace_sessions[repo_id]``. The primary
    ``app_state.session_factory`` does NOT see those rows. When ``repo_id``
    is ``None`` or unknown, falls back to the primary factory (single-repo
    mode, or the primary repo of a workspace).
    """
    if repo_id:
        ws_sessions = getattr(app_state, "workspace_sessions", None)
        if ws_sessions and repo_id in ws_sessions:
            return ws_sessions[repo_id]
    return app_state.session_factory


def resolve_request_session_factory(request: Request):
    """Return the session factory for the active route's ``repo_id``.

    Reads ``repo_id`` from the path (e.g. ``/api/repos/{repo_id}/...``)
    or query string (e.g. ``/api/pages?repo_id=xxx``), then delegates to
    :func:`resolve_session_factory`. Used by streaming endpoints that
    can't take a single ``Depends(get_db_session)`` because they need to
    open multiple sessions over the request lifetime.
    """
    repo_id = request.path_params.get("repo_id") or request.query_params.get("repo_id")
    return resolve_session_factory(request.app.state, repo_id)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session with auto-commit on success, rollback on error.

    In workspace mode, routes to the correct repo's DB based on the
    ``repo_id`` path parameter when a matching session factory exists.
    """
    factory = resolve_request_session_factory(request)
    async with get_session(factory) as session:
        yield session


async def get_vector_store(request: Request):
    """Return the vector store from app state."""
    return request.app.state.vector_store


async def get_fts(request: Request):
    """Return the full-text search engine from app state."""
    return request.app.state.fts


async def get_workspace_config(request: Request):
    """Return WorkspaceConfig from app state, or None in single-repo mode."""
    return getattr(request.app.state, "workspace_config", None)


async def get_cross_repo_enricher(request: Request):
    """Return CrossRepoEnricher from app state, or None."""
    return getattr(request.app.state, "cross_repo_enricher", None)


def workspace_alias_for(app_state, repo_id: str) -> str | None:
    """The workspace alias of the member stored as ``repo_id``; None outside a workspace."""
    ws_config = getattr(app_state, "workspace_config", None)
    ws_root = getattr(app_state, "workspace_root", None)
    path_to_id = getattr(app_state, "workspace_path_to_repo_id", None) or {}
    if ws_config is None or ws_root is None:
        return None
    for entry in ws_config.repos:
        if path_to_id.get(str((Path(ws_root) / entry.path).resolve())) == repo_id:
            return entry.alias
    return None


def client_is_local(request: Request) -> bool:
    """True when the request's peer address is a loopback address.

    The keyless-access decision is taken from the peer, not from
    ``REPOWISE_HOST``: that env var describes what an operator intended, and
    nothing on the CLI path sets it, so ``repowise serve --host 0.0.0.0``
    used to bind the world while the auth logic still read "127.0.0.1" and
    let every request through. An unknown peer counts as remote.

    Known limitation: behind a reverse proxy on the same host every peer is
    loopback, so those deployments still need ``REPOWISE_API_KEY``. Forwarded
    headers are deliberately not trusted, since any client can send them.
    """
    client = request.client
    if client is None or not client.host:
        return False
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        # A non-address peer (a UNIX socket, an ASGI test transport that
        # names itself) is not something we can vouch for.
        return False


def auth_is_open(request: Request) -> bool:
    """True when no credential is required (none configured, local caller)."""
    return not get_authenticator().configured() and client_is_local(request)


#: Routes a repo-scoped principal may call without naming a repo in the path,
#: because the handler filters its output to, or checks, the principal's scope.
_SCOPE_FILTERED_ROUTES = frozenset(
    {
        ("GET", "/api/repos"),
        ("GET", "/api/repos/summary"),
        ("POST", "/api/chat/sessions"),
        ("GET", "/api/chat/sessions"),
        ("POST", "/api/chat/sessions/{session_id}/messages"),
    }
)


async def _repo_identifiers(request: Request, repo_id: str) -> tuple[str, ...]:
    """The id plus the stored name, so a scope may list either."""
    from repowise.core.persistence import crud

    try:
        factory = resolve_session_factory(request.app.state, repo_id)
        async with get_session(factory) as session:
            repo = await crud.get_repository(session, repo_id)
    except Exception:
        repo = None
    return (repo_id, repo.name) if repo is not None else (repo_id,)


async def _enforce_repo_scope(request: Request, principal: Principal) -> None:
    """403 unless the route's repo is in scope. A restricted principal is
    refused on routes that name no repo, except the filtered listings."""
    if principal.repo_scope is None:
        return
    authorizer = get_authorizer()
    repo_id = request.path_params.get("repo_id") or request.query_params.get("repo_id")
    if repo_id:
        if authorizer.can_access_repo(principal, (repo_id,)):
            return
        if authorizer.can_access_repo(principal, await _repo_identifiers(request, repo_id)):
            return
    else:
        route = request.scope.get("route")
        if (request.method, getattr(route, "path", None)) in _SCOPE_FILTERED_ROUTES:
            return
    raise HTTPException(status_code=403, detail="Repository is outside this credential's scope")


def request_principal(request: Request) -> Principal | None:
    """The principal ``verify_api_key`` stored on this request, if any."""
    return getattr(request.state, "principal", None)


def scoped_repo_id(request: Request) -> str | None:
    """The repo a repo-restricted principal was authorised for on this request;
    None for unrestricted callers. Routes that load an object by its own id
    check it belongs to this repo."""
    principal = request_principal(request)
    if principal is None or principal.repo_scope is None:
        return None
    return request.path_params.get("repo_id") or request.query_params.get("repo_id")


async def verify_api_key(
    security_scopes: SecurityScopes,
    request: Request,
    auth: str | None = Security(_header_scheme),
) -> Principal:
    """Authenticate the caller and return its :class:`Principal`.

    With no credential configured only local callers are served; a request
    from anywhere else is rejected (fail-closed for network-exposed
    deployments, however the server was started). Otherwise requests must
    include ``Authorization: Bearer <key>`` carrying ``REPOWISE_API_KEY`` or a
    scoped token. The principal is also stored on ``request.state.principal``.

    ``auth`` is read again by the authenticator; declaring it here keeps the
    bearer scheme in the OpenAPI document. ``Security(verify_api_key,
    scopes=["tool:<name>"])`` additionally requires those tools in scope. A
    tool-scoped principal is refused on any route that declares neither tool
    scopes nor ``PER_CALL_TOOL_SCOPE``.
    """
    principal = await get_authenticator().authenticate(request)
    request.state.principal = principal
    await _enforce_repo_scope(request, principal)
    scopes = security_scopes.scopes
    if principal.tool_scope is not None and not any(
        s == PER_CALL_TOOL_SCOPE or s.startswith("tool:") for s in scopes
    ):
        raise HTTPException(status_code=403, detail="This credential is limited to tools")
    authorizer = get_authorizer()
    for scope in scopes:
        tool = scope.removeprefix("tool:")
        if tool != scope and not authorizer.can_use_tool(principal, tool):
            raise HTTPException(
                status_code=403, detail=f"Tool '{tool}' is outside this credential's scope"
            )
    return principal
