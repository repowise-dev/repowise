"""Move Method detector — Feature Envy as a graph query.

A method suffers *feature envy* when it is more interested in another class
than the one it lives in: it calls into a foreign class's members far more
than its own. The fix is to move it next to the data it actually uses — a
cross-file architectural refactoring that falls straight out of the call
graph the health pass already built.

Detection (Jaccard-distance feature envy over the call graph):

- A method ``m`` in class ``C`` accesses a set of class-owned members (its
  callees that belong to some class, resolved via the ``calls`` graph).
- For ``C`` and every foreign class ``T`` that ``m`` touches, the Jaccard
  distance ``1 - |accessed AND members(T)| / |accessed OR members(T)|``
  measures how close ``m`` is to that class.
- If a foreign class ``T`` is *strictly nearer* than ``m``'s own class — and
  ``m`` barely uses its own class while leaning on ``T`` — ``m`` belongs in
  ``T``.

Feature envy is notoriously noisy, so the gate is deliberately strict
(high-precision, low-recall): the method must access at least
``_MIN_FOREIGN_MEMBERS`` distinct members of the target, more of the target
than of its own class, and almost nothing of its own class. We only ever
consider classes ``m`` already calls into, so the target is always a class
the method can legally reach. Dunder / framework hook methods are skipped —
moving ``__init__`` or ``__eq__`` is never the intent.
"""

from __future__ import annotations

from typing import Any

from .models import RefactoringContext, RefactoringSuggestion
from .registry import RefactoringDetector, effort_bucket, register

# Distinct foreign members the method must access for the envy to be real
# (a single shared call is incidental, not envy).
_MIN_FOREIGN_MEMBERS = 2

# The method may touch at most this many distinct members of its OWN class —
# above this it still has a real reason to live where it is.
_MAX_OWN_MEMBERS = 1

# The method must be genuinely close to the target class, not merely closer
# than its (empty) own class: calling 2 methods of a 100-member god class
# (Jaccard distance ~0.98) is not envy, it is normal collaboration. A real
# move target shares a meaningful fraction of its members with the method.
_MAX_TARGET_DISTANCE = 0.7

# The target must be clearly nearer than the own class by this margin, so a
# near-tie (the method is about as related to both) never fires.
_MIN_DISTANCE_MARGIN = 0.25


def _is_test_path(path: str) -> bool:
    """Conservative test-file check (segment-aware).

    A test method that exercises the class under test calls into it heavily —
    that reads as feature envy but is never a move target, so test files are
    excluded on both ends (the method's file and the proposed destination).
    Catches both a ``test/`` / ``tests/`` directory anywhere in the path
    (including the first segment, e.g. Django's ``tests/app/tests.py``) and
    the usual per-file naming conventions."""
    p = path.lower().replace("\\", "/")
    segments = p.split("/")
    if any(seg in ("test", "tests", "__tests__") for seg in segments[:-1]):
        return True
    base = segments[-1]
    return (
        base in ("tests.py", "test.py", "conftest.py")
        or base.startswith("test_")
        or base.endswith(
            (
                "_test.py",
                "_test.go",
                ".test.ts",
                ".test.tsx",
                ".test.js",
                ".spec.ts",
                ".spec.js",
            )
        )
    )


def _node(graph: Any, node_id: str) -> dict | None:
    if node_id not in graph:
        return None
    return graph.nodes[node_id]


def _owning_class_id(graph: Any, callee_id: str) -> str | None:
    """The class node a callee belongs to: the method's parent class, or the
    class itself for a constructor call. ``None`` for free functions and
    unresolved callees (they carry no class membership)."""
    data = _node(graph, callee_id)
    if data is None:
        return None
    kind = data.get("kind")
    if kind == "method":
        parent = data.get("parent_name")
        file_path = data.get("file_path")
        if parent and file_path:
            class_id = f"{file_path}::{parent}"
            return class_id if class_id in graph else None
        return None
    if kind == "class":
        return callee_id
    return None


@register
class MoveMethodDetector(RefactoringDetector):
    name = "move_method"

    def detect(self, ctx: RefactoringContext) -> list[RefactoringSuggestion]:
        graph = ctx.graph
        if graph is None or _is_test_path(ctx.file_path):
            return []

        if ctx.file_methods is not None:
            methods = list(ctx.file_methods)
        else:
            methods = self._methods_in_file(graph, ctx.file_path)
        if not methods:
            return []

        members_cache: dict[str, set[str]] = {}
        out: list[RefactoringSuggestion] = []
        for method_id in methods:
            suggestion = self._envy_for(ctx, graph, method_id, members_cache)
            if suggestion is not None:
                out.append(suggestion)

        # Deterministic: impact is 0 for this graph-native type, so order by
        # the strength of the envy (foreign members accessed), then symbol.
        out.sort(key=lambda s: (-int(s.evidence.get("foreign_calls", 0)), s.target_symbol))
        return out

    def _methods_in_file(self, graph: Any, file_path: str) -> list[str]:
        """Symbol ids of methods defined in *file_path*, via ``defines`` edges
        (falling back to a prefix scan), sorted for determinism."""
        out: list[str] = []
        if file_path in graph:
            for _u, v, data in graph.out_edges(file_path, data=True):
                if data.get("edge_type") != "defines":
                    continue
                node = _node(graph, v)
                if node and node.get("kind") == "method":
                    out.append(v)
        if not out:
            prefix = f"{file_path}::"
            for node_id, data in graph.nodes(data=True):
                if (
                    data.get("node_type") == "symbol"
                    and data.get("kind") == "method"
                    and node_id.startswith(prefix)
                ):
                    out.append(node_id)
        return sorted(set(out))

    def _class_members(self, graph: Any, class_id: str, cache: dict[str, set[str]]) -> set[str]:
        cached = cache.get(class_id)
        if cached is not None:
            return cached
        members: set[str] = set()
        if class_id in graph:
            for _u, v, data in graph.out_edges(class_id, data=True):
                if data.get("edge_type") == "has_method":
                    members.add(v)
        cache[class_id] = members
        return members

    def _envy_for(
        self,
        ctx: RefactoringContext,
        graph: Any,
        method_id: str,
        members_cache: dict[str, set[str]],
    ) -> RefactoringSuggestion | None:
        data = _node(graph, method_id)
        if data is None:
            return None
        name = data.get("name") or ""
        if name.startswith("__"):  # dunder / hook methods don't move
            return None
        parent = data.get("parent_name")
        if not parent:
            return None
        own_class_id = f"{ctx.file_path}::{parent}"
        if own_class_id not in graph:
            return None

        # Group the method's class-owned callees by the class they belong to.
        accessed_by_class: dict[str, set[str]] = {}
        for _u, callee, edata in graph.out_edges(method_id, data=True):
            if edata.get("edge_type") != "calls" or callee == method_id:
                continue
            owner = _owning_class_id(graph, callee)
            if owner is None:
                continue
            accessed_by_class.setdefault(owner, set()).add(callee)

        accessed: set[str] = set()
        for members in accessed_by_class.values():
            accessed |= members
        if not accessed:
            return None

        own_accessed = accessed_by_class.get(own_class_id, set())
        own_distinct = len(own_accessed)
        if own_distinct > _MAX_OWN_MEMBERS:
            return None

        # Nearest foreign class by Jaccard distance (tie-break on class id).
        foreign = [(c, m) for c, m in accessed_by_class.items() if c != own_class_id]
        if not foreign:
            return None
        own_distance = self._distance(graph, own_class_id, accessed, members_cache)

        def _dist(item: tuple[str, set[str]]) -> tuple[float, str]:
            return (self._distance(graph, item[0], accessed, members_cache), item[0])

        target_id, target_accessed = min(foreign, key=_dist)
        target_distance = self._distance(graph, target_id, accessed, members_cache)
        foreign_distinct = len(target_accessed)

        if foreign_distinct < _MIN_FOREIGN_MEMBERS:
            return None
        if foreign_distinct <= own_distinct:
            return None
        if target_distance > _MAX_TARGET_DISTANCE:
            return None
        if own_distance - target_distance < _MIN_DISTANCE_MARGIN:
            return None

        target_node = _node(graph, target_id) or {}
        target_class = target_node.get("name") or target_id.rsplit("::", 1)[-1]
        target_file = target_node.get("file_path")
        if target_file and _is_test_path(target_file):
            return None  # never propose moving production code into a test class

        callers = self._caller_count(graph, method_id)
        plan = {
            "method": name,
            "from_class": parent,
            "to_class": target_class,
            "to_file": target_file,
        }
        evidence = {
            "foreign_calls": foreign_distinct,
            "own_calls": own_distinct,
            "own_distance": round(own_distance, 3),
            "target_distance": round(target_distance, 3),
        }
        blast_radius = {
            "callers": callers,
            "files": sorted({ctx.file_path, target_file} - {None}),
        }
        nloc = self._method_nloc(data)
        confidence = "high" if own_distinct == 0 and foreign_distinct >= 3 else "medium"
        return RefactoringSuggestion(
            refactoring_type=self.name,
            file_path=ctx.file_path,
            target_symbol=f"{parent}.{name}",
            line_start=data.get("start_line"),
            line_end=data.get("end_line"),
            plan=plan,
            evidence=evidence,
            impact_delta=0.0,
            effort_bucket=effort_bucket(nloc),
            blast_radius=blast_radius,
            confidence=confidence,
            source_biomarker="",
        )

    def _distance(
        self, graph: Any, class_id: str, accessed: set[str], cache: dict[str, set[str]]
    ) -> float:
        """Jaccard distance between the method's accessed-entity set and a
        class's members. 0 = the method only touches this class; 1 = no
        overlap. Empty union degrades to max distance."""
        members = self._class_members(graph, class_id, cache)
        union = accessed | members
        if not union:
            return 1.0
        return 1.0 - len(accessed & members) / len(union)

    @staticmethod
    def _caller_count(graph: Any, method_id: str) -> int:
        count = 0
        if method_id in graph:
            for _u, _v, data in graph.in_edges(method_id, data=True):
                if data.get("edge_type") == "calls":
                    count += 1
        return count

    @staticmethod
    def _method_nloc(data: dict) -> int:
        start = data.get("start_line")
        end = data.get("end_line")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            return end - start + 1
        return 0
