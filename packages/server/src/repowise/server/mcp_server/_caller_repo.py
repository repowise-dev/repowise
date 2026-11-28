"""The workspace member an MCP session works in, read from its client roots (#6468).

One workspace daemon serves clients from many member repos. A tool call that
omits ``repo`` should answer about the caller's repo, not the configured default.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from mcp.types import ClientCapabilities, RootsCapability

_log = logging.getLogger("repowise.mcp")

_ROOTS_TIMEOUT_S = 2.0

# shortcut: ignores notifications/roots/list_changed; upgrade if clients re-root mid-session
_session_aliases: weakref.WeakKeyDictionary[Any, str | None] = weakref.WeakKeyDictionary()


async def session_alias(session: Any, registry: Any) -> str | None:
    """The member alias the session's first mappable ``file://`` root names, else None."""
    try:
        return _session_aliases[session]
    except KeyError:
        pass
    alias = None
    # A stateless HTTP session has no client params, so this is False there.
    if session.check_client_capability(ClientCapabilities(roots=RootsCapability())):
        try:
            result = await asyncio.wait_for(session.list_roots(), _ROOTS_TIMEOUT_S)
            alias = _first_member(result.roots, registry)
        except Exception:  # a client that can't answer keeps the configured default
            _log.debug("repowise MCP: could not read client roots", exc_info=True)
    _session_aliases[session] = alias
    return alias


def _first_member(roots: Any, registry: Any) -> str | None:
    for root in roots:
        # The MCP schema makes every root a file:// URI.
        path = url2pathname(unquote(urlparse(str(root.uri)).path))
        try:
            alias = registry.resolve_repo_param(path)
        except ValueError:
            continue
        if isinstance(alias, str):
            return alias
    return None
