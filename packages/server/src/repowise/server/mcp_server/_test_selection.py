"""The tests a change needs, for the agent tools: one selection, rendered small.

``get_change_risk`` and ``get_risk`` ask the same question ``repowise
impacted-tests`` answers, so they run the same collection and the same
fail-closed :func:`~repowise.core.analysis.test_selection.select_tests`
(:mod:`repowise.core.analysis.test_collection`) and the same order
(:mod:`repowise.core.analysis.test_ranking`), and only shape the answer:
whether every test must run and why, the head of the run list with the reason
each test is in it, and which evidence decided each changed file.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core.analysis.test_selection import Selection, selected_by_change
from repowise.server.mcp_server._budget import OmissionCollector

#: Run-all reasons carried on the wire; the rest go to the omission store.
_REASONS_LIMIT = 3
#: Changed files whose deciding evidence is named.
_BASIS_LIMIT = 10
#: Past this the agent is told to run the CLI instead of waiting. Ceiling: the
#: call walk is per-level SQL; loading call edges once is the upgrade.
SELECTION_TIMEOUT_SECONDS = 30.0

#: What a failed selection tells the reader: it vouches for no subset.
UNAVAILABLE_REASON = (
    "The test selection is unavailable, so run every test (or `repowise impacted-tests`)."
)


@dataclass
class _Flight:
    """A selection still running in a repository, and how many calls await it."""

    change: Any
    task: asyncio.Future
    cancel: threading.Event
    waiters: int = 0


#: Repository path -> the selection still running there. One at a time per
#: repository: a selection past its budget keeps its threads until they notice
#: the cancel flag, and a second one would only add to them.
_IN_FLIGHT: dict[str, _Flight] = {}


class SelectionUnavailableError(Exception):
    """No selection could be made; ``status`` and the message say why."""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


async def select_change_tests(
    repo_path: Any, session_factory: Any, change: Any
) -> tuple[dict[str, Any], Selection, Any]:
    """``(collection result, selection in run order, checkout)`` for *change*, or
    :class:`SelectionUnavailableError`.

    The checkout read (``git ls-files`` plus pytest's conftests and configs)
    and every git call run off the event loop. A call for the change already
    running shares that selection; a call for another change waits for it to
    end, within the same budget, then runs its own.
    """
    from repowise.core.analysis.test_selection import TestSelectionConfig
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        config = TestSelectionConfig.from_repo_config(load_repo_config(repo_path))
    except (RepoConfigError, ValueError) as exc:
        raise SelectionUnavailableError("config_invalid", f"tests config is invalid: {exc}") from exc
    key = str(Path(repo_path).resolve())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SELECTION_TIMEOUT_SECONDS
    flight = await _flight_for(
        key,
        change,
        deadline,
        lambda cancel: _select(repo_path, session_factory, change, config, cancel),
    )
    # asyncio.wait never cancels the task: one caller leaving keeps it for the rest.
    flight.waiters += 1
    try:
        done, _ = await asyncio.wait({flight.task}, timeout=max(deadline - loop.time(), 0))
    finally:
        flight.waiters -= 1
        if not flight.waiters and not flight.task.done():
            flight.cancel.set()
    if not done:
        raise SelectionUnavailableError(
            "timeout",
            f"Selecting tests took over {SELECTION_TIMEOUT_SECONDS:.0f} s; run "
            "`repowise impacted-tests` for this change.",
        )
    return flight.task.result()


async def _flight_for(key: str, change: Any, deadline: float, start: Any) -> _Flight:
    """The selection running for *change*, or one *start* begins once any other ends."""
    loop = asyncio.get_running_loop()
    flight = _running(key)
    # Another change, or one every caller has left, is waited out, not joined.
    while flight is not None and (flight.change != change or flight.cancel.is_set()):
        done, _ = await asyncio.wait({flight.task}, timeout=deadline - loop.time())
        if not done:
            which = "another change" if flight.change != change else "this change, still stopping,"
            raise SelectionUnavailableError(
                "busy",
                f"A test selection for {which} in this repository ran past "
                f"this call's {SELECTION_TIMEOUT_SECONDS:.0f} s budget; run "
                "`repowise impacted-tests` for this change.",
            )
        flight = _running(key)
    if flight is None:
        cancel = threading.Event()
        task = asyncio.ensure_future(start(cancel))
        flight = _IN_FLIGHT[key] = _Flight(change, task, cancel)
        # Released when the selection ends, not when the callers stop waiting.
        task.add_done_callback(lambda _done, ended=flight: _release(key, ended))
    return flight


def _running(key: str) -> _Flight | None:
    flight = _IN_FLIGHT.get(key)
    return None if flight is None or flight.task.done() else flight


def _release(key: str, flight: _Flight) -> None:
    if _IN_FLIGHT.get(key) is flight:
        del _IN_FLIGHT[key]
    # An abandoned selection ends in SelectionCancelledError; nobody reads it.
    if not flight.task.cancelled():
        flight.task.exception()


async def _select(
    repo_path: Any, session_factory: Any, change: Any, config: Any, cancel: threading.Event
) -> tuple:
    from repowise.core.analysis.test_collection import read_checkout, select_for_change
    from repowise.core.analysis.test_ranking import ordered, rank_change
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo

    checkout = await asyncio.to_thread(read_checkout, repo_path, change.head)
    async with get_session(session_factory) as session:
        repository = await _get_repo(session)
        result, selection = await select_for_change(
            session,
            repository.id,
            repo_path,
            change,
            config,
            checkout,
            indexed_commit=repository.head_commit,
            cancelled=cancel.is_set,
        )
        ranked = await rank_change(
            session, repository.id, change, result, selection, checkout.read
        )
    return result, ordered(selection, ranked), checkout


def files_change(repo_path: Any, paths: list[str]) -> Any:
    """A change known only by its paths: no lines, based at ``HEAD``.

    A path the checkout lacks reads as deleted. With no lines every file is
    matched by file, so coverage at any commit names the tests touching it.
    """
    from repowise.core import git_refs
    from repowise.core.analysis.changed_lines import ChangeSet, FileDiff

    root = Path(repo_path)
    paths = [p.replace("\\", "/").removeprefix("./") for p in paths]
    present = {p for p in paths if (root / p).is_file()}
    return ChangeSet(
        files={p: FileDiff(path=p) for p in sorted(present)},
        deleted=set(paths) - present,
        label="the changed files",
        base=git_refs.resolve(str(root), "HEAD") or None,
        head=None,
    )


def run_kind(tests: list[str]) -> str | None:
    """``test_id`` when a coverage node id is listed, ``test_file`` otherwise, ``None`` empty."""
    if not tests:
        return None
    return "test_id" if any("::" in t for t in tests) else "test_file"


def basis_of(selection: Selection) -> str:
    """``measured`` when coverage decided any changed file, ``inferred`` when only the
    graph or a rule did, ``none`` when nothing named a test."""
    if not selection.tests:
        return "none"
    return "measured" if "coverage" in selection.basis.values() else "inferred"


def selection_block(
    result: dict[str, Any],
    selection: Selection,
    collector: OmissionCollector,
    *,
    limit: int,
    label: str,
) -> dict[str, Any]:
    """The wire view of *selection*: verdict, reasons, the run list's head and why.

    ``tests_to_run`` empty with ``run_all`` false means the change needs no
    test (documentation, a deleted test); with ``run_all`` true it means nothing
    narrower than the full suite is known.
    """
    run_list = list(selection.tests)  # in run order (``test_ranking``)
    shown = run_list[:limit]
    if len(run_list) > limit:
        collector.add(
            f"{label}.tests_to_run beyond cap={limit} ({len(run_list) - limit} dropped)",
            run_list[limit:],
        )
    reasons = list(selection.reasons)
    if len(reasons) > _REASONS_LIMIT:
        collector.add(
            f"{label}.reasons beyond cap={_REASONS_LIMIT} "
            f"({len(reasons) - _REASONS_LIMIT} dropped)",
            reasons[_REASONS_LIMIT:],
        )
    basis_rows = sorted(selection.basis.items())
    always = sum(not selected_by_change(selection, t) for t in selection.test_files)
    block: dict[str, Any] = {
        "status": "run_all" if selection.run_all else "selected",
        "run_all": selection.run_all,
        "reasons": reasons[:_REASONS_LIMIT],
        "reasons_total": len(reasons),
        "basis": basis_of(selection),
        "map_present": not result.get("map_empty", True),
        "tests_to_run": shown,
        "tests_to_run_kind": run_kind(shown),
        "total": len(run_list),
        "truncated": len(run_list) > limit,
        "always_run_total": always,
        "why": {
            path: selection.why[path]
            for path in dict.fromkeys(t.split("::", 1)[0] for t in shown)
            if path in selection.why
        },
        "basis_by_file": dict(basis_rows[:_BASIS_LIMIT]),
        "summary": _summary(selection, len(run_list), always, limit),
    }
    if len(basis_rows) > _BASIS_LIMIT:
        block["basis_by_file_total"] = len(basis_rows)
        collector.add(
            f"{label}.basis_by_file beyond cap={_BASIS_LIMIT} "
            f"({len(basis_rows) - _BASIS_LIMIT} dropped)",
            dict(basis_rows[_BASIS_LIMIT:]),
        )
    return block


def _summary(selection: Selection, total: int, always: int, limit: int) -> str:
    shown = f"; showing first {limit}" if total > limit else ""
    if selection.run_all:
        lead = selection.reasons[0] if selection.reasons else "the selection could not vouch"
        named = f" Run these {total} first{shown}." if total else ""
        return f"Run every test: {lead}{named}"
    if not total:
        return "No test needs to run for this change."
    # The share of the suite, so a selection holding most of it never reads as targeted.
    known = set(selection.known_tests)
    held = len(known & set(selection.test_files))
    share = f" ({held} of {len(known)} test files)" if known else ""
    rule = f", {always} of them because they run with every subset" if always else ""
    if known and held == len(known):
        return f"{total} test(s) to run for this change, every test file there is{rule}{shown}."
    return f"{total} test(s) to run for this change{share}{rule}{shown}; the rest can be skipped."
