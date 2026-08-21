"""One answer to "can anything reach this file?".

Two passes ask that question and used to answer it separately. The dead-code
analyzer asks so it can report a file nobody imports as ``unreachable_file``;
the repo-overview assembler asks so it can drop an execution flow whose entry
point is that same file. Execution-flow scoring treats zero inbound calls as
its *strongest positive* signal — a symbol nothing calls looks like a front
door — so the identical fact promoted a file to Primary Execution Flow #1 and
listed it as dead code, on one index, from one piece of evidence.

The assembler could not call the analyzer's version: it is a method reading
six attributes built during analysis. So it hand-copied the barrel filename
set and carried a list of the languages where the analyzer was known to be
kinder, standing in for per-language helpers it could not reach. This module
is that predicate with its state passed in, so both callers get one answer.

Leaf in its own imports: the three per-language reachability helpers,
:mod:`constants` for the never-flag globs, and :mod:`repowise.core.ids`.
Nothing else from either subsystem. It does not reach the analyzer, and in
particular it holds none of the analysis-time state that made the analyzer's
version uncallable, which is the coupling that mattered — ``constants`` is a
sibling of the analyzer, not the analyzer, and its only non-stdlib import is
the language registry.

It is not a free import, though, and the difference is worth stating rather
than implying. ``dead_code/__init__`` eagerly re-exports ``DeadCodeAnalyzer``,
so importing this module from generation executes that package init and loads
the analyzer with it: 12 extra modules, and under 10 ms, which is inside the
run-to-run spread of the roughly 550 ms assembler import it sits on top of.
Ceiling, accepted at that price. The
upgrade path if it ever stops being worth it is to move this module and the
three per-language helpers up to :mod:`repowise.core.analysis`, which none of
them has a reason to sit below; making the package init lazy would also work
but costs mypy the types of everything the package re-exports.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from repowise.core.ids import SYMBOL_SEP, file_path_of, is_external

from .constants import never_flag_match
from .cpp_reachability import build_cpp_package_files, is_cpp_file_reachable, is_cpp_path
from .go_reachability import build_go_package_files, is_go_file_reachable
from .jvm_reachability import build_jvm_package_files, is_jvm_file_reachable

#: Re-export barrel filenames. A barrel aggregates other modules' symbols and
#: is reached by importing those names (or, for a package's public entry, via
#: package.json ``exports``/``main``), so a barrel with no inbound graph edge
#: is not unreachable.
#:
#: Scope note for the analyzer: this rescues a file in the *unreachable-file*
#: pass only. It is NOT applied in the unused-export pass — a genuine symbol
#: defined in a barrel that nobody imports should still be flagged.
BARREL_FILENAMES: frozenset[str] = frozenset(
    {
        "__init__.py",
        "index.ts",
        "index.tsx",
        "index.js",
        "index.jsx",
        "index.mts",
        "index.cts",
        "index.mjs",
        "index.cjs",
        "mod.rs",
    }
)

#: Edge types that record association rather than dependency. A co-change edge
#: says two files were committed together, which is not a path any code can
#: take, so it must not answer "does anything import this".
_NON_DEPENDENCY_EDGES: frozenset[str] = frozenset({"co_changes"})

_JVM_SUFFIXES: tuple[str, ...] = (".java", ".kt")


@dataclass(frozen=True)
class PackageFileMap:
    """Package-directory → member-file maps for the package-granular languages.

    Built from the graph alone (see the ``build_*_package_files`` helpers), so
    any caller holding a graph can supply it. One object rather than three
    arguments because the predicate needs whichever map matches the path and a
    caller has no reason to supply a subset.
    """

    go: dict[str, list[str]] = field(default_factory=dict)
    jvm: dict[str, list[str]] = field(default_factory=dict)
    cpp: dict[str, list[str]] = field(default_factory=dict)


def build_package_file_map(graph: Any) -> PackageFileMap:
    """Build all three package maps in one call.

    Each builder is one pass over the node ids testing a filename suffix, so
    building all three on a repo that uses none of the languages costs three
    scans and yields three empty dicts. Callers cache the result rather than
    building per candidate.
    """
    return PackageFileMap(
        go=build_go_package_files(graph),
        jvm=build_jvm_package_files(graph),
        cpp=build_cpp_package_files(graph),
    )


@dataclass(frozen=True)
class ReachabilityRescues:
    """Evidence, built by the caller, that rescues a file with no importer.

    ``package_files`` is the one field whose absence changes the answer rather
    than narrowing it. Go, JVM and C/C++ imports name a *package*, so most
    files in a live package carry no file-level in-edge at all; a caller
    without the map would call an entire language unreachable. ``None`` means
    "not checked", and the predicate resolves an unchecked package-granular
    file to reachable — the forgiving direction, since a missed drop leaves
    one wrong flow on a page while an over-eager drop silently empties the
    section. Both production callers now supply the map, so that default is a
    safety net for a caller that cannot, not a stance either of them takes.

    ``whitelist`` is the path-exact exemption list from ``analyze()``'s config
    argument. It is here so the analyzer's unreachable-files pass has *no*
    reachability limb left of its own; no CLI path populates it today, so it is
    empty in every shipped run. The assembler has no config to read and passes
    none, which makes it narrower by exactly the paths an API caller listed.

    ``bundler_alias_targets`` is a narrow allowlist — a handful of shim paths
    per repo — and defaults to empty. A caller that omits it is stricter than
    the analyzer for exactly those paths, and no wider.

    Ceiling, deliberate: ``bundler_alias_targets`` is still caller-supplied
    because finding it needs a source scan of the bundler configs, which this
    module has no business doing. The never-flag glob set used to be listed
    here too; it is a pure function of a path, so it moved into
    :mod:`constants` and the predicate now applies it to every caller. What
    must *not* happen is widening the defaults into a blanket rescue: that
    would answer "reachable" for every unimported TS/JS file and stop this
    being a predicate.
    """

    bundler_alias_targets: AbstractSet[str] = frozenset()
    whitelist: AbstractSet[str] = frozenset()
    package_files: PackageFileMap | None = None


#: Prefix of an :class:`repowise.core.ids.ExternalId`, matched textually rather
#: than through ``ids.parse``. Every in-edge of every candidate goes through
#: the tests below, and ``parse`` allocates a dataclass per call: on a 175k-node
#: graph that one construction was most of a 13x slowdown against the
#: ``in_degree`` read this replaced. Rendered form is pinned by
#: ``tests/unit/test_ids.py``, and ``analyzer._is_synthetic_node`` already
#: matches the same two prefixes textually.
_EXTERNAL_PREFIX = "external:"


def _is_external_package(node: str) -> bool:
    """Third-party code we merely name, as opposed to a framework anchor."""
    return node.startswith(_EXTERNAL_PREFIX)


def is_foreign_edge(node: str, path: str) -> bool:
    """Whether a graph neighbour is another file rather than *path* itself.

    Graph node ids are either a file path or ``<file path>::<symbol>``. Both
    spellings of "this file" are dropped so a dependency list only ever names
    other files.
    """
    if is_external(node):
        return False
    return node != path and file_path_of(node) != path


def _is_structural_edge(neighbor: str, path: str, edge_data: Mapping[str, Any], graph: Any) -> bool:
    """Whether a graph edge between *neighbor* and *path* is a code dependency.

    Its own function because "which edges count" is exactly what the two
    callers disagreed on, and the disagreement is invisible when it is spelled
    inline at each site. Symbol containment (``defines``) is out because the
    neighbour is not a file; co-change is out because it is history, not code.
    """
    # Cheapest test first, and ordered so the two string tests below run only
    # on edges that survive it. This runs once per in-edge of every candidate.
    if edge_data.get("edge_type", "imports") in _NON_DEPENDENCY_EDGES:
        return False
    if neighbor == path:
        return False
    if graph.nodes.get(neighbor, {}).get("node_type", "file") != "file":
        return False
    # A symbol id is ``<file path>::<symbol>``, so only an id carrying the
    # separator can be another spelling of this file. Guarding the call keeps
    # ``ids.parse`` off the hot path for ordinary file-to-file edges.
    #
    # Load-bearing assumption: no knowledge-graph id ever enters this graph.
    # ``file_path_of`` also resolves a ``KgFileId`` (``file:a.py``) to a path,
    # and that spelling carries no ``::``, so it would slip through here. The
    # graph builder only ever adds a bare path, ``path::Name``, ``external:``
    # or ``framework:`` (``ingestion/graph/builder.py``), and no node id in the
    # 41-repo corpus carries a ``file:`` prefix.
    return not (SYMBOL_SEP in neighbor and file_path_of(neighbor) == path)


def file_dependency_neighbors(graph: Any, path: str, *, incoming: bool) -> list[str]:
    """Structural file neighbours of *path* in the requested direction.

    Every file-to-file edge except co-change counts, which keeps import,
    type-use, framework and future structural edge types while excluding
    symbol containment and historical association. Code we do not own is
    excluded outright: this list names files, and a reader following it wants
    somewhere in the repository to go.
    """
    if path not in graph:
        return []

    edges = graph.in_edges(path, data=True) if incoming else graph.out_edges(path, data=True)
    neighbors = []
    for source, target, edge_data in edges:
        neighbor = source if incoming else target
        if is_external(neighbor) or graph.nodes.get(neighbor, {}).get("language") == "external":
            continue
        if _is_structural_edge(neighbor, path, edge_data, graph):
            neighbors.append(neighbor)
    return neighbors


def has_dependency_importer(graph: Any, path: str) -> bool:
    """Whether anything in the graph depends on *path*.

    Replaces a raw ``graph.in_degree(path) > 0``, which counted a co-change
    edge — "these two files were committed together" — as an import, and
    counted a file's own symbols and a self-import as importers of it.

    A ``framework:`` anchor counts and an ``external:`` one does not, the same
    split the zombie-package pass already makes: a framework anchor records
    that a runtime loads this file (a TYPO3 ``ext_localconf.php`` has no
    source-level importer and is not dead), whereas an ``external:`` node is a
    third-party package we merely name.
    """
    if path not in graph:
        return False
    return any(
        not _is_external_package(source) and _is_structural_edge(source, path, edge_data, graph)
        for source, _target, edge_data in graph.in_edges(path, data=True)
    )


def is_file_reachable(
    path: str,
    graph: Any,
    rescues: ReachabilityRescues | None = None,
) -> bool:
    """Whether anything in the repository can reach *path*.

    ``False`` means "no importer and no rescue applies" — the condition the
    dead-code analyzer reports as ``unreachable_file`` and the overview
    assembler reads as "this cannot be an execution flow entry point".

    *path* must be a file node id; a caller holding a symbol id resolves it
    first. An id that is not in the graph answers ``True``, because an
    unresolvable node is unchecked rather than unreachable.
    """
    if path not in graph:
        return True

    rescues = rescues or ReachabilityRescues()
    node_data = graph.nodes.get(path, {})

    # Conventional entry points, framework-instantiated files and published
    # API contracts are reached from outside the graph. Nothing imports
    # ``main.py`` either.
    if node_data.get("is_entry_point", False):
        return True
    if node_data.get("is_api_contract", False):
        return True
    # Workspace-driven never-flag, set by language warmups that read the build
    # manifest (Gradle non-``main`` source sets, Cargo ``[[example]]`` targets).
    if node_data.get("is_never_flag", False):
        return True
    # Convention globs: shell scripts invoked by name, framework file-system
    # routes, migration scripts. Reached from outside the import graph the same
    # way an entry point is, so this belongs to the question, not to the
    # analyzer that used to own the matcher.
    if never_flag_match(path):
        return True
    if path in rescues.whitelist:
        return True
    # BARREL_FILENAMES entries are strictly lowercase matching standard file-system conventions
    # (e.g. index.ts, __init__.py, mod.rs).
    if PurePosixPath(path).name in BARREL_FILENAMES:
        return True
    # Bundler ``resolve.alias`` targets are reached through the aliased package
    # name; only the config names them by path.
    if path in rescues.bundler_alias_targets:
        return True

    packages = rescues.package_files
    if path.endswith(".go"):
        return True if packages is None else is_go_file_reachable(path, graph, packages.go)
    if path.endswith(_JVM_SUFFIXES):
        return True if packages is None else is_jvm_file_reachable(path, graph, packages.jvm)
    if is_cpp_path(path):
        return True if packages is None else is_cpp_file_reachable(path, graph, packages.cpp)

    return has_dependency_importer(graph, path)
