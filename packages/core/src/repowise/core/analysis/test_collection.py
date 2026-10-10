"""Collect the evidence :func:`~repowise.core.analysis.test_selection.select_tests` decides from.

One pipeline for every surface that names the tests a change needs: map each
changed file to the tests whose recorded coverage touches its changed lines and
to the tests the dependency graph shows reaching it, note what the index cannot
see, then hand it all to :func:`select_tests` (:func:`select`).

The inputs are plain data a server can build from what it holds: a
:class:`Checkout` (tracked paths, pytest's conftests and configs, a text
reader) and a change set. Git is read only for the files changed since the
index was built.

Ceiling: two reads still go through the repository path, the commit
``state.json`` records (:func:`_indexed_commit`) and the git diff between the
indexed commit and the change's base (:func:`_gap_routes`). A server without a
checkout passes *indexed_commit* and gets "cannot tell what changed since the
index was built" for the gap, which runs everything; taking the state commit
and the gap as inputs is the upgrade.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import structlog

from ..pytest_roots import PytestRoots
from .test_selection import Selection, TestSelectionConfig

log = structlog.get_logger(__name__)

# The whole reverse-import closure: a test importing a module that imports the
# changed file runs it too. No depth limit: the walk ends when no node gains a
# new seed, so a cut-off can never drop a test.
_IMPORT_CLOSURE_DEPTH = sys.maxsize


@dataclass(frozen=True)
class Checkout:
    """Tracked paths and the pytest files among them, each read once per selection.

    *read* returns a tracked file's text (``None`` when unreadable) and
    *exists* whether a path is present; :func:`read_checkout` answers both from
    disk, a server may answer them from what it stores.
    """

    tracked: list[str]
    pytest_texts: list[tuple[str, str]]
    roots: PytestRoots
    read: Callable[[str], str | None]
    exists: Callable[[str], bool]


def read_checkout(repo_path) -> Checkout:
    """Tracked files, their conftests and pytest configs, and pytest's collection roots."""
    from .. import git_refs
    from ..pytest_roots import PYTEST_CONFIG_NAMES, read_pytest_roots
    from .test_selection import is_code_file, is_scan_source

    root = Path(repo_path)

    def read(path: str) -> str | None:
        try:
            return (root / path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None

    tracked = sorted(git_refs.tracked_paths(str(root)))
    texts = [
        (p, text)
        for p in tracked
        if is_scan_source(p) and (p.endswith("conftest.py") or not is_code_file(p))
        if (text := read(p)) is not None
    ]
    roots = read_pytest_roots((p, t) for p, t in texts if Path(p).name in PYTEST_CONFIG_NAMES)
    return Checkout(tracked, texts, roots, read, lambda p: (root / p).is_file())


class SelectionCancelledError(Exception):
    """The caller gave up on this selection; whatever it had found is discarded."""


def _never() -> bool:
    return False


def _stop(cancelled: Callable[[], bool]) -> bool:
    if cancelled():
        raise SelectionCancelledError
    return False


class Plan(NamedTuple):
    """Who names each changed doc or asset, the scope each scoped file runs, and its routes."""

    namers: dict[str, list[str]]
    scopes: dict
    routes: list[str]


def plan_scopes(
    change, config, checkout: Checkout, cancelled: Callable[[], bool] = _never
) -> Plan:
    """The change's scopes, reading the sources once and only when a file needs its namers.

    *cancelled* is polled between file reads, so an abandoned selection stops
    reading the checkout.
    """
    from .selection_scopes import (
        keeps_full_run,
        needs_namers,
        trigger_scopes,
    )
    from .test_selection import file_namers, is_scan_source

    paths = [*change.files, *change.deleted]
    # A file that runs everything anyway makes every namer search moot.
    blocked = any(keeps_full_run(p, config) for p in paths)
    asked = [] if blocked else [p for p in paths if needs_namers(p, config)]
    namers: dict[str, list[str]] = {}
    if asked:
        known = dict(checkout.pytest_texts)  # conftests and pytest configs, read already
        texts = (
            (p, known[p]) if p in known else (p, checkout.read(p) or "")
            for p in checkout.tracked
            if is_scan_source(p) and not _stop(cancelled)
        )
        namers = file_namers(asked, texts)
    scopes = trigger_scopes(paths, checkout.tracked, namers, config)
    routes = sorted({r for scope in scopes.values() for r in scope.routes} - set(paths))
    return Plan(namers, scopes, routes)


async def collect(
    session,
    repo_id: str,
    repo_path,
    change,
    roots: PytestRoots | None = None,
    config: TestSelectionConfig | None = None,
    explain: str | None = None,
    scope_routes: list[str] | tuple[str, ...] = (),
    pytest_texts: dict | None = None,
    *,
    indexed_commit: str | None = None,
) -> dict:
    """Resolve the change's files to impacted tests + labelled fallbacks.

    *indexed_commit* is the index's repository row commit. With a selection
    *config*, files changed since the index was built are walked too, so
    selection can add the tests reaching them; so are *scope_routes*, the
    files a scope runs the tests of (:func:`plan_scopes`). With *explain*, the
    import route from that test to a changed file is looked up as well.
    *pytest_texts* are the checkout's conftests and pytest configs by path;
    without them no conftest route is narrowed.
    """
    from ..persistence.crud import get_health_metrics, get_test_coverage_summary
    from ..persistence.crud.analysis.coverage_map import MAX_TEST_COVERAGE_ROWS

    out = empty_result(len(change.files) + len(change.deleted))
    if not out["changed_files"]:
        return out
    _indexed_commit(repo_path, indexed_commit, out)

    summary = await get_test_coverage_summary(session, repo_id)
    out["map_empty"] = summary.get("pair_count", 0) == 0
    out["map_truncated"] = summary.get("pair_count", 0) >= MAX_TEST_COVERAGE_ROWS
    out["measured_commit"] = measured = summary.get("ingested_commit_sha")
    out["map_current"] = out["map_empty"] or measured in {change.base, change.head} - {None}

    # Repo file keys back the filename-pattern fallback (same source the
    # aggregate coverage ingest resolves against).
    repo_keys = {m.file_path for m in await get_health_metrics(session, repo_id)}
    # git runs off the event loop: a server shares it with other requests.
    routes = (
        []
        if config is None
        else await asyncio.to_thread(_gap_routes, repo_path, change, config, repo_keys, out)
    )
    routes = sorted({*routes, *scope_routes})
    await resolve_impacted(
        session,
        repo_id,
        query_lines(change, measured),
        repo_keys,
        out,
        roots,
        routes,
        pytest_texts,
    )
    await _place_tests(session, repo_id, out)
    if explain:
        await _explain_route(session, repo_id, explain, change, out)
    return out


async def _explain_route(session, repo_id: str, test: str, change, out: dict) -> None:
    """The dependency route from *test* to a changed file, for ``--explain`` only."""
    from .test_reachability import dependency_path

    changed = [*change.files, *change.deleted]
    try:
        out["explain_route"] = await dependency_path(
            session, repo_id, test.split("::", 1)[0], changed
        )
    except Exception as exc:  # the explanation still stands without its route
        log.debug("explain_route_failed", test=test, error=str(exc))
        out["explain_route"] = []


async def _place_tests(session, repo_id: str, out: dict) -> None:
    """Record the tests the graph can see into, and those the indexer found it cannot."""
    from .test_reachability import (
        always_run_test_files,
        placed_test_files,
        unscanned_test_files,
    )

    if out["graph_error"] is not None:
        return
    try:
        out["placed_tests"] = await placed_test_files(session, repo_id)
        out["always_run_tests"] = await always_run_test_files(session, repo_id)
        unscanned = sorted(await unscanned_test_files(session, repo_id) & out["placed_tests"])
    except Exception as exc:
        out["graph_error"] = f"{type(exc).__name__}: {exc}"
        return
    # An index older than the check cannot say which tests walk the tree, so
    # it is out of date like any other; unplaced tests run anyway.
    if unscanned and out["index_problem"] is None:
        out["index_problem"] = (
            f"The index has not checked {len(unscanned)} test file(s) for walking the source "
            f"tree or running the project (e.g. {unscanned[0]}); run `repowise update`."
        )


def _indexed_commit(repo_path, row_commit: str | None, out: dict) -> None:
    """The commit the index describes: ``state.json`` first, then the repository row.

    The same order the server's freshness check reads. When both are recorded
    and disagree, the index's own history is in doubt, so the commit is unknown.
    """
    from ..workspace.update import read_state_commit

    state = read_state_commit(Path(repo_path))
    if state and row_commit and state != row_commit:
        out["index_problem"] = (
            f"The index records two commits ({state[:7]} in state.json, {row_commit[:7]} "
            "in its database), so what it describes is unknown; run `repowise update`."
        )
        return
    out["indexed_commit"] = state or row_commit


def _gap_routes(repo_path, change, config, repo_keys: set[str], out: dict) -> list[str]:
    """Record the files changed since the indexed commit, and return those to walk.

    Only files whose edges may have moved are walked: each candidate is read at
    the indexed commit and at the base in one batch and compared.
    """
    from .change_health.sources import read_blobs
    from .changed_lines import index_gap
    from .import_drift import edges_may_differ
    from .test_selection import plan_gap, with_rewired

    out["index_gap"] = gap = index_gap(str(repo_path), out["indexed_commit"], change)
    if gap is None:
        return []
    plan = plan_gap(gap, config, [*change.files, *change.deleted])
    if plan.candidates:
        old, new = out["indexed_commit"], change.base
        specs = [(rev, p) for p in plan.candidates for rev in (old, new)]
        blobs = read_blobs(str(repo_path), specs)
        # Unreadable means nothing was compared, so every candidate may have moved.
        rewired = [
            p
            for p in plan.candidates
            if blobs is None or edges_may_differ(p, blobs.get((old, p)), blobs.get((new, p)))
        ]
        plan = with_rewired(plan, rewired, repo_keys)
    out["gap"] = plan
    return list(plan.targets)


def query_lines(change, measured: str | None) -> dict[str, set[int] | None]:
    """The lines to look up per file, on the side of the diff the map was measured at.

    Coverage measured at the change's base is keyed in the old file's line
    numbers, at its head in the new file's. Measured anywhere else, or for a
    deleted or hunkless file, only the file can be matched (``None``).
    """
    side = None
    if measured and measured == change.head:
        side = "new"
    elif measured and measured == change.base:
        side = "old"
    lines: dict[str, set[int] | None] = dict.fromkeys(change.deleted)
    for path, diff in change.files.items():
        lines[path] = _touched_lines(diff.hunks, side) if side and diff.hunks else None
    return lines


def _touched_lines(hunks, side: str) -> set[int]:
    """Lines the hunks touch on *side*; for a pure insertion or deletion, its two neighbours."""
    lines: set[int] = set()
    for old_start, old_count, new_start, new_count in hunks:
        start, count = (old_start, old_count) if side == "old" else (new_start, new_count)
        lines.update(range(start, start + count) if count else {start, start + 1} - {0})
    return lines


def empty_result(changed_files: int) -> dict:
    return {
        "no_index": False,
        "map_empty": False,
        "map_current": True,
        "map_truncated": False,
        "measured_commit": None,
        "indexed_commit": None,
        "index_problem": None,
        "index_gap": None,
        "gap": None,
        "graph_error": None,
        "placed_tests": None,
        "always_run_tests": {},
        "explain_route": [],
        "helper_importers": {},
        "conftest_notes": [],
        "changed_files": changed_files,
        "covered": {},  # test_id -> {test_file, source_files: [...]}
        "inferred": [],  # {source_file, test_file, via}
        "unknown": [],  # source_file (nothing knows of a test for it)
    }


async def resolve_impacted(
    session,
    repo_id: str,
    changed: dict[str, set[int] | None],
    repo_keys: set[str],
    out: dict,
    roots: PytestRoots | None = None,
    routes: tuple[str, ...] | list[str] = (),
    pytest_texts: dict | None = None,
) -> dict:
    """Classify each changed file: covered tests, inferred tests, or unknown.

    Mutates and returns *out*. Split from :func:`collect` (which reads the
    map summary and the gap) so the diff -> lines -> tests path is testable against a seeded
    ``test_coverage`` table. A file mapped to ``None`` is matched by file, not
    by line (a deleted file, or a map measured at another commit). Every file
    also asks the graph: coverage records what one run executed, so it adds to
    the graph's answer and never replaces it.

    *routes* (files changed since the index was built) are looked up the same
    way, by file, but only to report the tests reaching them: none is guessed
    from its name or listed as unknown.

    Three tiers, and the output says which one answered for every file, because
    they are not interchangeable:

    ``covered``
        Recorded per-test coverage intersecting the changed *lines*. The only
        tier that knows about lines, and the only one that proves execution.
    ``inferred``
        What the graph names, beside any coverage. A changed test is its own
        candidate (``via="changed-test"``). Then the dependency graph, which
        reports which tier answered: tests whose calls reach the file
        (``via="call-graph"``), and every test that imports it, directly or
        through other modules or other tests (``via="import-graph"``). Both are
        recorded edges, and they find suites whose tests are named for
        behaviour, not for the file. The filename pattern answers only
        when the graph is silent (``via="filename-pattern"``). All are
        file-level and all over-claim; none may be read as coverage.
    ``unknown``
        Nothing said anything. Run the full suite.
    """
    from ..persistence.crud import tests_covering, tests_covering_files
    from .test_reachability import tests_matching_by_name

    covered: dict[str, dict] = out["covered"]
    has_rows: set[str] = set()
    route_only = [r for r in routes if r not in changed]
    graph_targets = sorted({*changed, *route_only})
    by_file = {f: await tests_covering(session, repo_id, f, lines=ls) for f, ls in changed.items()}
    if route_only and not out.get("map_empty"):
        by_file.update(await tests_covering_files(session, repo_id, set(route_only)))
    for source_file, rows in sorted(by_file.items()):
        for r in rows:
            has_rows.add(source_file)
            entry = covered.setdefault(
                r["test_id"],
                {"test_file": r["test_file"], "source_files": []},
            )
            if source_file not in entry["source_files"]:
                entry["source_files"].append(source_file)

    if not graph_targets:
        return out

    try:
        candidates, importers, notes = await _graph_candidates(
            session, repo_id, graph_targets, roots, pytest_texts=pytest_texts
        )
    except Exception as exc:
        # Nothing the graph said can be trusted; selection runs everything.
        out["graph_error"] = f"{type(exc).__name__}: {exc}"
        candidates, importers, notes = {}, {}, []
    out["helper_importers"] = importers
    out["conftest_notes"] = notes
    for source_file in graph_targets:
        found = candidates.get(source_file)
        if found:
            out["inferred"].extend(
                {"source_file": source_file, "test_file": t, "via": via} for t, via in found
            )
            continue
        if source_file in has_rows or source_file not in changed:
            continue  # coverage answered, or a route; a name-shaped guess adds nothing
        guess = tests_matching_by_name([source_file], repo_keys).get(source_file)
        if guess:
            out["inferred"].append(
                {
                    "source_file": source_file,
                    "test_file": guess.tests[0],
                    "via": "filename-pattern",
                }
            )
        else:
            out["unknown"].append(source_file)
    return out


async def _graph_candidates(
    session,
    repo_id: str,
    targets: list[str],
    roots: PytestRoots | None = None,
    *,
    pytest_texts: dict | None = None,
) -> tuple[dict[str, list], dict[str, list[str]], list[str]]:
    """``{target: [(test file, via), ...]}`` from the graph, each test file's importers, notes.

    Candidates come strongest tier first. The importers map says, for every
    test file the walk met, which test files import it. A conftest reached only
    through its imports stands for the tests it can break
    (:mod:`repowise.core.analysis.conftest_routes`), read from *pytest_texts*;
    the notes say what was decided.

    One walk per tier for every target, not one per file: the seed set
    is what makes it cheap. Uncapped, since a trimmed list would drop tests a
    change needs. The import walk treats a test file as a leaf, so the tests
    importing a candidate (a shared base class, a helper module under
    ``tests/``) are added until nothing new appears. Only code counts as a test
    node: a JSON or golden file the index flags as test material is data, not a
    route. A read failure raises.
    """
    from .conftest_routes import scoped_candidates
    from .test_reachability import load_test_files
    from .test_selection import is_code_file, plugin_loader

    texts = pytest_texts or {}
    test_files = {f for f in await load_test_files(session, repo_id) if is_code_file(f)}
    found, entries = await _tier_picks(session, repo_id, targets, test_files, roots)
    seeds = {t for picks in found.values() for t in picks}
    parents, parent_entries = await _test_importers(session, repo_id, seeds, test_files)
    out, notes = await scoped_candidates(
        session,
        repo_id,
        found,
        entries,
        parents,
        parent_entries,
        test_files,
        texts.get,
        plugin_loader=plugin_loader(texts.items()),
    )
    return out, {test: sorted(found_by) for test, found_by in parents.items()}, notes


def _all_tests(reached) -> tuple[str, ...]:
    """Every test a walk found, not the capped list it reports."""
    return reached.all_tests or tuple(reached.tests)


async def _tier_picks(
    session, repo_id: str, targets: list[str], test_files: set[str], roots: PytestRoots | None
) -> tuple[dict[str, dict[str, str]], dict[str, dict]]:
    """``{target: {test file: via}}``: a changed test itself, then the call and import walks.

    Also ``{target: {test file: the files it was reached through}}`` from the
    import walk.
    """
    from .test_reachability import tests_reaching_by_tier
    from .test_selection import is_runnable_test

    reaching = await tests_reaching_by_tier(session, repo_id, targets, test_files=test_files)
    importers = await tests_reaching_by_tier(
        session,
        repo_id,
        targets,
        call_depth=0,
        import_depth=_IMPORT_CLOSURE_DEPTH,
        test_files=test_files,
    )
    found: dict[str, dict[str, str]] = {}
    entries = {t: dict(r.entries or {}) for t, r in importers.items()}
    for target in targets:
        picks = found.setdefault(target, {})
        # A test the index has not seen yet (new in this change) is still its own pick.
        if target in test_files or is_runnable_test(target, roots):
            picks[target] = "changed-test"
        for reached, via in ((reaching.get(target), None), (importers.get(target), "import-graph")):
            for t in _all_tests(reached) if reached else ():
                picks.setdefault(t, via or reached.via)
    return found, entries


async def _test_importers(
    session, repo_id: str, seeds: set[str], test_files: set[str]
) -> tuple[dict[str, set[str]], dict[str, dict]]:
    """``{test file: test files importing it}``, walked until nothing new appears.

    Also ``{test file: {importer: the files it imports on that route}}``.
    """
    from .test_reachability import tests_reaching_by_tier

    parents: dict[str, set[str]] = {}
    routes: dict[str, dict] = {}
    frontier, seen = set(seeds), set(seeds)
    while frontier:
        walked = await tests_reaching_by_tier(
            session,
            repo_id,
            sorted(frontier),
            call_depth=0,
            import_depth=_IMPORT_CLOSURE_DEPTH,
            test_files=test_files,
        )
        frontier = set()
        for test, reached in walked.items():
            parents.setdefault(test, set()).update(_all_tests(reached))
            routes.setdefault(test, {}).update(reached.entries or {})
            frontier.update(set(_all_tests(reached)) - seen)
        seen |= frontier
    return parents, routes


def select(change, result: dict, config, checkout: Checkout, plan: Plan) -> Selection:
    """The run-all-or-subset decision over *result* (from :func:`collect`)."""
    from .test_selection import (
        SelectionInput,
        is_documentation,
        is_runnable_test,
        plugin_loader,
        select_tests,
    )

    named = {g["test_file"] for g in result["inferred"]}
    named |= {i["test_file"] for i in result["covered"].values() if i["test_file"]}
    tracked = checkout.tracked
    go_test_dirs = {str(Path(p).parent.as_posix()) for p in tracked if p.endswith("_test.go")}
    placed = result["placed_tests"]
    known_tests = sorted(p for p in tracked if is_runnable_test(p, checkout.roots))
    return select_tests(
        SelectionInput(
            changed=change.files,
            deleted=change.deleted,
            tiers=result,
            config=config,
            index_available=not result["no_index"],
            label=change.label,
            map_current=result["map_current"],
            map_truncated=result["map_truncated"],
            index_gap=result["index_gap"],
            gap=result["gap"],
            index_problem=result["index_problem"]
            or (
                None
                if tracked
                else "git could not list the checkout's files, so its tests are unknown."
            ),
            graph_error=result["graph_error"],
            missing={f for f in named if not checkout.exists(f)},
            go_test_dirs=go_test_dirs,
            known_tests=known_tests,
            doc_readers={p: n[0] for p, n in plan.namers.items() if is_documentation(p)},
            plugin_loader=plugin_loader(checkout.pytest_texts),
            unplaced_tests=[] if placed is None else [t for t in known_tests if t not in placed],
            always_run_tests=result["always_run_tests"],
            scopes=plan.scopes,
        )
    )


async def select_for_change(
    session,
    repo_id: str,
    repo_path,
    change,
    config: TestSelectionConfig,
    checkout: Checkout,
    *,
    indexed_commit: str | None = None,
    cancelled: Callable[[], bool] = _never,
) -> tuple[dict[str, Any], Selection]:
    """:func:`plan_scopes`, :func:`collect` then :func:`select`: what *change* needs, and why.

    Raises :class:`SelectionCancelledError` once *cancelled* turns true.
    """
    # The namer search reads every source when a doc or asset changed: off the loop.
    plan = await asyncio.to_thread(plan_scopes, change, config, checkout, cancelled)
    _stop(cancelled)
    result = await collect(
        session,
        repo_id,
        repo_path,
        change,
        checkout.roots,
        config,
        None,
        plan.routes,
        dict(checkout.pytest_texts),
        indexed_commit=indexed_commit,
    )
    result["diff"] = change.label
    _stop(cancelled)
    return result, await asyncio.to_thread(select, change, result, config, checkout, plan)


async def narrow_scopes(
    session, repo_id: str, reached: Mapping[str, Sequence[str]], test_files: Collection[str]
) -> dict[str, list[str]]:
    """*reached* with each conftest or test package a walk stopped at replaced by its tests.

    For surfaces that walk a few hops and do not select: a conftest reached
    only through its imports stands for the tests the selection narrows it to
    (:mod:`repowise.core.analysis.conftest_routes`, the same graph walk), and
    any other scope for every runnable test under its directory. The walk runs
    only for targets that reached a scope. The conftests and pytest configs
    (nested ones included) come from :func:`read_checkout` of the indexed
    checkout. A target the walk gives nothing for, or a checkout that cannot
    be read, keeps every test under each scope it reached. Keys may be symbol
    ids (``path::name``); the walk starts at their file.
    """
    from ..persistence.models import Repository
    from .test_selection import expand_test_scopes, scope_kind

    scoped = {t: list(v) for t, v in reached.items() if any(scope_kind(x) for x in v)}
    out = {t: list(v) for t, v in reached.items()}
    if not scoped:
        return out
    local = await session.get(Repository, repo_id)
    texts: dict[str, str] = {}
    if local is not None and local.local_path and Path(local.local_path).is_dir():
        try:
            checkout = await asyncio.to_thread(read_checkout, local.local_path)
            texts = dict(checkout.pytest_texts)
        except Exception as exc:  # unread conftests keep every test under them
            log.debug("narrow_scopes_checkout_failed", error=str(exc))
    files = sorted({t.split("::", 1)[0] for t in scoped})
    try:
        candidates, _, _ = await _graph_candidates(session, repo_id, files, pytest_texts=texts)
    except Exception as exc:  # the walk's own answer stands: every test under each scope
        log.debug("narrow_scopes_failed", error=str(exc))
        candidates = {}
    for target, tests in scoped.items():
        scopes = [x for x in tests if scope_kind(x)]
        dirs = {str(PurePosixPath(s).parent) for s in scopes}
        picked = [t for t, _ in candidates.get(target.split("::", 1)[0], ())]
        under = [t for t in picked if any(d == "." or t.startswith(f"{d}/") for d in dirs)]
        kept = [x for x in tests if not scope_kind(x)]
        out[target] = expand_test_scopes([*kept, *(under or scopes)], test_files)
    return out
