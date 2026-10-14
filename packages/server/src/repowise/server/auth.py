"""Who is calling, and what they may reach.

``verify_api_key`` (REST), the MCP HTTP transports and the webhooks all ask the
same :class:`Authenticator` for a :class:`Principal`, and the same
:class:`Authorizer` whether that principal may reach a repository or a tool.

The default authenticator is the shared ``REPOWISE_API_KEY`` exactly as before,
plus an optional local scoped-tokens file (``~/.repowise/tokens.yaml``, or the
path in ``REPOWISE_TOKENS_FILE``). With neither configured, loopback callers are
served without a credential and everyone else is refused, as before.

Replacing either seam is one call (``set_authenticator`` / ``set_authorizer``);
the module-level default-plus-override shape is meant to register into the
generic plugin registry once that exists, without changing callers.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

TOKENS_FILE_ENV = "REPOWISE_TOKENS_FILE"


@dataclass(frozen=True)
class Principal:
    """An authenticated caller. ``None`` scopes mean unrestricted."""

    id: str
    kind: str  # "local" | "shared-key" | "token" | "webhook", or a plugin's own
    repo_scope: frozenset[str] | None = None
    tool_scope: frozenset[str] | None = None


#: A loopback caller on a server with no credential configured.
LOCAL_PRINCIPAL = Principal(id="local", kind="local")
#: A caller holding ``REPOWISE_API_KEY``.
SHARED_KEY_PRINCIPAL = Principal(id="shared-key", kind="shared-key")


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
    """Decides what an authenticated principal may reach."""

    def can_access_repo(self, principal: Principal, identifiers: Iterable[str | None]) -> bool:
        """True when any of ``identifiers`` (id, name, alias of one repo) is in scope."""
        ...

    def can_use_tool(self, principal: Principal, tool: str) -> bool: ...


# ---------------------------------------------------------------------------
# Scoped tokens file
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopedToken:
    id: str
    sha256: str
    repos: frozenset[str] | None
    tools: frozenset[str] | None


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
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"token '{token_id}' needs a 64-character 'sha256'")
        tokens.append(
            ScopedToken(
                id=token_id,
                sha256=digest.lower(),
                repos=_optional_set(entry, "repos"),
                tools=_optional_set(entry, "tools"),
            )
        )
    return tuple(tokens)


def load_scoped_tokens(path: Path | None = None) -> tuple[ScopedToken, ...]:
    """Tokens from the file, re-read when it changes. Raises ``ValueError`` on
    a malformed file so the caller can fail closed."""
    global _cache
    path = path or tokens_file_path()
    try:
        stat = path.stat()
    except OSError:
        return ()
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
        return _shared_key() is not None or tokens_file_path().is_file()

    async def verify_token(self, token: str) -> Principal | None:
        key = _shared_key()
        if key is not None and hmac.compare_digest(token, key):
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


_DEFAULT_AUTHENTICATOR = LocalAuthenticator()
_DEFAULT_AUTHORIZER = ScopeAuthorizer()
_authenticator: Authenticator | None = None
_authorizer: Authorizer | None = None


def get_authenticator() -> Authenticator:
    return _authenticator or _DEFAULT_AUTHENTICATOR


def set_authenticator(authenticator: Authenticator | None) -> None:
    """Install a replacement; ``None`` restores the default."""
    global _authenticator
    _authenticator = authenticator


def get_authorizer() -> Authorizer:
    return _authorizer or _DEFAULT_AUTHORIZER


def set_authorizer(authorizer: Authorizer | None) -> None:
    """Install a replacement; ``None`` restores the default."""
    global _authorizer
    _authorizer = authorizer
