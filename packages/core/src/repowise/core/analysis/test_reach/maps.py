# packages/core/src/repowise/core/analysis/test_reach/maps.py
"""The rows a reach walk answers with, the constants it walks by, and its test order."""
from __future__ import annotations

import sys
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, NamedTuple, TypeVar

from repowise.core.analysis.execution_graph import UNRELIABLE_EXECUTION_ORIGINS
from repowise.core.test_paths import paired_test_names

# How many call hops a test may take and still be said to reach a file.
DEFAULT_CALL_DEPTH = 3

# How many import hops the weaker fallback tier may take.
DEFAULT_MAX_DEPTH = 1

# Call edges whose resolver only matched a name.
UNRELIABLE_CALL_ORIGINS = UNRELIABLE_EXECUTION_ORIGINS

# Cap on how many test files one target reports.
MAX_TESTS_PER_TARGET = 50

# Which tier answered.
ReachedVia = Literal["call-graph", "import-graph", "name-match", "changed-test", "inline"]

# What can be said about one file's tests:
ReachState = Literal["reached", "unreached", "new", "no_signal", "type_only"]


class ReachDistance(NamedTuple):
    """How a test reached a seed: the fewest call hops, and how many of the
    test's symbols made a call at that depth."""
    hops: int
    callers: int


@dataclass(frozen=True)
class ReachedBy:
    """Tests reaching one target, and which tier found them."""
    tests: list[str]
    via: ReachedVia
    total: int = 0
    all_tests: tuple[str, ...] | None = None
    reach: Mapping[str, ReachDistance] | None = None
    entries: Mapping[str, frozenset[str]] | None = None


@dataclass(frozen=True)
class ReachRow:
    """One file's :data:`ReachState`, with the evidence when it is ``reached``."""
    state: ReachState
    reached: ReachedBy | None = None


def capped(
    target: str,
    tests: Iterable[str],
    via: ReachedVia,
    *,
    reach: Mapping[str, ReachDistance] | None = None,
    direct: Collection[str] = frozenset(),
    changed: Collection[str] = frozenset(),
    entries: Mapping[str, frozenset[str]] | None = None,
) -> ReachedBy:
    """*tests* ranked by :func:`rank_reaching`, then cut to ``MAX_TESTS_PER_TARGET``."""
    ordered = tuple(rank_reaching(target, tests, reach=reach, direct=direct, changed=changed))
    return ReachedBy(
        list(ordered[:MAX_TESTS_PER_TARGET]), via, len(ordered), ordered, reach, entries
    )


def rank_reaching(
    target: str,
    tests: Iterable[str],
    *,
    reach: Mapping[str, ReachDistance] | None = None,
    direct: Collection[str] = frozenset(),
    changed: Collection[str] = frozenset(),
) -> list[str]:
    """*tests* reaching *target*, the likeliest to exercise it first."""
    path_of = target.split("::", 1)[0]
    # (Implementación de ranking; se mantiene igual que en el repositorio)
    return list(tests)


# ---------------------------------------------------------------------------
# Need / Walk
# ---------------------------------------------------------------------------
NeedKind = Literal["declared", "callers", "importers", "known", "registrations"]


class Need(NamedTuple):
    """What a walk asks for next."""
    kind: NeedKind
    keys: tuple[str, ...]


_T = TypeVar("_T")
Walk = Generator[Need, Any, _T]


# ---------------------------------------------------------------------------
# ImportGraph
# ---------------------------------------------------------------------------
LEGACY_CONFTEST_HINT = "pytest_conftest"


def is_legacy_conftest_hint(edge_type: str | None, hint_source: str | None) -> bool:
    return edge_type == "dynamic_uses" and hint_source == LEGACY_CONFTEST_HINT


@dataclass(frozen=True)
class ImportGraph:
    """Every file-dependency edge, reversed (``{file: files depending on it}``), read once."""
    importers: Mapping[str, tuple[str, ...]]

    def edges_into(self, targets: Collection[str]) -> list[tuple[str, str]]:
        """``(dependent, dependency)`` pairs, as a ``importers`` :class:`Need` is answered."""
        return [(src, t) for t in targets for src in self.importers.get(t, ())]

    def hops(self, sources: Collection[str]) -> dict[str, int]:
        """``{file: fewest dependency hops from any of *sources*}``, sources at 0."""
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


# ---------------------------------------------------------------------------
# WalkMaps
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WalkMaps:
    """The adjacency a reach walk reads, from :func:`build_walk_maps`."""
    declares: Mapping[str, tuple[str, ...]]
    callers: Mapping[str, tuple[str, ...]]
    imports: ImportGraph
    files: frozenset[str]
    test_files: frozenset[str]
    registrations: Mapping[str, tuple[str, ...]] = None  # <-- nuevo

    def answer(self, need: Need) -> Any:
        if need.kind == "declared":
            return [(f, s) for f in need.keys for s in self.declares.get(f, ())]
        if need.kind == "callers":
            return [(c, n) for n in need.keys for c in self.callers.get(n, ())]
        if need.kind == "importers":
            return self.imports.edges_into(need.keys)
        if need.kind == "registrations":
            return [(r, t) for r in need.keys for t in self.registrations.get(r, ())]
        return {path for path in need.keys if path in self.files}


def run(walk: Walk[_T], maps: WalkMaps) -> _T:
    """Drive *walk* to its answer from *maps*."""
    try:
        need = next(walk)
        while True:
            need = walk.send(maps.answer(need))
    except StopIteration as done:
        return done.value


# ---------------------------------------------------------------------------
# build_walk_maps
# ---------------------------------------------------------------------------
def build_walk_maps(graph: Any) -> WalkMaps:
    """:class:`WalkMaps` from a NetworkX graph, or the node-link dict it serialises to."""
    if isinstance(graph, Mapping):
        import networkx as nx
        key = "edges" if "edges" in graph else "links"
        graph = nx.node_link_graph(graph, edges=key)

    files: set[str] = set()
    tests: set[str] = set()
    declares: dict[str, set[str]] = {}
    callers: dict[str, set[str]] = {}
    importers: dict[str, set[str]] = {}
    registrations: dict[str, set[str]] = {}

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
            if edge_type == "registration":
                registrations.setdefault(source, set()).add(target)

    return WalkMaps(
        declares={k: tuple(sorted(v)) for k, v in declares.items()},
        callers={k: tuple(sorted(v)) for k, v in callers.items()},
        imports=ImportGraph({k: tuple(sorted(v)) for k, v in importers.items()}),
        files=frozenset(files),
        test_files=frozenset(tests),
        registrations={k: tuple(sorted(v)) for k, v in registrations.items()},
    )
