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
import hashlib
import subprocess
import threading
from collections import OrderedDict
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Any

from repowise.core.analysis.test_selection import Selection, selected_by_change
from repowise.server.mcp_server._budget import OmissionCollector

#: Run-all reasons carried on the wire; the rest go to the omission store.
_REASONS_LIMIT = 3
#: Changed files whose deciding evidence is named.
_BASIS_LIMIT = 10
#: Share of the suite past which a selection made on a lagging index is
#: labelled ``degraded``: the files the index has not seen filled it, not the change.
_DEGRADED_SHARE = 0.5
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

#: Finished selections keyed on (repository, change, index state, config), so a
#: repeated get_change_risk or get_risk for the same change reuses one.
_DONE: OrderedDict[tuple, tuple] = OrderedDict()
_DONE_CAPACITY = 16


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
    done_key = await _done_key(key, session_factory, change, config)
    if done_key is not None and done_key in _DONE:
        _DONE.move_to_end(done_key)
        return _DONE[done_key]
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
    selected = flight.task.result()
    # A checkout edited while the selection read it is not cached under the old stamp.
    if done_key is not None and done_key == await _done_key(key, session_factory, change, config):
        _DONE[done_key] = selected
        while len(_DONE) > _DONE_CAPACITY:
            _DONE.popitem(last=False)
    return selected


async def _done_key(key: str, session_factory: Any, change: Any, config: Any) -> tuple | None:
    """The cache identity of this selection, or ``None`` when it cannot be cached."""
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo
    from repowise.server.mcp_server._index_state import index_state_key

    if session_factory is None:
        return None
    try:
        async with get_session(session_factory) as session:
            index = index_state_key(await _get_repo(session))
    except Exception:
        return None
    # A committed head pins what the selection reads; without one it reads the
    # live checkout, so the tree's state joins the key.
    tree = "" if change.head else await asyncio.to_thread(_tree_stamp, key)
    if tree is None:
        return None
    return (key, _change_identity(change), index, tree, repr(config))


def _change_identity(change: Any) -> str:
    """A digest of everything in *change*, order-independent where it is a set."""
    rows = [
        (path, sorted(diff.new_lines), *astuple(diff)[2:])
        for path, diff in sorted(change.files.items())
    ]
    raw = repr((change.base, change.head, change.label, sorted(change.deleted), rows))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _tree_stamp(root: str) -> str | None:
    """What the checkout's dirty paths are and when each last changed; ``None`` when git fails."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "-z", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    digest = hashlib.sha256(out)
    for entry in out.split(b"\0"):
        try:
            stat = (Path(root) / entry[3:].decode("utf-8", "replace")).stat()
        except (OSError, ValueError):
            continue
        digest.update(f"{stat.st_mtime_ns}:{stat.st_size}".encode())
    return digest.hexdigest()


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
    """Run the selection on a worker thread with its own event loop.

    The selection is mostly pure-Python CPU work, which on the server's loop
    would hold every other call's queries until it finished. A pooled
    engine's connections belong to the loop that opened them, so only an
    engine that opens one per checkout (``NullPool``, as every file SQLite
    store uses) moves off the loop.
    """
    if not _loop_agnostic(session_factory):
        return await _select_here(repo_path, session_factory, change, config, cancel)
    return await asyncio.to_thread(
        asyncio.run, _select_here(repo_path, session_factory, change, config, cancel)
    )


def _loop_agnostic(session_factory: Any) -> bool:
    from sqlalchemy.pool import NullPool

    bind = getattr(session_factory, "kw", {}).get("bind")
    return isinstance(getattr(getattr(bind, "sync_engine", None), "pool", None), NullPool)


async def _select_here(
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


def split_apart(selection: Selection) -> tuple[list[str], list[str], list[str]]:
    """``(the tests the change reaches, the broadcast ones, the import-closure ones)``,
    each in run order. All of them run; the last two are counted apart.

    A full run counts nothing apart: every test runs for the run-all reasons.
    """
    if selection.run_all:
        return list(selection.tests), [], []
    spread = [t for t in selection.tests if t.split("::", 1)[0] in selection.broadcast]
    closure = [t for t in selection.tests if t.split("::", 1)[0] in selection.import_closure]
    return selection.reached_tests(), spread, closure


def apart_sentence(broadcast: int, closure: int, keys: tuple[str, str]) -> str:
    """The tests counted apart from the headline, which must still run; ``""`` for none."""
    parts = []
    if broadcast:
        parts.append(
            f"{broadcast} more test(s) must still run because a conftest on a route keeps "
            f"every test under it ({keys[0]})."
        )
    if closure:
        parts.append(
            f"{closure} more test(s) must still run because they import the change through "
            f"other files, with no call path to it ({keys[1]})."
        )
    return "".join(f" {p}" for p in parts)


#: The counts of selected tests kept out of ``tests_to_run`` (they still run).
APART_KEYS = ("broadcast_total", "import_closure_total")


def selection_block(
    result: dict[str, Any],
    selection: Selection,
    collector: OmissionCollector,
    *,
    limit: int,
    label: str,
    detail: bool = True,
) -> dict[str, Any]:
    """The wire view of *selection*: verdict, reasons, the run list's head and why.

    ``tests_to_run`` empty with ``run_all`` false means the change needs no
    test (documentation, a deleted test); with ``run_all`` true it means nothing
    narrower than the full suite is known. ``status`` is ``degraded`` when the
    index predates files outside the change and the selection holds over half
    the suite: the list stands and ``summary`` says why. The tail of a list
    holding most of the suite is not stored when every test is to run anyway.
    ``total`` and ``tests_to_run`` leave out the tests counted apart
    (:func:`split_apart`), counted in ``broadcast_total`` and
    ``import_closure_total`` when there are any and stored for recovery.
    *detail* adds ``why`` and ``basis_by_file``.
    """
    # In run order (``test_ranking``).
    run_list, broadcast, closure = split_apart(selection)
    for key, tests in zip(APART_KEYS, (broadcast, closure), strict=True):
        if tests:
            collector.add(f"{label}.{key.removesuffix('_total')}_tests ({len(tests)})", tests)
    shown = run_list[:limit]
    known = set(selection.known_tests)
    held = len(known & set(selection.test_files))
    broad = held > len(known) * _DEGRADED_SHARE
    degraded = broad and not selection.run_all and bool(result.get("index_gap"))
    # Most of the suite under a verdict to run all of it: the tail restores nothing to act on.
    if len(run_list) > limit and not (broad and (selection.run_all or degraded)):
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
    status = "run_all" if selection.run_all else "degraded" if degraded else "selected"
    block: dict[str, Any] = {
        "status": status,
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
        "summary": (
            f"the index predates files changed outside this change, so {held} of "
            f"{len(known)} test files were selected; run the whole suite, or "
            "`repowise update` to narrow it."
            if degraded
            else _summary(selection, len(run_list), always, limit, len(broadcast), len(closure))
        ),
    }
    for key, tests in zip(APART_KEYS, (broadcast, closure), strict=True):
        if tests:
            block[key] = len(tests)
    if not detail:
        return block
    block["why"] = {
        path: selection.why[path]
        for path in dict.fromkeys(t.split("::", 1)[0] for t in shown)
        if path in selection.why
    }
    block["basis_by_file"] = dict(basis_rows[:_BASIS_LIMIT])
    if len(basis_rows) > _BASIS_LIMIT:
        block["basis_by_file_total"] = len(basis_rows)
        collector.add(
            f"{label}.basis_by_file beyond cap={_BASIS_LIMIT} "
            f"({len(basis_rows) - _BASIS_LIMIT} dropped)",
            dict(basis_rows[_BASIS_LIMIT:]),
        )
    return block


NO_TESTS = "No test needs to run for this change."


def _summary(
    selection: Selection,
    total: int,
    always: int,
    limit: int,
    broadcast: int = 0,
    closure: int = 0,
) -> str:
    shown = f"; showing first {limit}" if total > limit else ""
    if selection.run_all:
        lead = selection.reasons[0] if selection.reasons else "the selection could not vouch"
        named = f" Run these {total} first{shown}." if total else ""
        return f"Run every test: {lead}{named}"
    spread = apart_sentence(broadcast, closure, APART_KEYS)
    if not total:
        return f"No test the change reaches directly.{spread}" if spread else NO_TESTS
    # The share of the suite, so a selection holding most of it never reads as targeted.
    known = set(selection.known_tests)
    held = len(known & {f for f in selection.test_files if not selection.counted_apart(f)})
    share = f" ({held} of {len(known)} test files)" if known else ""
    rule = f", {always} of them because they run with every subset" if always else ""
    if known and held == len(known):
        return f"{total} test(s) to run for this change, every test file there is{rule}{shown}."
    if spread:
        return f"{total} test(s) to run for this change{share}{rule}{shown}.{spread}"
    return f"{total} test(s) to run for this change{share}{rule}{shown}; the rest can be skipped."
