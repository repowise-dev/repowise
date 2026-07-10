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

import contextlib
import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# _get_repo raises LookupError with these two message shapes; matching on the
# message keeps _helpers.py free of any import back-reference to this module.
_NOT_INDEXED_MARKER = "No repositories found"


def _shape_not_indexed() -> dict[str, Any]:
    """Success-shaped response for a repo that has never been indexed."""
    return {
        "error": "This repository has no repowise index yet.",
        "remedy": (
            "The user can build one by running 'repowise init' in the repo "
            "root. Indexing is the user's decision — suggest it once, do not "
            "run it yourself."
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
    return _shape_internal_error(tool, exc)


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
            return await fn(*args, **kwargs)
        except Exception as exc:
            logger.warning("mcp tool %s shielded exception: %s", tool, exc, exc_info=True)
            return _shape_exception(tool, exc)

    with contextlib.suppress(ValueError, TypeError):  # pragma: no cover - exotic callables
        _wrapped.__signature__ = inspect.signature(fn)  # type: ignore[attr-defined]
    return _wrapped
