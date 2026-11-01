"""Serve a ``repo`` argument that names another checkout of an indexed repo.

Agents working in a git worktree pass their own path as ``repo``. The path
resolves to the indexed repo it belongs to (same git common dir, never a name
match). A worktree with its own ``.repowise/wiki.db`` is served from that
index; otherwise the indexed checkout answers and the response says so:
``_meta.served_checkout`` names it, and freshness compares its index with the
caller's HEAD.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.git_head import CheckoutMatch, read_head_commit, same_path


@dataclass(frozen=True)
class CallerCheckout:
    """The checkout a call named and the one whose index answers it."""

    caller: Path
    served: Path

    @property
    def own_index(self) -> bool:
        return same_path(self.caller, self.served)


class _Slot:
    value: CallerCheckout | None = None


# One slot per tool call, opened by the middleware. A mutable slot, so a
# resolution made in a nested task still reaches the envelope.
_SLOT: ContextVar[_Slot | None] = ContextVar("_repowise_caller_checkout", default=None)


def open_call() -> Any:
    """Start a call with no caller checkout; returns the token to reset."""
    return _SLOT.set(_Slot())


def close_call(token: Any) -> None:
    _SLOT.reset(token)


def current() -> CallerCheckout | None:
    slot = _SLOT.get()
    return slot.value if slot is not None else None


def _note(value: CallerCheckout | None) -> None:
    slot = _SLOT.get()
    if slot is not None:
        slot.value = value


def note_match(match: CheckoutMatch | None, indexed: Path) -> bool:
    """Record a resolved ``repo`` path; True when its own index should answer."""
    if match is None or match.same_checkout:
        _note(None)
        return False
    from repowise.core.workspace.registry import repo_db_path  # keeps import light

    own = repo_db_path(match.checkout).exists()
    _note(CallerCheckout(match.checkout, match.checkout if own else indexed))
    return own


# Worktree indexes opened on request. Each is a one-repo registry, so loading,
# vector-store readiness and disposal are the workspace code paths.
_OWN_MAX = 2
_own: OrderedDict[str, Any] = OrderedDict()


async def own_index_context(alias: str, root: Path) -> Any:
    """The context for a worktree's own index, opened once and kept (LRU)."""
    from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
    from repowise.core.workspace.registry import RepoRegistry
    from repowise.server.mcp_server._server import _query_embedder

    key = str(root)
    registry = _own.get(key)
    if registry is None:
        config = WorkspaceConfig(repos=[RepoEntry(path=".", alias=alias, is_primary=True)])
        registry = RepoRegistry(root, config, embedder_factory=_query_embedder)
        _own[key] = registry
        while len(_own) > _OWN_MAX:
            _retire(_own.popitem(last=False)[1])
    _own.move_to_end(key)
    return await registry.get(alias)


# A call may still hold an evicted context, so it closes after a grace period.
# Ceiling: a call running longer than this loses its engine; a lease count
# would remove the bound.
_RETIRE_AFTER_S = 600.0
_retiring: dict[asyncio.Task[None], Any] = {}


def _retire(registry: Any) -> None:
    async def _close_later() -> None:
        try:
            await asyncio.sleep(_RETIRE_AFTER_S)
        finally:
            await registry.close()  # on cancellation too

    task = asyncio.create_task(_close_later())
    _retiring[task] = registry
    task.add_done_callback(lambda done: _retiring.pop(done, None))


async def close_own_index_contexts() -> None:
    # A task cancelled before it started never ran its finally, so the registry
    # closes here as well; RepoRegistry.close is a no-op the second time.
    for task, registry in list(_retiring.items()):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await registry.close()
    _retiring.clear()
    while _own:
        await _own.popitem()[1].close()


def checkout_meta() -> dict[str, str]:
    """``served_checkout`` for a call that named another checkout, else nothing."""
    caller = current()
    return {"served_checkout": str(caller.served)} if caller is not None else {}


def borrowed_checkout(local_path: str | None) -> CallerCheckout | None:
    """The caller checkout when *local_path*'s index answers for it, else None."""
    caller = current()
    if caller is None or caller.own_index or not local_path:
        return None
    return caller if same_path(local_path, caller.served) else None


def checkout_fact(
    caller: CallerCheckout, indexed: str | None, caller_head: str | None, served_file: bool
) -> str:
    """The freshness line for an index read on behalf of another checkout."""
    at = f" at {indexed[:12]}" if indexed else ""
    head = f" at {caller_head[:12]}" if caller_head else ""
    if served_file:
        return (
            f"Facts below describe {caller.served}{at}; a file this response "
            f"serves differs in {caller.caller}{head}."
        )
    return f"Facts below describe {caller.served}{at}; {caller.caller} is{head}."


# ``HEAD`` or ``@`` alone, with a suffix (``HEAD~2``) or in a range
# (``main..HEAD``); not ``ORIG_HEAD``, ``origin/HEAD`` or ``HEAD@{1}``.
_HEAD = re.compile(r"(?<![\w/@-])(?:HEAD|@)(?![\w/@{-])")


def caller_revspec(revspec: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """*revspec* as the caller's checkout means it, or a refusal.

    Commits are shared between worktrees; ``HEAD``, the reflog and the
    working tree are not. ``HEAD`` becomes the caller's commit; omitted, the
    revspec is the caller's HEAD when its tree is clean. Anything that needs
    the caller's own state and cannot get it is refused, never answered for
    the served checkout.
    """
    caller = current()
    if caller is None or caller.own_index:
        return revspec, None
    head = read_head_commit(caller.caller)
    if revspec is not None and "@{" not in revspec:
        if head is not None:
            return _HEAD.sub(head, revspec), None
        if not _HEAD.search(revspec):
            return revspec, None
    elif revspec is None and head is not None:
        from repowise.server.mcp_server._meta import _working_tree_dirty_paths

        if _working_tree_dirty_paths(str(caller.caller)) == frozenset():
            return head, None
    from repowise.server.mcp_server._savings.interaction import note_error_reason

    note_error_reason("invalid_args")
    return None, {
        "error": (
            f"The working tree, HEAD and reflog of {caller.caller} are not "
            f"readable here: this server reads {caller.served}."
        ),
        "remedy": "Pass revspec as commits or a range of them; commits are shared between worktrees.",
    }
