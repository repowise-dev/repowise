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
import re
from collections.abc import Collection, Iterable, Mapping
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
from repowise.core.analysis.execution_graph import is_walkable_execution_edge
from repowise.core.ids import file_path_of
from repowise.core.support_paths import is_support_path

from .concept_tree.grouping import package_root_of
from .flow_entries import (
    API,
    CLI,
    FUNCTION,
    ROUTE,
    TOOL,
    DocSection,
    Entry,
    LineReader,
    declared_entries,
    doc_mentions,
    doc_sections,
    is_ui_path,
    line_reader,
    program_name,
    python_scripts,
)
from .flow_trace import FlowStep, trace
from .models import compute_page_id

__all__ = ["FLOW_PAGE_TYPE", "FlowPlan", "FlowStep", "plans_for_index", "select_flow_plans"]

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
# A UI component's render tree is not an operation a walkthrough tells well.
_UI_PENALTY = 0.3
# Tier of a graph root no framework declares; declared entries are tiers 0 and 1.
_UNDECLARED = 2
# ``main.go`` or ``cmd/<name>/main.go``: the program a user runs.
_GO_MAIN = re.compile(r"(?:cmd/([\w-]+)/)?main\.go::main")
# Construction and protocol methods are not operations a reader asks about.
_NOT_ROOTS = frozenset(
    {"__init__", "__new__", "__post_init__", "__repr__", "__str__", "__eq__", "__hash__"}
)
_MODULE_SCOPE = "::__module__"


@dataclass(frozen=True)
class FlowPlan:
    """A selected operation and its traced steps."""

    entry_id: str
    title: str
    steps: tuple[FlowStep, ...]
    reach: int
    score: float
    # What a user runs or calls: entry kind (see :mod:`.flow_entries`) and name.
    kind: str = FUNCTION
    public: str = ""
    # Callees left out as presentation, logging or context setup.
    skipped: tuple[str, ...] = ()

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
    """Every qualifying undeclared root with its score and reach, best first."""
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


@dataclass(frozen=True)
class _Candidate:
    """An operation that could get a page, with what ranks it."""

    tier: int
    entry: Entry
    reach: frozenset[str]
    score: float
    rank: float


def _tier(reach: frozenset[str], files: int) -> int | None:
    """0 at the full minimum size, 1 at half (a small declared command), else none."""
    if len(reach) >= _MIN_REACH and files >= _MIN_FILES:
        return 0
    if len(reach) >= _MIN_REACH // 2 and files >= 2:
        return 1
    return None


def _rank(index: _CallIndex, node: str, score: float, mentions: int) -> float:
    """Documented first, reach second; a UI component counts for less."""
    ui = _UI_PENALTY if is_ui_path(index.attrs(node).get("file_path") or _file_of(node)) else 1.0
    return math.log2(2 + mentions) * math.log2(1 + score) * ui


def _declared_candidates(
    index: _CallIndex, roots: set[str], entries: Iterable[Entry], docs: Collection[DocSection]
) -> list[_Candidate]:
    out: list[_Candidate] = []
    for entry in entries:
        if entry.node not in index.graph:
            continue
        reach = _reach(index, entry.node)
        score, files = _score(reach, roots)
        tier = _tier(reach, files)
        if tier is not None:
            rank = _rank(index, entry.node, score, doc_mentions(entry, docs))
            out.append(_Candidate(tier, entry, reach, score, rank))
    return out


def _root_entry(node: str, attrs: dict, program: str) -> Entry:
    """An undeclared root; a Go program's ``main`` is the program itself."""
    if m := _GO_MAIN.fullmatch(node):
        return Entry(node, CLI, m.group(1) or program or "main")
    return Entry(node, FUNCTION, _display_name(attrs, node))


def _candidates(
    index: _CallIndex,
    roots: set[str],
    entries: Iterable[Entry],
    docs: Collection[DocSection],
    program: str,
) -> list[_Candidate]:
    """Declared entries (tiers 0 and 1), then undeclared graph roots (tier 2), best first."""
    out = _declared_candidates(index, roots, entries, docs)
    declared = {c.entry.node for c in out}
    for score, node, reach in _ranked_roots(index, roots):
        if node not in declared:
            entry = _root_entry(node, index.attrs(node), program)
            out.append(_Candidate(_UNDECLARED, entry, reach, score, _rank(index, node, score, 0)))
    out.sort(key=lambda c: (c.tier, -c.rank, c.entry.node))
    return out


def _same_operation(cand: _Candidate, other: _Candidate) -> bool:
    """Inside a picked one, wrapping it, mostly its reach, or the same public name."""
    return (
        cand.entry.node in other.reach
        or other.entry.node in cand.reach
        or len(cand.reach & other.reach) / max(1, len(cand.reach)) > _MAX_OVERLAP
        or (cand.entry.kind != FUNCTION and cand.entry.public == other.entry.public)
    )


def _pick_diverse(cands: list[_Candidate], roots: set[str], limit: int) -> list[_Candidate]:
    """The best candidates that are not the same operation twice."""
    picked: list[_Candidate] = []
    per_package: dict[str, int] = {}
    # Undeclared roots spread across packages when there are several, so one
    # hub does not fill the group. Declared entries are what the project
    # exposes, so they are not capped: capping them let a minor route in a
    # small package beat the second most documented command.
    packages = {package_root_of(_file_of(c.entry.node), roots) for c in cands}
    cap = max(_MAX_PER_PACKAGE, math.ceil(limit / max(1, len(packages))))
    for cand in cands:
        package = package_root_of(_file_of(cand.entry.node), roots)
        if cand.tier == _UNDECLARED and per_package.get(package, 0) >= cap:
            continue
        if any(_same_operation(cand, other) for other in picked):
            continue
        picked.append(cand)
        per_package[package] = per_package.get(package, 0) + 1
        if len(picked) >= limit:
            break
    return picked


def _step_weight(index: _CallIndex, node: str) -> float:
    """How much of the operation a callee carries.

    Its own breadth, divided down by how many places call it: a function a
    hundred callers share (a logger, a class-name joiner, an HTTP wrapper) is
    infrastructure the operation passes through, not part of its story.
    """
    breadth, _files = _score(_reach(index, node), index.roots)
    return breadth / math.log2(2 + index.in_deg.get(node, 0))


def _step_maker(index: _CallIndex) -> Any:
    def make(node: str, depth: int, parent: int, call_line: int | None, **extra: Any) -> FlowStep:
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
            **extra,
        )

    return make


def trace_steps(
    index: _CallIndex, root: str, read: LineReader | None = None
) -> tuple[tuple[FlowStep, ...], tuple[str, ...]]:
    """The root and the callees that carry the operation (see :mod:`.flow_trace`)."""
    weights: dict[str, float] = {}

    def weight(node: str) -> float:
        if node not in weights:
            weights[node] = _step_weight(index, node)
        return weights[node]

    return trace(index, root, weight, _step_maker(index), read)


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


def _public_title(entry: Entry) -> str | None:
    """The title a declared entry reads by: what the user types or calls."""
    if entry.kind == TOOL:
        return f"How the {entry.public} tool answers a call"
    if entry.kind == ROUTE:
        return f"How a request to {entry.public} is handled"
    if entry.kind in (CLI, API):
        return f"How {entry.public} works"
    return None


def _titles(entries: list[Entry], heads: list[FlowStep]) -> list[str]:
    """One title per plan, naming the file wherever two would read the same."""
    plain = [_public_title(e) or flow_title(h) for e, h in zip(entries, heads, strict=True)]
    return [
        t if plain.count(t) == 1 else (_public_title(e) or flow_title(h, with_path=True))
        for e, h, t in zip(entries, heads, plain, strict=True)
    ]


def select_flow_plans(
    graph: Any,
    *,
    package_roots: Collection[str] = (),
    code_file_count: int | None = None,
    read: LineReader | None = None,
    program: str = "",
    scripts: Mapping[str, str] | None = None,
    docs: Collection[DocSection] = (),
) -> list[FlowPlan]:
    """The operations this repository's flow pages walk, best first.

    A pure function of the graph, the checkout's text (*read*, *docs*) and the
    package roots, so generation, cost estimation and the update cascade
    compute the same plans. Without *read* the declared entries come from the
    graph's decorators alone and every call counts as on the main path.
    """
    if graph is None or graph.number_of_nodes() == 0:
        return []
    out_deg, in_deg = build_call_degree_maps(graph)
    roots = set(package_roots)
    index = _CallIndex(graph, out_deg, in_deg, roots)
    if code_file_count is None:
        code_file_count = _code_file_count(graph)
    entries = declared_entries(graph, read or _no_lines, program, scripts)
    cands = _candidates(index, roots, entries, docs, program)
    picked = _pick_diverse(cands, roots, flow_page_limit(code_file_count))
    traced = [trace_steps(index, c.entry.node, read) for c in picked]
    titles = _titles([c.entry for c in picked], [steps[0] for steps, _ in traced])
    return [
        FlowPlan(
            entry_id=c.entry.node,
            title=title,
            steps=steps,
            reach=len(c.reach),
            score=round(c.score, 3),
            kind=c.entry.kind,
            public=c.entry.public,
            skipped=skipped,
        )
        for c, (steps, skipped), title in zip(picked, traced, titles, strict=True)
    ]


def _no_lines(_path: str) -> list[str]:
    return []


def plans_for_index(graph_builder: Any, parsed_files: list[Any]) -> list[FlowPlan]:
    """:func:`select_flow_plans` over a built index: what generation and the cascade call."""
    from repowise.core.ingestion.package_roots import package_roots_from_paths

    from .page_generator.helpers import indexed_parsed_files

    paths = set(indexed_parsed_files(graph_builder, parsed_files))
    # A graph rebuilt from the database has no decorators until the re-parse
    # stamps them back; without them a declared command is not seen as one.
    restore = getattr(graph_builder, "restore_parse_only_attrs", None)
    if restore is not None:
        restore(parsed_files)
    repo_root = getattr(graph_builder, "_repo_path", None)
    graph = graph_builder.graph()
    return select_flow_plans(
        graph,
        package_roots=package_roots_from_paths(paths),
        read=line_reader(repo_root, getattr(graph_builder, "_source_map", None)),
        program=program_name(repo_root, graph),
        scripts=python_scripts(repo_root),
        docs=doc_sections(repo_root),
    )


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
