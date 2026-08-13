"""Failure shield — no recoverable state ever reaches the agent as isError.

One or two protocol-level error results early in a session teach an agent to
stop calling the server for the rest of that session, so every expected
condition (repo not indexed, unknown alias, even an unexpected internal crash)
must come back as a normal success-shaped response that explains what happened
and what to do next. FastMCP converts any exception that escapes a tool into
``isError: true``; this wrapper is the last line of defense that keeps that
from ever happening.

Composed around every tool at registration time (see ``__init__.py``):
``instrument(shield(fn))`` — the shield sits innermost so the savings layer
still sees (and dead-end-debits) the shaped error response.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

from repowise.server.mcp_server import _state

logger = logging.getLogger(__name__)

# Ceiling on how long a tool call waits for the deferred `import lancedb` to
# finish. The import is seconds, not minutes; the bound exists only so a
# pathological environment degrades to a slow answer rather than a stuck one.
_WARMUP_TIMEOUT_S = 60.0

# _get_repo raises LookupError with these two message shapes; matching on the
# message keeps _helpers.py free of any import back-reference to this module.
_NOT_INDEXED_MARKER = "No repositories found"

# SQLite's wording when the ORM selects a column or table the store does not
# have. Matched on the message rather than on an exception class so this module
# keeps importing nothing from SQLAlchemy, the way it always has.
_STALE_INDEX_MARKERS = ("no such column", "no such table")


def _shape_not_indexed() -> dict[str, Any]:
    """Success-shaped response for a repo that has never been indexed."""
    return {
        "error": "This repository has no repowise index yet.",
        "remedy": (
            "The user can build one by running 'repowise init --yes' in the "
            "repo root. No API key is required: without one it renders the "
            "whole wiki from the code's structure. Suggest --no-editor-setup "
            "too if this is a scratch clone, fixture, or worktree, so init "
            "leaves their global MCP config pointed where it is. Indexing is "
            "the user's decision — suggest it once, do not run it yourself."
        ),
        "guidance": (
            "Until an index exists, every repowise tool will return this "
            "notice. Answer questions about this repo with your built-in "
            "tools (Read/Grep/Glob) for the rest of the session."
        ),
    }


def _shape_unknown_repo(exc: Exception) -> dict[str, Any]:
    """Success-shaped response for an unknown repo alias/path/id."""
    return {
        "error": str(exc),
        "remedy": (
            "Call list_repos to see the valid repo aliases, or omit the "
            "'repo' argument to use the default repository."
        ),
    }


def _driver_message(exc: Exception) -> str:
    """The DBAPI error's own message, without SQLAlchemy's appended context.

    ``str()`` on a SQLAlchemy ``OperationalError`` appends the compiled
    statement and its bound parameters. For ``get_answer`` those parameters
    include the user's question, so matching on the full string lets a caller
    who merely *asks about* "no such column" be told their index is stale.
    ``exc.orig`` is the driver exception alone.
    """
    orig = getattr(exc, "orig", None)
    return str(exc if orig is None else orig).lower()


def _is_stale_index_error(exc: Exception) -> bool:
    """True for the ORM failure a store older than the models produces."""
    if type(exc).__name__ != "OperationalError":
        return False
    return any(marker in _driver_message(exc) for marker in _STALE_INDEX_MARKERS)


def _shape_stale_index(exc: Exception) -> dict[str, Any]:
    """Success-shaped response for a store whose schema predates this repowise.

    ``init_db`` back-fills *additive* drift on every read path, so reaching
    here means the drift is the kind ``_reconcile_schema`` deliberately does
    not touch (a removed or renamed column, a changed type — see
    ``database.py``). The generic internal-error shape is the wrong answer for
    it: it tells the caller to stop using the tool for the session, when one
    ``repowise update`` fixes it.
    """
    return {
        # The driver's own message: SQLAlchemy appends the whole compiled
        # statement and its parameters, which is pages of SQL an agent cannot
        # act on — and which carries the caller's own question back to them.
        "error": (
            "This repository's repowise index predates the installed "
            f"repowise: {_driver_message(exc).splitlines()[0].strip()}"
        ),
        "remedy": (
            "The user can rebuild it by running 'repowise update' in the repo "
            "root. Re-indexing is the user's decision — suggest it once, do "
            "not run it yourself."
        ),
        "guidance": (
            "Until the index is rebuilt, every repowise tool will return this "
            "notice. Answer questions about this repo with your built-in "
            "tools (Read/Grep/Glob) for the rest of the session."
        ),
    }


def _shape_internal_error(tool: str, exc: Exception) -> dict[str, Any]:
    """Success-shaped response for an unexpected internal failure."""
    return {
        "error": f"{tool} hit an internal error: {type(exc).__name__}: {exc}",
        "guidance": (
            "Retry this call once. If it fails again, stop using this tool "
            "for the rest of the session and mention the error to the user "
            "— the other repowise tools are unaffected."
        ),
    }


def _shape_exception(tool: str, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, LookupError):
        if _NOT_INDEXED_MARKER in str(exc):
            return _shape_not_indexed()
        return _shape_unknown_repo(exc)
    # Workspace mode raises ValueError("Unknown repo '<alias>'. Available:
    # [...]") from the registry — same expected condition, different type.
    if isinstance(exc, ValueError) and str(exc).startswith("Unknown repo"):
        return _shape_unknown_repo(exc)
    if _is_stale_index_error(exc):
        return _shape_stale_index(exc)
    return _shape_internal_error(tool, exc)


async def _await_import_warmup(tool: str) -> None:
    """Hold a tool call until the deferred ``import lancedb`` has finished.

    That import runs on a worker thread so the loop keeps answering while it
    proceeds. But an import on a worker thread holds Python's import locks, and
    every tool body lazily imports something: when the two overlap they block
    each other and the *event loop itself* stops making progress. Nothing
    recovers from that state, because the pending ``asyncio.wait_for`` bounds
    that would otherwise cap the call need the loop in order to fire. The
    observed symptom is the first tool call of a session never returning.

    Waiting here, before any handler body runs, keeps the main thread out of
    the import machinery for the duration, which is what breaks the cycle.
    """
    ready = _state._lancedb_ready
    if ready is None or ready.is_set():
        return
    try:
        await asyncio.wait_for(ready.wait(), timeout=_WARMUP_TIMEOUT_S)
    except TimeoutError:
        logger.warning(
            "mcp tool %s proceeding after waiting %.0fs for the lancedb import; "
            "vector search may be unavailable for this call",
            tool,
            _WARMUP_TIMEOUT_S,
        )


def shield(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap an async MCP tool so no exception escapes to FastMCP.

    Signature-preserving for the same reason as the savings wrapper: FastMCP
    introspects the wrapped function to build the tool's input schema.
    """
    if not inspect.iscoroutinefunction(fn):
        return fn

    tool = getattr(fn, "__name__", "tool")

    @functools.wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            await _await_import_warmup(tool)
            return await fn(*args, **kwargs)
        except Exception as exc:
            logger.warning("mcp tool %s shielded exception: %s", tool, exc, exc_info=True)
            return _shape_exception(tool, exc)

    with contextlib.suppress(ValueError, TypeError):  # pragma: no cover - exotic callables
        _wrapped.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return _wrapped
