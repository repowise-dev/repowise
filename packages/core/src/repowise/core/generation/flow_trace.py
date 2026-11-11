"""Trace one operation's steps from its entry function.

The walk keeps the callees that carry the operation and leaves out the ones
that only present it:

* **Skip filter.** A callee whose name or file says it prints, logs, styles
  or hints (``output``, ``logger``, ``hint``, ``progress`` ...) is listed as
  skipped, not traced, and so is a call made on a ``with`` line (a context
  manager sets up, it does not do the work).
* **Branch discounts.** Each call is classed by the block around it: always,
  conditional, loop, early return or error handler. A call under a branch
  weighs less than one on the main path, and the discount passes to its
  descendants, so the happy path is expanded first. A handler gets one step
  and no children: the page names it as a way the operation ends.
* **Heaviest first.** Kept callees go on one priority queue by weight
  (breadth reached, divided down by fan-in, see ``flow_pages._step_weight``),
  decayed per depth, and the queue is expanded until the step cap.

Branch kinds come from the caller's source lines around the recorded call
line (indentation and the block keyword above it). The parser's call sites do
not carry the enclosing block, so this reads the text; without source every
call counts as ``always`` and the walk is the weight order alone.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from repowise.core.analysis.execution_graph import call_line_values

#: ``path -> lines``; empty when the file cannot be read.
LineReader = Callable[[str], list[str]]

MAX_STEPS = 20
MAX_DEPTH = 4
# A node with this many fresh callees or fewer keeps them all (a thin orchestrator).
THIN_ORCHESTRATOR = 8
FANOUT = (6, 6, 3, 2)
DEPTH_DECAY = 0.6
BRANCH_DISCOUNT = {"conditional": 0.35, "early_exit": 0.12, "handler": 0.2, "loop": 0.9}

# Callees that present, log or hint rather than do the operation's work.
# Matched against name tokens and path segments: generic words only.
_PLUMBING = frozenset(
    ["output", "outputs", "logger", "logging", "ui", "console", "print", "echo", "hint", "hints", "mascot", "progress", "spinner", "banner", "emoji", "width", "color", "colour", "telemetry", "tip", "tips", "display", "panel", "notice", "notices", "announce"]
)
_TOKEN_SPLIT = re.compile(r"[_\W]+|(?<=[a-z])(?=[A-Z])")
_WITH = re.compile(r"\s*(async\s+)?with\b")
_ONE_LINE_GUARD = re.compile(r"(if|elif|else)\b[^#]*:\s*return\s")
_BRANCH_HEADS = frozenset({"if", "elif", "else", "match", "case", "switch"})
_HANDLER_HEADS = frozenset({"except", "catch", "finally"})
_SCOPE_HEADS = frozenset({"def", "function", "class", "func"})


@dataclass(frozen=True)
class FlowStep:
    """One function on the walk, with what a citation and an excerpt need."""

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
    # always / conditional / loop / early_exit / handler, from the caller's block.
    context: str = "always"
    # Lines where this step calls a callee the walk did not keep.
    other_calls: tuple[int, ...] = ()


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(text.lower()) if t}


def is_plumbing(attrs: dict, node: str) -> bool:
    """Whether a callee presents or logs rather than works, by its name or file."""
    name = attrs.get("name") or node.rsplit("::", 1)[-1]
    path = PurePosixPath(attrs.get("file_path") or "")
    words = _tokens(name) | _tokens(path.stem) | {s.lower() for s in path.parts[:-1]}
    return bool(words & _PLUMBING)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _block_kinds(lines: list[str], call_line: int, def_line: int) -> list[str]:
    """Keywords of the blocks enclosing *call_line*, innermost first, up to the def."""
    kinds: list[str] = []
    indent = _indent(lines[call_line - 1])
    for i in range(call_line - 2, max(def_line - 1, 0) - 1, -1):
        if indent == 0:
            break
        # ``} catch (e) {`` / ``} else {`` open a block like any other head.
        stripped = lines[i].strip().lstrip("}").strip()
        if not stripped or stripped.startswith(("#", "//", ")", "]")) or _indent(lines[i]) >= indent:
            continue
        words = stripped.split()
        head = words[1] if words[0] == "async" and len(words) > 1 else words[0]
        head = head.rstrip(":{")
        if head in _SCOPE_HEADS:
            break
        kinds.append(head)
        indent = _indent(lines[i])
    return kinds


def call_context(lines: list[str], call_line: int | None, def_line: int | None) -> str:
    """How a call runs: always, conditional, loop, early_exit, handler or with."""
    if not call_line or call_line > len(lines):
        return "always"
    text = lines[call_line - 1]
    if _WITH.match(text):
        return "with"
    kinds = _block_kinds(lines, call_line, def_line or 1)
    if any(k in _HANDLER_HEADS for k in kinds):
        return "handler"
    if any(k in ("for", "while") for k in kinds):
        return "loop"
    branched = any(k in _BRANCH_HEADS for k in kinds)
    if _returns_early(text.strip(), branched):
        return "early_exit"
    return "conditional" if branched else "always"


def _returns_early(text: str, branched: bool) -> bool:
    """``if x: return f()``, on one line or as a return inside a branch: another way it ends."""
    return bool(_ONE_LINE_GUARD.match(text)) or (branched and text.startswith("return "))


@dataclass
class _Walk:
    """State of one trace: what is kept, how each was called, what was left out."""

    index: Any
    weight: Callable[[str], float]
    read: LineReader | None
    kept: dict[str, list[str]] = field(default_factory=dict)
    context: dict[str, str] = field(default_factory=dict)
    factor: dict[str, float] = field(default_factory=dict)
    other_calls: dict[str, list[int]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def call_line(self, caller: str, callee: str) -> int | None:
        lines = call_line_values(self.index.graph.edges[caller, callee])
        return min(lines) if lines else None

    def classify(self, node: str) -> list[tuple[str, str]]:
        """Fresh callees of *node* with their call kind; plumbing and ``with`` are skipped."""
        attrs = self.index.attrs(node)
        lines = self.read(attrs.get("file_path") or "") if self.read else []
        fresh: list[tuple[str, str]] = []
        for callee in self.index.successors(node):
            if callee in self.kept or callee == node:
                continue
            line = self.call_line(node, callee)
            kind = call_context(lines, line, attrs.get("start_line"))
            if kind == "with" or is_plumbing(self.index.attrs(callee), callee):
                self._skip(callee)
                continue
            fresh.append((callee, kind))
        return fresh

    def _skip(self, callee: str) -> None:
        name = self.index.attrs(callee).get("name") or callee.rsplit("::", 1)[-1]
        if name not in self.skipped:
            self.skipped.append(name)

    def choose(self, fresh: list[tuple[str, str]], depth: int) -> list[tuple[str, str]]:
        """The callees to keep: main path first, at most one error handler."""
        fresh.sort(key=lambda item: (-self.weight(item[0]) * BRANCH_DISCOUNT.get(item[1], 1.0), item[0]))
        keep = len(fresh) if len(fresh) <= THIN_ORCHESTRATOR else FANOUT[min(depth, len(FANOUT) - 1)]
        chosen: list[tuple[str, str]] = []
        handlers = 0
        for callee, kind in fresh:
            if kind == "handler":
                if handlers:
                    continue
                handlers += 1
            chosen.append((callee, kind))
            if len(chosen) >= keep:
                break
        return chosen

    def expand(self, root: str) -> None:
        self.kept[root] = []
        self.context[root] = "always"
        self.factor[root] = 1.0
        heap: list[tuple[float, int, str, int]] = [(0.0, 0, root, 0)]
        counter = 0
        while heap and len(self.kept) < MAX_STEPS:
            _, _, node, depth = heapq.heappop(heap)
            # Error and interrupt exits get one step, never a subtree.
            if depth >= MAX_DEPTH or self.context[node] == "handler":
                continue
            fresh = self.classify(node)
            chosen = self.choose(fresh, depth)
            taken = {c for c, _ in chosen}
            self.other_calls[node] = sorted(
                {ln for c, _ in fresh if c not in taken and (ln := self.call_line(node, c))}
            )
            for callee, kind in chosen:
                if len(self.kept) >= MAX_STEPS:
                    break
                counter += 1
                prio = self._keep(node, callee, kind, depth + 1)
                heapq.heappush(heap, (-prio, counter, callee, depth + 1))

    def _keep(self, parent: str, callee: str, kind: str, depth: int) -> float:
        """Record *callee* under *parent*; return its expansion priority."""
        self.kept[parent].append(callee)
        self.kept[callee] = []
        self.context[callee] = kind
        self.factor[callee] = self.factor[parent] * BRANCH_DISCOUNT.get(kind, 1.0)
        return self.weight(callee) * self.factor[callee] * DEPTH_DECAY**depth


def trace(
    index: Any,
    root: str,
    weight: Callable[[str], float],
    make_step: Callable[..., FlowStep],
    read: LineReader | None = None,
) -> tuple[tuple[FlowStep, ...], tuple[str, ...]]:
    """The root and the callees that carry the operation, in reading order, plus skipped names.

    Chosen heaviest first, then numbered depth first with each node's callees
    in the order the code calls them, so step N's caller is always an earlier
    step and the list reads like the code runs.
    """
    walk = _Walk(index, weight, read)
    walk.expand(root)
    steps: list[FlowStep] = []

    def visit(node: str, depth: int, parent: int, call_line: int | None) -> None:
        steps.append(
            make_step(
                node, depth, parent, call_line,
                context=walk.context.get(node, "always"),
                other_calls=tuple(walk.other_calls.get(node, ())),
            )
        )
        here = len(steps) - 1
        calls = sorted((walk.call_line(node, c) or 0, c) for c in walk.kept[node])
        for line, child in calls:
            visit(child, depth + 1, here, line or None)

    visit(root, 0, -1, None)
    names = {s.name.rsplit(".", 1)[-1] for s in steps}
    return tuple(steps), tuple(n for n in walk.skipped if n not in names)
