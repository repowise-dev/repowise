"""Refresh ``.repowise/setup.json`` once, after an agent's first call here.

The handshake's setup line (``_server._setup_extra``) reads only that file,
and only a full read writes it: ``repowise next``, ``repowise status``, Do
next, and ``get_overview`` / ``get_health``, which the core profile does not
list. So an agent on the core profile never saw the finish-setup offer. Its
first successful call now runs the same full read in the background, and the
offer reaches the handshake of its next session.

The refresh never touches the response: it runs as its own task after the
call returns and swallows every failure. A served checkout is settled for the
server process once a refresh succeeds or the last answer cannot change
(completed, dismissed, or already offering). A failed refresh is retried on a
later session's call, at most :data:`MAX_ATTEMPTS` times per process, so a
broken store cannot start a task on every call.

The answer is stored only if the person's dismissal still reads as the view
saw it: a dismissal, or its undo, made while the refresh ran is left for the
next full read instead of being overwritten by a stale answer.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Refreshes one checkout may start in a process before it gives up.
MAX_ATTEMPTS = 3

#: Checkouts whose refresh succeeded or was not needed: one set lookup per call.
_settled: set[str] = set()

#: Per checkout, refreshes started and the session that started the last one.
_attempts: dict[str, tuple[int, str]] = {}

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
    """Start the background refresh after a successful agent call, if needed.

    An agent's call is one that arrived over an MCP session; a direct call
    (tests, embedders) has none and is left alone. One attempt per session:
    the refreshed answer only reaches a later handshake anyway.
    """
    if not isinstance(result, dict) or result.get("error"):
        return
    from repowise.server.mcp_server import _state
    from repowise.server.mcp_server._savings import interaction

    live = interaction.current()
    if live is None or live.session_id is None:
        return
    root = _state.served_path()
    # Checked before any file read, so later calls pay a set and a dict lookup.
    if not root or root in _settled:
        return
    tried, last_session = _attempts.get(root, (0, ""))
    if tried >= MAX_ATTEMPTS or last_session == live.session_id:
        return
    _attempts[root] = (tried + 1, live.session_id)
    if not (Path(root) / ".repowise").is_dir() or not _needed(root):
        _settled.add(root)
        return
    # The task must not write into the finished call's interaction.
    context = contextvars.copy_context()
    context.run(interaction._CURRENT.set, None)
    task = asyncio.get_running_loop().create_task(_refresh(root), context=context)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _refresh(root: str) -> None:
    """Gather the setup facts and the actions view, and store the answer.

    Settles *root* after a write from every fact, or a confirmed no-op
    (another checkout, or a dismissal that changed meanwhile). A failure, or
    setup facts read only in part, leaves it to a later session's attempt.
    """
    try:
        settled = await _read_and_record(_key(root))
    except Exception:  # best-effort: the handshake keeps the last answer
        logger.debug("setup refresh failed", exc_info=True)
        return
    if settled:
        _settled.add(root)


async def _read_and_record(key: str) -> bool:
    from repowise.core.analysis.actions.setup import SETUP_STATE_ID
    from repowise.core.persistence.crud.analysis.actions import (
        get_action_states,
        load_actions_view,
    )
    from repowise.core.persistence.database import get_session
    from repowise.core.setup_state import read_setup_state, record_setup
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
        return True
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        repo_id = repository.id
        setup = await setup_facts(session, repository)
        if setup is None:
            # setup_facts logs and swallows a failed read: retry it later.
            raise RuntimeError("setup facts unavailable")
        view = await load_actions_view(
            session, repo_id, setup=setup, record=False, **_cross_repo_cut(ctx.alias)
        )
    block = view.get("setup")
    if block is None:
        raise RuntimeError("actions view carried no setup block")
    # A fresh session sees a dismissal (or its undo) committed while the view
    # was built; the "setup" action state is the source of truth for both.
    # The CLI writes setup.json right after its commit, so a file that moves
    # past the re-read closes the gap between that read and the write.
    last = read_setup_state(setup.root)
    async with get_session(ctx.session_factory) as session:
        answer = (await get_action_states(session, repo_id)).get(SETUP_STATE_ID)
    if (answer is not None and answer.state == "dismissed") != bool(block.get("dismissed")):
        return True
    if read_setup_state(setup.root) != last:
        return True
    record_setup(setup.root, block)
    # A fact that could not be read still yields an answer worth storing, but
    # a later session reads them all again.
    return not setup.unavailable


__all__ = ["MAX_ATTEMPTS", "schedule_setup_refresh"]
