"""One reliable, deterministic view of symbol-level execution edges.

Test reachability and performance analysis ask different questions of the same
resolved graph.  This module owns the part they must never reinterpret:

* which edge types can transfer control;
* which resolver origins are too weak to count as execution evidence;
* file-to-symbol declarations and forward/reverse execution adjacency;
* source definition and call-site lookup; and
* the bounded reverse walk used to explain a path to a sink.

Construction is one node scan plus one edge scan.  Adjacencies preserve the
source graph's stable insertion order while removing duplicates, so consumers
do not pay for their own graph build or sort.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from repowise.core.ingestion.models import EXECUTION_EDGE_TYPES

UNRELIABLE_EXECUTION_ORIGINS = frozenset({"global_unique"})
_EXCLUDED_EXECUTION_PATHS = re.compile(
    r"(test[s_/]|_test\.|\.test\.|\.spec\.|__tests__|conftest|"
    r"fixture[s]?[/.]|mock[s]?[/.]|stub[s]?[/.]|fake[s]?[/.]|"
    r"demo[_/.]|example[s]?[/.]|sample[s]?[/.]|benchmark[s]?[/.]|"
    r"_bench\.|scripts?/)",
    re.IGNORECASE,
)
CallTargetBasis = Literal["call-site", "name-fallback"]
_KeyT = TypeVar("_KeyT", bound=Hashable)


def file_of_symbol(symbol_id: str) -> str:
    """Return the file component of a ``path::symbol`` node id."""
    return symbol_id.split("::", 1)[0]


def module_node_id(path: str) -> str:
    """Return the synthetic module-scope symbol id for *path*."""
    return f"{path}::__module__"


def is_reliable_execution_edge(edge_type: str | None, resolution_origin: str | None = None) -> bool:
    """Whether an edge is strong enough to support an execution claim."""
    return (
        edge_type in EXECUTION_EDGE_TYPES and resolution_origin not in UNRELIABLE_EXECUTION_ORIGINS
    )


def is_reliable_call_edge(edge_type: str | None, resolution_origin: str | None = None) -> bool:
    """Whether an edge is a resolved call, excluding weaker execution relations."""
    return edge_type == "calls" and resolution_origin not in UNRELIABLE_EXECUTION_ORIGINS


def is_walkable_execution_edge(
    edge_type: str | None,
    resolution_origin: str | None = None,
    confidence: float | None = None,
    *,
    minimum_confidence: float = 0.5,
) -> bool:
    """Execution-flow edge policy shared by memory, DB, and export adapters."""
    return (
        is_reliable_execution_edge(edge_type, resolution_origin)
        and float(confidence or 0.0) >= minimum_confidence
    )


def is_excluded_execution_path(path: str) -> bool:
    """Whether a flow target is test/demo/fixture/tooling material."""
    return bool(_EXCLUDED_EXECUTION_PATHS.search(path))


def _append_unique(
    adjacency: dict[_KeyT, list[str]],
    source: _KeyT,
    target: str,
    seen: set[tuple[_KeyT, str]] | None = None,
) -> None:
    if seen is None:
        adjacency.setdefault(source, []).append(target)
        return
    pair = (source, target)
    if pair not in seen:
        seen.add(pair)
        adjacency.setdefault(source, []).append(target)


def _line_values(attrs: Mapping[str, Any]) -> tuple[int, ...]:
    raw = attrs.get("call_lines")
    if raw is None:
        raw = (attrs.get("call_line"),)
    elif isinstance(raw, int):
        raw = (raw,)
    return tuple(line for line in raw if isinstance(line, int) and line > 0)


class ExecutionGraphIndex:
    """The shared read-only index over reliable execution edges.

    ``graph`` is normally the in-memory NetworkX graph.  ``declares`` and
    ``calls`` exist only as a compatibility construction path for small tests
    and callers that already own adjacency maps.
    """

    __slots__ = (
        "_by_caller_line",
        "_by_file_line",
        "_call_only",
        "_callers_with_site_metadata",
        "_ranges",
        "calls",
        "declares",
        "forward",
        "in_degree",
        "name",
        "nodes",
        "reverse",
    )

    def __init__(
        self,
        graph: Any | None = None,
        *,
        declares: Mapping[str, Iterable[str]] | None = None,
        calls: Mapping[str, Iterable[str]] | None = None,
        edge_rows: Iterable[tuple[str, str, str, str | None, Iterable[int] | None]] = (),
    ) -> None:
        self.nodes: set[str] = set()
        self.name: dict[str, str] = {}
        forward: dict[str, list[str]] = {}
        reverse: dict[str, list[str]] = {}
        call_only: dict[str, list[str]] = {}
        declaration_map: dict[str, list[str]] = {}
        by_caller_line: dict[tuple[str, int], list[str]] = {}
        by_file_line_candidates: dict[tuple[str, int], list[str]] = {}
        forward_seen: set[tuple[str, str]] = set()
        reverse_seen: set[tuple[str, str]] = set()
        declaration_seen: set[tuple[str, str]] = set()
        caller_line_seen: set[tuple[tuple[str, int], str]] = set()
        call_only_seen: set[tuple[str, str]] = set()
        callers_with_site_metadata: set[str] = set()
        ranges: dict[str, list[tuple[int, int, str]]] = {}

        if graph is not None:
            try:
                graph_nodes = graph.nodes(data=True)
            except Exception:
                graph_nodes = ()
            for node_id, attrs in graph_nodes:
                self.nodes.add(node_id)
                if attrs.get("node_type") != "symbol":
                    continue
                self.name[node_id] = attrs.get("name") or ""
                path = attrs.get("file_path")
                start = attrs.get("start_line")
                end = attrs.get("end_line")
                if path and isinstance(start, int):
                    _append_unique(by_file_line_candidates, (path, start), node_id)
                    if isinstance(end, int):
                        ranges.setdefault(path, []).append((start, end, node_id))
            try:
                graph_edges = graph.edges(data=True)
            except Exception:
                graph_edges = ()
            try:
                deduplicate_graph = bool(graph.is_multigraph())
            except Exception:
                deduplicate_graph = True
            for source, target, data in graph_edges:
                attrs = data or {}
                edge_type = attrs.get("edge_type")
                self._ingest_edge(
                    declaration_map,
                    declaration_seen if deduplicate_graph else None,
                    forward,
                    forward_seen if deduplicate_graph else None,
                    reverse,
                    reverse_seen if deduplicate_graph else None,
                    by_caller_line,
                    caller_line_seen if deduplicate_graph else None,
                    call_only,
                    call_only_seen if deduplicate_graph else None,
                    callers_with_site_metadata,
                    source,
                    target,
                    edge_type,
                    attrs.get("resolution_origin"),
                    _line_values(attrs) if edge_type == "calls" else (),
                )

        for source, targets in (declares or {}).items():
            for target in sorted(set(targets)):
                _append_unique(declaration_map, source, target, declaration_seen)
        for source, targets in (calls or {}).items():
            for target in sorted(set(targets)):
                _append_unique(forward, source, target, forward_seen)
                _append_unique(reverse, target, source, reverse_seen)
                _append_unique(call_only, source, target, call_only_seen)
        for source, target, edge_type, origin, call_lines in edge_rows:
            self._ingest_edge(
                declaration_map,
                declaration_seen,
                forward,
                forward_seen,
                reverse,
                reverse_seen,
                by_caller_line,
                caller_line_seen,
                call_only,
                call_only_seen,
                callers_with_site_metadata,
                source,
                target,
                edge_type,
                origin,
                tuple(call_lines or ()),
            )

        self.declares = {key: tuple(values) for key, values in declaration_map.items()}
        self.forward = {key: tuple(values) for key, values in forward.items()}
        self.calls = self.forward
        self.reverse = {key: tuple(values) for key, values in reverse.items()}
        self._by_caller_line = {key: tuple(values) for key, values in by_caller_line.items()}
        self._call_only = {key: tuple(values) for key, values in call_only.items()}
        self._callers_with_site_metadata = frozenset(callers_with_site_metadata)
        self._by_file_line = {key: min(values) for key, values in by_file_line_candidates.items()}
        self._ranges = {path: tuple(values) for path, values in ranges.items()}
        self.in_degree = {node: len(callers) for node, callers in self.reverse.items()}

    @staticmethod
    def _ingest_edge(
        declares: dict[str, list[str]],
        declaration_seen: set[tuple[str, str]] | None,
        forward: dict[str, list[str]],
        forward_seen: set[tuple[str, str]] | None,
        reverse: dict[str, list[str]],
        reverse_seen: set[tuple[str, str]] | None,
        by_caller_line: dict[tuple[str, int], list[str]],
        caller_line_seen: set[tuple[tuple[str, int], str]] | None,
        call_only: dict[str, list[str]],
        call_only_seen: set[tuple[str, str]] | None,
        callers_with_site_metadata: set[str],
        source: str,
        target: str,
        edge_type: str | None,
        origin: str | None,
        call_lines: Iterable[int],
    ) -> None:
        if edge_type == "defines":
            _append_unique(declares, source, target, declaration_seen)
            return
        if not is_reliable_execution_edge(edge_type, origin):
            return
        _append_unique(forward, source, target, forward_seen)
        _append_unique(reverse, target, source, reverse_seen)
        if edge_type == "calls":
            _append_unique(call_only, source, target, call_only_seen)
            for line in call_lines:
                if isinstance(line, int) and line > 0:
                    callers_with_site_metadata.add(source)
                    _append_unique(by_caller_line, (source, line), target, caller_line_seen)

    def resolve_function(self, path: str, func_start: int) -> str | None:
        """Resolve a function definition, tolerating decorator line offsets."""
        if func_start == 0:
            module = module_node_id(path)
            return module if module in self.nodes else None
        exact = self._by_file_line.get((path, func_start))
        if exact is not None:
            return exact
        best: str | None = None
        best_start = -1
        for start, end, node_id in self._ranges.get(path, ()):
            if start <= func_start <= end and (
                start > best_start or (start == best_start and (best is None or node_id < best))
            ):
                best, best_start = node_id, start
        return best

    def resolve_call_targets(
        self, caller: str, call_line: int, target_name: str
    ) -> tuple[tuple[str, ...], CallTargetBasis]:
        """Resolve a direct call using exact site evidence, then old-index fallback.

        New graphs preserve call lines.  Rehydrated indexes created before that
        fact existed have none, so they retain the previous resolved-callee name
        match until the next index refresh.
        """
        at_site = self._by_caller_line.get((caller, call_line))
        if at_site:
            named = tuple(node for node in at_site if self.name.get(node) == target_name)
            return named, "call-site"
        if caller in self._callers_with_site_metadata:
            # A refreshed caller can legitimately contain an unresolved site.
            # Falling back here would let a different same-named call fabricate
            # a target for it.  Only callers with no site facts are legacy.
            return (), "call-site"
        return (
            tuple(
                node
                for node in self._call_only.get(caller, ())
                if self.name.get(node) == target_name
            ),
            "name-fallback",
        )

    def affected_files(
        self,
        changed_files: Iterable[str],
        *,
        forward_depth: int = 3,
        reverse_depth: int = 4,
    ) -> set[str]:
        """Files whose bounded execution facts can change with *changed_files*.

        Forward closure covers a changed caller with an unchanged sink; reverse
        closure covers unchanged loop owners whose sink/helper changed.  Both
        traversals are multi-source and visit each reached node once, avoiding
        a graph walk per finding.
        """
        changed = set(changed_files)
        seeds = {symbol for path in changed for symbol in self.declares.get(path, ())}
        affected = set(changed)

        def walk(adjacency: Mapping[str, Iterable[str]], start: set[str], depth: int) -> set[str]:
            if depth <= 0 or not start:
                return set(start)
            seen = set(start)
            queue: deque[tuple[str, int]] = deque((seed, 0) for seed in sorted(start))
            while queue:
                node, distance = queue.popleft()
                affected.add(file_of_symbol(node))
                if distance >= depth:
                    continue
                for neighbor in adjacency.get(node, ()):
                    affected.add(file_of_symbol(neighbor))
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, distance + 1))
            return seen

        forward_reached = walk(self.forward, seeds, forward_depth)
        # Reverse from the entire reached frontier, not only the changed
        # symbols. If A and B both call an unchanged sink, changing A must pull
        # B into the recomputed causal group so its totals stay authoritative.
        walk(self.reverse, forward_reached, reverse_depth)
        return affected

    def forward_reachable(self, seeds: Iterable[str], *, max_depth: int | None = None) -> set[str]:
        """Reliable execution nodes reachable from all *seeds* in one BFS."""
        reached = set(seeds)
        queue: deque[tuple[str, int]] = deque((seed, 0) for seed in sorted(reached))
        while queue:
            node, depth = queue.popleft()
            if max_depth is not None and depth >= max_depth:
                continue
            for target in self.forward.get(node, ()):
                if target not in reached:
                    reached.add(target)
                    queue.append((target, depth + 1))
        return reached


@dataclass(frozen=True, slots=True)
class ReachInfo:
    distance: int
    next_hop: str | None
    sink: str


def reachable_to_sink(
    sink_nodes: Iterable[str],
    predecessors: Callable[[str], Iterable[str]],
    *,
    max_depth: int = 3,
) -> dict[str, ReachInfo]:
    """Map every node within *max_depth* forward hops of a sink."""
    if max_depth < 0:
        return {}
    info: dict[str, ReachInfo] = {}
    queue: deque[str] = deque()
    for sink in sink_nodes:
        if sink not in info:
            info[sink] = ReachInfo(distance=0, next_hop=None, sink=sink)
            queue.append(sink)
    while queue:
        node = queue.popleft()
        current = info[node]
        if current.distance >= max_depth:
            continue
        for predecessor in predecessors(node):
            if predecessor in info:
                continue
            info[predecessor] = ReachInfo(
                distance=current.distance + 1,
                next_hop=node,
                sink=current.sink,
            )
            queue.append(predecessor)
    return info


def path_to_sink(node: str, info: Mapping[str, ReachInfo], *, max_len: int = 16) -> list[str]:
    """Reconstruct ``[node, ..., sink]`` from a reverse-walk result."""
    if node not in info:
        return []
    path = [node]
    seen = {node}
    current = info[node]
    while current.next_hop is not None and len(path) < max_len:
        next_hop = current.next_hop
        if next_hop in seen:
            break
        path.append(next_hop)
        seen.add(next_hop)
        next_info = info.get(next_hop)
        if next_info is None:
            break
        current = next_info
    return path


__all__ = [
    "UNRELIABLE_EXECUTION_ORIGINS",
    "ExecutionGraphIndex",
    "ReachInfo",
    "file_of_symbol",
    "is_excluded_execution_path",
    "is_reliable_call_edge",
    "is_reliable_execution_edge",
    "is_walkable_execution_edge",
    "module_node_id",
    "path_to_sink",
    "reachable_to_sink",
]
