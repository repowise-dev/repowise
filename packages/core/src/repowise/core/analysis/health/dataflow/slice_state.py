"""State a span shares with code it does not hold: closure-shared locals and loop-carried values."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from typing import TYPE_CHECKING, NamedTuple

from .slice_liveness import _declaration_lines
from .slice_nodes import _descend, _loop_header_names, _receiver_root

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .analyze import FunctionAnalysis
    from .defuse import FunctionDefUse


class _SharedState(NamedTuple):
    """Per variable a closure shares with the function, sorted line lists:
    the closures' reads and writes (``Captured.shared`` / ``.writes``), the
    function's own defs, all its references, and its binding declarations."""

    reads: dict[str, list[int]]
    writes: dict[str, list[int]]
    defs: dict[str, list[int]]
    refs: dict[str, list[int]]
    binds: dict[str, frozenset[int]]


def _closure_state(
    def_use: FunctionDefUse,
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
) -> _SharedState | None:
    """The closure-shared locals of a function, or None when it has none
    (then the gate costs nothing per span)."""
    reads: dict[str, list[int]] = defaultdict(list)
    writes: dict[str, list[int]] = defaultdict(list)
    for out, occurrences in (
        (reads, def_use.captured.shared),
        (writes, def_use.captured.writes),
    ):
        for occ in occurrences:
            if occ.name in def_lines:  # a name this function binds
                out[occ.name].append(occ.line)
    if not reads and not writes:
        return None
    names = set(reads) | set(writes)
    return _SharedState(
        reads={var: sorted(lines) for var, lines in reads.items()},
        writes={var: sorted(lines) for var, lines in writes.items()},
        defs={var: def_lines.get(var, []) for var in names},
        refs={var: sorted((*def_lines.get(var, ()), *use_lines.get(var, ()))) for var in names},
        binds=_declaration_lines(def_use, binding=True),
    )


def _count_in(lines: list[int], s: int, e: int) -> int:
    return bisect_right(lines, e) - bisect_left(lines, s)


def _closure_state_crosses(
    st: _SharedState, span: list[Node], s: int, e: int, block: Node, lmap: LanguageNodeMap
) -> bool:
    """True when a local a closure shares crosses the span boundary.

    A closure runs when it is called, not where it is written, so line
    liveness cannot place its reads and writes; an IN/OUT signature copies a
    value where the closure needs the variable itself. Refused:

    - a closure in the span writes a local anything outside the span refers
      to (code, or a closure there): the lifted closure writes the helper's
      copy. A counter declared in the span with its ``inc`` closure is
      refused this way when the count is read after the span;
    - a closure outside the span writes a local the span refers to: the span
      reads or writes a stale copy;
    - the span writes a local that a closure written above it reads: the
      closure runs later and sees the old value.

    The last two are waived when the span's name is a binding of its own
    (:func:`_span_binds`, :func:`_span_only_rebinds`): the closure's variable
    is then another one.
    """
    for var in st.reads.keys() | st.writes.keys():
        writes = st.writes.get(var, [])
        reads = st.reads.get(var, [])
        refs = st.refs[var]
        w_in = _count_in(writes, s, e)
        if w_in and (
            w_in < len(writes)
            or _count_in(reads, s, e) < len(reads)
            or _count_in(refs, s, e) < len(refs)
        ):
            return True
        stale = w_in < len(writes) and _count_in(refs, s, e) > 0
        if stale and not _span_binds(st, var, span, block, lmap):
            return True
        overwritten = bool(reads) and reads[0] < s and _count_in(st.defs[var], s, e) > 0
        if overwritten and not _span_only_rebinds(st, var, s, e, block, lmap):
            return True
    return False


def _declares_in(node: Node, lines: frozenset[int], lmap: LanguageNodeMap) -> bool:
    """True when *node* is a declaration statement holding one of *lines*."""
    lo, hi = node.start_point[0] + 1, node.end_point[0] + 1
    return node.type in lmap.local_decl_kinds and any(lo <= ln <= hi for ln in lines)


def _declared_above(binds: frozenset[int], block: Node, s: int, lmap: LanguageNodeMap) -> bool:
    """True when a statement of *block* above the span already declares the
    name: a re-declaration in the same scope (Go's ``v, err := h()``) is an
    assignment to it, not a new binding."""
    return any(
        node.end_point[0] + 1 < s and _declares_in(node, binds, lmap)
        for node in block.named_children
    )


def _span_binds(
    st: _SharedState, var: str, span: list[Node], block: Node, lmap: LanguageNodeMap
) -> bool:
    """True when one of the span's own top-level statements declares a new
    binding of *var*, so every reference to it in the span is to that one.

    Top-level only: a declaration in a nested block shadows just that block.
    """
    binds = st.binds.get(var, frozenset())
    s = span[0].start_point[0] + 1
    return (
        bool(binds)
        and not _declared_above(binds, block, s, lmap)
        and any(_declares_in(node, binds, lmap) for node in span)
    )


def _span_only_rebinds(
    st: _SharedState, var: str, s: int, e: int, block: Node, lmap: LanguageNodeMap
) -> bool:
    """True when every write the span makes to *var* creates a new binding
    (a declaration or a loop binder, at any depth), so none of them reaches
    the variable a closure outside the span reads."""
    binds = st.binds.get(var, frozenset())
    defs = st.defs[var][bisect_left(st.defs[var], s) : bisect_right(st.defs[var], e)]
    return all(ln in binds for ln in defs) and not _declared_above(binds, block, s, lmap)


def _rebinding_defs_dropped(
    analysis: FunctionAnalysis, def_lines: dict[str, list[int]], lmap: LanguageNodeMap
) -> dict[str, list[int]]:
    """*def_lines* without the names exception handlers bind on their head
    line (``except E as e:``, ``catch (err)``). Such a binding never reaches
    the next iteration: Python unbinds it at the handler's end and a catch
    parameter is scoped to its block, so a read of the name elsewhere in the
    loop is another handler's own."""
    heads = _catch_heads(analysis, lmap)
    if not heads:
        return def_lines
    bound: set[tuple[str, int]] = set()
    for head in heads:
        bound |= _head_bindings(head, analysis.def_use, lmap)
    return {var: [ln for ln in lines if (var, ln) not in bound] for var, lines in def_lines.items()}


def _catch_heads(analysis: FunctionAnalysis, lmap: LanguageNodeMap) -> list[Node]:
    return [
        st.node
        for block in analysis.cfg.blocks
        for st in block.statements
        if st.is_head and st.kind in lmap.catch_kinds and st.node is not None
    ]


def _head_bindings(
    head: Node, def_use: FunctionDefUse, lmap: LanguageNodeMap
) -> set[tuple[str, int]]:
    # Only the clause's own binder: before its body, so a one-line body's
    # write on the same line still counts.
    body = next((c for c in head.named_children if c.type in lmap.block_kinds), None)
    end = body.start_point if body is not None else head.end_point
    return {
        (d.var, d.line)
        for d in def_use.definitions
        if head.start_point <= (d.line - 1, d.column) < end
    }


def _loop_carry_free(
    span: list[Node],
    loop: Node,
    s: int,
    e: int,
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    lmap: LanguageNodeMap,
) -> bool:
    """True when a span nested in *loop* carries no state between iterations.

    Refused: a variable the span writes that the loop reads with no in-span
    write before the read (the read takes the previous iteration's value, even
    when it sits inside the span above the write), and a call on a name the
    loop header reads (``entries.remove(entry)`` in ``for entry in entries``).
    """
    if _carries_a_write(loop.start_point[0] + 1, s, e, def_lines, use_lines):
        return False
    carried = _loop_header_names(loop, lmap)
    return not carried or not _mutates_carried(span, carried, lmap)


def _carries_a_write(
    loop_start: int,
    s: int,
    e: int,
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
) -> bool:
    for var, lines in def_lines.items():
        in_span_writes = [ln for ln in lines if s <= ln <= e]
        if not in_span_writes:
            continue
        for read in use_lines.get(var, []):
            if loop_start <= read <= e and not any(write < read for write in in_span_writes):
                return True
    return False


def _mutates_carried(span: list[Node], carried: set[str], lmap: LanguageNodeMap) -> bool:
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    for st in span:
        for call in _descend(st, lmap.call_kinds, scope_kinds):
            callee = call.child_by_field_name("function")
            if callee is None:
                named = call.named_children
                callee = named[0] if named else None
            root = _receiver_root(callee) if callee is not None else None
            if root is not None and root in carried:
                return True
    return False
