"""Who is calling, and what they may reach.

``verify_api_key`` (REST), the MCP HTTP transports and the webhooks all ask the
same :class:`Authenticator` for a :class:`Principal`, and the same
:class:`Authorizer` whether that principal may reach a repository or a tool.

The default authenticator is the shared ``REPOWISE_API_KEY`` exactly as before,
plus an optional local scoped-tokens file (``~/.repowise/tokens.yaml``, or the
path in ``REPOWISE_TOKENS_FILE``). With neither configured, loopback callers are
served without a credential and everyone else is refused, as before.

Both are named seams (``authenticator``, ``authorizer``) with ``local`` as the
default. A plugin registers its own implementation from the ``repowise.plugins``
entry point and selects it with ``default=True`` or
``REPOWISE_SEAM_AUTHENTICATOR`` / ``REPOWISE_SEAM_AUTHORIZER``.

An authorizer whose methods accept an ``action`` keyword is asked on every
call, for every principal, with :class:`Action` ``READ`` or ``WRITE``. One that
does not is called as before: only for principals that carry a ``repo_scope``,
and without the action. :func:`authorize_repo` and :func:`authorize_tool` are
the one place that difference is handled.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar, runtime_checkable

from fastapi import HTTPException, Request

from repowise.core.registry.plugins import SeamRegistry

logger = logging.getLogger(__name__)

TOKENS_FILE_ENV = "REPOWISE_TOKENS_FILE"
#: Declared by a REST route that checks tool scope on each tool it runs (chat);
#: every other route refuses a tool-scoped principal.
PER_CALL_TOOL_SCOPE = "tools:per-call"


class Action(StrEnum):
    """What a call does to the repository or tool it reaches."""

    READ = "read"
    WRITE = "write"


@dataclass(frozen=True)
class Principal:
    """An authenticated caller. ``None`` scopes mean unrestricted.

    ``claims`` is the authenticator's own data about the caller (roles, groups,
    tenant), for its authorizer to read. repowise never interprets it, and it
    survives the MCP HTTP token round trip, so it must be JSON-serializable.
    """

    id: str
    kind: str  # "local" | "shared-key" | "token" | "webhook", or a plugin's own
    repo_scope: frozenset[str] | None = None
    tool_scope: frozenset[str] | None = None
    claims: Mapping[str, Any] = field(default_factory=dict, hash=False)


#: A loopback caller on a server with no credential configured.
LOCAL_PRINCIPAL = Principal(id="local", kind="local")
#: A caller holding ``REPOWISE_API_KEY``.
SHARED_KEY_PRINCIPAL = Principal(id="shared-key", kind="shared-key")

#: The principal a tool is running for (MCP HTTP and chat), so a tool that
#: lists repos can filter to its scope. None outside an authenticated call.
current_principal: ContextVar[Principal | None] = ContextVar("current_principal", default=None)


@runtime_checkable
class Authenticator(Protocol):
    """Turns a request or a bearer token into a :class:`Principal`."""

    #: OAuth authorization servers that issue tokens for this server, published
    #: in the RFC 9728 metadata. Empty for static keys.
    authorization_servers: tuple[str, ...]

    def configured(self) -> bool:
        """True when a credential is required (otherwise loopback is open)."""
        ...

    async def verify_token(self, token: str) -> Principal | None:
        """The principal a bearer token stands for, or None. Mirrors the MCP SDK
        ``TokenVerifier.verify_token``."""
        ...

    async def authenticate(self, request: Request) -> Principal:
        """The principal for ``request``; raises ``HTTPException`` 401/403."""
        ...


@runtime_checkable
class Authorizer(Protocol):
    """Decides what an authenticated principal may reach.

    Both methods may also take an ``action: Action`` keyword (``READ`` or
    ``WRITE``). An implementation that declares it is called with it on every
    call, for every principal; one that does not keeps the original contract
    (see the module docstring). The protocol lists the original signatures so
    both kinds satisfy it.
    """

    def can_access_repo(self, principal: Principal, identifiers: Iterable[str | None]) -> bool:
        """True when ``principal`` may reach the repo ``identifiers`` name.

        ``identifiers`` are the forms one repo is known by: its id, stored name
        or workspace alias, any of which may be None. An action-aware
        implementation can also get an empty ``identifiers``: a call that names
        no single repo (a server-wide route such as registering a repo), to
        decide from the action alone.
        """
        ...

    def can_use_tool(self, principal: Principal, tool: str) -> bool:
        """True when ``principal`` may run ``tool``. The action is ``WRITE`` for
        a tool declared ``safety="mutating"``."""
        ...


# ---------------------------------------------------------------------------
# Scoped tokens file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopedToken:
    id: str
    sha256: str
    repos: frozenset[str] | None
    tools: frozenset[str] | None
    expires_at: datetime | None = None


_TOKEN_KEYS = frozenset({"id", "sha256", "repos", "tools", "expires_at"})


def tokens_file_path() -> Path:
    override = os.environ.get(TOKENS_FILE_ENV)
    return Path(override) if override else Path.home() / ".repowise" / "tokens.yaml"


def hash_token(token: str) -> str:
    """The digest stored in the tokens file. Tokens are random, so a plain
    SHA-256 is enough; no slow KDF is needed."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_cache: tuple[tuple[str, int, int], tuple[ScopedToken, ...]] | None = None


def _optional_set(entry: dict[str, Any], key: str) -> frozenset[str] | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{key}' must be a list of strings")
    return frozenset(value)


def _parse_tokens(raw: Any) -> tuple[ScopedToken, ...]:
    if not isinstance(raw, dict) or not isinstance(raw.get("tokens"), list):
        raise ValueError("expected a top-level 'tokens' list")
    tokens = []
    for entry in raw["tokens"]:
        if not isinstance(entry, dict):
            raise ValueError("each token must be a mapping")
        token_id, digest = entry.get("id"), entry.get("sha256")
        if not isinstance(token_id, str) or not token_id:
            raise ValueError("each token needs an 'id'")
        unknown = set(entry) - _TOKEN_KEYS
        if unknown:
            raise ValueError(f"token '{token_id}' has unknown keys: {sorted(unknown)}")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"token '{token_id}' needs a 64-character 'sha256'")
        tokens.append(
            ScopedToken(
                id=token_id,
                sha256=digest.lower(),
                repos=_optional_set(entry, "repos"),
                tools=_optional_set(entry, "tools"),
                expires_at=_expiry(token_id, entry.get("expires_at")),
            )
        )
    return tuple(tokens)


def _expiry(token_id: str, value: Any) -> datetime | None:
    """ISO 8601; a time without an offset is read as UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):  # YAML parses unquoted timestamps itself
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"token '{token_id}' has an invalid 'expires_at'") from exc
    else:
        raise ValueError(f"token '{token_id}' has an invalid 'expires_at'")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _tokens_file_present(path: Path) -> bool:
    """True when the file exists or cannot be checked (fail closed)."""
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def load_scoped_tokens(path: Path | None = None) -> tuple[ScopedToken, ...]:
    """Tokens from the file, re-read when it changes. Raises ``ValueError`` on
    a malformed file so the caller can fail closed."""
    global _cache
    path = path or tokens_file_path()
    try:
        stat = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return ()
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    import yaml

    try:
        tokens = _parse_tokens(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(str(exc)) from exc
    _cache = (key, tokens)
    return tokens


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def _shared_key() -> str | None:
    # Read through deps so the import-time capture (and tests patching it) stay
    # the single source.
    from repowise.server import deps

    return deps._API_KEY


class LocalAuthenticator:
    """The shared ``REPOWISE_API_KEY`` plus the optional scoped-tokens file."""

    authorization_servers: tuple[str, ...] = ()

    def configured(self) -> bool:
        return _shared_key() is not None or _tokens_file_present(tokens_file_path())

    async def verify_token(self, token: str) -> Principal | None:
        key = _shared_key()
        # Bytes, so a non-ASCII bearer is a mismatch rather than a TypeError.
        if key is not None and hmac.compare_digest(token.encode("utf-8"), key.encode("utf-8")):
            return SHARED_KEY_PRINCIPAL
        try:
            tokens = load_scoped_tokens()
        except ValueError as exc:
            # Fail closed: a broken file grants nothing, the shared key still works.
            logger.error("Ignoring unreadable tokens file %s: %s", tokens_file_path(), exc)
            return None
        if not tokens:
            return None
        digest = hash_token(token)
        for entry in tokens:
            if hmac.compare_digest(digest, entry.sha256):
                if entry.expires_at is not None and entry.expires_at <= datetime.now(UTC):
                    return None
                return Principal(
                    id=f"token:{entry.id}",
                    kind="token",
                    repo_scope=entry.repos,
                    tool_scope=entry.tools,
                )
        return None

    async def authenticate(self, request: Request) -> Principal:
        from repowise.server.deps import client_is_local

        if not self.configured():
            if not client_is_local(request):
                raise HTTPException(
                    status_code=403,
                    detail="Server is reachable from the network but REPOWISE_API_KEY "
                    "is not set. Set REPOWISE_API_KEY or bind to 127.0.0.1.",
                )
            return LOCAL_PRINCIPAL
        auth = request.headers.get("Authorization")
        if not auth or not auth.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing API key")
        principal = await self.verify_token(auth[7:])
        if principal is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return principal


class ScopeAuthorizer:
    """Checks a principal's own repo and tool scopes."""

    def can_access_repo(self, principal: Principal, identifiers: Iterable[str | None]) -> bool:
        if principal.repo_scope is None:
            return True
        return any(i in principal.repo_scope for i in identifiers if i)

    def can_use_tool(self, principal: Principal, tool: str) -> bool:
        return principal.tool_scope is None or tool in principal.tool_scope


# ---------------------------------------------------------------------------
# Calling an authorizer
# ---------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _takes_action(fn: Callable[..., Any]) -> bool:
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "action" or p.kind is p.VAR_KEYWORD for p in params)


def _accepts_action(method: Callable[..., Any]) -> bool:
    # Keyed on the underlying function, so a bound method per call still hits.
    fn = getattr(method, "__func__", method)
    try:
        return _takes_action(fn)
    except TypeError:  # an unhashable callable: inspect it uncached
        return _takes_action.__wrapped__(fn)


def is_action_aware(authorizer: Authorizer | None = None) -> bool:
    """True when either authorizer method takes an ``action``, so a caller
    that must work the action out (a tool lookup) only does it when it counts."""
    authorizer = authorizer or get_authorizer()
    return _accepts_action(authorizer.can_access_repo) or _accepts_action(authorizer.can_use_tool)


def checks_repo_access(principal: Principal, authorizer: Authorizer | None = None) -> bool:
    """True when repo checks run for ``principal``: it carries a repo scope, or
    the authorizer takes an action and so decides for every principal."""
    if principal.repo_scope is not None:
        return True
    return _accepts_action((authorizer or get_authorizer()).can_access_repo)


def authorize_repo(
    principal: Principal,
    identifiers: Iterable[str | None],
    action: Action = Action.READ,
    authorizer: Authorizer | None = None,
) -> bool:
    """``can_access_repo``, passing ``action`` only to an authorizer that takes it."""
    # The protocol lists the original signature; ``action`` is checked for here.
    method: Callable[..., bool] = (authorizer or get_authorizer()).can_access_repo
    if _accepts_action(method):
        return method(principal, identifiers, action=action)
    return method(principal, identifiers)


def authorize_tool(
    principal: Principal,
    tool: str,
    action: Action = Action.READ,
    authorizer: Authorizer | None = None,
) -> bool:
    """``can_use_tool``, passing ``action`` only to an authorizer that takes it."""
    # The protocol lists the original signature; ``action`` is checked for here.
    method: Callable[..., bool] = (authorizer or get_authorizer()).can_use_tool
    if _accepts_action(method):
        return method(principal, tool, action=action)
    return method(principal, tool)


def action_for_safety(safety: str) -> Action:
    """The action a tool's declared safety kind stands for."""
    return Action.WRITE if safety == "mutating" else Action.READ


_Endpoint = TypeVar("_Endpoint", bound=Callable[..., Any])
#: Set on a route endpoint by :func:`read_route`.
ROUTE_ACTION_ATTR = "__repowise_action__"


def read_route(endpoint: _Endpoint) -> _Endpoint:
    """Mark a POST route that only reads, so authorizers see ``Action.READ``.

    Without it a route's action follows its method: GET, HEAD and OPTIONS read,
    anything else writes. Put it under the router decorator::

        @router.post("/{repo_id}/preflight")
        @read_route
        async def preflight(...): ...
    """
    setattr(endpoint, ROUTE_ACTION_ATTR, Action.READ)
    return endpoint


authenticators: SeamRegistry[Authenticator] = SeamRegistry("authenticator")
authorizers: SeamRegistry[Authorizer] = SeamRegistry("authorizer")
_DEFAULT_AUTHENTICATOR = LocalAuthenticator()
_DEFAULT_AUTHORIZER = ScopeAuthorizer()
authenticators.register("local", _DEFAULT_AUTHENTICATOR, default=True)
authorizers.register("local", _DEFAULT_AUTHORIZER, default=True)


def get_authenticator() -> Authenticator:
    return authenticators.get()


def get_authorizer() -> Authorizer:
    return authorizers.get()


def set_authenticator(authenticator: Authenticator | None) -> None:
    """Select ``authenticator`` in-process (tests, embedders); ``None`` restores
    the local default."""
    if authenticator is None:
        authenticators.register("local", _DEFAULT_AUTHENTICATOR, default=True)
    else:
        authenticators.register("override", authenticator, default=True)


def set_authorizer(authorizer: Authorizer | None) -> None:
    """Select ``authorizer`` in-process; ``None`` restores the local default."""
    if authorizer is None:
        authorizers.register("local", _DEFAULT_AUTHORIZER, default=True)
    else:
        authorizers.register("override", authorizer, default=True)
