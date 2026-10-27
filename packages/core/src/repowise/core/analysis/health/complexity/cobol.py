"""Per-paragraph complexity for COBOL.

The COBOL grammar does not wrap a procedure in a node: a ``paragraph_header``
or ``section_header`` is a leaf, and its statements are the siblings that
follow it under ``procedure_division`` (as ``extractors/cobol.py``'s
``cobol_symbol_end_line`` explains). Control flow is flat the same way:
``if_header`` ... ``else_header`` ... ``END_IF`` are siblings, so nesting is
rebuilt from the openers, their scope terminators and the sentence period,
which closes every open scope.

One row per paragraph, per section with statements of its own (those before
its first paragraph), and per program with statements before its first
header. Counting follows ``cyclomatic.py``: a branch, loop or ``WHEN`` arm is
+1 CCN and ``1 + depth`` cognitive; ``ELSE IF`` and an ``AT END`` / ``INVALID
KEY`` / ``ON SIZE ERROR`` / ``ON EXCEPTION`` / ``ON OVERFLOW`` handler are +1
CCN and a flat +1 cognitive; ``AND`` / ``OR`` are +1 each; ``EVALUATE`` and
``SEARCH`` open a level and ``WHEN`` / ``WHEN OTHER`` open another, like a
switch and its cases. ``GO TO`` and ``ALTER`` are breaks in flow: a flat +1
cognitive. A handler's statements stay at its statement's depth (the ceiling
of not scoping them to their ``END-READ``). NLOC counts the non-blank lines of
the code view (comments and sequence areas blank) from the first statement to
the last, so a masked ``EXEC`` block counts every line it spans.

The same pass records each jump that leaves structured flow as a
``cobol_unstructured_flow`` hit on ``FileComplexity.perf_hits``: every
``ALTER``, every ``GO TO ... DEPENDING ON``, a ``GO TO`` with no target (the
one an ``ALTER`` rewrites), and any other ``GO TO`` unless it jumps forward to
an exit of its own range. An exit is a header named ``EXIT`` or ``...-EXIT``.
A range is a ``PERFORM X THRU Y`` in the same procedure division (or a
performed section, which ends at its last paragraph); a procedure from ``X``
through ``Y`` stays silent on a ``GO TO`` to a later exit up to ``Y``, and a
procedure in no range seen in the file stays silent on a ``GO TO`` to any
later exit. A name qualified ``OF``/``IN`` a section resolves in that section;
an unqualified name used in several sections resolves to the one in the
referring procedure's section, else the first after it, else the first.
Ceiling: the second case trusts the name, since its performer (the program
that copies a copybook, or nothing at all) is out of sight; nested ranges are
not told apart, so a jump to an enclosing range's exit stays silent.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.ingestion.cobol import code_view
from repowise.core.ingestion.extractors.cobol import normalize_cobol_symbol_name

from .dispatch import _MIN_ARMS, dispatch_share
from .models import FileComplexity, FunctionComplexity, PerfHit

if TYPE_CHECKING:
    from tree_sitter import Node

UNSTRUCTURED_FLOW = "cobol_unstructured_flow"

_HEADERS = frozenset({"paragraph_header", "section_header"})
# Children of the procedure division that are not statements of a body.
_NOT_STATEMENTS = frozenset({"procedure_using_chaining", "comment"})
# Scope opener -> the terminator that closes it.
_CLOSERS = {
    "END_IF": "if_header",
    "END_EVALUATE": "evaluate_header",
    "END_SEARCH": "search_statement",
    "END_PERFORM": "perform_statement_loop",
}
_ARMS = frozenset({"when", "when_other"})
_HANDLERS = frozenset({"at_end", "invalid_key", "on_size_error", "on_exception", "on_overflow"})
_JUMPS = frozenset({"goto_statement", "alter_statement"})
_BOOLEANS = frozenset({"AND", "OR"})
_WHEN = re.compile(rb"\bWHEN\b", re.IGNORECASE)
_LITERALS = re.compile(rb"'[^']*'|\"[^\"]*\"")


def walk_cobol(root: Node, source: bytes) -> FileComplexity:
    """Rows for every procedure in every program of the parsed file."""
    lines = code_view(source).split("\n")
    functions: list[FunctionComplexity] = []
    hits: list[PerfHit] = []
    for division in _procedure_divisions(root):
        ranges = _Ranges(division)
        for name, header, body in _procedures(division):
            functions.append(_measure(name, header, body, lines))
            hits.extend(_jumps(name, header, body, ranges))
    return FileComplexity(
        functions=functions,
        classes=[],
        file_nloc=sum(1 for line in lines if line.strip()),
        perf_hits=hits,
    )


def _label(node: Node) -> tuple[str, str]:
    """``(name, section)`` of a procedure label; ``section`` is "" unless qualified."""
    word = next((c for c in node.named_children if c.type == "qualified_word"), node)
    parts = [(c.text or b"").decode("utf-8", "replace").upper() for c in word.named_children]
    parts = parts or (node.text or b"").decode("utf-8", "replace").upper().split()[:1]
    return (parts[0], parts[-1] if len(parts) > 1 else "") if parts else ("", "")


def _is_exit(name: str) -> bool:
    return name == "EXIT" or name.endswith("-EXIT")


def _performs(division: Node) -> list[Node]:
    """Every ``perform_procedure`` (the ``X [THRU Y]`` of a PERFORM) under *division*."""
    found: list[Node] = []
    stack = [division]
    while stack:
        node = stack.pop()
        if node.type == "perform_procedure":
            found.append(node)
        else:
            stack.extend(node.named_children)
    return found


class _Ranges:
    """The headers of one procedure division and the PERFORM ranges over them."""

    def __init__(self, division: Node) -> None:
        # Per header: name, kind, the section it is in, and where it starts.
        self.order: list[tuple[str, str, str]] = []
        self.starts: list[int] = []
        self._collect_headers(division)
        self.where: dict[str, list[int]] = {}
        for i, (name, _, _) in enumerate(self.order):
            self.where.setdefault(name, []).append(i)
        # Header index -> the furthest end of a range it sits in.
        self.bound: dict[int, int] = {}
        for perform in _performs(division):
            span = self._span(perform)
            if span is not None:
                start, end = span
                for i in range(start, end + 1):
                    self.bound[i] = max(self.bound.get(i, end), end)

    def _collect_headers(self, division: Node) -> None:
        section = ""
        for child in division.named_children:
            if child.type not in _HEADERS:
                continue
            raw = (child.text or b"").decode("utf-8", "replace")
            name = normalize_cobol_symbol_name(raw, child.type)
            if child.type == "section_header":
                section = name
            self.order.append((name, child.type, section))
            self.starts.append(child.start_byte)

    def _span(self, perform: Node) -> tuple[int, int] | None:
        """First and last header a ``perform_procedure`` runs, or None if unresolved."""
        labels = [_label(c) for c in perform.named_children if c.type == "label"]
        if not labels:
            return None
        start = self.find(*labels[0], at=bisect_right(self.starts, perform.start_byte) - 1)
        end = None if start is None else self.find(*labels[-1], at=start)
        if start is None or end is None or end < start:
            return None
        return start, self._last(end)

    def at(self, header: Node) -> int:
        """The header's index; -1 for a program's leading statements."""
        i = bisect_right(self.starts, header.start_byte) - 1
        return i if i >= 0 and self.starts[i] == header.start_byte else -1

    def find(self, name: str, section: str, at: int) -> int | None:
        """The header *name* (qualified by *section*) means when used from header *at*."""
        found = self.where.get(name, [])
        if section:
            found = [i for i in found if self.order[i][2] == section]
        if len(found) < 2:
            return found[0] if found else None
        here = self.order[at][2] if at >= 0 else ""
        same = [i for i in found if self.order[i][2] == here]
        later = [i for i in found if i > at]
        return (same or later or found)[0]

    def _last(self, i: int) -> int:
        """A section runs to the header before the next section."""
        if self.order[i][1] != "section_header":
            return i
        j = i + 1
        while j < len(self.order) and self.order[j][1] != "section_header":
            j += 1
        return j - 1

    def exits_to(self, header: Node, target: tuple[str, str]) -> bool:
        """Whether ``GO TO target`` from *header* jumps forward to an exit of its range."""
        at = self.at(header)
        to = self.find(*target, at=at)
        return (
            to is not None
            and _is_exit(target[0])
            and at < to <= self.bound.get(at, len(self.order) - 1)
        )


def _jumps(name: str, header: Node, body: list[Node], ranges: _Ranges) -> list[PerfHit]:
    """The procedure's jumps that leave structured flow (see the module doc)."""
    hits: list[PerfHit] = []
    for node in body:
        if node.type not in _JUMPS:
            continue
        text = " ".join((node.text or b"").decode("utf-8", "replace").split())
        if node.type == "goto_statement":
            labels = [_label(c) for c in node.named_children if c.type == "label"]
            if (
                len(labels) == 1
                and "DEPENDING" not in text.upper()
                and ranges.exits_to(header, labels[0])
            ):
                continue
        hits.append(
            PerfHit(
                kind=UNSTRUCTURED_FLOW,
                line=node.start_point[0] + 1,
                function=name,
                detail=text,
                func_start=header.start_point[0] + 1,
            )
        )
    return hits


def _procedure_divisions(root: Node) -> list[Node]:
    found: list[Node] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "procedure_division":
            found.append(node)
        else:
            stack.extend(reversed(node.children))
    return found


def _procedures(division: Node) -> list[tuple[str, Node, list[Node]]]:
    """``(name, header, statements)`` per procedure, statements grouped under
    the header before them. The leading statements belong to the program.
    """
    procedures: list[tuple[str, Node, list[Node]]] = []
    name, header, body = _program_name(division), division, []
    for child in division.named_children:
        if child.type in _HEADERS:
            if body and name:
                procedures.append((name, header, body))
            raw = (child.text or b"").decode("utf-8", "replace")
            name, header, body = normalize_cobol_symbol_name(raw, child.type), child, []
        elif child.type not in _NOT_STATEMENTS:
            body.append(child)
    # A copybook's leading statements have no program to name them; they get no row.
    if body and name:
        procedures.append((name, header, body))
    return procedures


def _program_name(division: Node) -> str:
    program = division.parent
    if program is None or program.type != "program_definition":
        return ""
    ident = next((c for c in program.named_children if c.type == "identification_division"), None)
    raw = (ident.text or b"").decode("utf-8", "replace") if ident is not None else ""
    return (
        normalize_cobol_symbol_name(raw, "program_definition")
        if "PROGRAM-ID" in raw.upper()
        else ""
    )


def _measure(name: str, header: Node, body: list[Node], lines: list[str]) -> FunctionComplexity:
    ccn, cognitive, max_nesting = 1, 0, 0
    # Open scopes, innermost last: an opener's node type, or "when" for an arm.
    stack: list[str] = []
    # A top-level EVALUATE of one value is a dispatch (see ``dispatch.py``).
    tally: _Dispatch | None = None
    best = _Dispatch()

    def charge(points: int) -> None:
        nonlocal ccn
        ccn += points
        if tally is not None:
            tally.points += points
            tally.arm += points

    def close_dispatch() -> None:
        nonlocal tally, best
        if tally is not None:
            tally.heaviest = max(tally.heaviest, tally.arm)
            if tally.arms >= _MIN_ARMS and tally.points > best.points:
                best = tally
        tally = None

    def open_scope(kind: str, points: int) -> None:
        nonlocal cognitive, max_nesting
        charge(points)
        cognitive += 1 + len(stack)
        stack.append(kind)
        max_nesting = max(max_nesting, len(stack))

    for node in body:
        kind = node.type
        if kind == "period":
            stack.clear()
            close_dispatch()
        elif kind in _CLOSERS:
            opener = _CLOSERS[kind]
            if opener in stack:
                del stack[len(stack) - 1 - stack[::-1].index(opener) :]
                if not stack:
                    close_dispatch()
        elif kind in _ARMS:
            if stack and stack[-1] == "when":
                stack.pop()
            # Stacked ``WHEN a WHEN b`` parse as one arm; each selection is a point.
            points = _selections(node) if kind == "when" else 1
            open_scope("when", points)
            if tally is not None and len(stack) == 2:
                # The arm's own points are the dispatch's, not its body's.
                tally.arms += 1
                tally.heaviest = max(tally.heaviest, tally.arm - points)
                tally.arm = 0
        elif kind in ("if_header", "search_statement"):
            open_scope(kind, 1)
        elif kind == "perform_statement_loop":
            open_scope(kind, 1 if _has_option(node) else 0)
        elif kind == "evaluate_header":
            if not stack and _tests_one_value(node):
                tally = _Dispatch()
            open_scope(kind, 0)
        elif kind == "perform_statement_call_proc" and _has_option(node):
            # An out-of-line loop: its body is another paragraph.
            charge(1)
            cognitive += 1 + len(stack)
            max_nesting = max(max_nesting, len(stack) + 1)
        elif kind in _HANDLERS or kind == "else_if_header":
            charge(1)
            cognitive += 1
        elif kind in _JUMPS:
            cognitive += 1
        operators = _boolean_operators(node)
        charge(operators)
        cognitive += operators
    close_dispatch()

    first = body[0].start_point[0]
    last = body[-1].end_point[0]
    return FunctionComplexity(
        name=name,
        start_line=header.start_point[0] + 1,
        end_line=last + 1,
        ccn=ccn,
        max_nesting=max_nesting,
        cognitive=cognitive,
        nloc=sum(1 for row in range(first, last + 1) if row < len(lines) and lines[row].strip()),
        dispatch_share=dispatch_share(best.points, ccn),
        dispatch_arm=best.heaviest,
    )


@dataclass
class _Dispatch:
    """A dispatch's arms, its points, the open arm's points and the heaviest arm's."""

    arms: int = 0
    points: int = 0
    arm: int = 0
    heaviest: int = 0


def _has_option(perform: Node) -> bool:
    """``UNTIL`` / ``VARYING`` / ``TIMES``: the PERFORM loops."""
    return any(c.type == "perform_option" for c in perform.named_children)


def _tests_one_value(evaluate: Node) -> bool:
    """``EVALUATE X``, not ``EVALUATE TRUE`` (an IF chain) or ``EVALUATE X ALSO Y``."""
    subjects = [c for c in evaluate.named_children if c.type == "evaluate_subject"]
    return len(subjects) == 1 and all(
        c.type not in ("TRUE", "FALSE") for c in subjects[0].named_children
    )


def _selections(when: Node) -> int:
    """The ``WHEN`` keywords in an arm, string literals aside."""
    text = _LITERALS.sub(b"", when.text or b"")
    return max(1, len(_WHEN.findall(text)))


def _boolean_operators(node: Node) -> int:
    count = 0
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in _BOOLEANS:
            count += 1
        else:
            stack.extend(current.children)
    return count
