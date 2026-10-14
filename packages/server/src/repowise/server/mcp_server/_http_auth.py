"""Bearer auth for the MCP HTTP transports (streamable HTTP and SSE).

Installed only when the server's :class:`~repowise.server.auth.Authenticator`
says a credential is configured; with none, the transports are served exactly
as before. The gate speaks the MCP authorization shape: an SDK ``TokenVerifier``
checks the bearer, a 401 carries ``WWW-Authenticate: Bearer
resource_metadata="..."``, and the RFC 9728 protected-resource document is
served at ``/.well-known/oauth-protected-resource``. An OAuth-backed
authenticator therefore plugs in by listing its ``authorization_servers``.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken, TokenVerifier
from starlette.authentication import AuthCredentials
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from repowise.server.auth import Authenticator, Principal, get_authenticator, get_authorizer

#: Lists repos rather than reading one; it filters its own output to the scope.
_SELF_SCOPED_TOOLS = frozenset({"list_repos"})

WELL_KNOWN = "/.well-known/oauth-protected-resource"
#: Externally visible base URL (``https://host[/prefix]``) for the metadata and
#: the 401 challenge, for servers behind a proxy. Otherwise a validated Host.
PUBLIC_URL_ENV = "REPOWISE_PUBLIC_URL"
_HOST_RE = re.compile(r"^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")
_TOOL_PREFIX = "tool:"
_ALL_TOOLS = "tool:*"


def access_token_for(principal: Principal, token: str) -> AccessToken:
    """Express a principal in the SDK's token shape. Tool scope travels as
    ``tool:<name>`` scopes; repo scope and kind as claims."""
    scopes = (
        [_ALL_TOOLS]
        if principal.tool_scope is None
        else sorted(_TOOL_PREFIX + t for t in principal.tool_scope)
    )
    claims: dict[str, Any] = {"repowise_kind": principal.kind}
    if principal.repo_scope is not None:
        claims["repowise_repos"] = sorted(principal.repo_scope)
    return AccessToken(
        token=token, client_id=principal.id, scopes=scopes, subject=principal.id, claims=claims
    )


def principal_from_access_token(access: AccessToken) -> Principal:
    """The inverse of :func:`access_token_for`, for any SDK verifier's token.
    No ``tool:`` scopes at all means no tool restriction."""
    claims = access.claims or {}
    tools = [s[len(_TOOL_PREFIX) :] for s in access.scopes if s.startswith(_TOOL_PREFIX)]
    repos = claims.get("repowise_repos")
    return Principal(
        id=access.subject or access.client_id,
        kind=str(claims.get("repowise_kind", "token")),
        repo_scope=frozenset(repos) if isinstance(repos, list) else None,
        tool_scope=None if not tools or "*" in tools else frozenset(tools),
    )


class PrincipalTokenVerifier(TokenVerifier):
    """An MCP SDK ``TokenVerifier`` backed by the server's Authenticator."""

    def __init__(self, authenticator: Authenticator | None = None) -> None:
        self._authenticator = authenticator

    async def verify_token(self, token: str) -> AccessToken | None:
        principal = await (self._authenticator or get_authenticator()).verify_token(token)
        return access_token_for(principal, token) if principal is not None else None


def protected_resource_metadata(resource: str, authorization_servers: tuple[str, ...]) -> dict:
    """RFC 9728 document. ``authorization_servers`` is optional there and
    omitted for static keys, which no authorization server issues."""
    doc: dict[str, Any] = {
        "resource": resource,
        "bearer_methods_supported": ["header"],
        "resource_name": "repowise",
    }
    if authorization_servers:
        doc["authorization_servers"] = list(authorization_servers)
    return doc


def _origin_and_prefix(scope: Scope) -> tuple[str, str]:
    """``scheme://host`` and any path prefix the public URL adds. The Host
    header is used only when it is a plain host[:port]."""
    public = os.environ.get(PUBLIC_URL_ENV, "").strip().rstrip("/")
    if public:
        parts = urlsplit(public)
        return f"{parts.scheme}://{parts.netloc}", parts.path
    scheme = scope.get("scheme") if scope.get("scheme") in ("http", "https") else "http"
    host = Headers(scope=scope).get("host", "")
    if not _HOST_RE.match(host):
        server = scope.get("server")
        host = f"{_bracket(server[0])}:{server[1]}" if server else "localhost"
    return f"{scheme}://{host}", ""


def _bracket(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _quoted(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _metadata_doc(scope: Scope, resource_path: str) -> dict:
    origin, prefix = _origin_and_prefix(scope)
    servers = tuple(getattr(get_authenticator(), "authorization_servers", ()))
    return protected_resource_metadata(origin + prefix + resource_path, servers)


class BearerGate:
    """ASGI wrapper: serves the metadata document, rejects missing or invalid
    bearers with 401, and hands the principal to the MCP app.

    Whether a credential is configured is checked per request, so a tokens
    file created after start-up gates the transport too; with none, requests
    pass straight through as before.
    """

    def __init__(self, app: ASGIApp, endpoint: str) -> None:
        self.app = app
        self.endpoint = endpoint
        self.verifier = PrincipalTokenVerifier()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not get_authenticator().configured():
            await self.app(scope, receive, send)
            return
        root = scope.get("root_path", "")
        path = scope["path"]
        relative = path[len(root) :] if root and path.startswith(root) else path
        resource_path = root + self.endpoint
        if relative in (WELL_KNOWN, WELL_KNOWN + self.endpoint) or path == WELL_KNOWN + resource_path:
            await _send_json(send, 200, _metadata_doc(scope, resource_path), [])
            return

        access = None
        auth = Headers(scope=scope).get("authorization", "")
        if auth.lower().startswith("bearer "):
            access = await self.verifier.verify_token(auth[7:])
            if access is not None and access.expires_at and access.expires_at < int(time.time()):
                access = None
        if access is None:
            origin, prefix = _origin_and_prefix(scope)
            # RFC 9728 3.1: the well-known segment goes between host and path.
            metadata_url = f"{origin}{WELL_KNOWN}{prefix}{resource_path}"
            challenge = (
                'Bearer error="invalid_token", error_description="Authentication required", '
                f'resource_metadata="{_quoted(metadata_url)}"'
            )
            body = {"error": "invalid_token", "error_description": "Authentication required"}
            await _send_json(send, 401, body, [(b"www-authenticate", challenge.encode())])
            return

        user = AuthenticatedUser(access)
        scope["user"] = user
        scope["auth"] = AuthCredentials(access.scopes)
        scope.setdefault("state", {})["principal"] = principal_from_access_token(access)
        reset = auth_context_var.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            auth_context_var.reset(reset)


def gate(app: ASGIApp, endpoint: str) -> BearerGate:
    """Wrap an MCP transport app served at ``endpoint`` (``/mcp`` or ``/sse``)."""
    return BearerGate(app, endpoint)


def metadata_route(resource_path: str) -> Route:
    """The host-root RFC 9728 route for an MCP app mounted under a sub-path,
    e.g. ``metadata_route("/sub/mcp")`` beside ``Mount("/sub", app=...)``."""

    async def endpoint(request: Request) -> Response:
        if not get_authenticator().configured():
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return JSONResponse(_metadata_doc(request.scope, resource_path))

    return Route(WELL_KNOWN + resource_path, endpoint, methods=["GET"])


async def _send_json(send: Send, status: int, body: dict, headers: list) -> None:
    raw = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
                *headers,
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})


# ---------------------------------------------------------------------------
# Per-call scope
# ---------------------------------------------------------------------------


def _call_targets(arguments: dict[str, Any]) -> list[tuple[str | None, ...]]:
    """The repos one tool call would read, each as its accepted identifiers."""
    from repowise.server.mcp_server import _state

    repo = arguments.get("repo") or None
    registry = _state._registry
    if registry is not None:
        try:
            resolved = registry.resolve_repo_param(repo)
        except ValueError:
            return [(repo,)]
        aliases = resolved if isinstance(resolved, list) else [resolved]
        return [(alias,) for alias in aliases]
    if repo is not None:
        return [(repo,)]
    name = Path(_state._repo_path).name if _state._repo_path else None
    return [(_state._served_repo_id, name)]


def call_denial(principal: Principal, tool: str, arguments: dict[str, Any]) -> str | None:
    """Why ``principal`` may not make this call, or None when it may."""
    authorizer = get_authorizer()
    if not authorizer.can_use_tool(principal, tool):
        return f"Tool '{tool}' is outside this credential's scope."
    if principal.repo_scope is not None and tool not in _SELF_SCOPED_TOOLS:
        if arguments.get("repo") == "all":
            return "repo='all' is not available to a repository-scoped credential."
        for identifiers in _call_targets(arguments):
            if not authorizer.can_access_repo(principal, identifiers):
                return "Repository is outside this credential's scope."
    return None
