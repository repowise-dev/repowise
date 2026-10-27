"""The edges a reach walk reads, held in memory, and the loop that feeds them to it.

A walk (:mod:`.walk`) never reads edges itself. It yields a :class:`Need` - "the
reliable callers of these symbols", "the importers of these files" - and is sent
the rows back. :func:`run` answers from a :class:`WalkMaps` built from a graph
held in memory; the database adapter answers the same needs with one query per
level. Both drive the one walk, so the two cannot disagree about a row.
"""

from __future__ import annotations

from collections.abc import Collection, Generator, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal, NamedTuple, TypeVar

from repowise.core.ingestion.models import EXECUTION_EDGE_TYPES, FILE_DEPENDENCY_EDGE_TYPES
from repowise.core.test_paths import is_test_related_path

from .hubs import call_hub_files
from .model import UNRELIABLE_CALL_ORIGINS

NeedKind = Literal["declared", "callers", "importers", "known"]


class Need(NamedTuple):
    """What a walk asks for next.

    ``declared``: ``(file, symbol)`` for each symbol the files define.
    ``callers``: ``(caller, callee)`` along reliable execution edges into the nodes.
    ``importers``: ``(dependent, dependency)`` along file-dependency edges into the files.
    ``known``: the subset of the paths the index holds a node for.
    """

    kind: NeedKind
    keys: tuple[str, ...]


_T = TypeVar("_T")
Walk = Generator[Need, Any, _T]

# The conftest -> test hint older indexes stored made a conftest read as each
# test's importer; the test -> conftest edge already records the relation.
# Upgrade path: drop this once such indexes are rebuilt.
LEGACY_CONFTEST_HINT = "pytest_conftest"


def is_legacy_conftest_hint(edge_type: str | None, hint_source: str | None) -> bool:
    return edge_type == "dynamic_uses" and hint_source == LEGACY_CONFTEST_HINT


@dataclass(frozen=True)
class ImportGraph:
    """Every file-dependency edge, reversed (``{file: files depending on it}``), read once.

    The import walk is unbounded on the selection path, so on a hub it runs
    dozens of levels; one bulk read replaces a query per level. Either source
    gives the same answer: the walk reaches a fixpoint, so edge order does not
    matter. Ceiling: read per selection, not kept across calls; caching it per
    index stamp is the upgrade path if the read shows up.
    """

    importers: Mapping[str, tuple[str, ...]]

    def edges_into(self, targets: Collection[str]) -> list[tuple[str, str]]:
        """``(dependent, dependency)`` pairs, as a ``importers`` :class:`Need` is answered."""
        return [(src, t) for t in targets for src in self.importers.get(t, ())]

    def hops(self, sources: Collection[str]) -> dict[str, int]:
        """``{file: fewest dependency hops from any of *sources*}``, sources at 0.

        One multi-source breadth-first pass through every file, tests
        included, so a test importing a reached helper is one hop past it.
        """
        best = dict.fromkeys(sources, 0)
        frontier = list(best)
        depth = 0
        while frontier:
            depth += 1
            fresh = [
                d for node in frontier for d in self.importers.get(node, ()) if d not in best
            ]
            fresh = list(dict.fromkeys(fresh))
            best.update(dict.fromkeys(fresh, depth))
            frontier = fresh
        return best

    def with_package_inits(self) -> ImportGraph:
        """This graph plus the edge every Python import adds: a module depends on each
        production ``__init__.py`` above it.

        Importing ``pkg.a.b`` runs ``pkg/__init__.py`` and ``pkg/a/__init__.py``
        first, so whatever those import reaches every test importing anything
        under ``pkg``. No import statement records that, so the graph holds no
        such edge. Every ancestor counts, whether or not the directories between
        are regular packages (a namespace subpackage still runs the regular
        package above it): over-selection, never a missed test. A test
        package's ``__init__.py`` is left out; selection already stands it for
        every test under its directory.
        """
        files = set(self.importers) | {s for srcs in self.importers.values() for s in srcs}
        inits = {
            f for f in files
            if PurePosixPath(f).name == "__init__.py" and not is_test_related_path(f)
        }  # fmt: skip
        if not inits:
            return self
        out = {t: list(s) for t, s in self.importers.items()}
        for f in files:
            if not f.endswith(".py"):
                continue
            for parent in PurePosixPath(f).parents:
                init = (parent / "__init__.py").as_posix()
                if init in inits and init != f:
                    out.setdefault(init, []).append(f)
        return ImportGraph({t: tuple(sorted(set(s))) for t, s in out.items()})


@dataclass(frozen=True)
class WalkMaps:
    """The adjacency a reach walk reads, from :func:`build_walk_maps`.

    ``declares`` maps a file to the symbols it defines, ``callers`` a node to the
    nodes with a reliable execution edge into it, ``imports`` a file to the
    files depending on it. ``files`` are the file nodes and ``test_files`` the
    ones ingestion stored ``is_test`` on.
    """

    declares: Mapping[str, tuple[str, ...]]
    callers: Mapping[str, tuple[str, ...]]
    imports: ImportGraph
    files: frozenset[str]
    test_files: frozenset[str]

    def hubs(self) -> frozenset[str]:
        """:func:`~.hubs.call_hub_files` by these maps' call fan-in."""
        return call_hub_files({n: len(c) for n, c in self.callers.items()}, self.test_files)

    def answer(self, need: Need) -> Any:
        if need.kind == "declared":
            return [(f, s) for f in need.keys for s in self.declares.get(f, ())]
        if need.kind == "callers":
            return [(c, n) for n in need.keys for c in self.callers.get(n, ())]
        if need.kind == "importers":
            return self.imports.edges_into(need.keys)
        return {path for path in need.keys if path in self.files}


def run(walk: Walk[_T], maps: WalkMaps) -> _T:
    """Drive *walk* to its answer from *maps*."""
    try:
        need = next(walk)
        while True:
            need = walk.send(maps.answer(need))
    except StopIteration as done:
        return done.value


def build_walk_maps(graph: Any) -> WalkMaps:
    """:class:`WalkMaps` from a NetworkX graph, or the node-link dict it serialises to.

    Nodes carry ``node_type`` and ``is_test``; edges carry ``edge_type`` and,
    for calls, ``resolution_origin``. Edges are kept the way the stored graph's
    reader keeps them: calls resolved by a name guess
    (:data:`UNRELIABLE_CALL_ORIGINS`) are dropped. ``None`` gives empty maps.
    """
    if isinstance(graph, Mapping):
        import networkx as nx

        key = "edges" if "edges" in graph else "links"
        graph = nx.node_link_graph(graph, edges=key)
    files: set[str] = set()
    tests: set[str] = set()
    declares: dict[str, set[str]] = {}
    callers: dict[str, set[str]] = {}
    importers: dict[str, set[str]] = {}
    if graph is not None:
        for node, attrs in graph.nodes(data=True):
            if attrs.get("node_type") == "file":
                files.add(node)
                if attrs.get("is_test"):
                    tests.add(node)
        for source, target, attrs in graph.edges(data=True):
            edge_type = attrs.get("edge_type")
            if edge_type == "defines":
                declares.setdefault(source, set()).add(target)
            if (
                edge_type in EXECUTION_EDGE_TYPES
                and attrs.get("resolution_origin") not in UNRELIABLE_CALL_ORIGINS
            ):
                callers.setdefault(target, set()).add(source)
            if edge_type in FILE_DEPENDENCY_EDGE_TYPES and not is_legacy_conftest_hint(
                edge_type, attrs.get("hint_source")
            ):
                importers.setdefault(target, set()).add(source)
    # Sorted, so a bounded walk does not depend on the order edges were added in.
    return WalkMaps(
        declares={k: tuple(sorted(v)) for k, v in declares.items()},
        callers={k: tuple(sorted(v)) for k, v in callers.items()},
        imports=ImportGraph({k: tuple(sorted(v)) for k, v in importers.items()}),
        files=frozenset(files),
        test_files=frozenset(tests),
    )
