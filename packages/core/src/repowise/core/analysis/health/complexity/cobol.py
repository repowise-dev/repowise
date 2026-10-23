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
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from repowise.core.ingestion.cobol import code_view
from repowise.core.ingestion.extractors.cobol import normalize_cobol_symbol_name

from .dispatch import _MIN_ARMS, dispatch_share
from .models import FileComplexity, FunctionComplexity

if TYPE_CHECKING:
    from tree_sitter import Node

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
    for division in _procedure_divisions(root):
        for name, header, body in _procedures(division):
            functions.append(_measure(name, header, body, lines))
    return FileComplexity(
        functions=functions, classes=[], file_nloc=sum(1 for line in lines if line.strip())
    )


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
