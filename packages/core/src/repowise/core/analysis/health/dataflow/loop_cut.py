"""What a staged split cut from a loop body must respect.

A stage inside a loop body runs once per iteration, called by the function,
which keeps the loop. Two things the lines alone do not show:

- **Jumps.** A ``break`` / ``continue`` inside a stage must stay inside it: one
  that leaves it would leave the helper instead (:func:`escapes`).
- **Carried values.** A value one iteration writes and the next reads must come
  back out of the stage writing it, or the function would hand the next
  iteration the old value (:func:`carried`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from .reaching import definitions_at, observed_definitions
from .slice import _statements_by_line
from .span import is_comment

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .analyze import FunctionAnalysis


def escapes(st: Node, lmap: LanguageNodeMap) -> bool:
    """Whether *st* holds a ``break`` / ``continue`` that leaves it."""
    scopes = lmap.function_kinds | lmap.lambda_kinds
    # A C-family ``switch`` (Go's ``select``) holds a ``break``; a Python,
    # Rust or Kotlin ``match`` / ``when`` does not.
    switches = frozenset(k for k in lmap.switch_kinds if "switch" in k or k == "select_statement")
    stack: list[tuple[Node, bool, bool]] = [(st, False, False)]
    while stack:
        node, in_loop, in_switch = stack.pop()
        if node.type in lmap.break_kinds or node.type in lmap.continue_kinds:
            if _leaves(node, in_loop, in_switch, lmap):
                return True
            continue
        # Only the loop's body repeats: a ``break`` in a Python ``for ... else``
        # leaves the loop around it.
        body = node.child_by_field_name("body") if node.type in lmap.loop_kinds else None
        switch = in_switch or node.type in switches
        for child in node.children:
            if child.type not in scopes:
                looped = in_loop or (body is not None and child.id == body.id)
                stack.append((child, looped, switch))
    return False


def _leaves(jump: Node, in_loop: bool, in_switch: bool, lmap: LanguageNodeMap) -> bool:
    """Whether *jump* leaves the statement it was found in: a labelled one
    always may, else one no enclosing loop body (or switch, for ``break``)
    inside it holds."""
    if any(c.type.endswith("identifier") for c in jump.named_children):
        return True
    return not (in_loop or (in_switch and jump.type in lmap.break_kinds))


def carried(
    analysis: FunctionAnalysis, loops: tuple[Node, ...], def_lines: dict[str, list[int]]
) -> frozenset[str]:
    """Names some loop in *loops* carries into its next iteration. Every read
    counts, a may-def's paired use included: it is how a value passes a write
    that may not happen."""
    defs = analysis.reaching.definitions
    reads: dict[str, list[tuple[int, list[int]]]] = defaultdict(list)
    for use, seen in observed_definitions(analysis.def_use, analysis.reaching):
        reads[use.name].append((use.line, [defs[i].line for i in seen]))
    statements = _statements_by_line(analysis.cfg)
    out: set[str] = set()
    for loop in loops:
        out |= _carried_in(analysis, loop, statements, reads, def_lines)
    return frozenset(out)


def _carried_in(
    analysis: FunctionAnalysis,
    loop: Node,
    statements: dict[int, tuple[int, int]],
    reads: dict[str, list[tuple[int, list[int]]]],
    def_lines: dict[str, list[int]],
) -> set[str]:
    """Names *loop* carries: written in its body by a definition reaching the
    body's first statement (around the back edge), and read in the loop where
    a value from outside it may still arrive, so a later iteration's read sees
    the earlier one's write."""
    body = loop.child_by_field_name("body")
    code = [c for c in body.named_children if not is_comment(c)] if body is not None else []
    at = statements.get(code[0].start_point[0] + 1) if code else None
    if at is None:
        return set()
    defs = analysis.reaching.definitions
    lo, hi = loop.start_point[0] + 1, loop.end_point[0] + 1
    b_lo, b_hi = body.start_point[0] + 1, body.end_point[0] + 1
    out = set()
    for var, lines in def_lines.items():
        if not any(b_lo <= ln <= b_hi for ln in lines):
            continue
        entering = definitions_at(analysis.def_use, analysis.reaching, at[0], var, at[1])
        if not any(b_lo <= defs[i].line <= b_hi for i in entering):
            continue
        if any(
            lo <= ln <= hi and any(d < lo or d > hi for d in seen)
            for ln, seen in reads.get(var, ())
        ):
            out.add(var)
    return out


__all__ = ["carried", "escapes"]
