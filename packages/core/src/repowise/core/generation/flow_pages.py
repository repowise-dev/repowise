"""Choose the operations a flow page walks, and trace each one's steps.

A flow page explains one operation end to end ("how ``init`` works") across
the modules it passes through. Two questions are answered here, both from the
call graph alone so a keyed and a keyless run of the same commit pick the same
pages with the same steps:

1. **Which operations.** A root is a production function nothing in production
   calls (module-level ``if __name__ == "__main__"`` calls do not count), or a
   conventional ``main``/``run`` in a file ingestion flagged as an entry point.
   That is where a CLI command, a route handler, an MCP tool or a UI page
   starts: the framework calls it, not the code. Each root is scored by how far
   it reaches (symbols, files, directories and packages within a bounded walk),
   and the best are taken with two diversity rules so one hub does not fill the
   group: at most two per package and no root whose reach is mostly another
   pick's.
2. **Which steps.** The walk from a root keeps the callees that lead furthest,
   a few at the top and fewer deeper down, so a step list reads as a story and
   not a dump of every helper. Every step carries its file and line span, which
   is what the page cites.

Edges are filtered by the same rules the execution-flow tracer uses
(:mod:`repowise.core.analysis.execution_flows`), so a test fake or a
low-confidence resolution never becomes a step.

Why not that tracer's own flows: it ranks entries by name and fan-out shape
and follows one highest-fan-out successor per hop, which answers "where might
execution start" and draws a single thread. A walkthrough needs the operations
that run through the most of the system and the branches that carry each, so
it scores roots by reach and keeps a few callees per step.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.execution_flows import (
    TIER1_NAMES,
    build_call_degree_maps,
    call_successors,
    is_excluded_node,
    reach_set,
)
from repowise.core.analysis.execution_graph import call_line_values, is_walkable_execution_edge
from repowise.core.ids import file_path_of
from repowise.core.support_paths import is_support_path

from .concept_tree.grouping import package_root_of
from .models import compute_page_id

FLOW_PAGE_TYPE = "flow_page"

# How far the reach walk looks. Deep enough to leave a CLI command's own
# package and arrive in the engine; shallow enough that every root's walk on a
# 100k-node graph finishes in about a second in total.
_REACH_DEPTH = 6
# Below these a "flow" is a helper calling a few others, not an operation.
_MIN_REACH = 12
_MIN_FILES = 3
# A root whose reach is mostly a picked root's is the same operation entered
# from a different door.
_MAX_OVERLAP = 0.8
_MAX_PER_PACKAGE = 2  # when the repo has packages enough to spread over
# The step list: callees kept per depth (index = depth), one beyond, and caps.
_STEP_FANOUT = (4, 3, 2)
_STEP_DEPTH = 5
_MAX_STEPS = 14
# Construction and protocol methods are not operations a reader asks about.
_NOT_ROOTS = frozenset(
    {"__init__", "__new__", "__post_init__", "__repr__", "__str__", "__eq__", "__hash__"}
)
_MODULE_SCOPE = "::__module__"


@dataclass(frozen=True)
class FlowStep:
    """One function on the walk, with what a citation needs."""

    symbol_id: str
    name: str
    path: str
    start_line: int | None
    end_line: int | None
    depth: int
    # Index of the step that calls this one; -1 for the root.
    parent: int
    # Line in the caller where the call happens, when the graph recorded it.
    call_line: int | None


@dataclass(frozen=True)
class FlowPlan:
    """A selected operation and its traced steps."""

    entry_id: str
    title: str
    steps: tuple[FlowStep, ...]
    reach: int
    score: float

    @property
    def page_id(self) -> str:
        return compute_page_id(FLOW_PAGE_TYPE, self.entry_id)

    @property
    def files(self) -> list[str]:
        """Every file a step lives in, sorted: what makes the page stale."""
        return sorted({s.path for s in self.steps if s.path})


@dataclass
class _CallIndex:
    """The graph plus the walkable-successor view every question here reads."""

    graph: Any
    out_deg: dict[str, int]
    in_deg: dict[str, int] = field(default_factory=dict)
    roots: set[str] = field(default_factory=set)
    _succ: dict[str, list[str]] = field(default_factory=dict)
    _reach: dict[str, frozenset[str]] = field(default_factory=dict)

    def successors(self, node: str) -> list[str]:
        if node not in self._succ:
            self._succ[node] = call_successors(node, self.graph, self.out_deg).walkable
        return self._succ[node]

    def attrs(self, node: str) -> dict:
        return self.graph.nodes.get(node, {}) if node in self.graph else {}


def flow_page_limit(code_file_count: int) -> int:
    """How many flow pages a repository of this size gets (3 to 6)."""
    if code_file_count < 200:
        return 3
    if code_file_count < 800:
        return 4
    if code_file_count < 2500:
        return 5
    return 6


def _called_in_production(index: _CallIndex, node: str) -> bool:
    """Whether a production function calls *node* over a walkable edge."""
    graph = index.graph
    for caller in graph.predecessors(node):
        if caller == node or caller.endswith(_MODULE_SCOPE):
            continue
        data = graph.edges[caller, node]
        if is_walkable_execution_edge(
            data.get("edge_type"), data.get("resolution_origin"), data.get("confidence")
        ) and not is_excluded_node(graph, caller):
            return True
    return False


def _is_declared_main(index: _CallIndex, attrs: dict) -> bool:
    """A ``main``/``run`` in a file ingestion flagged as an entry point."""
    if not TIER1_NAMES.match(attrs.get("name") or ""):
        return False
    return bool(index.attrs(attrs.get("file_path") or "").get("is_entry_point"))


def _is_candidate(index: _CallIndex, node: str, attrs: dict) -> bool:
    """A production function that calls at least two others."""
    if attrs.get("node_type") != "symbol" or attrs.get("kind") not in ("function", "method"):
        return False
    if index.out_deg.get(node, 0) < 2 or (attrs.get("name") or "") in _NOT_ROOTS:
        return False
    return not (
        is_excluded_node(index.graph, node) or is_support_path(attrs.get("file_path") or "")
    )


def _is_root(index: _CallIndex, node: str, attrs: dict) -> bool:
    """Whether *node* starts an operation rather than serving one."""
    if not _is_candidate(index, node, attrs):
        return False
    return not _called_in_production(index, node) or _is_declared_main(index, attrs)


def _reach(index: _CallIndex, root: str) -> frozenset[str]:
    """Every symbol a bounded walk from *root* arrives at (the shared walk, memoised)."""
    cached = index._reach.get(root)
    if cached is None:
        cached = reach_set(root, index.graph, index.out_deg, _REACH_DEPTH, index._succ)
        index._reach[root] = cached
    return cached


def _file_of(node: str) -> str:
    return file_path_of(node) or node


def _score(reach: frozenset[str], roots: set[str]) -> tuple[float, int]:
    """How much of the system an operation runs through, and its file count.

    Size counts logarithmically; breadth (files, directories, packages) counts
    linearly, because an operation that crosses packages is the one a reader
    cannot reconstruct from any single module page.
    """
    files = {_file_of(n) for n in reach}
    dirs = {str(PurePosixPath(f).parent) for f in files}
    packages = {package_root_of(f, roots) for f in files}
    breadth = len(files) + 2 * len(dirs) + 4 * (len(packages) - 1)
    return math.log2(1 + len(reach)) * breadth, len(files)


def _ranked_roots(
    index: _CallIndex, roots: set[str]
) -> list[tuple[float, str, frozenset[str]]]:
    """Every qualifying root with its score and reach, best first."""
    ranked: list[tuple[float, str, frozenset[str]]] = []
    for node, attrs in index.graph.nodes(data=True):
        if not _is_root(index, node, attrs):
            continue
        reach = _reach(index, node)
        score, files = _score(reach, roots)
        if len(reach) >= _MIN_REACH and files >= _MIN_FILES:
            ranked.append((score, node, reach))
    # Score, then id: the order is total, so two runs of one commit agree.
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def _pick_diverse(
    ranked: list[tuple[float, str, frozenset[str]]], roots: set[str], limit: int
) -> list[tuple[float, str, frozenset[str]]]:
    """The best roots that are not the same operation twice."""
    picked: list[tuple[float, str, frozenset[str]]] = []
    per_package: dict[str, int] = {}
    # Spread across packages when there are several; a one-package repo takes
    # its whole budget from the one it has.
    packages = {package_root_of(_file_of(node), roots) for _, node, _ in ranked}
    cap = max(_MAX_PER_PACKAGE, math.ceil(limit / max(1, len(packages))))
    for item in ranked:
        _, node, reach = item
        package = package_root_of(_file_of(node), roots)
        if per_package.get(package, 0) >= cap:
            continue
        if any(
            node in other or len(reach & other) / len(reach) > _MAX_OVERLAP
            for _s, _n, other in picked
        ):
            continue
        picked.append(item)
        per_package[package] = per_package.get(package, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def _call_line(index: _CallIndex, caller: str, callee: str) -> int | None:
    lines = call_line_values(index.graph.edges[caller, callee])
    return min(lines) if lines else None


def _step_weight(index: _CallIndex, node: str) -> float:
    """How much of the operation a callee carries.

    Its own breadth, divided down by how many places call it: a function a
    hundred callers share (a logger, a class-name joiner, an HTTP wrapper) is
    infrastructure the operation passes through, not part of its story.
    """
    breadth, _files = _score(_reach(index, node), index.roots)
    return breadth / math.log2(2 + index.in_deg.get(node, 0))


def _children(index: _CallIndex, node: str, depth: int, taken: set[str]) -> list[str]:
    """The callees of *node* worth a step, heaviest first."""
    keep = _STEP_FANOUT[depth] if depth < len(_STEP_FANOUT) else 1
    fresh = [c for c in index.successors(node) if c not in taken]
    # Ties by id so the cut is the same on every run.
    fresh.sort(key=lambda c: (-_step_weight(index, c), c))
    return fresh[:keep]


def _step(index: _CallIndex, node: str, depth: int, parent: int, call_line: int | None) -> FlowStep:
    attrs = index.attrs(node)
    start = attrs.get("start_line")
    end = attrs.get("end_line")
    return FlowStep(
        symbol_id=node,
        name=_display_name(attrs, node),
        path=attrs.get("file_path") or _file_of(node),
        start_line=start if isinstance(start, int) and start > 0 else None,
        end_line=end if isinstance(end, int) and end > 0 else None,
        depth=depth,
        parent=parent,
        call_line=call_line,
    )


def _allocate(index: _CallIndex, root: str) -> dict[str, list[str]]:
    """``node -> kept callees``, filled one depth at a time.

    Breadth first so the step budget is shared across the operation's phases:
    filled depth first, the first phase's helpers used it all up and the part
    that does the work arrived last or not at all.
    """
    kept: dict[str, list[str]] = {root: []}
    frontier = [root]
    for depth in range(_STEP_DEPTH):
        frontier = _fill_depth(index, frontier, depth, kept)
        if not frontier or len(kept) >= _MAX_STEPS:
            break
    return kept


def _fill_depth(
    index: _CallIndex, frontier: list[str], depth: int, kept: dict[str, list[str]]
) -> list[str]:
    """Keep the next depth's callees of *frontier* in *kept*; return them."""
    added: list[str] = []
    for node in frontier:
        for child in _children(index, node, depth, set(kept)):
            if len(kept) >= _MAX_STEPS:
                return added
            kept[node].append(child)
            kept[child] = []
            added.append(child)
    return added


def trace_steps(index: _CallIndex, root: str) -> tuple[FlowStep, ...]:
    """The root and the callees that carry the operation, in reading order.

    Chosen breadth first (see :func:`_allocate`), then numbered depth first
    with each node's callees in the order the code calls them, so step N's
    caller is always an earlier step and the list reads like the code runs.
    """
    kept = _allocate(index, root)
    steps: list[FlowStep] = []

    def visit(node: str, depth: int, parent: int, call_line: int | None) -> None:
        steps.append(_step(index, node, depth, parent, call_line))
        here = len(steps) - 1
        calls = sorted((_call_line(index, node, c) or 0, c) for c in kept[node])
        for line, child in calls:
            visit(child, depth + 1, here, line or None)

    visit(root, 0, -1, None)
    return tuple(steps)


def _display_name(attrs: dict, node: str) -> str:
    """``Class.method`` for a method, the bare name otherwise."""
    name = attrs.get("name") or node.rsplit("::", 1)[-1]
    parent = attrs.get("parent_name")
    if parent and attrs.get("kind") == "method":
        return f"{parent}.{name}"
    tail = node.split("::", 1)[-1]
    return tail.replace("::", ".") if "::" in tail else name


def flow_title(entry: FlowStep, *, with_path: bool = False) -> str:
    """``How <operation> works``, with the file when the name says nothing alone."""
    bare = entry.name.rsplit(".", 1)[-1]
    if with_path or TIER1_NAMES.match(bare) or bare in {"App", "constructor", "handle", "index"}:
        return f"How {entry.name} in {entry.path} works"
    return f"How {entry.name} works"


def _titles(entries: list[FlowStep]) -> list[str]:
    """One title per entry, naming the file wherever two would read the same."""
    plain = [flow_title(e) for e in entries]
    return [
        flow_title(e, with_path=plain.count(t) > 1) for e, t in zip(entries, plain, strict=True)
    ]


def select_flow_plans(
    graph: Any, *, package_roots: Collection[str] = (), code_file_count: int | None = None
) -> list[FlowPlan]:
    """The operations this repository's flow pages walk, best first.

    Pure function of the graph (and the package roots derived from the same
    file set), so generation, cost estimation and the update cascade compute
    the same plans.
    """
    if graph is None or graph.number_of_nodes() == 0:
        return []
    out_deg, in_deg = build_call_degree_maps(graph)
    roots = set(package_roots)
    index = _CallIndex(graph, out_deg, in_deg, roots)
    if code_file_count is None:
        code_file_count = _code_file_count(graph)
    picked = _pick_diverse(_ranked_roots(index, roots), roots, flow_page_limit(code_file_count))
    traced = [trace_steps(index, node) for _, node, _ in picked]
    titles = _titles([steps[0] for steps in traced])
    return [
        FlowPlan(
            entry_id=node,
            title=title,
            steps=steps,
            reach=len(reach),
            score=round(score, 3),
        )
        for (score, node, reach), steps, title in zip(picked, traced, titles, strict=True)
    ]


def plans_for_index(graph_builder: Any, parsed_files: list[Any]) -> list[FlowPlan]:
    """:func:`select_flow_plans` over a built index: what generation and the cascade call."""
    from repowise.core.ingestion.package_roots import package_roots_from_paths

    from .page_generator.helpers import indexed_parsed_files

    paths = set(indexed_parsed_files(graph_builder, parsed_files))
    return select_flow_plans(graph_builder.graph(), package_roots=package_roots_from_paths(paths))


def _code_file_count(graph: Any) -> int:
    """Production files in the graph: the size the page budget scales with."""
    return sum(
        1
        for node, attrs in graph.nodes(data=True)
        if attrs.get("node_type") == "file"
        and not attrs.get("is_test")
        and not is_excluded_node(graph, node)
    )


def flow_pages_of_files(plans: Iterable[FlowPlan]) -> dict[str, tuple[str, ...]]:
    """``file -> flow page ids`` whose steps live in that file."""
    out: dict[str, list[str]] = {}
    for plan in plans:
        for path in plan.files:
            out.setdefault(path, []).append(plan.page_id)
    return {path: tuple(ids) for path, ids in out.items()}
