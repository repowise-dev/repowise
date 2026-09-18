"""One logical MCP invocation, as seen by every layer that measures part of it.

No single middleware layer can account for a tool call on its own, and that is
why the numbers were wrong rather than merely incomplete. The three input sizes
the accounting contract wants are observable at three different depths:

* the **raw tool output**, visible only inside the innermost budget layer,
  before anything has been shed;
* the **counterfactual baseline**, computable only at the savings layer, because
  the estimators read fields (``results[].target_path``,
  ``targets[].skeleton.full_tokens``) that a later budget pass may drop;
* the **delivered payload**, final only after the outermost budget has run,
  which is *after* the old ledger row was written.

So the invocation gets an identity at the outermost layer and each depth records
what only it can see. One event per call, measured where each number is true.

The carrier is a context variable rather than a parameter because FastMCP builds
each tool's input schema from its signature: threading state through the call
would change the schema every layer is carefully written to preserve.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakKeyDictionary

from repowise.core.agents.identity import UNKNOWN_AGENT
from repowise.core.savings.correlation import new_event_id

logger = logging.getLogger(__name__)


@dataclass
class Interaction:
    """What one MCP tool call accumulated on its way out."""

    event_id: str
    tool: str
    #: Set by the budget layers, which already resolve it; recording it here
    #: saves a third resolution per call on a path that runs per tool use.
    repo_root: str | None = None
    agent: str = UNKNOWN_AGENT
    identity_metadata: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
    request_id: str | None = None
    #: The counterfactual the answer replaced, when one could be derived.
    baseline_input_tokens: int | None = None
    #: Raw tool output, before any budget shed anything.
    pre_budget_input_tokens: int | None = None

    def observe_pre_budget(self, tokens: int | None) -> None:
        """Record the raw size, first writer only.

        Both budget layers run this closure, and the innermost one runs first,
        so the first value is the only one that has seen the untrimmed output.
        """
        if self.pre_budget_input_tokens is None and tokens is not None:
            self.pre_budget_input_tokens = tokens


_CURRENT: ContextVar[Interaction | None] = ContextVar("_repowise_mcp_interaction", default=None)

#: Per-connection attribution, resolved once and reused.
#:
#: ``clientInfo`` is announced at initialize and cannot change within a session,
#: so resolving it per event would be pure repetition on a hot path. Keyed
#: weakly on the session object, so a closed connection's entry goes away with
#: it rather than accumulating for the life of the server.
_SESSIONS: WeakKeyDictionary[Any, tuple[str, dict[str, str], str]] = WeakKeyDictionary()


def current() -> Interaction | None:
    """The invocation in flight, or ``None`` outside one."""
    return _CURRENT.get()


@contextmanager
def begin(tool: str) -> Iterator[Interaction]:
    """Open an invocation, mint its id, and attribute it. Never raises."""
    interaction = Interaction(event_id=new_event_id(), tool=tool)
    try:
        interaction.agent, interaction.identity_metadata, interaction.session_id = _attribution()
        interaction.request_id = _request_id()
    except Exception:  # pragma: no cover - attribution is best-effort
        logger.debug("mcp attribution failed for %s", tool, exc_info=True)
    token = _CURRENT.set(interaction)
    try:
        yield interaction
    finally:
        _CURRENT.reset(token)


def _context() -> Any | None:
    """The FastMCP request context, or ``None`` when there is no live request.

    ``get_context()`` swallows the missing-contextvar ``LookupError`` and hands
    back a context whose ``session`` property then raises, so both have to be
    guarded here rather than at the call site.
    """
    try:
        from repowise.server.mcp_server._server import mcp

        return mcp.get_context()
    except Exception:
        return None


def _attribution() -> tuple[str, dict[str, str], str | None]:
    """Resolve who is calling, once per connection.

    Returns ``unknown`` for a client that announced nothing. Never the CLI's
    auto-detection fallback: guessing here would file one host's traffic under
    another, which is worse than admitting we cannot tell.
    """
    from repowise.core.savings.normalization import normalize_mcp_identity

    context = _context()
    session = getattr(context, "session", None) if context is not None else None
    if session is None:
        return UNKNOWN_AGENT, {}, None

    cached = _SESSIONS.get(session)
    if cached is not None:
        return cached

    announced = None
    params = getattr(session, "client_params", None)
    client_info = getattr(params, "clientInfo", None)
    if client_info is not None:
        announced = getattr(client_info, "name", None)
    agent, metadata = normalize_mcp_identity(announced)

    # A server-minted namespace, not a protocol value: stdio exposes no session
    # id, and the JSON-RPC request id is client-chosen and per-request. This is
    # stable for the life of the connection, which is what a session means here.
    resolved = (agent, metadata, new_event_id())
    with contextlib.suppress(TypeError):  # a session that cannot be weak-referenced
        _SESSIONS[session] = resolved
    return resolved


def _request_id() -> str | None:
    """The JSON-RPC request id, for correlation only.

    Client-chosen and connection-scoped, so it is never an event id and is
    hashed rather than stored by the time it reaches the ledger.
    """
    context = _context()
    if context is None:
        return None
    try:
        value = context.request_id
    except Exception:
        return None
    return str(value) if value not in (None, "") else None
