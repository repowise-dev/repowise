"""The reach walks over the stored graph: one query per level, the same walks as in memory.

Each reader here answers the :class:`~.maps.Need` a walk yields with SQL over
``graph_edges`` / ``graph_nodes`` and drives the generators of :mod:`.walk`, so
a walk over the database and one over a graph held in memory return the same
rows.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Collection, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.dead_code.file_reachability import BARREL_FILENAMES
from repowise.core.analysis.execution_graph import ExecutionGraphIndex, file_of_symbol
from repowise.core.ingestion.models import EXECUTION_EDGE_TYPES, FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.persistence.index_cache import IndexStampCache, index_stamp
from repowise.core.persistence.models import GraphNode, Repository
from repowise.core.persistence.read_snapshots import read_snapshot, refresh_snapshot, snapshot_key

from .forward import CallGraphView, files_reached_by_tests
from .hubs import call_hub_files
from .maps import LEGACY_CONFTEST_HINT, ImportGraph, Need, Walk
from .mocks import MASKABLE_SUFFIXES, replaced_files
from .model import (
    DEFAULT_CALL_DEPTH,
    DEFAULT_MAX_DEPTH,
    UNRELIABLE_CALL_ORIGINS,
    ReachDistance,
    ReachedBy,
    ReachRow,
)
from .walk import call_walk, helper_walk, states_walk, tier_walk

_T = TypeVar("_T")


async def drive(
    walk: Walk[_T],
    session: AsyncSession,
    repo_id: str,
    *,
    import_graph: ImportGraph | None = None,
) -> _T:
    """Drive *walk* to its answer from the stored graph, one query per :class:`Need`.

    With *import_graph* (:func:`load_import_graph`) the import levels are read
    from it instead, for a walk that goes deep or runs more than once.
    """
    try:
        need = next(walk)
        while True:
            need = walk.send(await _answer(need, session, repo_id, import_graph))
    except StopIteration as done:
        return done.value


async def _answer(
    need: Need, session: AsyncSession, repo_id: str, import_graph: ImportGraph | None
) -> Any:
    keys = list(need.keys)
    if need.kind == "declared":
        return await _edges_from(session, repo_id, keys, ["defines"])
    if need.kind == "callers":
        return await _edges_into(
            session, repo_id, keys, sorted(EXECUTION_EDGE_TYPES), UNRELIABLE_CALL_ORIGINS
        )
    if need.kind == "importers":
        if import_graph is not None:
            return import_graph.edges_into(keys)
        return await _edges_into(
            session, repo_id, keys, sorted(FILE_DEPENDENCY_EDGE_TYPES), frozenset()
        )
    return await _known_files(session, repo_id, keys)


async def call_graph_from_db(session: AsyncSession, repo_id: str) -> CallGraphView:
    """The same view as :func:`call_graph_from_graph`, read from ``graph_edges``.

    The health engine holds the parsed graph in memory; a request handler holds
    a session. Both need the identical view, so this is a loader and not a
    second walk - :func:`files_reached_by_tests` still does the walking, and
    the two callers therefore cannot disagree about which files a test reaches.

    One bulk read rather than the reverse walk's per-level ``IN`` queries. That
    walk is seeded with a change's files; this question is seeded with the
    repository, and a per-level ``IN`` list over every file is the shape it was
    explicitly not built for.

    The view carries no call-site lines: reachability reads only ``declares``
    and the forward/reverse adjacency, and ``call_lines_json`` was most of the
    read's cost (measured on this repository, 189k edges: 8.4 s cold with the
    column, under 2 s without it).
    """
    wanted = ["defines", *sorted(EXECUTION_EDGE_TYPES)]
    params: dict[str, Any] = {"repo_id": repo_id}
    ets = _in_clause("e", wanted, params)
    rows = await session.execute(
        text(
            "SELECT source_node_id, target_node_id, edge_type, resolution_origin "
            "FROM graph_edges WHERE repository_id = :repo_id "
            f"AND edge_type IN ({ets}) "
            "ORDER BY source_node_id, target_node_id, edge_type"
        ),
        params,
    )
    # No call or spawn lines here: test reachability reads only forward/reverse,
    # never first-hop sites, ``spawn_only`` or roles, and a spawned callee is
    # still reached.
    return ExecutionGraphIndex(
        edge_rows=((src, dst, edge_type, origin, None) for src, dst, edge_type, origin in rows)
    )


async def load_test_files(session: AsyncSession, repo_id: str) -> set[str]:
    """Paths of every file node ingestion classified as a test."""
    res = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.is_test == True,  # noqa: E712
        )
    )
    return {row[0] for row in res.all()}


# Answers kept in process per (database, repository, index stamp), so one
# request reads the test files once whoever asks. Ceiling: a per-file reach is
# still walked once per index; persisting it at index time (the engine's
# ``RepoTestIndex.reached_files`` set, stored as a per-file bool) is the upgrade path.
_cache = IndexStampCache(32)


def clear_test_map_cache() -> None:
    _cache.clear()


async def cached_test_files(session: AsyncSession, repo_id: str) -> frozenset[str]:
    """:func:`load_test_files`, read once per index state; frozen because it is shared."""
    key = await _cache.key(session, repo_id, "test_files")
    if key in _cache:
        return _cache.get(key)
    return _cache.remember(key, frozenset(await load_test_files(session, repo_id)))


async def cached_file_paths(session: AsyncSession, repo_id: str) -> frozenset[str]:
    """Every indexed file path, read once per index state."""
    key = await _cache.key(session, repo_id, "file_paths")
    if key in _cache:
        return _cache.get(key)
    res = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
        )
    )
    return _cache.remember(key, frozenset(row[0] for row in res.all()))


async def cached_hub_files(session: AsyncSession, repo_id: str) -> frozenset[str]:
    """:func:`~.hubs.call_hub_files` by the stored call edges, read once per index state."""
    key = await _cache.key(session, repo_id, "hubs")
    if key in _cache:
        return _cache.get(key)
    params: dict[str, Any] = {"repo_id": repo_id}
    ets = _in_clause("e", sorted(EXECUTION_EDGE_TYPES), params)
    bad = _in_clause("o", sorted(UNRELIABLE_CALL_ORIGINS), params)
    rows = await session.execute(
        text(
            "SELECT target_node_id, COUNT(DISTINCT source_node_id) FROM graph_edges "
            f"WHERE repository_id = :repo_id AND edge_type IN ({ets}) "
            f"AND (resolution_origin IS NULL OR resolution_origin NOT IN ({bad})) "
            "GROUP BY target_node_id"
        ),
        params,
    )
    test_files = await cached_test_files(session, repo_id)
    return _cache.remember(key, call_hub_files(dict(rows.all()), test_files))


async def cached_files_reached_by_tests(session: AsyncSession, repo_id: str) -> frozenset[str]:
    """:func:`files_reached_by_tests` over the stored graph, walked once per index state.

    The answer moves only when an index run rewrites the graph, so the bulk
    edge read is paid once per index.
    """
    key = await _cache.key(session, repo_id, "files_reached")
    if key in _cache:
        return _cache.get(key)
    test_files = set(await cached_test_files(session, repo_id))
    view = await call_graph_from_db(session, repo_id)
    return _cache.remember(key, frozenset(files_reached_by_tests(view, test_files)))


#: The ``read_snapshots`` kind that keeps :func:`cached_test_masks` across
#: processes; listed in ``read_snapshots.SELF_KEYED_KINDS``.
MASKS_SNAPSHOT_KIND = "test_masks"


def _masks_snapshot_key(stamp: tuple[Any, ...]) -> str:
    return snapshot_key(MASKS_SNAPSHOT_KIND, stamp)


async def cached_test_masks(session: AsyncSession, repo_id: str) -> dict[str, frozenset[str]]:
    """:func:`~.mocks.replaced_files` for the stored test files, read from the checkout.

    Read once per index state, like the graph it pairs with: from this
    process's cache, else the copy the last index run stored
    (:func:`write_test_masks_snapshot`), else built here. Building reads and
    parses every test file, which is most of a first call's cost on a large
    repository. A test edited since the index is read as indexed, like the
    import edges the masks are applied to; the next update reads the edit.
    No checkout on disk means no masks, so every test keeps its reach.
    """
    key = await _cache.key(session, repo_id, "masks")
    if key in _cache:
        return _cache.get(key)
    stored = await read_snapshot(session, repo_id, MASKS_SNAPSHOT_KIND, _masks_snapshot_key(key[2]))
    if isinstance(stored, dict):
        return _cache.remember(key, {test: frozenset(hits) for test, hits in stored.items()})
    return _cache.remember(key, await _build_test_masks(session, repo_id))


async def write_test_masks_snapshot(session: AsyncSession, repo_id: str) -> bool:
    """Store :func:`cached_test_masks` under the index state it was built from."""

    async def build() -> dict[str, list[str]]:
        masks = await cached_test_masks(session, repo_id)
        return {test: sorted(hits) for test, hits in sorted(masks.items())}

    key = _masks_snapshot_key(await index_stamp(session, repo_id))
    return await refresh_snapshot(session, repo_id, MASKS_SNAPSHOT_KIND, key, build)


async def _build_test_masks(session: AsyncSession, repo_id: str) -> dict[str, frozenset[str]]:
    root = (
        await session.execute(select(Repository.local_path).where(Repository.id == repo_id))
    ).scalar_one_or_none()
    tests = [t for t in await cached_test_files(session, repo_id) if t.endswith(MASKABLE_SUFFIXES)]
    texts = await asyncio.to_thread(_read_texts, root, tests) if root else {}
    files = await _file_paths(session, repo_id) if texts else set()
    return replaced_files(texts, files)


def _read_texts(root: str, paths: list[str]) -> dict[str, str]:
    """``{path: text}`` for the files under *root* that read; none when *root* is gone."""
    base = Path(root)
    if not base.is_dir():
        return {}
    out: dict[str, str] = {}
    for path in paths:
        try:
            out[path] = (base / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


async def _file_paths(session: AsyncSession, repo_id: str) -> set[str]:
    res = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
        )
    )
    return {row[0] for row in res.all()}


async def any_tests_reaching(
    session: AsyncSession,
    repo_id: str,
    targets: Collection[str],
    *,
    rank: bool = True,
) -> dict[str, ReachRow]:
    """The targets some test reaches, or only tests replacing them reach; others are unreached.

    The ``reached`` and ``mocked_only`` rows of :func:`reach_states_from_db`,
    masked by :func:`cached_test_masks`: the call and import tiers
    :func:`tests_reaching_by_tier` runs at its defaults (so the two never
    disagree about a file it answers), the helper and barrel hop, then the
    whole import closure, so absence is a claim. Cached per index state.
    """
    seeds = tuple(sorted(set(targets)))
    cache_tag = "any_reach" if rank else "any_reach_rankfree"
    key = await _cache.key(session, repo_id, cache_tag, seeds)
    if key in _cache:
        return _cache.get(key)
    test_files = set(await cached_test_files(session, repo_id))
    masks = await cached_test_masks(session, repo_id)
    rows = await drive(
        states_walk(list(seeds), test_files, masks=masks, rank=rank), session, repo_id
    )
    found = {path: row for path, row in rows.items() if row.state in ("reached", "mocked_only")}
    return _cache.remember(key, found)


async def any_reached(
    session: AsyncSession, repo_id: str, targets: Collection[str]
) -> dict[str, ReachRow]:
    """Rank-free reached-ness for tab-level views that only need which files are reached."""
    return await any_tests_reaching(session, repo_id, targets, rank=False)


async def reach_states_from_db(
    session: AsyncSession,
    repo_id: str,
    paths: Collection[str],
    *,
    changed_tests: Collection[str] = frozenset(),
    contents: Mapping[str, str | bytes] | None = None,
    masks: Mapping[str, Collection[str]] | None = None,
) -> dict[str, ReachRow]:
    """:func:`~.walk.reach_states` over the stored graph."""
    test_files = set(await cached_test_files(session, repo_id))
    walk = states_walk(
        list(paths), test_files, changed_tests=changed_tests, contents=contents, masks=masks
    )
    return await drive(walk, session, repo_id)


async def tests_reaching_through_helpers(
    session: AsyncSession, repo_id: str, targets: list[str], test_files: set[str]
) -> dict[str, ReachedBy]:
    """:func:`~.walk.helper_walk` over the stored graph."""
    return await drive(helper_walk(targets, test_files), session, repo_id)


async def placed_test_files(session: AsyncSession, repo_id: str) -> set[str]:
    """Test files with a resolved dependency edge into a non-test file of the repository.

    Only these can be found by walking back from a changed file; a test with
    none (every import unresolved, or it only runs a subprocess) is invisible.
    An edge into test material (a conftest, a helper) or a third-party module
    (``external:``) does not count: it says nothing about which of the
    repository's files the test exercises.
    """
    params: dict[str, Any] = {"repo_id": repo_id, "is_test": True, "not_test": False}
    ets = _in_clause("e", sorted(FILE_DEPENDENCY_EDGE_TYPES), params)
    rows = await session.execute(
        text(
            "SELECT DISTINCT e.source_node_id FROM graph_edges e "
            "JOIN graph_nodes s ON s.repository_id = e.repository_id "
            "AND s.node_id = e.source_node_id "
            "JOIN graph_nodes t ON t.repository_id = e.repository_id "
            "AND t.node_id = e.target_node_id "
            f"WHERE e.repository_id = :repo_id AND e.edge_type IN ({ets}) "
            "AND s.is_test = :is_test AND t.is_test = :not_test AND t.node_type = 'file' "
            "AND t.node_id NOT LIKE 'external:%'"
        ),
        params,
    )
    return {row[0] for row in rows}


async def cached_placement(
    session: AsyncSession, repo_id: str
) -> tuple[frozenset[str], dict[str, str], frozenset[str]]:
    """Placed, always-run and unscanned test files, read once per index state.

    Every selection asks all three and each is a scan of the graph, so a
    process serving many calls on one index reads them once. Shared: the
    mapping is copied on the way out, the sets are frozen.
    """
    key = await _cache.key(session, repo_id, "placement")
    if key not in _cache:
        placed = frozenset(await placed_test_files(session, repo_id))
        always = await always_run_test_files(session, repo_id)
        unscanned = frozenset(await unscanned_test_files(session, repo_id))
        _cache.remember(key, (placed, always, unscanned))
    placed, always, unscanned = _cache.get(key)
    return placed, dict(always), unscanned


async def always_run_test_files(session: AsyncSession, repo_id: str) -> dict[str, str]:
    """``{test file: why}`` for the tests the indexer found the graph cannot see into.

    Stored per file at index time (``repowise.core.ingestion.always_run``), so
    this is one indexed read, never a scan of the test files.
    """
    res = await session.execute(
        select(GraphNode.node_id, GraphNode.always_run_reason).where(
            GraphNode.repository_id == repo_id,
            GraphNode.always_run_reason.is_not(None),
            GraphNode.always_run_reason != "",
        )
    )
    return {node_id: reason for node_id, reason in res.all()}


async def unscanned_test_files(session: AsyncSession, repo_id: str) -> set[str]:
    """Judged test files the indexer never stamped (an index built before the check).

    A stamped test holds ``""`` when it is ordinary, so ``NULL`` means unknown,
    not "nothing found": such a test may walk the tree unseen.
    """
    from repowise.core.ingestion.always_run import is_judged_test

    res = await session.execute(
        select(GraphNode.node_id).where(
            GraphNode.repository_id == repo_id,
            GraphNode.node_type == "file",
            GraphNode.is_test == True,  # noqa: E712
            GraphNode.always_run_reason.is_(None),
        )
    )
    return {node_id for (node_id,) in res.all() if is_judged_test(node_id)}


async def dependency_path(
    session: AsyncSession, repo_id: str, source: str, targets: Collection[str], max_depth: int = 64
) -> list[str]:
    """The shortest file-dependency route from *source* to any of *targets*, ends included.

    ``[]`` when none is found within *max_depth* hops. Breadth-first, one ``IN``
    query per level, over the same edges the import walk follows
    (``FILE_DEPENDENCY_EDGE_TYPES``, every resolution origin). Ceiling: a test
    found through the call walk alone may have no such route; following call
    edges with ``UNRELIABLE_CALL_ORIGINS`` excluded is the upgrade path.
    """
    goals = set(targets)
    parent: dict[str, str | None] = {source: None}
    frontier = [source]
    for _ in range(max_depth):
        if not frontier or goals & parent.keys():
            break
        level, frontier = frontier, []
        for dependent, dependency in await _edges_from(
            session, repo_id, level, sorted(FILE_DEPENDENCY_EDGE_TYPES)
        ):
            if dependency not in parent:
                parent[dependency] = dependent
                frontier.append(dependency)
    hit = goals & parent.keys()
    if not hit:
        return []
    route, node = [], min(hit)
    while node is not None:
        route.append(node)
        node = parent[node]
    return route[::-1]


async def tests_reaching(
    session: AsyncSession,
    repo_id: str,
    targets: list[str],
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
    test_files: set[str] | None = None,
) -> dict[str, list[str]]:
    """Test files that reach each of *targets*, keyed by target path.

    Targets nothing reaches are absent from the result. An empty dict is
    "no test reaches these"; the caller decides whether that reads as untested
    or as unknown.
    """
    found = await tests_reaching_by_tier(
        session,
        repo_id,
        targets,
        call_depth=call_depth,
        import_depth=import_depth,
        test_files=test_files,
    )
    return {target: reached.tests for target, reached in found.items()}


async def tests_reaching_by_tier(
    session: AsyncSession,
    repo_id: str,
    targets: list[str],
    *,
    call_depth: int = DEFAULT_CALL_DEPTH,
    import_depth: int = DEFAULT_MAX_DEPTH,
    symbol_seeds: Mapping[str, Collection[str]] | None = None,
    test_files: set[str] | None = None,
    avoid: Collection[str] = frozenset(),
    import_graph: ImportGraph | None = None,
    hubs: Collection[str] = frozenset(),
) -> dict[str, ReachedBy]:
    """:func:`tests_reaching`, also saying which tier answered each target.

    *hubs* (:func:`cached_hub_files`) tags the call-tier tests that reach a
    target only through a hub, and lists them last (``ReachedBy.transitive``).

    *test_files* is :func:`load_test_files`'s answer, for a caller that walks
    more than once and need not read it twice. A target may be a symbol id
    (``path::name``); its tests are ranked against the file part.

    *symbol_seeds* narrows the call walk for the targets it names: the walk
    enters at exactly those symbol ids instead of at every symbol the file
    declares. A target it does not name, or names with no ids, keeps the
    ``defines`` lookup, and the
    import tier stays file-level either way.

    *avoid* names files the call walk does not pass reach through, except
    into a target's own symbols. The import tier is
    one hop, so it never passes through anything.

    *import_graph* (:func:`load_import_graph`) answers the import tier's
    levels from memory, for a caller that walks deep or more than once.

    The walk is :func:`~.walk.tier_walk` without the helper hop: the call walk
    first, then the import walk seeded with only the targets it left
    unanswered, so the weaker tier never speaks over the stronger one and
    costs nothing where the stronger one already spoke.

    Both walks are attributed: they run backwards from the targets carrying the
    seed each visited node came from, so the answer says *which* target each
    test guards rather than only that some test does. That is why these are
    separate walks rather than a filter over the forward one, and it stays cheap
    because the seed set is a change's files, not the repository - one ``IN``
    query per level, which is the shape ``pr_blast._transitive_affected``
    already uses.
    """
    if not any(targets):
        return {}
    if test_files is None:
        test_files = await cached_test_files(session, repo_id)
    walk = tier_walk(
        list(targets),
        test_files,
        call_depth=call_depth,
        import_depth=import_depth,
        symbol_seeds=symbol_seeds,
        avoid=avoid,
        hubs=hubs,
    )
    return await drive(walk, session, repo_id, import_graph=import_graph)


async def reach_into_symbols(
    session: AsyncSession,
    repo_id: str,
    symbol_ids: Collection[str],
    test_files: set[str],
    *,
    max_depth: int = DEFAULT_CALL_DEPTH,
    avoid: Collection[str] = frozenset(),
) -> dict[str, dict[str, ReachDistance]]:
    """Tests that call into each symbol id, keyed by the symbol, with their hops.

    The file walk answers "does a test reach this file"; this one answers how
    close each test gets to one symbol in it, which is what decides which of a
    file's many tests to run first after changing that symbol.
    """
    seeds = sorted({symbol for symbol in symbol_ids if symbol})
    if not seeds or not test_files:
        return {}
    walk = call_walk(
        seeds,
        test_files,
        max_depth,
        symbol_seeds={symbol: (symbol,) for symbol in seeds},
        avoid=avoid,
    )
    return await drive(walk, session, repo_id)


async def imported_names_by_test(
    session: AsyncSession, repo_id: str, files: Collection[str], test_files: set[str]
) -> dict[str, dict[str, frozenset[str]]]:
    """The names each test file imports from each of *files*, keyed by file then test.

    An empty set is an import of the module itself. One hop, plus one through a
    re-export barrel (``index.ts``, ``__init__.py``) that imports the file: a
    test importing ``foo`` from the barrel imports it from the file. What a test
    names, not what it reaches.
    """
    targets = sorted({path for path in files if path})
    if not targets or not test_files:
        return {}
    out: dict[str, dict[str, frozenset[str]]] = {}
    barrels: dict[str, dict[str, frozenset[str]]] = {}
    for source, target, names in await _imported_names(session, repo_id, targets):
        if source in test_files:
            _add_names(out, target, source, names)
        elif PurePosixPath(source).name in BARREL_FILENAMES:
            barrels.setdefault(source, {})[target] = names
    if barrels:
        await _add_barrel_imports(session, repo_id, barrels, test_files, out)
    return out


async def _add_barrel_imports(
    session: AsyncSession,
    repo_id: str,
    barrels: dict[str, dict[str, frozenset[str]]],
    test_files: set[str],
    out: dict[str, dict[str, frozenset[str]]],
) -> None:
    """Credit each test importing a barrel with the names the barrel passes on from each file."""
    for source, barrel, names in await _imported_names(session, repo_id, sorted(barrels)):
        if source not in test_files:
            continue
        for target, exported in barrels[barrel].items():
            # ``export *`` passes every name on; otherwise only the ones it re-exports.
            passed = names if not exported or "*" in exported else names & exported
            if passed or not names:
                _add_names(out, target, source, passed)


def _add_names(
    out: dict[str, dict[str, frozenset[str]]], target: str, test: str, names: frozenset[str]
) -> None:
    by_test = out.setdefault(target, {})
    by_test[test] = by_test.get(test, frozenset()) | names


async def _imported_names(
    session: AsyncSession, repo_id: str, targets: list[str]
) -> list[tuple[str, str, frozenset[str]]]:
    """``(importer, imported file, names)`` for every import edge into *targets*."""
    shared: dict[str, Any] = {"repo_id": repo_id}
    ets = _in_clause("e", sorted(FILE_DEPENDENCY_EDGE_TYPES), shared)
    targets = list(dict.fromkeys(targets))
    out = []
    for start in range(0, len(targets), _IN_BATCH):
        params = dict(shared)
        tgt = _in_clause("p", targets[start : start + _IN_BATCH], params)
        rows = await session.execute(
            text(
                "SELECT source_node_id, target_node_id, imported_names_json FROM graph_edges "
                "WHERE repository_id = :repo_id "
                f"AND target_node_id IN ({tgt}) AND edge_type IN ({ets})"
            ),
            params,
        )
        out.extend((source, target, _names_of(names_json)) for source, target, names_json in rows)
    return out


def _names_of(names_json: str | None) -> frozenset[str]:
    """The names an import edge records; unreadable JSON records none."""
    try:
        names = json.loads(names_json or "[]")
    except (TypeError, ValueError):
        return frozenset()
    return frozenset(n for n in names if isinstance(n, str))


async def load_import_graph(session: AsyncSession, repo_id: str) -> ImportGraph:
    """The edges the import walk follows (``_edges_into`` over every file), in one read."""
    params: dict[str, Any] = {"repo_id": repo_id}
    ets = _in_clause("e", sorted(FILE_DEPENDENCY_EDGE_TYPES), params)
    rows = await session.execute(
        text(
            "SELECT DISTINCT source_node_id, target_node_id FROM graph_edges "
            f"WHERE repository_id = :repo_id AND edge_type IN ({ets}){_LEGACY_CONFTEST_FILTER}"
        ),
        params,
    )
    importers: dict[str, list[str]] = {}
    for source, target in rows:
        importers.setdefault(target, []).append(source)
    # Sorted, so a bounded walk does not depend on the order rows come back in.
    return ImportGraph({t: tuple(sorted(s)) for t, s in importers.items()})


async def cached_import_graphs(
    session: AsyncSession, repo_id: str
) -> tuple[ImportGraph, ImportGraph]:
    """:func:`load_import_graph` and its :meth:`~ImportGraph.with_package_inits`,
    read once per index state. Shared, so neither is changed by a reader."""
    key = await _cache.key(session, repo_id, "import_graphs")
    if key in _cache:
        return _cache.get(key)
    graph = await load_import_graph(session, repo_id)
    return _cache.remember(key, (graph, graph.with_package_inits()))


async def direct_dependents(
    session: AsyncSession, repo_id: str, files: list[str]
) -> dict[str, set[str]]:
    """Files with an import edge into each of *files*, or a call into a symbol it declares.

    One hop, no tests excluded: the direct callers and importers a change to
    them could have moved coverage through. A file with none is absent.
    """
    out: dict[str, set[str]] = {}
    for dependent, dependency in await _edges_into(
        session, repo_id, files, sorted(FILE_DEPENDENCY_EDGE_TYPES), frozenset()
    ):
        out.setdefault(dependency, set()).add(dependent)
    declared = await _edges_from(session, repo_id, files, ["defines"])
    owner = {symbol: path for path, symbol in declared}
    for caller, callee in await _edges_into(
        session, repo_id, sorted(owner), sorted(EXECUTION_EDGE_TYPES), UNRELIABLE_CALL_ORIGINS
    ):
        if (source := file_of_symbol(caller)) != owner[callee]:
            out.setdefault(owner[callee], set()).add(source)
    return out


# See :data:`~.maps.LEGACY_CONFTEST_HINT`.
_LEGACY_CONFTEST_FILTER = (
    " AND NOT (edge_type = 'dynamic_uses' "
    f"AND COALESCE(hint_source, '') = '{LEGACY_CONFTEST_HINT}')"
)


#: Edges a test file writes itself: imports and references, not the wiring a
#: test runner adds (a conftest or setup file reaches every test under it).
_AUTHORED_EDGE_TYPES = sorted(FILE_DEPENDENCY_EDGE_TYPES - {"framework"})


async def direct_importers(session: AsyncSession, repo_id: str, files: list[str]) -> set[str]:
    """Files whose own code imports or references one of *files*, one hop."""
    rows = await _edges_into(session, repo_id, files, _AUTHORED_EDGE_TYPES, frozenset())
    return {source for source, _ in rows}


async def _known_files(session: AsyncSession, repo_id: str, paths: list[str]) -> set[str]:
    """The subset of *paths* the index holds a node for."""
    out: set[str] = set()
    for start in range(0, len(paths), _IN_BATCH):
        batch = paths[start : start + _IN_BATCH]
        res = await session.execute(
            select(GraphNode.node_id).where(
                GraphNode.repository_id == repo_id,
                GraphNode.node_type == "file",
                GraphNode.node_id.in_(batch),
            )
        )
        out.update(row[0] for row in res.all())
    return out


def _in_clause(prefix: str, values: list[str], params: dict[str, Any]) -> str:
    """Bind *values* as ``:prefix0, :prefix1, ...`` and return the clause body."""
    params.update({f"{prefix}{i}": v for i, v in enumerate(values)})
    return ",".join(f":{prefix}{i}" for i in range(len(values)))


#: Values one ``IN`` list binds. SQLite refuses a statement with more variables
#: than SQLITE_MAX_VARIABLE_NUMBER (999 before 3.32, 32766 after), and the lists
#: below are a change's files or every file and symbol of a repository.
_IN_BATCH = 500


async def _edges_from(
    session: AsyncSession, repo_id: str, sources: list[str], edge_types: list[str]
) -> list[tuple[str, str]]:
    """``(source, target)`` pairs leaving *sources* along *edge_types*."""
    shared: dict[str, Any] = {"repo_id": repo_id}
    ets = _in_clause("e", edge_types, shared)
    out: list[tuple[str, str]] = []
    # Each source is in one batch only, so a pair is distinct across batches too.
    sources = list(dict.fromkeys(sources))
    for start in range(0, len(sources), _IN_BATCH):
        params = dict(shared)
        src = _in_clause("s", sources[start : start + _IN_BATCH], params)
        rows = await session.execute(
            text(
                "SELECT DISTINCT source_node_id, target_node_id FROM graph_edges "
                "WHERE repository_id = :repo_id "
                f"AND source_node_id IN ({src}) AND edge_type IN ({ets})"
            ),
            params,
        )
        out.extend((s, t) for s, t in rows)
    return out


async def _edges_into(
    session: AsyncSession,
    repo_id: str,
    targets: list[str],
    edge_types: list[str],
    excluded_origins: frozenset[str],
) -> list[tuple[str, str]]:
    """``(source, target)`` pairs arriving at *targets* along *edge_types*.

    Raw text SQL with an ``IN`` list, matching ``pr_blast._transitive_affected``:
    the edge table is the one place a per-level parameterised ``IN`` beats
    loading every row, and the two walks should not disagree about how they read
    it.
    """
    shared: dict[str, Any] = {"repo_id": repo_id}
    ets = _in_clause("e", edge_types, shared)
    origin_filter = ""
    if excluded_origins:
        # NULL means the row predates the vocabulary, not "unknown", so it has
        # to survive the filter, and a bare NOT IN would drop it.
        bad = _in_clause("o", sorted(excluded_origins), shared)
        origin_filter = f" AND (resolution_origin IS NULL OR resolution_origin NOT IN ({bad}))"
    out: list[tuple[str, str]] = []
    # Each target is in one batch only, so a pair is distinct across batches too.
    targets = list(dict.fromkeys(targets))
    for start in range(0, len(targets), _IN_BATCH):
        params = dict(shared)
        tgt = _in_clause("p", targets[start : start + _IN_BATCH], params)
        rows = await session.execute(
            text(
                "SELECT DISTINCT source_node_id, target_node_id FROM graph_edges "
                "WHERE repository_id = :repo_id "
                f"AND target_node_id IN ({tgt}) AND edge_type IN ({ets}){origin_filter}"
                f"{_LEGACY_CONFTEST_FILTER}"
            ),
            params,
        )
        out.extend((s, t) for s, t in rows)
    return out
