"""Workspace fan-out: one per-repo call run across every repo, or a chosen few.

Every tool that answers across repos (``repo="all"``, and targets routed to
several repos) goes through :class:`Federation`, so concurrency, timeouts,
partial results and freshness mean the same thing in each of them. A repo
that cannot be opened, raises or runs past its timeout is named in
``repos_failed`` and the rest still answer; a repo whose index is behind its
checkout is named in ``repos_stale`` and its rows carry ``stale: true``. Both
keys are added only when non-empty, so a healthy workspace gets the response
shape it always had.

The merge rules live here too, one per kind of result: ranked lists
(:func:`merge_ranked`), findings (:func:`merge_findings`) and aggregates
(:func:`sum_fields`).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable, Iterable, Sequence
from typing import Any, TypeVar

from repowise.core.persistence.database import get_session
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
from repowise.server.mcp_server._meta import freshness_from_repo, served_root

_log = logging.getLogger("repowise.mcp")

T = TypeVar("T")

_CONCURRENCY_ENV = "REPOWISE_WORKSPACE_CONCURRENCY"
# Kept below the registry's open-repo cap (5 plus the default) so the repos in
# flight fit in it together.
_CONCURRENCY_DEFAULT = 4
_TIMEOUT_ENV = "REPOWISE_WORKSPACE_REPO_TIMEOUT_S"
# A cold repo pays its vector-store open (bounded at 30s) before searching.
_TIMEOUT_DEFAULT_S = 60.0
_TIMEOUT_MAX_S = 300.0
_REASON_MAX_CHARS = 200


def _env_number(name: str, default: float, ceiling: float) -> float:
    """A positive number from the environment, else *default* (with a warning)."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not value > 0:
        _log.warning("Ignoring unusable %s=%r", name, raw)
        return default
    return min(value, ceiling)


def fanout_concurrency() -> int:
    """Repos queried at once, from env or the default, never above the open-repo cap."""
    limit = int(_env_number(_CONCURRENCY_ENV, _CONCURRENCY_DEFAULT, 64)) or 1
    cap = getattr(_state._registry, "MAX_LOADED", None)
    return min(limit, cap) if isinstance(cap, int) and cap > 0 else limit


def fanout_timeout_s() -> float:
    """Seconds one repo's share of a call may take, from env or the default."""
    return _env_number(_TIMEOUT_ENV, _TIMEOUT_DEFAULT_S, _TIMEOUT_MAX_S)


async def _open(alias: str | None) -> Any:
    registry = _state._registry
    if registry is None:
        return await _resolve_repo_context(None)
    return await registry.get(alias)


def _stale_entry(alias: str, repository: Any, root: str | None) -> dict[str, Any] | None:
    """The repo-level freshness verdict every single-repo response already gives.

    Stale means ``_meta`` would warn for a repo-level answer: HEAD moved past the
    indexed commit with real file changes, or the index is very old with no git.
    """
    fresh = freshness_from_repo(repository, targets=None, root=root)
    if "stale_warning" not in fresh:
        return None
    entry: dict[str, Any] = {"repo": alias}
    for key in ("indexed_commit", "live_head", "index_age_days"):
        if fresh.get(key) is not None:
            entry[key] = fresh[key]
    return entry


class Federation:
    """One ``repo="all"`` call: run per-repo work, remember who failed or is stale.

    :meth:`run` may be called more than once in a call (a tool with several
    stages); a repo that failed an earlier stage is skipped by later ones.

    Ceiling: contexts come from the registry's LRU, so a workspace larger than
    its cap still reopens repos on every call and can evict one mid-call (that
    repo then lands in ``repos_failed`` rather than failing the call). Short-lived
    read-only connections for fan-out are the upgrade path.
    """

    def __init__(
        self,
        *,
        aliases: Sequence[str] | None = None,
        concurrency: int | None = None,
        timeout_s: float | None = None,
        check_freshness: bool = True,
    ):
        """*aliases* limits the call to those repos (default: all of them);
        ``check_freshness=False`` skips ``repos_stale`` for answers that carry
        their own freshness."""
        registry = _state._registry
        if aliases is not None:
            self._aliases: list[str | None] = list(aliases)
        else:
            self._aliases = registry.get_all_aliases() if registry else [None]
        self._check = check_freshness
        self._semaphore = asyncio.Semaphore(concurrency or fanout_concurrency())
        self._timeout_s = timeout_s or fanout_timeout_s()
        self._failed: dict[str | None, str] = {}
        self._stale: dict[str, dict[str, Any]] = {}
        self._checked: set[str] = set()

    @property
    def multi(self) -> bool:
        """More than one repo configured: rows are tagged by repo even if one failed."""
        return len(self._aliases) > 1

    async def run(self, fn: Callable[[Any], Awaitable[T]]) -> list[tuple[Any, T]]:
        """``(context, fn(context))`` for every repo that answered, in workspace order."""
        live = [alias for alias in self._aliases if alias not in self._failed]
        outcomes = await asyncio.gather(*(self._one(alias, fn) for alias in live))
        return [outcome for outcome in outcomes if outcome is not None]

    async def _one(self, alias: str | None, fn: Callable[[Any], Awaitable[T]]) -> tuple[Any, T] | None:
        async with self._semaphore:
            try:
                return await asyncio.wait_for(self._call(alias, fn), self._timeout_s)
            except TimeoutError:
                reason = f"timed out after {self._timeout_s:g}s"
            except Exception as exc:
                _log.warning("repo=all: repo %r failed", alias, exc_info=True)
                reason = f"{type(exc).__name__}: {exc}"[:_REASON_MAX_CHARS]
            self._failed[alias] = reason
            return None

    async def _call(self, alias: str | None, fn: Callable[[Any], Awaitable[T]]) -> tuple[Any, T]:
        ctx = await _open(alias)
        if self._check and ctx.alias not in self._checked:
            self._checked.add(ctx.alias)
            await self._check_freshness(ctx)
        return ctx, await fn(ctx)

    async def _check_freshness(self, ctx: Any) -> None:
        # A freshness probe that fails says nothing about the repo's answer.
        try:
            async with get_session(ctx.session_factory) as session:
                repository = await _get_repo(session)
            root = served_root(repository, ctx.path)
            # May spawn git (ancestry, changed files), so off the event loop.
            entry = await asyncio.to_thread(_stale_entry, ctx.alias, repository, root)
        except Exception:
            _log.debug("repo=all: freshness check failed for %r", ctx.alias, exc_info=True)
            return
        if entry is not None:
            self._stale[ctx.alias] = entry

    def annotate(
        self, response: dict[str, Any], rows: Iterable[Any] = (), *, repo_key: str = "repo"
    ) -> dict[str, Any]:
        """Add ``repos_failed`` / ``repos_stale`` when non-empty; mark stale repos' rows."""
        if self._stale:
            for row in rows:
                if isinstance(row, dict) and row.get(repo_key) in self._stale:
                    row["stale"] = True
        if self._failed:
            response["repos_failed"] = [
                {"repo": alias or "default", "reason": reason}
                for alias in self._aliases
                if (reason := self._failed.get(alias)) is not None
            ]
        if self._stale:
            response["repos_stale"] = [
                self._stale[alias] for alias in self._aliases if alias in self._stale
            ]
        return response


# ---------------------------------------------------------------------------
# Merge rules
# ---------------------------------------------------------------------------


def merge_ranked(
    groups: Iterable[Sequence[T]],
    key: Callable[[T], Any],
    *,
    limit: int | None = None,
    per_repo_cap: int | None = None,
) -> list[T]:
    """Ranked lists: one order across repos by ascending *key*.

    Each group is one repo's list in its own best-first order, given in
    workspace order. The sort is stable, so equal keys fall back to workspace
    order and then to each repo's own order: the same inputs always merge the
    same way. *per_repo_cap* keeps one repo from filling the window.
    """
    pooled: list[T] = []
    for group in groups:
        pooled.extend(group[:per_repo_cap] if per_repo_cap is not None else group)
    pooled.sort(key=key)
    return pooled[:limit] if limit is not None else pooled


def merge_findings(groups: Iterable[tuple[str, Iterable[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Findings: concatenated in workspace order, each tagged with its ``repo``.

    Ranking is left to the caller, whose scores (confidence, severity) already
    mean the same thing in every repo.
    """
    merged: list[dict[str, Any]] = []
    for alias, rows in groups:
        for row in rows:
            row["repo"] = alias
            merged.append(row)
    return merged


def sum_fields(rows: Iterable[dict[str, Any]], fields: Sequence[str]) -> dict[str, int]:
    """Aggregates: per-field totals over the repos that answered.

    The basis is the rows summed; a repo in ``repos_failed`` is not in it.
    """
    totals = dict.fromkeys(fields, 0)
    for row in rows:
        for field in fields:
            totals[field] += row.get(field) or 0
    return totals
