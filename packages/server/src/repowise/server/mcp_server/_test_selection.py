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
from collections.abc import Sequence
from dataclasses import astuple, dataclass
from functools import partial
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.test_selection import Selection, selected_by_change
from repowise.server.mcp_server._budget import OmissionCollector

_log = structlog.get_logger(__name__)

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
    repo_path: Any, session_factory: Any, change: Any, repo: str | None = None
) -> tuple[dict[str, Any], Selection, Any]:
    """``(collection result, selection in run order, checkout)`` for *change*, or
    :class:`SelectionUnavailableError`.

    *repo* names the indexed repository (path, id or name) when the store
    holds several; omitted, the served one.

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
    done_key = await _done_key(key, session_factory, change, config, repo)
    if done_key is not None and done_key in _DONE:
        _DONE.move_to_end(done_key)
        return _DONE[done_key]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SELECTION_TIMEOUT_SECONDS
    flight = await _flight_for(
        key,
        change,
        deadline,
        lambda cancel: _select(repo_path, session_factory, change, config, cancel, repo),
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
    if done_key is not None and done_key == await _done_key(
        key, session_factory, change, config, repo
    ):
        _DONE[done_key] = selected
        while len(_DONE) > _DONE_CAPACITY:
            _DONE.popitem(last=False)
    return selected


async def _done_key(
    key: str, session_factory: Any, change: Any, config: Any, repo: str | None
) -> tuple | None:
    """The cache identity of this selection, or ``None`` when it cannot be cached."""
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo
    from repowise.server.mcp_server._index_state import index_state_key

    if session_factory is None:
        return None
    try:
        async with get_session(session_factory) as session:
            index = index_state_key(await _get_repo(session, repo))
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
            # .repowise is excluded: the server writes there on every call.
            [
                "git",
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=all",
                "--",
                ":(top)",
                ":(exclude).repowise",
            ],
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
        # The trailing empty entry would stat the root, whose mtime moves on any write in it.
        if not entry:
            continue
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
    repo_path: Any,
    session_factory: Any,
    change: Any,
    config: Any,
    cancel: threading.Event,
    repo: str | None,
) -> tuple:
    """Run the selection on a worker thread with its own event loop.

    The selection is mostly pure-Python CPU work, which on the server's loop
    would hold every other call's queries until it finished. A pooled
    engine's connections belong to the loop that opened them, so only an
    engine that opens one per checkout (``NullPool``, as every file SQLite
    store uses) moves off the loop.
    """
    if not _loop_agnostic(session_factory):
        return await _select_here(repo_path, session_factory, change, config, cancel, repo)
    return await asyncio.to_thread(
        asyncio.run, _select_here(repo_path, session_factory, change, config, cancel, repo)
    )


def _loop_agnostic(session_factory: Any) -> bool:
    from sqlalchemy.pool import NullPool

    bind = getattr(session_factory, "kw", {}).get("bind")
    return isinstance(getattr(getattr(bind, "sync_engine", None), "pool", None), NullPool)


async def _select_here(
    repo_path: Any,
    session_factory: Any,
    change: Any,
    config: Any,
    cancel: threading.Event,
    repo: str | None,
) -> tuple:
    from repowise.core.analysis.test_collection import read_checkout, select_for_change
    from repowise.core.analysis.test_ranking import ordered, rank_change
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo

    checkout = await asyncio.to_thread(read_checkout, repo_path, change.head)
    async with get_session(session_factory) as session:
        repository = await _get_repo(session, repo)
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
    direct: Sequence[str] | None = None,
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

    With *direct* (an agent's head, see :func:`agent_head`) the listed head
    leads with the selectors of tests that call a changed function themselves
    and never lists a test that runs with every subset. Without it the head is
    the run list itself, which the Review page and ``repowise risk`` turn into
    the command to run.
    """
    # In run order (``test_ranking``).
    run_list, broadcast, closure = split_apart(selection)
    for key, tests in zip(APART_KEYS, (broadcast, closure), strict=True):
        if tests:
            collector.add(f"{label}.{key.removesuffix('_total')}_tests ({len(tests)})", tests)
    if direct is None:
        shown = run_list[:limit]
        rest = run_list[limit:]
    else:
        shown, rest = agent_head(selection, run_list, direct, limit)
    known = set(selection.known_tests)
    held = len(known & set(selection.test_files))
    broad = held > len(known) * _DEGRADED_SHARE
    degraded = broad and not selection.run_all and bool(result.get("index_gap"))
    # Most of the suite under a verdict to run all of it: the tail restores nothing to act on.
    if rest and not (broad and (selection.run_all or degraded)):
        collector.add(f"{label}.tests_to_run not listed ({len(rest)})", rest)
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
        "truncated": bool(rest),
        "always_run_total": always,
        "summary": (
            f"the index predates files changed outside this change, so {held} of "
            f"{len(known)} test files were selected; run the whole suite, or "
            "`repowise update` to narrow it."
            if degraded
            else _summary(
                selection, len(run_list), always, len(shown), len(broadcast), len(closure)
            )
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


def of_the_change(selection: Selection, test: str) -> bool:
    """Whether a changed file put *test* in the selection, or the change edits
    it: either way it is a test of this change, whatever rule also runs it."""
    return selected_by_change(selection, test) or test.split("::", 1)[0] in selection.basis


def agent_head(
    selection: Selection, run_list: list[str], direct: Sequence[str], limit: int
) -> tuple[list[str], list[str]]:
    """``(listed head, run-list entries it leaves out)`` for an agent.

    *direct* selectors lead, then the tests the change reaches or edits; a file
    whose selector is listed is not listed again. Any other test that runs
    with every subset is never listed: nothing links it to this change, so
    listing it would read as a test of the changed code (``always_run_total`` counts them). The head
    takes no more characters than the first *limit* reached tests would, since
    a test id is longer than its file and the head must not grow the response.
    """
    reached = [t for t in run_list if of_the_change(selection, t)]
    budget = sum(len(t) for t in reached[:limit])
    head: list[str] = []
    files: set[str] = set()
    for test in dict.fromkeys([*direct, *reached]):
        path = test.split("::", 1)[0]
        if test == path and path in files:
            continue
        budget -= len(test)
        if len(head) == limit or (head and budget < 0):
            break
        head.append(test)
        files.add(path)
    listed = set(head)
    rest = [t for t in run_list if t not in listed and t not in files]
    return head, rest


NO_TESTS = "No test needs to run for this change."


def _summary(
    selection: Selection,
    total: int,
    always: int,
    listed: int,
    broadcast: int = 0,
    closure: int = 0,
) -> str:
    shown = f"; showing {listed}" if 0 < listed < total else ""
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
    rule = (
        f", {always} of them because they run with every subset (not listed)" if always else ""
    )
    if known and held == len(known):
        return f"{total} test(s) to run for this change, every test file there is{rule}{shown}."
    if spread:
        return f"{total} test(s) to run for this change{share}{rule}{shown}.{spread}"
    return f"{total} test(s) to run for this change{share}{rule}{shown}; the rest can be skipped."


def unselected_block(status: str, summary: str) -> dict[str, Any]:
    """The tests block where no selection was made: nothing vouches for a subset.

    It says ``run_all`` with the reason, like a selection that cannot vouch for
    one. ``basis`` is ``none`` and nothing is named, so there is no
    ``tests_to_run_kind`` or ``map_present``.
    """
    return {
        "status": status,
        "run_all": True,
        "reasons": [summary, UNAVAILABLE_REASON],
        "basis": "none",
        "tests_to_run": [],
        "total": 0,
        "truncated": False,
        "summary": summary,
    }


async def change_tests_block(
    repo_path: Any,
    session_factory: Any,
    revspec: str | None,
    collector: OmissionCollector,
    *,
    limit: int,
    label: str,
    working_tree: bool = False,
    owned_excludes: tuple[str, ...] = (),
    detail: bool = True,
    repo: str | None = None,
    agent: bool = False,
) -> tuple[dict[str, Any], tuple[dict[str, Any], Any] | None]:
    """The tests a scored change needs: ``get_change_risk``, ``repowise risk`` and
    the Review page all read them here.

    The change is read whole, every touched path included, so a manifest, a
    conftest or a test that walks the source tree weighs here as it does in CI;
    only *owned_excludes*, repowise's own output, leaves it. *working_tree* and
    *owned_excludes* come from the score
    (:func:`~repowise.core.analysis.change_risk.score_live_change`), so the
    tests are for the change it scored. *agent* shapes the listed head for an
    agent (:func:`agent_head`); the Review page and ``repowise risk`` keep the
    run list, which they turn into the command. Returns ``(block, (collection result,
    change))``, or ``(block, None)`` when no selection was made; never raises,
    so it cannot fail the score beside it.
    """
    from repowise.core.analysis.changed_lines import change_set

    if session_factory is None:
        return unselected_block(
            "no_index", "No index; run `repowise init` to enable impacted tests."
        ), None
    try:
        change = await asyncio.to_thread(
            partial(
                change_set,
                str(repo_path),
                None if working_tree else revspec or "HEAD",
                working_tree=working_tree,
                exclude_patterns=owned_excludes,
            )
        )
    except Exception:  # git refused the change, or could not run
        return unselected_block("unknown", "Could not read the change from git."), None
    try:
        result, selection, _ = await select_change_tests(repo_path, session_factory, change, repo)
    except Exception as exc:
        return degraded_block(exc), None
    direct = await direct_test_selectors(session_factory, change, selection, repo) if agent else None
    block = selection_block(
        result, selection, collector, limit=limit, label=label, detail=detail, direct=direct
    )
    return block, (result, change)


async def direct_test_selectors(
    session_factory: Any, change: Any, selection: Selection, repo: str | None = None
) -> list[str]:
    """Runnable selectors of the tests that call a function the change edits,
    one call away: the first per test file, then the rest, each in changed-file
    and line order. Never raises: an empty list only means no such link is stored.

    One-hop links from ``function_facts.tests_json`` were right on every
    hand-checked sample, where a file's test list mixes in import-only and
    rule-selected tests. A test file the selection does not run is left out.
    Changed lines are the change's new side, read against the indexed spans;
    a change far ahead of the index can miss a function that moved (the
    upgrade is mapping lines through the diff since the indexed commit).
    """
    from repowise.core.analysis.function_test_map import decode_tests
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo

    lines = {path: diff.new_lines for path, diff in change.files.items() if diff.new_lines}
    if not lines:
        return []
    try:
        async with get_session(session_factory) as session:
            repo_id = (await _get_repo(session, repo)).id
            links = await _edited_function_links(session, repo_id, lines)
    except Exception as exc:  # the selection beside it stands without these
        _log.debug("direct_test_selectors_failed", error=f"{type(exc).__name__}: {exc}")
        return []
    runs = set(selection.test_files)
    selectors = (
        t.selector
        for tests_json in links
        for t in decode_tests(tests_json) or ()
        if t.hops == 1 and (selection.run_all or t.selector.split("::", 1)[0] in runs)
    )
    return _file_first(list(dict.fromkeys(selectors)))


async def _edited_function_links(
    session: Any, repo_id: str, lines: dict[str, set[int]]
) -> list[str | None]:
    """The stored ``tests_json`` of each indexed symbol whose span holds a
    changed line, in file and line order."""
    from sqlalchemy import select

    from repowise.core.persistence.models import FunctionFact, WikiSymbol

    spans = await session.execute(
        select(WikiSymbol.file_path, WikiSymbol.start_line, WikiSymbol.end_line, WikiSymbol.symbol_id)
        .where(WikiSymbol.repository_id == repo_id, WikiSymbol.file_path.in_(sorted(lines)))
        .order_by(WikiSymbol.file_path, WikiSymbol.start_line)
    )
    edited = list(
        dict.fromkeys(
            symbol_id
            for path, start, end, symbol_id in spans.all()
            if any(start <= line <= end for line in lines[path])
        )
    )
    facts: dict[str, str | None] = {}
    # Batched to stay under SQLite's bound-parameter limit.
    for i in range(0, len(edited), 500):
        rows = await session.execute(
            select(FunctionFact.symbol_id, FunctionFact.tests_json).where(
                FunctionFact.repository_id == repo_id,
                FunctionFact.symbol_id.in_(edited[i : i + 500]),
            )
        )
        facts.update(rows.all())
    return [facts[sid] for sid in edited if sid in facts]


def _file_first(selectors: list[str]) -> list[str]:
    """One selector per test file first, so a short head spans the files that
    test the edit; then the rest, each in their own order."""
    seen: set[str] = set()
    lead: list[str] = []
    for test in selectors:
        path = test.split("::", 1)[0]
        if path not in seen:
            seen.add(path)
            lead.append(test)
    chosen = set(lead)
    return [*lead, *(t for t in selectors if t not in chosen)]


def degraded_block(exc: Exception) -> dict[str, Any]:
    """The tests block for a selection that raised *exc*."""
    from sqlalchemy.exc import SQLAlchemyError

    if isinstance(exc, SelectionUnavailableError):
        return unselected_block(exc.status, str(exc))
    if isinstance(exc, LookupError):
        return unselected_block("no_index", "No indexed repository; run `repowise init`.")
    if isinstance(exc, SQLAlchemyError):
        return unselected_block("unknown", "Could not read the index.")
    # A selection bug must not take the score with it.
    _log.warning("impacted_tests_failed", error=f"{type(exc).__name__}: {exc}")
    return unselected_block("unknown", f"Selecting tests failed ({type(exc).__name__}).")


#: Tests the Review page and ``repowise risk --format json`` list for a change;
#: ``total`` counts them all, and ``get_change_risk`` shows the first ten.
REVIEW_TESTS_LIMIT = 200


async def scored_change_tests(
    repo_path: Any, session_factory: Any, revspec: str | None, scored: Any, repo: str | None = None
) -> dict[str, Any]:
    """:func:`change_tests_block` for the change *scored* (a ``ChangeRiskResult``) holds.

    The Review page and ``repowise risk`` call this beside the score, so their
    tests are the ones ``get_change_risk`` names for the same change.
    """
    block, _ = await change_tests_block(
        repo_path,
        session_factory,
        revspec,
        OmissionCollector("get_change_risk", repo_root=repo_path),
        limit=REVIEW_TESTS_LIMIT,
        label="impacted_tests",
        working_tree=scored.working_tree,
        owned_excludes=scored.owned_excludes,
        repo=repo,
    )
    return block
