"""Refresh ``.repowise/setup.json`` once, after an agent's first call here.

The handshake's setup line (``_server._setup_extra``) reads only that file,
and only a full read writes it: ``repowise next``, ``repowise status``, Do
next, and ``get_overview`` / ``get_health``, which the core profile does not
list. So an agent on the core profile never saw the finish-setup offer. Its
first successful call now runs the same full read in the background, and the
offer reaches the handshake of its next session.

The refresh never touches the response: it runs as its own task after the
call returns, swallows every failure, and runs at most once per served
checkout per server process, even when that one attempt fails (the next
full read or session retries it). It is skipped when the last answer is final
(completed or dismissed) or already carries the offer.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Checkouts this process has already considered, so the check runs once each.
_considered: set[str] = set()

#: Strong references to running refreshes, so none is collected mid-run.
_tasks: set[asyncio.Task[None]] = set()


def _key(root: str) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def _needed(root: str) -> bool:
    """Whether the last answer could still turn into an offer.

    Missing, written before any agent call, or read with a fact unavailable:
    refresh. Completed or dismissed never offers again, and an answer that
    already offers is what the handshake shows.
    """
    from repowise.core.setup_state import FINISH_SETUP_OFFER, read_setup_state

    state = read_setup_state(root)
    if state.get("completed") or state.get("dismissed"):
        return False
    return state.get("offer") != FINISH_SETUP_OFFER


def schedule_setup_refresh(result: Any) -> None:
    """Start the one background refresh after a successful agent call, if needed.

    An agent's call is one that arrived over an MCP session; a direct call
    (tests, embedders) has none and is left alone.
    """
    if not isinstance(result, dict) or result.get("error"):
        return
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._savings import interaction

    live = interaction.current()
    if live is None or live.session_id is None:
        return
    root = _state.served_path()
    # Checked before any file read, so later calls pay one set lookup.
    if not root or root in _considered:
        return
    _considered.add(root)
    if not (Path(root) / ".repowise").is_dir() or not _needed(root):
        return
    # The task must not write into the finished call's interaction.
    context = contextvars.copy_context()
    context.run(interaction._CURRENT.set, None)
    task = asyncio.get_running_loop().create_task(_refresh(_key(root)), context=context)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _refresh(key: str) -> None:
    """Gather the setup facts and the actions view, which records the answer."""
    try:
        from repowise.core.persistence.crud.analysis.actions import load_actions_view
        from repowise.core.persistence.database import get_session
        from repowise.server.mcp_server._helpers import (
            _cross_repo_cut,
            _get_repo,
            _resolve_repo_context,
        )
        from repowise.server.mcp_server.tool_overview.actions import setup_facts

        ctx = await _resolve_repo_context(None)
        # In a workspace the default repo may live elsewhere than the served
        # root, whose setup.json is the one the handshake reads.
        if ctx.path is None or _key(str(ctx.path)) != key:
            return
        async with get_session(ctx.session_factory) as session:
            repository = await _get_repo(session)
            setup = await setup_facts(session, repository)
            if setup is not None:
                await load_actions_view(
                    session, repository.id, setup=setup, **_cross_repo_cut(ctx.alias)
                )
    except Exception:  # best-effort: the handshake keeps the last answer
        logger.debug("setup refresh failed", exc_info=True)


__all__ = ["schedule_setup_refresh"]
