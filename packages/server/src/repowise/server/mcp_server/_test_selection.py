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
from pathlib import Path
from typing import Any

from repowise.core.analysis.test_selection import Selection, selected_by_change
from repowise.server.mcp_server._budget import OmissionCollector

#: Run-all reasons carried on the wire; the rest go to the omission store.
_REASONS_LIMIT = 3
#: Changed files whose deciding evidence is named.
_BASIS_LIMIT = 10
#: Past this the agent is told to run the CLI instead of waiting. Ceiling: a
#: change whose triggers scope a whole workspace walks every route to it (two
#: minutes on a 2k-test repository); loading the graph once per call is the
#: upgrade that lifts it.
SELECTION_TIMEOUT_SECONDS = 30.0

#: What a failed selection tells the reader: it vouches for no subset.
UNAVAILABLE_REASON = (
    "The test selection is unavailable, so run every test (or `repowise impacted-tests`)."
)

#: Repository path -> the cancel flag of a selection still running there. One
#: at a time per repository: a selection past its budget keeps its threads
#: until they notice the flag, and a second one would only add to them.
_IN_FLIGHT: dict[str, threading.Event] = {}


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
    and every git call run off the event loop.
    """
    from repowise.core.analysis.test_selection import TestSelectionConfig
    from repowise.core.repo_config import RepoConfigError, load_repo_config

    try:
        config = TestSelectionConfig.from_repo_config(load_repo_config(repo_path))
    except (RepoConfigError, ValueError) as exc:
        raise SelectionUnavailableError("config_invalid", f"tests config is invalid: {exc}") from exc
    key = str(Path(repo_path).resolve())
    if key in _IN_FLIGHT:
        raise SelectionUnavailableError(
            "busy",
            "A test selection for this repository is still running; run "
            "`repowise impacted-tests` for this change.",
        )
    cancel = _IN_FLIGHT[key] = threading.Event()
    task = asyncio.ensure_future(_select(repo_path, session_factory, change, config, cancel))
    # Released when the selection ends, not when the caller stops waiting.
    task.add_done_callback(lambda done: _release(key, done))
    try:
        done, _ = await asyncio.wait({task}, timeout=SELECTION_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        cancel.set()
        raise
    if not done:
        cancel.set()
        raise SelectionUnavailableError(
            "timeout",
            f"Selecting tests took over {SELECTION_TIMEOUT_SECONDS:.0f} s; run "
            "`repowise impacted-tests` for this change.",
        )
    return task.result()


def _release(key: str, task: asyncio.Future) -> None:
    _IN_FLIGHT.pop(key, None)
    # An abandoned selection ends in SelectionCancelledError; nobody reads it.
    if not task.cancelled():
        task.exception()


async def _select(
    repo_path: Any, session_factory: Any, change: Any, config: Any, cancel: threading.Event
) -> tuple:
    from repowise.core.analysis.test_collection import read_checkout, select_for_change
    from repowise.core.analysis.test_ranking import ordered, rank_change
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo

    checkout = await asyncio.to_thread(read_checkout, repo_path)
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
    rule = f", {always} of them because they run with every subset" if always else ""
    return f"{total} test(s) to run for this change{rule}{shown}; the rest can be skipped."
