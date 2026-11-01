"""The reverse reach walks, one implementation for every graph source.

Each walk is a generator: it yields a :class:`~.maps.Need` per level and is sent
the rows back. :func:`reaching_by_tier` and :func:`reach_states` drive them from
:class:`~.maps.WalkMaps`; the database adapter drives the same generators from
its queries.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Mapping
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TypeVar

from repowise.core.analysis.dead_code.file_reachability import BARREL_FILENAMES
from repowise.core.analysis.execution_graph import file_of_symbol
from repowise.core.analysis.test_selection import is_code_file, is_runnable_test
from repowise.core.test_paths import names_test_for

from .hubs import HUB_MIN_FAN_IN
from .inline import inline_tests
from .maps import Need, Walk, WalkMaps, run
from .model import (
    DEFAULT_CALL_DEPTH,
    DEFAULT_MAX_DEPTH,
    MAX_TESTS_PER_TARGET,
    TRANSITIVE_HOPS,
    ReachDistance,
    ReachedBy,
    ReachRow,
    capped,
)
from .type_only import is_type_only

# The whole reverse-import closure: no depth limit, the walk ends when no node
# gains a new seed, so a cut-off can never drop a test.
CLOSURE_DEPTH = sys.maxsize

# Seeds one closure walk carries. Every node keeps the set of seeds it was
# reached from, so a walk's memory grows with seeds times files; more targets
# than this are walked in batches of it. Ceiling: the batches repeat the walk
# over shared ancestors; a seed bitset per node is the upgrade path.
CLOSURE_SEED_CAP = 200

_V = TypeVar("_V")


def call_walk(
    seeds: list[str],
    test_files: Collection[str],
    max_depth: int,
    *,
    symbol_seeds: Mapping[str, Collection[str]] | None = None,
    avoid: Collection[str] = frozenset(),
) -> Walk[dict[str, dict[str, ReachDistance]]]:
    """Tests that can execute into each seed file, walking call edges backwards.

    Starts one layer below the other walk: the seeds are files and call edges
    join symbols, so the first need resolves each seed to the symbols it
    declares, and the walk carries the seed from there.

    Each test carries the fewest call hops it took and how many of its symbols
    made a call at that depth. Every node records each seed's distance, not
    the level it was found at: one level's batch can carry a seed through a
    node another seed entered, so the level alone overstates how close a test
    is. A shorter distance found later re-queues the node; which tests reach
    which seed is unchanged by that, only how far.

    *symbol_seeds* enters the named targets at exactly those symbol ids. *avoid*
    names files reach does not pass through, except into a target's own
    symbols. Each level reads the distances it started with, so a seed moves
    one hop per level: no test is reached past *max_depth*, and the answer
    depends neither on which other seeds share the walk nor on edge order.
    """
    # node -> seed -> call hops from the node to the seed.
    origins = yield from _call_origins(seeds, symbol_seeds)
    if not origins:
        return {}

    found: dict[str, dict[str, tuple[int, set[str]]]] = {}
    frontier = list(origins)
    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        queued: set[str] = set()
        start = {node: dict(origins[node]) for node in level}
        for caller, callee in (yield Need("callers", tuple(level))):
            carried = start.get(callee)
            if not carried:
                continue
            owner = file_of_symbol(caller)
            if owner in test_files:
                _record_test_hit(found, owner, caller, carried)
                # A test is a leaf. Walking through one would let "test A calls
                # shared helper B" drag B's unrelated targets in.
                continue
            if owner in avoid:
                # A hub carries on only the seeds it declares itself.
                carried = {s: d for s, d in carried.items() if s.split("::", 1)[0] == owner}
            if not _carry_closer(origins, caller, carried):
                continue
            if caller not in queued:
                queued.add(caller)
                frontier.append(caller)
    return {
        seed: {test: ReachDistance(depth, len(callers)) for test, (depth, callers) in tests.items()}
        for seed, tests in found.items()
    }


def _call_origins(
    seeds: list[str], symbol_seeds: Mapping[str, Collection[str]] | None
) -> Walk[dict[str, dict[str, int]]]:
    """The symbols each seed enters the call walk at, each at distance 0 from its seed."""
    origins: dict[str, dict[str, int]] = {}
    # A caller that knows which symbol in the file it cares about enters there,
    # so a test reaching an unrelated symbol in the same file does not count.
    # An empty entry names no symbol, so that file is unseeded and keeps the
    # ``defines`` lookup; treating it as seeded would silence the file entirely.
    seeded = {seed: symbol_seeds[seed] for seed in seeds if symbol_seeds and symbol_seeds.get(seed)}
    for seed, symbol_ids in seeded.items():
        for symbol in symbol_ids:
            origins.setdefault(symbol, {})[seed] = 0
    unseeded = tuple(seed for seed in seeds if seed not in seeded)
    if unseeded:
        for seed, symbol in (yield Need("declared", unseeded)):
            origins.setdefault(symbol, {})[seed] = 0
    return origins


def _record_test_hit(
    found: dict[str, dict[str, tuple[int, set[str]]]],
    test: str,
    caller: str,
    carried: Mapping[str, int],
) -> None:
    """Keep the fewest hops *test* took to each carried seed, and its callers at that depth."""
    for seed, distance in carried.items():
        hops = distance + 1
        by_test = found.setdefault(seed, {})
        best = by_test.get(test)
        if best is None or hops < best[0]:
            by_test[test] = (hops, {caller})
        elif hops == best[0]:
            best[1].add(caller)


def _carry_closer(
    origins: dict[str, dict[str, int]], caller: str, carried: Mapping[str, int]
) -> bool:
    """Record the seeds *caller* reaches closer than before; True when any did."""
    if not carried:
        return False
    known = origins.setdefault(caller, {})
    closer = {
        seed: distance + 1
        for seed, distance in carried.items()
        if seed not in known or distance + 1 < known[seed]
    }
    known.update(closer)
    return bool(closer)


def import_walk(
    seeds: list[str], test_files: Collection[str], max_depth: int
) -> Walk[tuple[dict[str, set[str]], dict[str, dict[str, set[str]]]]]:
    """Tests that import each seed file, directly or within *max_depth* hops.

    Also ``{seed: {test: the files it imports on a route to the seed}}``.
    """
    origins: dict[str, set[str]] = {s: {s} for s in seeds}
    # (test, file it imports) -> seeds carried in: one set union per edge, not
    # a dict write per seed; split per seed once the walk ends.
    reached: dict[tuple[str, str], set[str]] = {}
    frontier = list(seeds)

    for _ in range(max_depth):
        if not frontier:
            break
        level, frontier = frontier, []
        queued: set[str] = set()
        # Read each level against the origins it started with, so a seed that
        # imports another seed moves one hop per level, whatever the edge order.
        start = {f: frozenset(origins[f]) for f in level}
        for dependent, dependency in (yield Need("importers", tuple(level))):
            carried = start.get(dependency)
            if not carried:
                continue
            if dependent in test_files:
                reached.setdefault((dependent, dependency), set()).update(carried)
                continue
            # Another seed is an ordinary node here: a route through it must
            # reach the same tests a one-seed walk would.
            known = origins.setdefault(dependent, set())
            fresh = carried - known
            if not fresh:
                # Already reached carrying nothing new. Without this, a diamond
                # in the import graph re-walks the whole subtree once per path
                # into it.
                continue
            known |= fresh
            if dependent not in queued:
                queued.add(dependent)
                frontier.append(dependent)
    found: dict[str, set[str]] = {}
    entries: dict[str, dict[str, set[str]]] = {}
    for (test, dependency), carried in reached.items():
        for seed in carried:
            found.setdefault(seed, set()).add(test)
            entries.setdefault(seed, {}).setdefault(test, set()).add(dependency)
    return found, entries


def helper_walk(
    targets: list[str],
    test_files: Collection[str],
    *,
    rank: bool = True,
) -> Walk[dict[str, ReachedBy]]:
    """Tests that import a file only tests import, or a barrel, which imports the target.

    A second import hop, kept off hubs: a blanket second hop was measured and
    rejected (see Depth), but a module nothing outside the test suite imports
    is test support whatever its name (``attempt.spawn-workspace.test-support.ts``
    loading ``attempt.ts`` for 30-odd tests), so walking through it claims
    nothing a hub would. A barrel (``index.ts``, ``__init__.py``) passes on
    what it imports even when source imports it too: a package's tests import
    its entry, not each module behind it.
    """
    middles: dict[str, set[str]] = {}
    for dependent, target in (yield Need("importers", tuple(targets))):
        if dependent not in test_files:
            middles.setdefault(dependent, set()).add(target)
    importers: dict[str, set[str]] = {}
    for dependent, middle in (yield Need("importers", tuple(sorted(middles)))):
        importers.setdefault(middle, set()).add(dependent)
    reached: dict[str, set[str]] = {}
    all_tests = frozenset(test_files)
    for middle, who in importers.items():
        # Test support is code. A manifest a test reads (``package.json``,
        # whose ``exports`` point at every entry of the package) runs nothing.
        if not is_code_file(middle):
            continue
        tests = who & all_tests
        if tests and (tests == who or PurePosixPath(middle).name in BARREL_FILENAMES):
            for target in middles[middle]:
                reached.setdefault(target, set()).update(tests)
    edited = frozenset(c.split("::", 1)[0] for c in targets)
    return {
        target: capped(target, tests, "import-graph", changed=targets, edited=edited, rank=rank)
        for target, tests in reached.items()
    }


def package_walk(
    targets: list[str],
    test_files: Collection[str],
    *,
    rank: bool = True,
) -> Walk[dict[str, ReachedBy]]:
    """Tests that load each target through the target's own package.

    A reverse import walk with no depth limit whose every step stays inside
    the package: a module in the target's directory, or the barrel
    (``__init__.py``, ``index.ts``) of that directory or of its parent. A test
    importing ``tool_why`` runs its ``__init__``, which imports ``tool.py``,
    which imports and calls ``workspace.py``; a registry beside the detectors
    it imports is how tests run each detector. A blanket second import hop
    misses the first and is not needed for the second, and neither route
    leaves the package, so the rest of the repository cannot carry a test in.

    A module with more than ``HUB_MIN_FAN_IN`` importers passes on only
    itself: a hub inside the package reaches the target the way every test
    importing the hub does, which says nothing about this file.

    Targets are files: import edges join files, so a symbol id finds nothing.
    """
    seeds = [t for t in dict.fromkeys(targets) if t]
    tests = frozenset(test_files)
    home = {s: _directory(s) for s in seeds}
    origins: dict[str, set[str]] = {s: {s} for s in seeds}
    reached: dict[str, set[str]] = {}
    frontier = list(seeds)
    while frontier:
        start = {f: frozenset(origins[f]) for f in frontier}
        importers: dict[str, list[str]] = {}
        for dependent, dependency in (yield Need("importers", tuple(frontier))):
            importers.setdefault(dependency, []).append(dependent)
        queued: dict[str, None] = {}
        for node, who in importers.items():
            carried = start.get(node, frozenset())
            if len(who) > HUB_MIN_FAN_IN:
                carried &= {node}
            for dependent in who:
                if dependent in tests:
                    _record_tests(reached, carried, dependent)
                elif _carry_in_package(dependent, carried, home, origins):
                    queued[dependent] = None
        frontier = list(queued)
    edited = frozenset(c.split("::", 1)[0] for c in seeds)
    return {
        target: capped(target, found, "import-graph", changed=seeds, edited=edited, rank=rank)
        for target, found in reached.items()
    }


def _record_tests(reached: dict[str, set[str]], seeds: Collection[str], test: str) -> None:
    for seed in seeds:
        reached.setdefault(seed, set()).add(test)


def _carry_in_package(
    node: str,
    carried: Collection[str],
    home: Mapping[str, str],
    origins: dict[str, set[str]],
) -> bool:
    """Give *node* the carried seeds whose package it is in; True when it gained any.

    In a seed's package: beside it, or the barrel of its directory or its parent's.
    """
    if not is_code_file(node):
        return False
    where = _directory(node)
    barrel = PurePosixPath(node).name in BARREL_FILENAMES
    fresh = {s for s in carried if home[s] == where or (barrel and _directory(home[s]) == where)}
    fresh -= origins.get(node, set())
    if fresh:
        origins.setdefault(node, set()).update(fresh)
    return bool(fresh)


def _directory(path: str) -> str:
    """The directory part of a file path or a directory, ``""`` at the root."""
    return path.split("::", 1)[0].rpartition("/")[0]


def tier_walk(
    targets: list[str],
    test_files: Collection[str],
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
    symbol_seeds: Mapping[str, Collection[str]] | None = None,
    avoid: Collection[str] = frozenset(),
    helpers: bool = False,
    hubs: Collection[str] = frozenset(),
    rank: bool = True,
) -> Walk[dict[str, ReachedBy]]:
    """The call walk, the import walk for what it left, then (*helpers*) the helper and package hops.

    Each tier is seeded with only the targets the stronger ones left
    unanswered, so a weaker tier never speaks over a stronger one. The targets
    are usually a change's files, so a test among them leads every list.

    With *hubs*, a call-tier test is tagged ``transitive`` when it reaches the
    target only through a hub (:func:`transitive_tests`). A listing passes
    them; a selection does not, since a tagged test still runs the target.
    """
    seeds = sorted({t for t in targets if t})
    if not seeds or not test_files:
        return {}
    out: dict[str, ReachedBy] = {}
    edited = frozenset(c.split("::", 1)[0] for c in seeds)
    if call_depth >= 1:
        found = yield from call_walk(
            seeds, test_files, call_depth, symbol_seeds=symbol_seeds, avoid=avoid
        )
        tagged: dict[str, set[str]] = {}
        if hubs:
            tagged = yield from transitive_tests(
                found, test_files, call_depth, symbol_seeds=symbol_seeds, avoid=avoid, hubs=hubs
            )
        for seed, reach in found.items():
            out[seed] = capped(
                seed,
                reach,
                "call-graph",
                reach=reach,
                changed=seeds,
                edited=edited,
                transitive=tagged.get(seed, ()),
                rank=rank,
            )
    unanswered = [seed for seed in seeds if seed not in out]
    if unanswered and import_depth >= 1:
        found, entries = yield from import_walk(unanswered, test_files, import_depth)
        for seed, tests in found.items():
            file = seed.split("::", 1)[0]
            by_test = {t: frozenset(via) for t, via in entries[seed].items()}
            # A test importing the target itself, not a file on the way to it.
            direct = {t for t, via in by_test.items() if file in via}
            out[seed] = capped(
                seed,
                tests,
                "import-graph",
                direct=direct,
                changed=seeds,
                edited=edited,
                entries=by_test,
                rank=rank,
            )
    unanswered = [seed for seed in seeds if seed not in out]
    if unanswered and helpers:
        out |= yield from helper_walk(unanswered, test_files, rank=rank)
        unanswered = [seed for seed in seeds if seed not in out]
        if unanswered:
            out |= yield from package_walk(unanswered, test_files, rank=rank)
    return out


def transitive_tests(
    found: Mapping[str, Mapping[str, ReachDistance]],
    test_files: Collection[str],
    max_depth: int,
    *,
    symbol_seeds: Mapping[str, Collection[str]] | None = None,
    avoid: Collection[str] = frozenset(),
    hubs: Collection[str],
) -> Walk[dict[str, set[str]]]:
    """``{seed: tests reaching it only through a hub}``, among *found*'s far, single-caller tests.

    A candidate is ``TRANSITIVE_HOPS`` or more calls out from one test
    function. The call walk runs again for the seeds with candidates, with
    *hubs* added to *avoid*; a candidate it no longer finds had no route that
    skips a hub. A target that is a hub itself still carries its own seeds.
    """
    candidates = {
        seed: {t for t, d in reach.items() if d.hops >= TRANSITIVE_HOPS and d.callers == 1}
        for seed, reach in found.items()
    }
    candidates = {seed: tests for seed, tests in candidates.items() if tests}
    if not candidates:
        return {}
    around = yield from call_walk(
        sorted(candidates),
        test_files,
        max_depth,
        symbol_seeds=symbol_seeds,
        avoid={*avoid, *hubs},
    )
    tagged = {seed: tests - set(around.get(seed, ())) for seed, tests in candidates.items()}
    return {seed: tests for seed, tests in tagged.items() if tests}


def reaching_by_tier(
    maps: WalkMaps,
    targets: Collection[str],
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
    symbol_seeds: Mapping[str, Collection[str]] | None = None,
    avoid: Collection[str] = frozenset(),
    helpers: bool = True,
    hubs: Collection[str] = frozenset(),
) -> dict[str, ReachedBy]:
    """Test files reaching each of *targets*, and which tier found them: :func:`tier_walk`.

    Targets nothing reaches are absent; that absence is a weak signal, not a
    claim (:func:`reach_states` makes the claim). A target may be a symbol id
    (``path::name``); its tests are ranked against the file part.
    """
    return run(
        tier_walk(
            list(targets),
            maps.test_files,
            call_depth=call_depth,
            import_depth=import_depth,
            symbol_seeds=symbol_seeds,
            avoid=avoid,
            helpers=helpers,
            hubs=hubs,
        ),
        maps,
    )


def closure_walk(
    targets: list[str],
    test_files: Collection[str],
    *,
    rank: bool = True,
) -> Walk[dict[str, ReachedBy]]:
    """Tests anywhere up the import closure of each target, in batches of ``CLOSURE_SEED_CAP``."""
    out: dict[str, ReachedBy] = {}
    edited = frozenset(c.split("::", 1)[0] for c in targets)
    for start in range(0, len(targets), CLOSURE_SEED_CAP):
        batch = targets[start : start + CLOSURE_SEED_CAP]
        found, entries = yield from import_walk(batch, test_files, CLOSURE_DEPTH)
        for seed, tests in found.items():
            by_test = {t: frozenset(via) for t, via in entries[seed].items()}
            out[seed] = capped(
                seed,
                tests,
                "import-graph",
                changed=targets,
                edited=edited,
                entries=by_test,
                rank=rank,
            )
    return out


def states_walk(
    paths: list[str],
    test_files: Collection[str],
    *,
    changed_tests: Collection[str] = frozenset(),
    contents: Mapping[str, str | bytes] | None = None,
    masks: Mapping[str, Collection[str]] | None = None,
    rank: bool = True,
) -> Walk[dict[str, ReachRow]]:
    """A :class:`~.model.ReachRow` per path, the negative claim made only when it holds.

    In order: ``type_only`` (from *contents*); ``reached`` by a test the change
    adds or edits (*changed_tests*) that is named for the path or imports it
    and does not replace it, then by the path's own inline tests; ``new`` for
    a path the index lacks; ``no_signal`` when the index holds no test; ``reached`` by the call walk,
    the import walk, the helper and barrel hop or the package walk; ``reached`` through the whole
    import closure; ``mocked_only`` when every test those found replaces the
    path (*masks*, ``{test: files it replaces}``, :func:`~.mocks.replaced_files`);
    else ``unreached``. A path whose walked tiers find only replacing tests
    goes on to the import closure, so a test that runs it is still found.
    """
    contents = contents or {}
    mocked: dict[str, set[str]] = {}
    rows: dict[str, ReachRow] = {}
    rest: list[str] = []
    # Grouped once by directory, so each Go file scans only its own folder's tests.
    go_tests: dict[str, list[str]] = {}
    for t in test_files:
        if t.endswith("_test.go"):
            go_tests.setdefault(str(PurePosixPath(t).parent), []).append(t)
    # Only a test a runner collects is evidence; a touched conftest or helper is not.
    touched = sorted(
        t for t in set(changed_tests) if is_runnable_test(t, is_test=(t in test_files) or None)
    )
    imported_by = yield from _touched_imports(paths, touched)
    for path in dict.fromkeys(paths):
        content = contents.get(path)
        if content is not None and is_type_only(
            path, content, is_test=(path in test_files) or None
        ):
            rows[path] = ReachRow("type_only")
            continue
        named = [t for t in touched if t in imported_by.get(path, ()) or names_test_for(t, path)]
        replacing = [t for t in named if masks and path in masks.get(t, ())]
        if replacing:
            mocked.setdefault(path, set()).update(replacing)
            named = [t for t in named if t not in replacing]
        if named:
            rows[path] = ReachRow(
                "reached", capped(path, named, "changed-test", changed=named, rank=rank)
            )
            continue
        own = inline_tests(path, content, go_tests.get(str(PurePosixPath(path).parent), ()))
        if own:
            rows[path] = ReachRow("reached", capped(path, own, "inline", rank=rank))
            continue
        rest.append(path)
    known = (yield Need("known", tuple(rest))) if rest else set()
    for path in rest:
        if path not in known:
            rows[path] = ReachRow("new")
        elif not test_files:
            rows[path] = ReachRow("no_signal")
    rest = [p for p in rest if p not in rows]
    found = _unmasked(
        (yield from tier_walk(rest, test_files, helpers=True, rank=rank)), masks, mocked
    )
    left = [p for p in rest if p not in found]
    found |= _unmasked((yield from closure_walk(left, test_files, rank=rank)), masks, mocked)
    for path in rest:
        hit = found.get(path)
        if hit:
            rows[path] = ReachRow("reached", hit)
        elif path in mocked:
            rows[path] = ReachRow("mocked_only", mocked_by=tuple(sorted(mocked[path])))
        else:
            rows[path] = ReachRow("unreached")
    return {path: rows[path] for path in dict.fromkeys(paths)}


def _unmasked(
    found: dict[str, ReachedBy],
    masks: Mapping[str, Collection[str]] | None,
    mocked: dict[str, set[str]],
) -> dict[str, ReachedBy]:
    """*found* without the tests that replace each target; *mocked* gains the ones dropped.

    A target every test of which replaces it is left out. The rest keep their
    rank order, so the cap still keeps the closest tests.
    """
    if not masks:
        return found
    out: dict[str, ReachedBy] = {}
    for target, by in found.items():
        tests = by.all_tests if by.all_tests is not None else tuple(by.tests)
        replaced = [t for t in tests if target in masks.get(t, ())]
        if not replaced:
            out[target] = by
            continue
        mocked.setdefault(target, set()).update(replaced)
        kept = tuple(t for t in tests if t not in replaced)
        if kept:
            out[target] = replace(
                by,
                tests=list(kept[:MAX_TESTS_PER_TARGET]),
                total=len(kept),
                all_tests=kept,
                reach=_only(by.reach, kept),
                entries=_only(by.entries, kept),
                transitive=by.transitive - set(replaced),
            )
    return out


def _touched_imports(paths: list[str], touched: list[str]) -> Walk[dict[str, set[str]]]:
    """``{path: the touched tests importing it}``, one hop; nothing to ask without touched tests."""
    if not touched or not paths:
        return {}
    wanted = set(touched)
    out: dict[str, set[str]] = {}
    for dependent, dependency in (yield Need("importers", tuple(paths))):
        if dependent in wanted:
            out.setdefault(dependency, set()).add(dependent)
    return out


def _only(by_test: Mapping[str, _V] | None, tests: Collection[str]) -> dict[str, _V] | None:
    return None if by_test is None else {t: v for t, v in by_test.items() if t in tests}


def reach_states(
    maps: WalkMaps,
    paths: Collection[str],
    *,
    changed_tests: Collection[str] = frozenset(),
    contents: Mapping[str, str | bytes] | None = None,
    masks: Mapping[str, Collection[str]] | None = None,
    rank: bool = True,
) -> dict[str, ReachRow]:
    """:func:`states_walk` over *maps*: whether tests reach each path, as a typed claim.

    *changed_tests* are test files the change adds or edits, indexed or not.
    *contents* maps a path to its bytes, for the type-only and Rust inline
    checks; a path without them skips both. *masks* maps a test to the files
    it replaces outright (:func:`~.mocks.replaced_files`).
    """
    return run(
        states_walk(
            list(paths),
            maps.test_files,
            changed_tests=changed_tests,
            contents=contents,
            masks=masks,
            rank=rank,
        ),
        maps,
    )
